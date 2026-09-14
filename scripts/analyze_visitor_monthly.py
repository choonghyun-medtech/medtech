#!/usr/bin/env python3
"""
visitor_stats.json(scripts/scrape_visitor_stats.py가 갱신)의 월별 방한 외국인 총 방문자수·
국가별 방문자수에서 이번 달 유의미한 변동을 골라 LLM으로 서술형 분석을 생성해
medtour_monthly_analysis.json의 "visitor" 섹션으로 추가한다(analyze_medtour_monthly.py가
쓰는 "medtour" 섹션과 같은 파일, 같은 target_month 키를 공유 — monthly_analysis_common.
load_months_file/save_months_file 참고).

- 별도 스크립트/워크플로로 분리한 이유(2026-09-14 사용자 지시): 방한외국인 통계는
  한국관광데이터랩 갱신 시차가 의료관광 소비 통계보다 길어("9월 말이면 8월 데이터가
  들어옴") update-visitor-stats.yml의 수집 창(그 달 마지막 이틀 + 다음달 처음 이틀)이
  update-medical-tour.yml(매월 12~15일)보다 훨씬 늦게 끝난다. 같은 워크플로에서 같이
  분석했다면 방한외국인 데이터를 기다리느라 의료관광 소비 분석 자체가 매달 지연됐을 것 —
  그래서 스케줄(매월 3일)과 스크립트를 완전히 분리해서, 의료관광 소비 분석(매월 16일)이
  방한외국인 때문에 지연되지 않게 한다.
- target_month는 "고정된 달력 날짜"가 아니라 visitor_stats.json에 실제로 들어있는 가장
  최신 ym을 그대로 쓴다 — analyze_medtour_monthly.py가 같은 달에 먼저 써둔 "medtour"
  섹션이 있으면 그 옆에 "visitor" 섹션만 추가되고, 아직 없으면(아주 드물게 방한외국인
  데이터가 의료관광 소비 데이터보다 먼저 들어온 달) "visitor" 섹션만 있는 채로 저장했다가
  다음 medtour 실행 때 같은 키에 합쳐진다.
- 이 단계도 "보강" 단계라 실패해도 sys.exit(1)로 워크플로를 실패시키지 않는다.

사용법:
    python analyze_visitor_monthly.py --data visitor_stats.json --out medtour_monthly_analysis.json
"""
import argparse
import datetime
import json
import re
import sys

from monthly_analysis_common import (
    DailyQuotaExhausted,
    build_object_provider,
    call_llm_with_retries,
    fmt_pct,
    fmt_rank,
    is_notable,
    load_months_file,
    month_stats,
    save_months_file,
)

MAX_TOKENS = 1500

SYSTEM_PROMPT = """당신은 한국 증권사의 의료관광/여행 담당 애널리스트를 돕는 리서치
보조원입니다. 아래에 이번 달 "방한 외국인" 방문자 통계 중 유의미하게 변동했거나 중요하다고
이미 걸러진 사실(전월비/전년동월비/역대 순위/국가별 비중 등, 전부 파이썬으로 미리 계산된
값)이 주어집니다. 이 사실들만 근거로 자연스러운 한국어 서술형 분석을 작성하세요.

규칙:
- 반드시 주어진 사실(숫자 포함)만 사용하세요. 목록에 없는 수치를 새로 계산하거나 추측하지
  마세요.
- "전체 방문자수"를 먼저 쓰고, 그다음 국가별(중국/일본/대만/미국) 중 유의미한 변동이 있는
  국가 순으로 쓰세요.
- 문체는 리서치 노트 스타일 개조식으로 쓰세요("~함", "~로 확인됨", "~로 부각" 등 명사형/
  축약형 종결. "~습니다", "~입니다", "~이다" 같은 종결어미는 쓰지 마세요).
- 특별히 유의미한 변동이 없는 항목은 아예 쓰지 마세요(억지로 채우지 않음). 주어진 사실을
  전부 언급할 의무는 없습니다 — 평범한 수치는 건너뛰어도 됩니다.
- 투자 조언이나 매수/매도 의견은 절대 포함하지 마세요(사실 종합만).
- 전체 분량은 항목당 1~2문장 정도로 간결하게 쓰세요.
- 출력은 JSON 객체 하나만: {"summary": "전체 분석...\\n\\n중국 분석..."} 형식으로, 항목
  사이는 빈 줄("\\n\\n")로 구분하세요."""


def parse_json_object(text):
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
        text = text.strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            obj = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


def fmt_visitors(value):
    if value is None:
        return "N/A"
    return f"{value / 10000:,.1f}만명"


def build_country_series(country_monthly, country_name):
    return [
        {"ym": rec["ym"], "visitors": rec["countries"].get(country_name)}
        for rec in country_monthly
        if rec.get("countries", {}).get(country_name) is not None
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="visitor_stats.json")
    ap.add_argument("--out", default="medtour_monthly_analysis.json")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.data, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[WARN] {args.data}이 아직 없어 월간 분석을 건너뜁니다.", file=sys.stderr)
        sys.exit(0)

    total_stats = month_stats(data.get("monthly", []), "visitors")
    if total_stats is None:
        print("[WARN] visitor_stats.json에 월별 데이터가 없어 분석을 건너뜁니다.", file=sys.stderr)
        sys.exit(0)
    target_month = total_stats["latest_ym"]

    fact_blocks = []
    rank_txt = fmt_rank(total_stats["rank"], total_stats["n_months_total"])
    lines = [
        f"- {target_month} 총 방문자수 {fmt_visitors(total_stats['latest_value'])} "
        f"(전월대비 {fmt_pct(total_stats['mom_pct'])}, 전년동월대비 {fmt_pct(total_stats['yoy_pct'])}"
        + (f", {rank_txt}" if rank_txt else "") + ")"
    ]
    fact_blocks.append("[전체 방한 외국인]\n" + "\n".join(lines))

    country_monthly = data.get("countryMonthly", [])
    for country in ("중국", "일본", "대만", "미국"):
        series = build_country_series(country_monthly, country)
        stats = month_stats(series, "visitors")
        if stats is None or stats["latest_ym"] != target_month or not is_notable(stats):
            continue
        rank_txt = fmt_rank(stats["rank"], stats["n_months_total"])
        fact_blocks.append(
            f"[국가별: {country}]\n"
            f"- {target_month} 방문자수 {fmt_visitors(stats['latest_value'])} "
            f"(전월대비 {fmt_pct(stats['mom_pct'])}, 전년동월대비 {fmt_pct(stats['yoy_pct'])}"
            + (f", {rank_txt}" if rank_txt else "") + ")"
        )

    if not is_notable(total_stats) and len(fact_blocks) == 1:
        print("[INFO] 이번 달 유의미한 변동이 없어 분석을 건너뜁니다.", file=sys.stderr)
        sys.exit(0)

    provider = build_object_provider()
    if provider is None:
        sys.exit(0)

    user_content = "\n\n".join(fact_blocks)
    tag = f"visitor monthly ({target_month}, {len(fact_blocks)}개 항목)"
    try:
        raw = call_llm_with_retries(provider, SYSTEM_PROMPT, user_content, MAX_TOKENS, tag, debug=args.debug)
    except DailyQuotaExhausted:
        print("[WARN] 일별 쿼터 소진으로 이번 실행은 건너뜁니다.", file=sys.stderr)
        sys.exit(0)

    obj = parse_json_object(raw)
    summary = (obj or {}).get("summary", "").strip()
    if not summary:
        print(f"[WARN] LLM 응답 파싱 실패 또는 빈 응답 — 분석 파일을 갱신하지 않습니다. 원문: {(raw or '')[:300]}", file=sys.stderr)
        sys.exit(0)

    now = datetime.datetime.now(datetime.timezone.utc)
    out_data = load_months_file(args.out)
    out_data["months"].setdefault(target_month, {})["visitor"] = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": f"visitor_stats.json 기반 자동 분석 ({provider.name})",
        "summary": summary,
    }
    save_months_file(args.out, out_data)
    print(f"저장 완료: {args.out} (대상월={target_month}, visitor 섹션, {len(fact_blocks)}개 항목)")


if __name__ == "__main__":
    main()
