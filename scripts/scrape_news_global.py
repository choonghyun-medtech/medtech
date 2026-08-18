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
- 최근 RECENCY_DAYS일 이내 기사만 채택. 수집 결과가 0건이면 기존 news.json을 보존한다.

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

RECENCY_DAYS = 10
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
GLOBAL_COMPANY_CATEGORY = {
    "Boston Scientific": "MedTech",
    "Stryker": "MedTech",
    "Medtronic": "MedTech",
    "Thermo Fisher": "MedTech",
    "Edwards Lifesciences": "MedTech",
    "Dexcom": "MedTech",
    "Abbott": "MedTech",
    "Intuitive Surgical": "Surgical Robot",
    "Exact Sciences": "IVD",
    "Guardant Health": "IVD",
    "Hims & Hers": "Digital Health",
    "Hims and Hers": "Digital Health",
    "Teladoc": "Digital Health",
    "UnitedHealth": "Healthcare Provider",
    "InMode": "Cash Pay Market",
    "Cooper Companies": "Cash Pay Market",
}

CATEGORY_ORDER = ["MedTech", "Surgical Robot", "IVD", "Digital Health", "Healthcare Provider", "Cash Pay Market"]

CUSTOM_DATE_FMT = "%b %d, %Y %I:%M%p"  # Fierce 계열 매체가 쓰는 'Aug 18, 2026 7:53am' 형식


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
    cutoff = now - datetime.timedelta(days=RECENCY_DAYS)

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
