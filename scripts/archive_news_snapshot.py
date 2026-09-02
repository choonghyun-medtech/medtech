#!/usr/bin/env python3
"""
news.json(당일 스냅샷, 매일 덮어써짐)의 기사를 news_history.jsonl에 누적 기록한다.
index.html의 산업·기업 뉴스 탭 안의 "뉴스 아카이브"/"월간 브리핑" 서브탭이 이 파일을
fetch해 날짜별 원문 조회·카테고리별 AI 월간 분석에 쓴다. news.json은 수집 윈도우(평일
24시간/월요일 72시간)만 담고 매일 덮어써지기 때문에, 과거 데이터를 보려면 이렇게 별도로
누적해 두는 파일이 필요하다.

- 한 줄 = 기사 1건. 필드: date, region(domestic|global), cat(카테고리), co(기업명,
  콤마로 여러 개일 수 있음 — scrape_news.py/scrape_news_global.py의 교차 기업 중복 병합
  결과를 그대로 보존), t(제목), url, ctx(국내만, [맥락] 태그), summary(자동 요약,
  summarize_news.py가 이미 채운 값을 그대로 가져옴 — 이 스크립트가 요약을 새로 만들지
  않는다). ctx/summary는 요약 단계가 실패했거나 아직 안 붙었으면 빈 문자열일 수 있다.
- 같은 url은 이미 기록된 경우 다시 추가하지 않는다(news.json의 수집 윈도우가 겹쳐도
  history가 중복으로 쌓이지 않도록). url이 없는 항목은 건너뛴다(식별 불가).
- 보관 기간 상한 RETENTION_DAYS(2026-09-03 추가, 사용자 요청) — "뉴스 아카이브"가 최대
  3개월(기간 토글이 오늘/7일/30일까지만 있어 그 이상은 화면에서도 쓸 일이 없음)까지만
  다루므로, news_history.jsonl도 그 이상 지난 기사는 매일 실행마다 지워서 파일 용량이
  무한정 커지지 않게 한다. 그래서 이전처럼 순수 append-only가 아니라, 매번 파일 전체를
  다시 읽어 만료분을 걸러내고 통째로 다시 쓴다(그래도 기존 줄의 내용 자체를 수정하지는
  않는다 — 삭제만 한다).

사용법:
    python archive_news_snapshot.py --news news.json --history news_history.jsonl
"""
import argparse
import datetime
import json
import sys

RETENTION_DAYS = 90  # 뉴스 아카이브의 실질 조회 범위(최대 30일)보다 여유 있게 3개월 보관


def load_existing_records(history_path: str):
    records = []
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
                if rec.get("url"):
                    records.append(rec)
    except FileNotFoundError:
        pass
    return records


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

    existing_records = load_existing_records(args.history)
    seen_urls = {rec["url"] for rec in existing_records}
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

    cutoff = (datetime.date.today() - datetime.timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    all_records = existing_records + new_records
    kept_records = [rec for rec in all_records if rec.get("date", "") >= cutoff]
    expired = len(all_records) - len(kept_records)

    with open(args.history, "w", encoding="utf-8") as f:
        for rec in kept_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"저장 완료: {args.history} 신규 {len(new_records)}건 추가, "
          f"{RETENTION_DAYS}일 초과 {expired}건 삭제 (누적 {len(kept_records)}건)")


if __name__ == "__main__":
    main()
