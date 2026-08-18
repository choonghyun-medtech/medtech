#!/usr/bin/env python3
"""
해외 트래킹 기업의 최근 뉴스를 medtech/헬스케어 전문매체 RSS 피드에서 가져와
news.json의 "global" 섹션을 자동 갱신한다. "domestic"(국내) 섹션은 scrape_news.py가
관리하므로 이 스크립트는 건드리지 않고 그대로 보존한다.

- API 키가 필요 없다: 아래 매체들이 공개 RSS 피드를 제공하며 실제 접속으로 확인했다.
    · MedTech Dive   https://www.medtechdive.com/feeds/news/
    · Fierce Biotech(Medtech) https://www.fiercebiotech.com/rss/medtech/xml
    · Fierce Healthcare       https://www.fiercehealthcare.com/rss/xml
- 각 피드를 한 번씩만 가져온 뒤, 기사 제목/요약에 우리가 트래킹하는 해외 기업명이
  실제로 들어있는 것만 골라 카테고리별로 묶는다(뉴스클리핑_gathering 대신 회사명 매칭).
- 매체마다 pubDate 포맷이 달라(RFC822 vs 'Aug 18, 2026 7:53am' 커스텀 포맷) feedparser의
  기본 파서로 안 되는 경우 직접 포맷을 하나 더 시도한다.
- 수집 윈도우는 scrape_news.py와 동일 규칙(평일 24시간 / 월요일 72시간, 공휴일 미반영).
  수집 결과가 0건이면 기존 news.json을 보존한다.

사용법:
    python scrape_news_global.py --out news.json
"""
import argparse
import datetime
import json
import sys

import feedparser
import requests

FEEDS = [
    "https://www.medtechdive.com/feeds/news/",
    "https://www.fiercebiotech.com/rss/medtech/xml",
    "https://www.fiercehealthcare.com/rss/xml",
]

MAX_ITEMS_PER_CATEGORY = 6
REQUEST_TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# 해외 기업명 -> 뉴스클리핑_medtech 6개 카테고리(MedTech/Surgical Robot/IVD/
# Digital Health/Healthcare Provider/Cash Pay Market). 영문 기사이므로 영문 회사명으로 매칭.
# medtech_news_clipping_rules.md의 공식 기업 리스트를 그대로 반영(2026-08-18 기준 사용자 제공 버전).
# 이전 버전에 있던 Exact Sciences / Cooper Companies는 이 md에 없어 제거했고,
# Thermo Fisher(MedTech→IVD), Dexcom(MedTech→Digital Health)는 카테고리를 md에 맞게 수정.
GLOBAL_COMPANY_CATEGORY = {
    # 1. MedTech
    "Abbott": "MedTech",
    "Stryker": "MedTech",
    "Medtronic": "MedTech",
    "Boston Scientific": "MedTech",
    "Edwards Lifesciences": "MedTech",
    # 2. Surgical Robot
    "Intuitive Surgical": "Surgical Robot",
    "Edge Medical": "Surgical Robot",
    "Microport MedBot": "Surgical Robot",
    # 3. IVD
    "Thermo Fisher": "IVD",
    "Natera": "IVD",
    "Guardant Health": "IVD",
    "Tempus AI": "IVD",
    # 4. Digital Health
    "Dexcom": "Digital Health",
    "RadNet": "Digital Health",
    "iRhythm": "Digital Health",
    "Hims & Hers": "Digital Health",
    "Hims and Hers": "Digital Health",
    "Teladoc": "Digital Health",
    # 5. Healthcare Provider
    "UnitedHealth": "Healthcare Provider",
    # 6. Cash Pay Market
    "Align Technology": "Cash Pay Market",
    "InMode": "Cash Pay Market",
    "Straumann": "Cash Pay Market",
}

CATEGORY_ORDER = ["MedTech", "Surgical Robot", "IVD", "Digital Health", "Healthcare Provider", "Cash Pay Market"]

CUSTOM_DATE_FMT = "%b %d, %Y %I:%M%p"  # Fierce 계열 매체가 쓰는 'Aug 18, 2026 7:53am' 형식


def recency_hours_for_today(today: datetime.date) -> int:
    """scrape_news.py와 동일한 단순화 규칙: 월요일(KST 기준 실행일)은 72시간,
    그 외 평일은 24시간. 이 스크립트는 KST 평일에만 실행되므로(cron 0-4=일~목 UTC),
    한국 공휴일 반영은 아직 없다 — medtech_news_clipping_rules.md가 경고하는
    '해외는 토요일에도 기사가 나올 수 있다'는 점은 실행 요일이 아니라 실행 간격(주말 공백)
    문제라 이 규칙으로 충분히 커버된다."""
    if today.weekday() == 0:  # Monday
        return 72
    return 24


def parse_entry_date(entry):
    if entry.get("published_parsed"):
        import time
        return time.strftime("%Y-%m-%d", entry.published_parsed), datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc)
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None, None
    try:
        dt = datetime.datetime.strptime(raw.strip(), CUSTOM_DATE_FMT)
        return dt.strftime("%Y-%m-%d"), dt.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None, None


def fetch_feed(url: str, debug=False):
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    if debug:
        print(f"[DEBUG] {url}: HTTP {resp.status_code}, entries={len(parsed.entries)}", file=sys.stderr)
        if parsed.bozo:
            print(f"[DEBUG] {url}: feedparser bozo(파싱 경고)={parsed.bozo_exception}", file=sys.stderr)
    return parsed.entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="news.json")
    args = ap.parse_args()

    try:
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    existing_domestic = existing.get("domestic", [])

    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff_hours = recency_hours_for_today(now.astimezone().date())
    cutoff = now - datetime.timedelta(hours=cutoff_hours)
    print(f"[INFO] 오늘 기준 수집 윈도우: 최근 {cutoff_hours}시간 이내", file=sys.stderr)

    by_category = {cat: [] for cat in CATEGORY_ORDER}
    seen_links = set()
    total_entries = 0
    debug_budget = 3

    for feed_url in FEEDS:
        try:
            entries = fetch_feed(feed_url, debug=debug_budget > 0)
            debug_budget -= 1
        except Exception as e:
            print(f"[WARN] {feed_url}: 피드 수집 실패 ({e})", file=sys.stderr)
            continue
        total_entries += len(entries)

        for entry in entries:
            title = (entry.get("title") or "").strip()
            summary = (entry.get("summary") or entry.get("description") or "").strip()
            link = entry.get("link")
            if not title or not link or link in seen_links:
                continue
            date_str, dt = parse_entry_date(entry)
            if date_str is None or dt is None or dt < cutoff:
                continue

            haystack = f"{title} {summary}"
            for company, category in GLOBAL_COMPANY_CATEGORY.items():
                if company.lower() in haystack.lower():
                    by_category.setdefault(category, []).append({
                        "co": company,
                        "ctx": "News",
                        "t": title,
                        "src": feed_url.split("/")[2].replace("www.", ""),
                        "date": date_str,
                        "url": link,
                    })
                    seen_links.add(link)
                    break  # 한 기사에 여러 회사명이 겹쳐도 카테고리 중복 방지 위해 첫 매치만 채택

    ok_categories = sum(1 for items in by_category.values() if items)
    if ok_categories == 0:
        print(f"[ERROR] 총 {total_entries}개 기사를 훑었지만 매칭된 회사가 0개라 기존 news.json을 보존하고 종료합니다.", file=sys.stderr)
        sys.exit(1)

    global_section = []
    for cat in CATEGORY_ORDER:
        items = sorted(by_category.get(cat, []), key=lambda x: x["date"], reverse=True)[:MAX_ITEMS_PER_CATEGORY]
        global_section.append({"cat": cat, "items": items})

    payload = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": existing.get("source", "") or "네이버 뉴스검색 API(국내, 자동) + medtech/헬스케어 매체 RSS(해외, 자동) · 뉴스클리핑 가이드라인 카테고리 기준",
        "domestic": existing_domestic,
        "global": global_section,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    total_items = sum(len(g["items"]) for g in global_section)
    print(f"저장 완료: {args.out} (해외 {ok_categories}개 카테고리, {total_items}건 / 국내는 기존 값 {len(existing_domestic)}개 카테고리 유지)")


if __name__ == "__main__":
    main()
