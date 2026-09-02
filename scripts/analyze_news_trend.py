#!/usr/bin/env python3
"""
news_history.jsonl(scripts/archive_news_snapshot.py가 매일 누적)을 카테고리별로 모아
"최근 30일간 이 카테고리에서 어떤 흐름/이벤트가 있었는지" LLM으로 종합 서술한다.

index.html의 산업·기업 뉴스 탭 안 "월간 브리핑" 서브탭이 이 결과(news_trend.json)를 읽어,
건수 집계나 헤드라인 재나열이 아니라 "미용 카테고리에서 최근 한 달간 신제품 효과 관련
이슈가 부각됨" 같은 내용 종합을 카테고리 선택 없이 한 페이지에 쭉 보여준다(2026-09-02
사용자 요청 반영).

- provider(Gemini 무료/Anthropic 유료)는 summarize_news.py의 것을 그대로 재사용한다
  (같은 scripts/ 디렉터리에 있어 import 가능).
- region(domestic/global)별로 따로 종합한다 — 두 지역을 섞으면 국내/해외 서브탭 필터와
  안 맞고, 문체·통화 단위 등 맥락도 달라 뒤섞으면 어색한 글이 나온다.
- 카테고리 단위로만 생성한다. 처음엔 기업 단위도 함께 생성했었는데(2026-09-02), 기업이
  60개 넘게 있어 소요 시간이 너무 길어져 뺐다 — 기업별 흐름이 궁금하면 뉴스 아카이브
  서브탭에서 기업으로 필터링해 원문 기사를 직접 훑어보는 쪽으로 대체한다.
- 지역 하나당 API 호출 1번으로 그 지역의 카테고리 전체를 한꺼번에 처리한다(2026-09-03
  추가). 원래는 카테고리마다 호출을 따로 냈는데(최대 19번), 실제 워크플로 로그로 확인해
  보니 Gemini 무료 티어가 분당 제한(5회) 말고 "하루 20회"짜리 일별(daily) 쿼터도 같이
  걸려 있어서 summarize_news.py 호출까지 합치면 하루 한도를 넘어 대부분 429로 실패했다.
  분당 페이싱은 이 일별 한도엔 전혀 도움이 안 되므로(하루 지나야 풀림), 호출 자체를
  국내 1번 + 해외 1번(최대 2번)으로 줄이는 쪽으로 근본적으로 바꿨다 — summarize_news.py
  가 이미 기사 20개씩 한 번의 호출로 묶어 처리하는 것과 같은 원리를 카테고리 단위에도
  적용한 것.
- 기사가 3건 미만인 카테고리×지역 조합은 애초에 이 배치 호출 대상에서 제외한다(근거
  부족, 억지로 트렌드를 지어내지 않도록).
- 프롬프트에 "주어진 기사 목록에 없는 내용은 절대 추측하지 말라"는 지침과 "반드시 한국어로만
  작성하라"는 지침(해외 기사도 summarize_news.py 단계에서 이미 한국어 2줄 요약으로 변환돼
  있지만, summary가 비어 원문 영문 제목이 그대로 들어간 항목이 섞일 수 있어 명시적으로 못박음)
  을 명시하고, 실제 기사 목록(날짜/기업/맥락/요약)을 카테고리별로 나눠 프롬프트에 넣어 그
  안에서만 종합하게 한다.
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
import time

from summarize_news import build_provider, gemini_backoff_seconds, gemini_pace

PERIOD_DAYS = 30  # 월간 분석만 생성(2026-09-02, 7일치는 뺐다 — 위 docstring 참고)
MIN_ARTICLES = 3  # 이보다 적으면 트렌드 생성을 건너뜀(근거 부족)
MAX_LINES_PER_CATEGORY = 15  # 카테고리 하나당 프롬프트에 넣는 기사 목록 상한(토큰 보호,
# 한 호출에 카테고리 전체를 몰아넣다 보니 예전(50줄/카테고리)보다 더 줄였다)
MAX_TOKENS_PER_CATEGORY = 300  # 카테고리 하나당 배정하는 출력 토큰 예산(불릿 3~5개 기준)
REGIONS = ["domestic", "global"]
REGION_LABEL = {"domestic": "국내", "global": "해외"}

TREND_SYSTEM = """당신은 한국 증권사의 의료기기/디지털헬스/로보틱스 담당 애널리스트를 돕는
리서치 보조원입니다. 아래에 여러 카테고리 각각의 최근 한 달간 나온 기사 목록(날짜/기업/
맥락/제목 또는 요약)이 카테고리별로 구분되어 주어집니다. 카테고리마다 그 목록만 근거로
삼아, 이 기간 동안 두드러진 이벤트·주제·흐름을 애널리스트 관점에서 3~5개의 불릿(bullet)
으로 종합하세요.

규칙:
- 카테고리끼리 내용을 섞지 마세요. 각 카테고리는 그 카테고리 밑에 나열된 기사만 근거로
  삼으세요.
- 각 카테고리의 출력 형식은 반드시 줄바꿈("\\n")으로 구분된 불릿 목록이어야 합니다. 각 줄은
  "- "로 시작하세요(예: "- 세포라 입점으로 북미 유통망 확대함\\n- 상반기 매출 역대 최대
  기록\\n- ..."). 문장을 죽 이어붙인 하나의 문단으로 쓰지 마세요 — 가독성을 위해 항목별로
  줄을 나눕니다.
- 각 불릿은 한 가지 사실/흐름만 담되, 배경·근거까지 최대 2줄 분량(문장 1~2개)까지는
  풀어써도 됩니다 — 너무 짧게 끊어 정보가 부족해지지 않도록 하세요. 다만 한 불릿 안에
  서로 다른 사실 여러 개를 욱여넣지는 마세요(그럴 땐 불릿을 나누세요).
- 출력은 반드시 한국어로만 작성하세요. 입력 기사 목록에 영문 제목이 섞여 있어도(해외 기사
  원문 제목) 그대로 옮기지 말고 한국어로 종합하세요.
- 문체는 반드시 개조식 명사·동사 종결형만 쓰세요("~이다", "~였다", "~됩니다", "~했습니다" 같은
  '다'로 끝나는 종결어미는 절대 쓰지 말고, "~예정", "~임", "~함", "~진행", "~확대", "~지속",
  "~부각", "~실행"처럼 명사형이나 "~함/~임"으로 짧게 끊으세요). 예: "세포라 입점으로 북미
  유통망 확대함", "상반기 매출 역대 최대 기록", "수출규제는 여전히 지속".
- 반드시 주어진 기사 목록에 있는 내용만 근거로 삼으세요. 목록에 없는 사실을 추측하거나
  지어내지 마세요.
- 가능한 한 구체적인 기업명을 언급하며 "~기업들이 ~하는 흐름이 나타남" 식으로 종합하세요.
- 단순히 기사 제목을 나열하지 말고, 여러 기사를 관통하는 공통 주제/패턴이나 사건의 흐름을
  짚어내세요(예: "신제품 출시 효과가 부각됨", "M&A 움직임이 이어짐", "실적 발표가 몰림" 등).
- 뚜렷한 공통 주제가 안 보이면 억지로 만들지 말고 "특별히 두드러진 단일 흐름보다는
  개별 기업 이슈가 산발적으로 있었음" 같이 있는 그대로 서술하세요.
- 투자 조언이나 매수/매도 의견은 절대 포함하지 마세요(사실 종합만).
- 입력에 주어진 카테고리 개수와 순서를 정확히 맞춰서 모두 답하세요. 출력은 JSON 하나만:
  {"categories": [{"key": "카테고리명", "text": "- 첫째 불릿\\n- 둘째 불릿"}, ...]}"""


def parse_json_object(text):
    """단일 JSON 객체 응답을 방어적으로 파싱(코드펜스 제거 포함). 지금은
    {"categories": [{"key":..., "text":...}, ...]} 형태를 기대한다."""
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
    return lines[:MAX_LINES_PER_CATEGORY]


def generate_trends_batch(provider, region, cat_items, debug=False):
    """cat_items: {카테고리명: 기사목록} — 한 지역의 카테고리 전체를 API 호출 1번으로 처리한다
    (2026-09-03, Gemini 무료 티어의 "하루 20회" 쿼터에 걸리지 않도록 호출 수 자체를 줄임).
    반환값: {카테고리명: 불릿 텍스트}. 실패하거나 응답에 없는 카테고리는 결과에서 빠진다."""
    sections = []
    for cat, items in cat_items.items():
        lines = build_lines(items)
        sections.append(f"[카테고리: {cat}] (최근 {PERIOD_DAYS}일 기사 {len(items)}건)\n" + "\n".join(lines))
    user_content = "\n\n".join(sections)
    max_tokens = min(8000, 200 + MAX_TOKENS_PER_CATEGORY * len(cat_items))
    tag = f"{REGION_LABEL[region]} {len(cat_items)}개 카테고리 일괄"

    for attempt in range(2):  # 1차 시도 + 실패 시 1회 재시도(요약 배치와 동일한 방식)
        try:
            raw = provider.call(TREND_SYSTEM, user_content, max_tokens=max_tokens)
        except Exception as e:
            retry = "재시도도 " if attempt else ""
            print(f"[WARN] 트렌드 생성 API 호출 {retry}실패({tag}): {e}", file=sys.stderr)
            # Gemini 무료 티어는 분당 제한 외에 "하루 20회"짜리 일별 쿼터도 있다(2026-09-03
            # 실제 로그로 확인 — quotaId가 PerDay로 찍힘). 429가 일별 쿼터 초과라면 아무리
            # 오래 기다려도 이 실행 안에서는 못 풀리므로, 카테고리를 지역당 호출 1번으로
            # 묶어 호출 횟수 자체를 줄이는 쪽으로 대응했다(이 함수의 존재 이유).
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                time.sleep(gemini_backoff_seconds(e))
            else:
                gemini_pace(provider)
            continue
        gemini_pace(provider)
        obj = parse_json_object(raw)
        items_out = obj.get("categories") if obj else None
        if isinstance(items_out, list):
            result = {}
            for it in items_out:
                if not isinstance(it, dict):
                    continue
                key = str(it.get("key") or "").strip()
                text = str(it.get("text") or "").strip()
                if key and text:
                    result[key] = text
            if result:
                return result
        if debug:
            print(f"[DEBUG] 트렌드 응답 파싱 실패({tag}) 원문: {raw[:300]}", file=sys.stderr)
    return {}


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
    cutoff = (now - datetime.timedelta(days=PERIOD_DAYS)).strftime("%Y-%m-%d")

    trends = []
    for region in REGIONS:
        region_records = [r for r in records if r.get("region") == region]
        if not region_records:
            continue
        categories = sorted({r.get("cat") for r in region_records if r.get("cat")})

        cat_items = {}
        for cat in categories:
            items = [r for r in region_records if r.get("cat") == cat and r.get("date", "") >= cutoff]
            if len(items) < MIN_ARTICLES:
                continue
            items.sort(key=lambda r: r.get("date", ""))
            cat_items[cat] = items
        if not cat_items:
            continue

        results = generate_trends_batch(provider, region, cat_items, debug=args.debug)
        for cat, items in cat_items.items():
            text = results.get(cat)
            if not text:
                print(f"[WARN] 트렌드 응답에 카테고리 누락: [{REGION_LABEL[region]}] {cat}", file=sys.stderr)
                continue
            trends.append({
                "scope": "category",
                "key": cat,
                "region": region,
                "period_days": PERIOD_DAYS,
                "text": text,
                "n_articles": len(items),
            })
            print(f"[INFO] 트렌드 생성 완료: [{REGION_LABEL[region]}] {cat} / 최근 {PERIOD_DAYS}일 ({len(items)}건)", file=sys.stderr)

    payload = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": f"news_history.jsonl 기반 카테고리별 월간 자동 종합 ({provider.name})",
        "trends": trends,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} ({len(trends)}건 트렌드 생성)")


if __name__ == "__main__":
    main()
