#!/usr/bin/env python3
"""
news_history.jsonl(scripts/archive_news_snapshot.py가 매일 누적)을 카테고리별로 모아
"최근 N일간 이 카테고리에서 어떤 흐름/주제가 두드러졌는지" LLM으로 종합 서술한다.

index.html의 산업·기업 뉴스 탭 안 "뉴스 플로우" 섹션이 이 결과(news_trend.json)를 읽어,
사용자가 카테고리를 하나 선택하면 건수 집계가 아니라 "미용 카테고리에서 최근 1주일간
신제품 효과 관련 이슈가 부각됨" 같은 내용 종합을 보여준다.

- provider(Gemini 무료/Anthropic 유료)는 summarize_news.py의 것을 그대로 재사용한다
  (같은 scripts/ 디렉터리에 있어 import 가능).
- 기업 단위가 아니라 "카테고리" 단위로만 생성한다 — 국내 10개 + 해외 9개 카테고리 정도면
  하루 최대 19×2(7일/30일)=38콜 수준으로 무료 티어 한도 안에서 충분히 돈다. 기업 단위(약
  90개)까지 하면 콜 수가 너무 많아져 무료 한도를 위협할 수 있어 일단 카테고리로 제한했다.
- 기사가 3건 미만인 카테고리×기간 조합은 근거가 부족하다고 보고 생성을 건너뛴다(억지로
  트렌드를 지어내지 않도록).
- 프롬프트에 "주어진 기사 목록에 없는 내용은 절대 추측하지 말라"는 지침을 명시하고, 실제
  기사 목록(날짜/기업/맥락/요약)을 통째로 프롬프트에 넣어 그 안에서만 종합하게 한다.
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

TREND_SYSTEM = """당신은 한국 증권사의 의료기기/디지털헬스/로보틱스 담당 애널리스트를 돕는
리서치 보조원입니다. 아래에 특정 카테고리의 최근 기간 동안 나온 기사 목록(날짜/기업/맥락/
제목 또는 요약)이 주어집니다. 이 목록만 근거로 삼아, 이 기간 동안 이 카테고리에서 두드러진
주제·흐름·이슈를 애널리스트 관점에서 3~5문장으로 종합하세요.

규칙:
- 반드시 주어진 기사 목록에 있는 내용만 근거로 삼으세요. 목록에 없는 사실을 추측하거나
  지어내지 마세요.
- 가능하면 구체적인 기업명을 언급하며 "~기업들이 ~하는 흐름이 나타남" 식으로 종합하세요.
- 단순히 기사 제목을 나열하지 말고, 여러 기사를 관통하는 공통 주제/패턴을 짚어내세요
  (예: "신제품 출시 효과가 부각됨", "M&A 움직임이 이어짐", "실적 발표가 몰림" 등).
- 뚜렷한 공통 주제가 안 보이면 억지로 만들지 말고 "특별히 두드러진 단일 흐름보다는
  개별 기업 이슈가 산발적으로 있었음" 같이 있는 그대로 서술하세요.
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


def generate_trend(provider, category, items, period_days, debug=False):
    lines = build_lines(items)
    user_content = (
        f"카테고리: {category}\n최근 {period_days}일간 기사 {len(items)}건(아래 목록):\n\n"
        + "\n".join(lines)
    )
    for attempt in range(2):  # 1차 시도 + 실패 시 1회 재시도(요약 배치와 동일한 방식)
        try:
            raw = provider.call(TREND_SYSTEM, user_content, max_tokens=800)
        except Exception as e:
            tag = "재시도도 " if attempt else ""
            print(f"[WARN] 트렌드 생성 API 호출 {tag}실패({category}, {period_days}일): {e}", file=sys.stderr)
            continue
        obj = parse_json_object(raw)
        text = obj.get("text") if obj else None
        if text:
            return str(text).strip()
        if debug:
            print(f"[DEBUG] 트렌드 응답 파싱 실패({category}, {period_days}일) 원문: {raw[:200]}", file=sys.stderr)
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
    categories = sorted({r.get("cat") for r in records if r.get("cat")})

    trends = []
    for period_days in PERIOD_DAYS:
        cutoff = (now - datetime.timedelta(days=period_days)).strftime("%Y-%m-%d")
        for cat in categories:
            items = [r for r in records if r.get("cat") == cat and r.get("date", "") >= cutoff]
            if len(items) < MIN_ARTICLES:
                continue
            items.sort(key=lambda r: r.get("date", ""))
            text = generate_trend(provider, cat, items, period_days, debug=args.debug)
            if not text:
                continue
            trends.append({
                "scope": "category",
                "key": cat,
                "period_days": period_days,
                "text": text,
                "n_articles": len(items),
            })
            print(f"[INFO] 트렌드 생성 완료: {cat} / 최근 {period_days}일 ({len(items)}건)", file=sys.stderr)

    payload = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": f"news_history.jsonl 기반 카테고리별 자동 종합 ({provider.name})",
        "trends": trends,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} ({len(trends)}건 트렌드 생성)")


if __name__ == "__main__":
    main()
