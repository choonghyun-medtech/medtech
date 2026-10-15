#!/usr/bin/env python3
"""
export_data.json(scripts/scrape_export_data.py가 갱신)의 카테고리별/국가별/지역별 월별
수출 시계열에서 "이번 달 유의미하게 변동했거나 중요한 사항"만 골라 LLM으로 짧은 서술형
분석을 생성해 export_monthly_analysis.json으로 저장한다.

index.html의 "수출 데이터" 탭 "월간 분석" 카드가 이 파일을 읽는다(2026-09-14 신설,
사용자가 준 실제 작성 예시 — "8월 전국 3304999000 수출 잠정 4.0955억 달러(+89.1% YoY)로
7월에 이어 두 달 연속 4억 달러대..." 같은 스타일 — 를 참고해 설계).

- 숫자 계산(전월비/전년동월비/역대 순위/연초누계 vs 전년 연간)은 전부 이 스크립트가 파이썬
  으로 미리 계산해서 LLM에는 "계산된 사실"만 넘긴다 — LLM이 직접 YoY%를 암산하게 하면
  틀리기 쉽다(monthly_analysis_common.month_stats 참고).
- "유의미한" 카테고리만 고른다(monthly_analysis_common.is_notable) — 매달 12개 카테고리를
  전부 언급하면 노이즈만 늘어난다. 국가별/지역별 브레이크다운은 시계열이 짧아(예: byRegion은
  최근 몇 달치만 있음) 전년동월 비교가 불가능한 경우가 흔한데, 이 경우 YoY 없이 절대값 +
  전월비만 사실로 넘긴다(억지로 YoY를 만들지 않음).
- "지역별" 항목은 scrape_export_data.py의 CATEGORIES[].regions가 특정 기업의 공장/본사
  소재지를 관세청 지역별 수출 통계로 추적하는 프록시라(예: 강원 강릉시 = 파마리서치),
  companies 필드와 함께 프롬프트에 넣어 LLM이 "~기업 추정치"처럼 자연스럽게 언급하게 한다.
- 대상 월(target_month)은 "고정된 달력 날짜"가 아니라 export_data.json에 실제로 들어있는
  가장 최신 ym을 그대로 쓴다 — 워크플로 스케줄(KST 매월 15~18일 창)이 그날그날 실제로 반영된
  데이터를 자동으로 따라가게 하기 위함(관세청 확정 발표가 예정보다 며칠 밀려도 최신 데이터
  기준으로 정확히 그 달을 분석하게 됨).
- 이 단계는 "보강" 단계다 — 실패해도 export_data.json 자체는 이미 저장돼 있으므로
  sys.exit(1)로 워크플로를 실패시키지 않는다(경고만 남기고 0으로 종료).

사용법:
    python analyze_export_monthly.py --data export_data.json --out export_monthly_analysis.json
"""
import argparse
import datetime
import json
import re
import sys

from monthly_analysis_common import (
    DailyQuotaExhausted,
    ServerBusy,
    build_object_provider,
    call_llm_with_retries,
    fmt_pct,
    add_months_args,
    fmt_rank,
    is_notable,
    load_months_file,
    month_stats,
    run_months,
    save_months_file,
)


def load_export_months(path):
    """export_monthly_analysis.json을 {"months": {"YYYY-MM": {"export": {...}}}}로 읽는다.
    2026-10-13 이전 형식(최신 한 달만 최상위 target_month/summary로 저장, 매달 덮어씀)이면
    그 한 달을 새 구조로 옮겨 담는다 — 그래야 기존 분석이 사라지지 않는다."""
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        old = None
    if isinstance(old, dict) and old.get("target_month") and old.get("summary") and "months" not in old:
        return {"months": {old["target_month"]: {"export": {
            "generated_at": old.get("generated_at"), "source": old.get("source"), "summary": old["summary"]}}}}
    return load_months_file(path)

MAX_TOKENS = 2500

SYSTEM_PROMPT = """당신은 한국 증권사의 의료기기/미용/헬스케어 담당 애널리스트를 돕는 리서치
보조원입니다. 아래에 이번 달 수출 데이터 중 "유의미하게 변동했거나 중요하다"고 이미 걸러진
카테고리별 사실(전월비/전년동월비/역대 순위/연초 누계 등, 전부 파이썬으로 미리 계산된 값)이
주어집니다. 이 사실들만 근거로, 애널리스트가 리서치 노트에 쓸 법한 자연스러운 한국어 서술형
분석을 작성하세요.

규칙:
- 반드시 주어진 사실(숫자 포함)만 사용하세요. 목록에 없는 수치를 새로 계산하거나 추측하지
  마세요. 특히 %, 순위, 금액은 절대 스스로 다시 계산하지 말고 주어진 값을 그대로 옮기세요.
- 카테고리마다 문단(또는 여러 문장)을 나누되, 가장 눈에 띄는(YoY 변동폭이 크거나 역대
  기록을 세운) 카테고리부터 먼저 쓰세요.
- 문체는 리서치 노트 스타일 개조식으로 쓰세요("~함", "~로 확인됨", "~로 부각", "~부상" 등
  명사형/축약형 종결. "~습니다", "~입니다" 같은 존댓말이나 "~이다" 같은 평서문 종결어미는
  쓰지 마세요). 예시: "8월 전국 필러·리쥬란류 수출 잠정 4.0955억 달러(+89.1% YoY)로 7월에
  이어 두 달 연속 4억 달러대 — 역대 2위 월간 기록. 1~8월 누계 27.78억 달러(+52%)로 2025년
  연간의 96%를 8개월 만에 달성."
- 국가별/지역별 세부 사실이 주어진 카테고리는 "~국 1위", "~기업(지역 프록시) 추정치 ~" 처럼
  자연스럽게 엮어서 언급하세요. 지역별 수치는 그 자체가 회사 전체 실적이 아니라 관세청
  지역 통계 기준 "프록시(추정)"라는 점을 잊지 말고, 확정치가 아닌 값에는 "잠정"이라는
  표현을 넣어도 됩니다.
- 특별히 언급할 유의미한 변동이 없는 카테고리는 아예 쓰지 마세요(억지로 채우지 않음). 순위
  ("역대 N위")는 실제로 상위권일 때만 사실로 주어지니, 주어졌다면 자연스럽게 언급하되 그 자체를
  분석의 결론처럼 매번 강조하진 마세요 — 핵심은 수치 자체(금액/증감폭)와 그게 무엇을 뜻하는지
  (예: 특정 지역 수요 확대, 구조적 성장 등 주어진 사실 안에서 유추 가능한 해석)입니다.
- 투자 조언이나 매수/매도 의견은 절대 포함하지 마세요(사실 종합만).
- 전체 분량은 카테고리당 2~4문장 정도로, 너무 길게 늘어지지 않게 하세요.
- 출력은 JSON 객체 하나만: {"summary": "카테고리1 분석...\\n\\n카테고리2 분석..."} 형식으로,
  카테고리 사이는 빈 줄("\\n\\n")로 구분하세요."""


def fmt_usd(value):
    if value is None:
        return "N/A"
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1e8:
        return f"{sign}{v / 1e8:.4f}억 달러"
    if v >= 1e4:
        return f"{sign}{v / 1e4:.1f}만 달러"
    return f"{sign}{v:,.0f}달러"


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


def build_category_facts(cat):
    """카테고리 하나의 monthly/byCountry/byRegion에서 유의미한 사실 텍스트 블록을 만든다.
    유의미한 게 하나도 없으면 None을 반환(이 카테고리는 통째로 프롬프트에서 제외)."""
    stats = month_stats(cat.get("monthly", []), "expDlr")
    if stats is None:
        return None

    lines = []
    header_notable = is_notable(stats)
    if header_notable:
        rank_txt = fmt_rank(stats["rank"], stats["n_months_total"])
        lines.append(
            f"- {stats['latest_ym']} 수출액 {fmt_usd(stats['latest_value'])} "
            f"(전월대비 {fmt_pct(stats['mom_pct'])}, 전년동월대비 {fmt_pct(stats['yoy_pct'])}"
            + (f", {rank_txt}" if rank_txt else "") + ")"
        )
        if stats["ytd_sum"] is not None:
            ytd_line = (
                f"- {stats['latest_ym'][:4]}년 1~{int(stats['latest_ym'][5:7])}월 누계 "
                f"{fmt_usd(stats['ytd_sum'])}"
            )
            if stats["ytd_yoy_pct"] is not None:
                ytd_line += f" (전년동기대비 {fmt_pct(stats['ytd_yoy_pct'])})"
            if stats["ytd_vs_prior_year_pct"] is not None:
                ytd_line += f", 이는 전년 연간 실적의 {stats['ytd_vs_prior_year_pct']:.0f}%에 해당"
            lines.append(ytd_line)

    # 국가별: 최신월 기준 최대 비중 1개국만(전체를 나열하면 노이즈).
    by_country = cat.get("byCountry") or {}
    top_country = None
    top_country_stats = None
    for name, series in by_country.items():
        s = month_stats(series, "expDlr")
        if s is None or s["latest_ym"] != stats["latest_ym"]:
            continue
        if top_country_stats is None or s["latest_value"] > top_country_stats["latest_value"]:
            top_country, top_country_stats = name, s
    country_notable = top_country_stats and (is_notable(top_country_stats) or header_notable)
    if country_notable:
        rank_txt = fmt_rank(top_country_stats["rank"], top_country_stats["n_months_total"])
        extra = f", 전년동월대비 {fmt_pct(top_country_stats['yoy_pct'])}" if top_country_stats["yoy_pct"] is not None else " (전년동월 비교 불가·데이터 축적 중)"
        lines.append(
            f"- 국가별 최대 비중: {top_country} {top_country_stats['latest_ym']} "
            f"{fmt_usd(top_country_stats['latest_value'])}{extra}" + (f", {rank_txt}" if rank_txt else "")
        )

    # 지역별(기업 프록시): 최신월 기준 최대 1개 지역만.
    by_region = cat.get("byRegion") or {}
    top_region = None
    top_region_stats = None
    for name, series in by_region.items():
        s = month_stats(series, "expDlr")
        if s is None or s["latest_ym"] != stats["latest_ym"]:
            continue
        if top_region_stats is None or s["latest_value"] > top_region_stats["latest_value"]:
            top_region, top_region_stats = name, s
    region_notable = top_region_stats and (is_notable(top_region_stats) or header_notable)
    if region_notable:
        rank_txt = fmt_rank(top_region_stats["rank"], top_region_stats["n_months_total"])
        extra = f", 전월대비 {fmt_pct(top_region_stats['mom_pct'])}"
        if top_region_stats["yoy_pct"] is not None:
            extra += f", 전년동월대비 {fmt_pct(top_region_stats['yoy_pct'])}"
        else:
            extra += " (전년동월 비교 불가·데이터 축적 중)"
        # [2026-10-14] byRegion(시군구별 API)은 "천달러" 단위로 저장돼 있다(scrape_export_data.py
        # fetch_sigungu_breakdown 주석 참고) — 달러로 보고 표기하던 탓에 "5,934달러"처럼 1000배
        # 작게 서술되던 버그 수정(실제 5,934천달러 = 593.4만 달러).
        lines.append(
            f"- 지역별(기업 추정 프록시) {top_region}: {top_region_stats['latest_ym']} "
            f"{fmt_usd(top_region_stats['latest_value'] * 1000)}{extra}" + (f", {rank_txt}" if rank_txt else "")
        )

    if not lines:
        return None
    return stats["latest_ym"], "\n".join(lines)


def analyze_once(data, get_provider, debug=False):
    """data의 최신 달을 분석한다. 반환: (대상월, 요약, provider 이름) — 요약을 못 만들면
    (대상월, None, 사유). 하루 쿼터 소진은 DailyQuotaExhausted로 올린다.
    과거 달 분석(--months)은 data를 그 달까지 자른 사본으로 이 함수를 그대로 부른다."""
    fact_blocks = []
    target_month = None
    for cat in data.get("categories", []):
        result = build_category_facts(cat)
        if result is None:
            continue
        ym, block = result
        target_month = max(target_month, ym) if target_month else ym
        label = cat.get("label", cat.get("key", ""))
        companies = cat.get("companies", "")
        header = f"[카테고리: {label}]" + (f" (관련기업: {companies})" if companies else "")
        fact_blocks.append(f"{header}\n{block}")

    if not fact_blocks:
        return target_month, None, "유의미한 변동이 있는 카테고리가 없음"
    provider = get_provider()
    if provider is None:
        return target_month, None, "AI 키 없음"

    tag = f"export monthly ({target_month}, {len(fact_blocks)}개 카테고리)"
    raw = call_llm_with_retries(provider, SYSTEM_PROMPT, "\n\n".join(fact_blocks), MAX_TOKENS, tag, debug=debug)
    summary = ((parse_json_object(raw) or {}).get("summary") or "").strip()
    if not summary:
        return target_month, None, f"LLM 응답 파싱 실패 또는 빈 응답 — 원문: {(raw or '')[:300]}"
    print(f"[INFO] {target_month}: {len(fact_blocks)}개 카테고리 분석", file=sys.stderr)
    return target_month, summary, provider.name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="export_data.json")
    ap.add_argument("--out", default="export_monthly_analysis.json")
    ap.add_argument("--debug", action="store_true")
    add_months_args(ap)
    args = ap.parse_args()

    try:
        with open(args.data, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[WARN] {args.data}이 아직 없어 월간 분석을 건너뜁니다.", file=sys.stderr)
        sys.exit(0)

    provider_cache = []
    def get_provider():
        if not provider_cache:
            provider_cache.append(build_object_provider())
        return provider_cache[0]

    # 2026-10-13: 매달 덮어쓰던 것을 월별로 쌓는다(의료관광 분석과 같은 구조) — 화면에서 지난
    # 분석도 고를 수 있게(사수 피드백). 같은 달을 다시 돌리면 그 달 분석만 새로 바뀐다.
    out_data = load_export_months(args.out)
    def save(ym, summary, provider_name):
        now = datetime.datetime.now(datetime.timezone.utc)
        out_data["months"].setdefault(ym, {})["export"] = {
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": f"export_data.json 기반 자동 분석 ({provider_name})"
                      + (" · 과거 시점 데이터로 사후 작성" if args.months else ""),
            "summary": summary,
        }
        save_months_file(args.out, out_data)

    if args.months:
        run_months(args, data, out_data, "export", lambda d: analyze_once(d, get_provider, args.debug), save)
        return

    try:
        target_month, summary, info = analyze_once(data, get_provider, args.debug)
    except (DailyQuotaExhausted, ServerBusy):
        print("[WARN] 일별 쿼터 소진 또는 서버 과부하로 이번 실행은 건너뜁니다.", file=sys.stderr)
        sys.exit(0)
    if not summary:
        print(f"[INFO] 분석을 건너뜁니다 — {info}", file=sys.stderr)
        sys.exit(0)
    save(target_month, summary, info)
    print(f"저장 완료: {args.out} (대상월={target_month})")


if __name__ == "__main__":
    main()
