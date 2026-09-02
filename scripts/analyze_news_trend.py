#!/usr/bin/env python3
"""
news_history.jsonl(scripts/archive_news_snapshot.py가 매일 누적)을 카테고리별·기업별로 모아
"최근 N일간 이 카테고리/기업에서 어떤 흐름/이벤트가 있었는지" LLM으로 종합 서술한다.

index.html의 산업·기업 뉴스 탭 안 "뉴스 플로우" 섹션이 이 결과(news_trend.json)를 읽어,
사용자가 카테고리 또는 기업을 하나 선택하면 건수 집계나 헤드라인 재나열이 아니라 "미용
카테고리에서 최근 1주일간 신제품 효과 관련 이슈가 부각됨" 같은 내용 종합을 보여준다
(2026-09-02 사용자 요청 반영 — 뉴스 플로우는 오늘의 클리핑을 다시 나열하는 곳이 아니라
시계열 관점의 AI 분석 글을 보여주는 곳).

- provider(Gemini 무료/Anthropic 유료)는 summarize_news.py의 것을 그대로 재사용한다
  (같은 scripts/ 디렉터리에 있어 import 가능).
- region(domestic/global)별로 따로 종합한다 — 두 지역을 섞으면 국내/해외 서브탭 필터와
  안 맞고, 문체·통화 단위 등 맥락도 달라 뒤섞으면 어색한 글이 나온다.
- 카테고리 단위에 더해 기업 단위도 생성한다(2026-09-02 추가). "co" 필드가
  "메쥬, 스카이랩스"처럼 콤마로 여러 기업을 묶어 표시하는 경우(merge_cross_company_duplicates
  결과) 각 기업 앞으로 모두 집계한다. 기사가 MIN_ARTICLES건 미만인 카테고리×기간/기업×기간
  조합은 애초에 생성 대상에서 제외되므로(근거 부족), 실제로 LLM을 호출하는 조합 수는 이
  임계값 덕분에 억제된다.
- 기사가 3건 미만인 조합은 근거가 부족하다고 보고 생성을 건너뛴다(억지로 트렌드를 지어내지
  않도록).
- 프롬프트에 "주어진 기사 목록에 없는 내용은 절대 추측하지 말라"는 지침과 "반드시 한국어로만
  작성하라"는 지침(해외 기사도 summarize_news.py 단계에서 이미 한국어 2줄 요약으로 변환돼
  있지만, summary가 비어 원문 영문 제목이 그대로 들어간 항목이 섞일 수 있어 명시적으로 못박음)
  을 명시하고, 실제 기사 목록(날짜/기업/맥락/요약)을 통째로 프롬프트에 넣어 그 안에서만
  종합하게 한다.
- 이 단계도 요약 단계와 마찬가지로 "보강" 단계다 — 실패해도 news_history.jsonl/news.json
  자체는 이미 저장된 상태이므로 sys.exit(1)로 워크플로를 실패시키지 않는다.

사용법:
    python analyze_news_trend.py --history news_history.jsonl --out news_trend.json
"""
import argparse
import datetime
import json
import re
import sys

from summarize_news import build_provider

PERIOD_DAYS = [7, 30]
MIN_ARTICLES = 3  # 이보다 적으면 트렌드 생성을 건너뜀(근거 부족)
MAX_LINES_PER_PROMPT = 50  # 프롬프트에 넣는 기사 목록 상한(토큰/비용 보호)
REGIONS = ["domestic", "global"]
REGION_LABEL = {"domestic": "국내", "global": "해외"}

TREND_SYSTEM = """당신은 한국 증권사의 의료기기/디지털헬스/로보틱스 담당 애널리스트를 돕는
리서치 보조원입니다. 아래에 특정 카테고리 또는 특정 기업의 최근 기간 동안 나온 기사 목록
(날짜/기업/맥락/제목 또는 요약)이 주어집니다. 이 목록만 근거로 삼아, 이 기간 동안 두드러진
이벤트·주제·흐름을 애널리스트 관점에서 3~5문장으로 종합하세요.

규칙:
- 출력은 반드시 한국어로만 작성하세요. 입력 기사 목록에 영문 제목이 섞여 있어도(해외 기사
  원문 제목) 그대로 옮기지 말고 한국어로 종합하세요.
- 문체는 반드시 개조식 명사·동사 종결형만 쓰세요("~이다", "~였다", "~됩니다", "~했습니다" 같은
  '다'로 끝나는 종결어미는 절대 쓰지 말고, "~예정", "~임", "~함", "~진행", "~확대", "~지속",
  "~부각", "~실행"처럼 명사형이나 "~함/~임"으로 짧게 끊으세요). 예: "세포라 입점으로 북미
  유통망 확대함", "상반기 매출 역대 최대 기록", "수출규제는 여전히 지속".
- 반드시 주어진 기사 목록에 있는 내용만 근거로 삼으세요. 목록에 없는 사실을 추측하거나
  지어내지 마세요.
- 대상이 카테고리면 가능한 한 구체적인 기업명을 언급하며 "~기업들이 ~하는 흐름이 나타남"
  식으로, 대상이 기업 하나면 그 기업이 이 기간 동안 겪은 핵심 이벤트를 시간 순으로 짚어
  종합하세요.
- 단순히 기사 제목을 나열하지 말고, 여러 기사를 관통하는 공통 주제/패턴이나 사건의 흐름을
  짚어내세요(예: "신제품 출시 효과가 부각됨", "M&A 움직임이 이어짐", "실적 발표가 몰림" 등).
- 뚜렷한 공통 주제가 안 보이면 억지로 만들지 말고 "특별히 두드러진 단일 흐름보다는
  개별 이슈가 산발적으로 있었음" 같이 있는 그대로 서술하세요.
- 투자 조언이나 매수/매도 의견은 절대 포함하지 마세요(사실 종합만).
- 출력은 JSON 하나만: {"text": "종합 내용..."}"""


def parse_json_object(text):
    """{"text": "..."} 단일 객체 응답을 방어적으로 파싱(코드펜스 제거 포함)."""
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


def build_lines(items):
    lines = []
    for it in items:
        co = it.get("co", "")
        date = it.get("date", "")
        ctx = it.get("ctx", "")
        content = it.get("summary") or it.get("t") or ""
        tag = f"[{ctx}] " if ctx else ""
        lines.append(f"- {date} {co}: {tag}{content}")
    return lines[:MAX_LINES_PER_PROMPT]


def generate_trend(provider, scope_label, key, items, period_days, debug=False):
    lines = build_lines(items)
    user_content = (
        f"{scope_label}: {key}\n최근 {period_days}일간 기사 {len(items)}건(아래 목록):\n\n"
        + "\n".join(lines)
    )
    tag = f"{key}, {period_days}일"
    for attempt in range(2):  # 1차 시도 + 실패 시 1회 재시도(요약 배치와 동일한 방식)
        try:
            raw = provider.call(TREND_SYSTEM, user_content, max_tokens=800)
        except Exception as e:
            retry = "재시도도 " if attempt else ""
            print(f"[WARN] 트렌드 생성 API 호출 {retry}실패({tag}): {e}", file=sys.stderr)
            continue
        obj = parse_json_object(raw)
        text = obj.get("text") if obj else None
        if text:
            return str(text).strip()
        if debug:
            print(f"[DEBUG] 트렌드 응답 파싱 실패({tag}) 원문: {raw[:200]}", file=sys.stderr)
    return None


def companies_of(record):
    """"co" 필드는 merge_cross_company_duplicates 결과로 "메쥬, 스카이랩스"처럼 콤마로
    여러 기업을 묶어 표시할 수 있다 — 기업 단위 집계를 위해 개별 기업명으로 쪼갠다."""
    co = (record.get("co") or "").strip()
    if not co:
        return []
    return [c.strip() for c in co.split(",") if c.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default="news_history.jsonl")
    ap.add_argument("--out", default="news_trend.json")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.history, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        print(f"[WARN] {args.history}이 아직 없어 트렌드 생성을 건너뜁니다.", file=sys.stderr)
        sys.exit(0)

    provider = build_provider()
    if provider is None:
        sys.exit(0)  # build_provider가 이미 WARN 로그를 남김

    now = datetime.datetime.now(datetime.timezone.utc)

    trends = []
    for region in REGIONS:
        region_records = [r for r in records if r.get("region") == region]
        if not region_records:
            continue
        categories = sorted({r.get("cat") for r in region_records if r.get("cat")})
        companies = sorted({c for r in region_records for c in companies_of(r)})

        for period_days in PERIOD_DAYS:
            cutoff = (now - datetime.timedelta(days=period_days)).strftime("%Y-%m-%d")

            for cat in categories:
                items = [r for r in region_records if r.get("cat") == cat and r.get("date", "") >= cutoff]
                if len(items) < MIN_ARTICLES:
                    continue
                items.sort(key=lambda r: r.get("date", ""))
                text = generate_trend(provider, "카테고리", cat, items, period_days, debug=args.debug)
                if not text:
                    continue
                trends.append({
                    "scope": "category",
                    "key": cat,
                    "region": region,
                    "period_days": period_days,
                    "text": text,
                    "n_articles": len(items),
                })
                print(f"[INFO] 트렌드 생성 완료: [{REGION_LABEL[region]}] {cat} / 최근 {period_days}일 ({len(items)}건)", file=sys.stderr)

            for co in companies:
                items = [r for r in region_records if co in companies_of(r) and r.get("date", "") >= cutoff]
                if len(items) < MIN_ARTICLES:
                    continue
                items.sort(key=lambda r: r.get("date", ""))
                text = generate_trend(provider, "기업", co, items, period_days, debug=args.debug)
                if not text:
                    continue
                trends.append({
                    "scope": "company",
                    "key": co,
                    "region": region,
                    "period_days": period_days,
                    "text": text,
                    "n_articles": len(items),
                })
                print(f"[INFO] 트렌드 생성 완료: [{REGION_LABEL[region]}] {co} / 최근 {period_days}일 ({len(items)}건)", file=sys.stderr)

    payload = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": f"news_history.jsonl 기반 카테고리·기업별 자동 종합 ({provider.name})",
        "trends": trends,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} ({len(trends)}건 트렌드 생성)")


if __name__ == "__main__":
    main()
