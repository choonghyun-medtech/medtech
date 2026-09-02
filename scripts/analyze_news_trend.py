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
  60개 넘게 있어 Gemini 무료 티어 분당 5회 제한과 맞물려 소요 시간이 너무 길어졌다(약
  80건, 17분 이상). 사용자가 소요 시간을 카테고리 개수(19개, ~4분) 수준으로 줄이기 위해
  기업 단위 생성 자체를 빼기로 결정 — 기업별 흐름이 궁금하면 뉴스 아카이브 서브탭에서
  기업으로 필터링해 원문 기사를 직접 훑어보는 쪽으로 대체한다.
- 기사가 3건 미만인 카테고리×지역 조합은 근거가 부족하다고 보고 생성을 건너뛴다(억지로
  트렌드를 지어내지 않도록).
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
import time

from summarize_news import build_provider, gemini_backoff_seconds, gemini_pace

PERIOD_DAYS = 30  # 월간 분석만 생성(2026-09-02, 7일치는 뺐다 — 위 docstring 참고)
MIN_ARTICLES = 3  # 이보다 적으면 트렌드 생성을 건너뜀(근거 부족)
MAX_LINES_PER_PROMPT = 50  # 프롬프트에 넣는 기사 목록 상한(토큰/비용 보호)
REGIONS = ["domestic", "global"]
REGION_LABEL = {"domestic": "국내", "global": "해외"}

TREND_SYSTEM = """당신은 한국 증권사의 의료기기/디지털헬스/로보틱스 담당 애널리스트를 돕는
리서치 보조원입니다. 아래에 특정 카테고리의 최근 한 달간 나온 기사 목록(날짜/기업/맥락/
제목 또는 요약)이 주어집니다. 이 목록만 근거로 삼아, 이 기간 동안 두드러진 이벤트·주제·
흐름을 애널리스트 관점에서 3~5개의 불릿(bullet)으로 종합하세요.

규칙:
- 출력 형식은 반드시 줄바꿈("\\n")으로 구분된 불릿 목록이어야 합니다. 각 줄은 "- "로
  시작하세요(예: "- 세포라 입점으로 북미 유통망 확대함\\n- 상반기 매출 역대 최대 기록\\n- ...").
  문장을 죽 이어붙인 하나의 문단으로 쓰지 마세요 — 가독성을 위해 항목별로 줄을 나눕니다.
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
- 출력은 JSON 하나만: {"text": "- 첫째 불릿\\n- 둘째 불릿\\n- 셋째 불릿"}"""


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


def generate_trend(provider, category, items, debug=False):
    lines = build_lines(items)
    user_content = (
        f"카테고리: {category}\n최근 {PERIOD_DAYS}일간 기사 {len(items)}건(아래 목록):\n\n"
        + "\n".join(lines)
    )
    tag = f"{category}, {PERIOD_DAYS}일"
    for attempt in range(2):  # 1차 시도 + 실패 시 1회 재시도(요약 배치와 동일한 방식)
        try:
            raw = provider.call(TREND_SYSTEM, user_content, max_tokens=800)
        except Exception as e:
            retry = "재시도도 " if attempt else ""
            print(f"[WARN] 트렌드 생성 API 호출 {retry}실패({tag}): {e}", file=sys.stderr)
            # Gemini 무료 티어 분당 5회 제한(2026-09-02 실제 로그로 확인) — 429면 일반
            # 페이싱보다 더 오래 쉬어야 다음 시도가 또 429로 낭비되지 않는다.
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                time.sleep(gemini_backoff_seconds(e))
            else:
                gemini_pace(provider)
            continue
        gemini_pace(provider)
        obj = parse_json_object(raw)
        text = obj.get("text") if obj else None
        if text:
            return str(text).strip()
        if debug:
            print(f"[DEBUG] 트렌드 응답 파싱 실패({tag}) 원문: {raw[:200]}", file=sys.stderr)
    return None


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

        for cat in categories:
            items = [r for r in region_records if r.get("cat") == cat and r.get("date", "") >= cutoff]
            if len(items) < MIN_ARTICLES:
                continue
            items.sort(key=lambda r: r.get("date", ""))
            text = generate_trend(provider, cat, items, debug=args.debug)
            if not text:
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
