#!/usr/bin/env python3
"""
news.json(당일 스냅샷, 매일 덮어써짐)의 기사를 news_history.jsonl에 누적 기록한다.
index.html의 산업·기업 뉴스 탭 안의 "뉴스 플로우" 섹션이 이 파일을 fetch해 날짜별로
무슨 뉴스가 있었는지 서술형으로 보여준다(건수 집계가 아니라 ctx/summary 내용 자체가
목적). news.json은 수집 윈도우(평일 24시간/월요일 72시간)만 담고 매일 덮어써지기
때문에, 과거 데이터를 보려면 이렇게 별도로 누적해 두는 파일이 필요하다.

- 한 줄 = 기사 1건. 필드: date, region(domestic|global), cat(카테고리), co(기업명,
  콤마로 여러 개일 수 있음 — scrape_news.py/scrape_news_global.py의 교차 기업 중복 병합
  결과를 그대로 보존), t(제목), url, ctx(국내만, [맥락] 태그), summary(자동 요약,
  summarize_news.py가 이미 채운 값을 그대로 가져옴 — 이 스크립트가 요약을 새로 만들지
  않는다). ctx/summary는 요약 단계가 실패했거나 아직 안 붙었으면 빈 문자열일 수 있다.
- 같은 url은 이미 기록된 경우 다시 추가하지 않는다(news.json의 수집 윈도우가 겹쳐도
  history가 중복으로 쌓이지 않도록). url이 없는 항목은 건너뛴다(식별 불가).
- 기존 줄은 절대 수정/삭제하지 않는다 — append-only. (그래서 summarize_news.py가 이
  스크립트보다 먼저 실행돼 summary를 채워둔 상태여야 history에도 요약이 함께 남는다 —
  update-news.yml에서 실제로 Summarize news 스텝 다음에 이 스텝을 배치했다.)

사용법:
    python archive_news_snapshot.py --news news.json --history news_history.jsonl
"""
import argparse
import json
import sys


def load_existing_urls(history_path: str) -> set:
    seen = set()
    try:
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = rec.get("url")
                if url:
                    seen.add(url)
    except FileNotFoundError:
        pass
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--news", default="news.json")
    ap.add_argument("--history", default="news_history.jsonl")
    args = ap.parse_args()

    try:
        with open(args.news, encoding="utf-8") as f:
            news = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[WARN] {args.news}을(를) 읽지 못해 아카이빙을 건너뜁니다: {e}", file=sys.stderr)
        sys.exit(0)  # news.json 문제는 다른 스크립트가 이미 실패 처리하므로 여기서는 조용히 종료

    seen_urls = load_existing_urls(args.history)
    new_records = []

    for region, key in (("domestic", "domestic"), ("global", "global")):
        for group in news.get(key, []) or []:
            cat = group.get("cat", "")
            for item in group.get("items", []) or []:
                url = item.get("url")
                if not url or url in seen_urls:
                    continue
                new_records.append({
                    "date": item.get("date", ""),
                    "region": region,
                    "cat": cat,
                    "co": item.get("co", ""),
                    "t": item.get("t", ""),
                    "url": url,
                    "ctx": item.get("ctx", ""),
                    "summary": item.get("summary", ""),
                })
                seen_urls.add(url)

    if new_records:
        with open(args.history, "a", encoding="utf-8") as f:
            for rec in new_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"저장 완료: {args.history}에 신규 {len(new_records)}건 추가 (누적 {len(seen_urls)}건)")


if __name__ == "__main__":
    main()
