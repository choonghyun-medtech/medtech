#!/usr/bin/env python3
"""
medical_tour.json(scripts/scrape_medical_tour.py가 갱신)의 "전체/중국/일본/미국/태국/대만"
탭별 월별 의료 소비액·소비건수와 진료과목별 비율에서 이번 달 유의미한 변동을 골라 LLM으로
서술형 분석을 생성해 medtour_monthly_analysis.json의 "medtour" 섹션으로 저장한다.

- 이 파일(medtour_monthly_analysis.json)은 analyze_visitor_monthly.py도 같이 쓴다 —
  방한외국인(visitor_stats.json)은 갱신 시차가 더 길어(2026-09-14 사용자 설명: "9월 말이면
  8월 데이터가 들어옴", 즉 의료관광 소비 데이터보다 한 사이클 늦게 그 달 수치가 확정) 같은
  워크플로에서 같이 돌리면 방한외국인 데이터를 기다리느라 의료관광 소비 분석 자체가 매달
  지연될 위험이 있다(2026-09-14 사용자 지적) — 그래서 스케줄과 스크립트를 분리하고, 파일은
  월(target_month)별로 구획된 {"months": {"YYYY-MM": {"medtour": {...}, "visitor": {...}}}}
  구조로 공유해서 서로 다른 날 실행돼도 같은 달 키에 각자 안전하게 병합된다
  (monthly_analysis_common.load_months_file/save_months_file 참고).
- target_month는 "전체" 탭 monthly에 실제로 들어있는 최신 ym을 그대로 쓴다(고정 날짜가
  아니라 실제 반영된 데이터를 따라감 — analyze_export_monthly.py와 동일한 방침).
- 진료과목별 비율(deptAmt)은 "전체" 탭 최신월 기준 소비액 비중 상위 부서 중, 12개월 전
  대비 비중이 눈에 띄게(기본 3%p 이상) 변한 것만 사실로 뽑는다 — 매달 상위 3개 부서를
  기계적으로 나열하면 새로울 게 없는 내용이 됨.
- 이 단계도 "보강" 단계라 실패해도 sys.exit(1)로 워크플로를 실패시키지 않는다.

사용법:
    python analyze_medtour_monthly.py --data medical_tour.json --out medtour_monthly_analysis.json
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

MAX_TOKENS = 2000
DEPT_RATIO_CHANGE_THRESHOLD = 3.0  # percentage point

SYSTEM_PROMPT = """당신은 한국 증권사의 의료관광/미용 담당 애널리스트를 돕는 리서치
보조원입니다. 아래에 이번 달 "외국인 의료관광 소비" 데이터 중 유의미하게 변동했거나
중요하다고 이미 걸러진 사실(전월비/전년동월비/역대 순위/진료과목별 비중 변화 등, 전부
파이썬으로 미리 계산된 값)이 주어집니다. 이 사실들만 근거로 자연스러운 한국어 서술형
분석을 작성하세요.

규칙:
- 반드시 주어진 사실(숫자 포함)만 사용하세요. 목록에 없는 수치를 새로 계산하거나 추측하지
  마세요.
- "전체" 항목을 가장 먼저 쓰고, 그다음 국가별(중국/일본/미국/태국/대만) 중 유의미한 변동이
  있는 국가, 마지막으로 진료과목별 비중 변화 순으로 쓰세요.
- 문체는 리서치 노트 스타일 개조식으로 쓰세요("~함", "~로 확인됨", "~로 부각" 등 명사형/
  축약형 종결. "~습니다", "~입니다", "~이다" 같은 종결어미는 쓰지 마세요).
- 특별히 유의미한 변동이 없는 항목은 아예 쓰지 마세요(억지로 채우지 않음). 주어진 사실을
  전부 언급할 의무는 없습니다 — 평범한 수치는 건너뛰어도 됩니다.
- 순위("역대 N위")는 실제로 상위권일 때만 사실로 주어지니 그럴 땐 자연스럽게 언급하되,
  그 자체를 매번 headline처럼 강조하진 마세요 — 핵심은 수치(소비액/건수/증감폭)와 그게
  무엇을 뜻하는지(예: 특정 국가 수요 확대, 진료과목 쏠림 등 주어진 사실 안에서 유추 가능한
  해석)입니다.
- 투자 조언이나 매수/매도 의견은 절대 포함하지 마세요(사실 종합만).
- 전체 분량은 항목당 1~3문장 정도로 간결하게 쓰세요.
- 출력은 JSON 객체 하나만: {"summary": "전체 분석...\\n\\n중국 분석...\\n\\n진료과목별..."}
  형식으로, 항목 사이는 빈 줄("\\n\\n")로 구분하세요."""


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


def fmt_krw_thousand(value):
    """medical_tour.json의 amt는 '천원' 단위(monthlyAmtUnit 참고)로 저장돼 있다.
    천원 * 1000 / 1e8 = 억원."""
    if value is None:
        return "N/A"
    eok = value * 1000 / 1e8
    return f"{eok:,.1f}억원"


def fmt_cnt(value):
    if value is None:
        return "N/A"
    return f"{value:,.0f}건"


def build_tab_facts(tab, is_all_tab):
    stats_amt = month_stats(tab.get("monthly", []), "amt")
    if stats_amt is None:
        return None
    stats_cnt = month_stats(tab.get("monthly", []), "cnt")
    latest_ym = stats_amt["latest_ym"]

    notable_amt = is_notable(stats_amt)
    notable_cnt = is_notable(stats_cnt) if stats_cnt else False
    if not (is_all_tab or notable_amt or notable_cnt):
        return None

    lines = []
    if is_all_tab or notable_amt:
        rank_txt = fmt_rank(stats_amt["rank"], stats_amt["n_months_total"])
        lines.append(
            f"- {latest_ym} 소비액 {fmt_krw_thousand(stats_amt['latest_value'])} "
            f"(전월대비 {fmt_pct(stats_amt['mom_pct'])}, 전년동월대비 {fmt_pct(stats_amt['yoy_pct'])}"
            + (f", {rank_txt}" if rank_txt else "") + ")"
        )
    if stats_cnt and (is_all_tab or notable_cnt):
        rank_txt = fmt_rank(stats_cnt["rank"], stats_cnt["n_months_total"])
        lines.append(
            f"- {latest_ym} 소비건수 {fmt_cnt(stats_cnt['latest_value'])} "
            f"(전월대비 {fmt_pct(stats_cnt['mom_pct'])}, 전년동월대비 {fmt_pct(stats_cnt['yoy_pct'])}"
            + (f", {rank_txt}" if rank_txt else "") + ")"
        )
        if stats_amt["latest_value"] and stats_cnt["latest_value"]:
            asp = stats_amt["latest_value"] * 1000 / stats_cnt["latest_value"]
            lines.append(f"- {latest_ym} ASP(건당 소비액) 약 {asp:,.0f}원")

    if not lines:
        return None
    return latest_ym, "\n".join(lines)


def build_dept_facts(dept_list, value_key, latest_ym):
    """진료과목별 비율(deptAmt 또는 deptCnt) 중 12개월 전 대비 비중이 크게 바뀐 과목만."""
    from monthly_analysis_common import month_add
    latest_by_dept = {d["dept"]: d for d in dept_list if d.get("ym") == latest_ym}
    prior_ym = month_add(latest_ym, -12)
    prior_by_dept = {d["dept"]: d for d in dept_list if d.get("ym") == prior_ym}
    if not latest_by_dept or not prior_by_dept:
        return []
    facts = []
    for dept, cur in latest_by_dept.items():
        prev = prior_by_dept.get(dept)
        if not prev:
            continue
        change = cur.get("ratio", 0) - prev.get("ratio", 0)
        if abs(change) >= DEPT_RATIO_CHANGE_THRESHOLD:
            facts.append(
                f"- {dept} 비중 {cur.get('ratio')}% ({latest_ym}) — 전년동월({prior_ym} "
                f"{prev.get('ratio')}%) 대비 {change:+.1f}%p"
            )
    return facts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="medical_tour.json")
    ap.add_argument("--out", default="medtour_monthly_analysis.json")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.data, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[WARN] {args.data}이 아직 없어 월간 분석을 건너뜁니다.", file=sys.stderr)
        sys.exit(0)

    tabs = {t["key"]: t for t in data.get("tabs", [])}
    all_tab = tabs.get("all")
    if not all_tab:
        print("[WARN] medical_tour.json에 'all' 탭이 없어 월간 분석을 건너뜁니다.", file=sys.stderr)
        sys.exit(0)

    fact_blocks = []
    target_month = None

    result = build_tab_facts(all_tab, is_all_tab=True)
    if result:
        target_month, block = result
        fact_blocks.append(f"[전체 외국인 의료관광 소비]\n{block}")

    for key, label in (("china", "중국"), ("japan", "일본"), ("usa", "미국"), ("thailand", "태국"), ("taiwan", "대만")):
        tab = tabs.get(key)
        if not tab:
            continue
        result = build_tab_facts(tab, is_all_tab=False)
        if result is None:
            continue
        ym, block = result
        fact_blocks.append(f"[국가별 소비: {label}]\n{block}")

    if target_month:
        dept_amt_facts = build_dept_facts(all_tab.get("deptAmt", []), "amt", target_month)
        if dept_amt_facts:
            fact_blocks.append("[진료과목별 소비액 비중 변화(전체)]\n" + "\n".join(dept_amt_facts))

    if not fact_blocks or target_month is None:
        print("[INFO] 이번 달 유의미한 변동이 없어 분석을 건너뜁니다.", file=sys.stderr)
        sys.exit(0)

    provider = build_object_provider()
    if provider is None:
        sys.exit(0)

    user_content = "\n\n".join(fact_blocks)
    tag = f"medtour monthly ({target_month}, {len(fact_blocks)}개 항목)"
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
    out_data["months"].setdefault(target_month, {})["medtour"] = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": f"medical_tour.json 기반 자동 분석 ({provider.name})",
        "summary": summary,
    }
    save_months_file(args.out, out_data)
    print(f"저장 완료: {args.out} (대상월={target_month}, medtour 섹션, {len(fact_blocks)}개 항목)")


if __name__ == "__main__":
    main()
