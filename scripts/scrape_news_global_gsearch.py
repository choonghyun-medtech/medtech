#!/usr/bin/env python3
"""
사용자가 새 대화창에서 medtech_news_clipping_rules(-ed29020a).md를 붙여넣고 "클리핑 시작"을
입력했을 때와 같은 방식 — Google 검색(site:도메인 + 기업명 단독 검색) — 을 **완전 무료**로
자동화한다.

배경(2026-08-21): 처음에는 Anthropic API의 유료 web_search 도구(검색 1,000회당 $10)로
같은 걸 구현했었는데, 사용자가 "이 자동화는 최대한 돈을 쓰지 않는 방향으로 하고 유료
서비스는 제시하지 말라"고 명확히 선을 그었다. 사용자가 실제로 Claude 채팅창에서 md 파일을
직접 돌릴 때도 구글 검색을 쓰는 것 같다고 했으므로, API 과금 없이 진짜 "구글 검색"을 쓰는
방법 — Google 뉴스 검색의 공개 RSS 엔드포인트(news.google.com/rss/search) — 로 다시
구현했다. 이 엔드포인트는 API 키가 필요 없고, 완전한 Google 검색 문법(site:, 따옴표,
OR 등)을 그대로 지원한다. 비용은 $0(그냥 HTTP 요청 + XML 파싱).

- scrape_news_global.py(직접 사이트 RSS 피드)를 대체하는 게 아니라 "보강"이다. 이미
  news.json에 기록된 global 섹션에 URL/제목 기준으로 중복되지 않는 기사만 추가로
  병합한다 — 이 스크립트가 통째로 실패해도 RSS 결과는 그대로 남는다.
- 검색 방법(md 원문 그대로):
  1) 카테고리 전용 사이트마다 `site:도메인` 검색으로 그 사이트의 최근 기사를 확인.
  2) 카테고리별 지정 기업마다 `"기업명"` 단독 검색(사이트 제한 없음).
  두 종류 모두 news.google.com/rss/search로 보내고, scrape_news_global.py와 동일한
  회사명 매칭(GLOBAL_COMPANY_CATEGORY/COMPANY_SEARCH_ALIASES/CONTEXT_REQUIRED_GLOBAL)과
  수집 윈도우 규칙(recency_hours_for_today)을 그대로 재사용해 어떤 카테고리에 넣을지
  판단한다 — 두 스크립트가 다른 기준으로 분류하면 index.html에서 국내외 뉴스가 어긋나기
  때문에 반드시 단일 소스(scrape_news_global.py)를 그대로 가져다 쓴다.
- 알려진 한계(정직하게 밝혀둠):
  · Google 뉴스 RSS의 <link>는 실제 기사 URL이 아니라 news.google.com의 리다이렉트
    URL이다(공식 API가 아니라 공개 RSS라 어쩔 수 없음) — 브라우저로 열면 정상적으로
    실제 기사로 넘어가지만, "깔끔한 원문 URL"은 아니라는 점 참고.
  · 비공식 엔드포인트라 Google이 마크업/정책을 바꾸면 깨질 수 있다 — 실패해도
    sys.exit(0)으로 조용히 종료하고 기존 news.json을 보존한다.
  · 요청이 너무 잦으면 일시적으로 차단될 수 있어 요청 사이 딜레이(REQUEST_DELAY_SEC)를
    둔다.

사용법:
    python scrape_news_global_gsearch.py --out news.json
    python scrape_news_global_gsearch.py --out news.json --debug
"""
import argparse
import datetime
import json
import sys
import time
import urllib.parse

import feedparser
import requests

# scrape_news_global.py와 동일한 카테고리/회사 매핑·수집 윈도우 규칙·중복 판정·회사명
# 매칭 로직을 그대로 재사용한다(단일 소스 유지).
from scrape_news_global import (
    CATEGORY_ORDER,
    GLOBAL_COMPANY_CATEGORY,
    COMPANY_SEARCH_ALIASES,
    CONTEXT_REQUIRED_GLOBAL,
    MAX_ITEMS_PER_CATEGORY,
    HEADERS,
    recency_hours_for_today,
    parse_entry_date,
    company_mentioned,
    context_ok,
    is_duplicate_title,
    clean_text,
)

GNEWS_RSS_BASE = "https://news.google.com/rss/search"
REQUEST_DELAY_SEC = 0.5
REQUEST_TIMEOUT = 20

# md(medtech_news_clipping_rules-ed29020a.md, 2026-08-18 최신본)의 "3. 뉴스 수집 사이트"
# 그대로. site: 검색이라 RSS 피드 유무와 무관하게(massdevice.com처럼 RSS가 없던 곳도)
# 커버할 수 있다.
CATEGORY_SITES = {
    "MedTech": ["fiercebiotech.com", "medtechdive.com", "massdevice.com"],
    "Surgical Robot": ["surgicalroboticstechnology.com", "medchina.tech"],
    "IVD": ["360dx.com"],
    "Digital Health": ["fiercehealthcare.com"],
    "Healthcare Provider": ["fiercehealthcare.com", "healthcaredive.com"],
    "Cash Pay Market": ["dental-tribune.com", "dermatologytimes.com", "theaestheticguide.com"],
    "Humanoid": ["therobotreport.com", "roboticstomorrow.com", "reuters.com", "techcrunch.com",
                 "semafor.com", "irobotnews.com"],
    "산업용·서비스 로봇": ["therobotreport.com", "roboticstomorrow.com", "reuters.com",
                       "techcrunch.com", "semafor.com", "irobotnews.com"],
    "로보틱스 밸류체인": ["therobotreport.com", "roboticstomorrow.com", "reuters.com",
                     "techcrunch.com", "semafor.com", "irobotnews.com"],
}

QUERY_NAME_OVERRIDES = {
    "Abbott": "Abbott Laboratories",
    "UnitedHealth": "UnitedHealth Group",
}


def gnews_rss_url(query: str, when_days: int) -> str:
    q = f"{query} when:{when_days}d"
    return f"{GNEWS_RSS_BASE}?q={urllib.parse.quote(q)}&hl=en-US&gl=US&ceid=US:en"


def build_query_list(when_days: int):
    """(설명용 라벨, RSS URL) 리스트. 사이트 리스트는 여러 카테고리가 공유하는 경우가
    많아(Robotics 채널 6개 사이트를 3개 카테고리가 공유) 중복 호출을 피하려고 도메인
    기준으로 한 번만 모은다 — scrape_news_global.py의 FEEDS 리스트와 동일한 역할."""
    seen_domains = set()
    queries = []
    for cat in CATEGORY_ORDER:
        for domain in CATEGORY_SITES.get(cat, []):
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            queries.append((f"site:{domain}", gnews_rss_url(f"site:{domain}", when_days)))

    for company in GLOBAL_COMPANY_CATEGORY:
        name = QUERY_NAME_OVERRIDES.get(company, company)
        aliases = COMPANY_SEARCH_ALIASES.get(company)
        if aliases and len(aliases) > 1:
            q = " OR ".join(f'"{a}"' for a in aliases)
        else:
            q = f'"{name}"'
        queries.append((f"company:{company}", gnews_rss_url(q, when_days)))
    return queries


def fetch_gnews(url: str, debug=False):
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    if debug:
        print(f"[DEBUG] {url}: HTTP {resp.status_code}, entries={len(parsed.entries)}", file=sys.stderr)
    return parsed.entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="news.json")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    existing_domestic = existing.get("domestic", [])
    existing_global = existing.get("global", [])

    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff_hours = recency_hours_for_today(now.astimezone().date())
    cutoff = now - datetime.timedelta(hours=cutoff_hours)
    when_days = max(1, -(-cutoff_hours // 24))  # 올림(ceil) — Google when: 은 일 단위만 지원
    print(f"[INFO] Google 뉴스 검색 보강: 최근 {cutoff_hours}시간 이내(when:{when_days}d) 기사 대상",
          file=sys.stderr)

    by_category = {g.get("cat"): list(g.get("items", [])) for g in existing_global}
    seen_links = {it.get("url") for items in by_category.values() for it in items}

    queries = build_query_list(when_days)
    print(f"[INFO] Google 뉴스 검색 {len(queries)}건 실행 예정(사이트 {sum(1 for l,_ in queries if l.startswith('site:'))}건 "
          f"+ 기업 {sum(1 for l,_ in queries if l.startswith('company:'))}건)", file=sys.stderr)

    total_entries = 0
    ok_queries = 0
    failed_queries = 0
    debug_budget = 5

    for label, url in queries:
        try:
            entries = fetch_gnews(url, debug=debug_budget > 0)
            debug_budget -= 1
            ok_queries += 1
        except Exception as e:
            failed_queries += 1
            if args.debug:
                print(f"[WARN] 검색 실패({label}): {e}", file=sys.stderr)
            time.sleep(REQUEST_DELAY_SEC)
            continue
        total_entries += len(entries)

        for entry in entries:
            # Google 뉴스 RSS 제목은 보통 "기사 제목 - 매체명" 형식이라 매체명 접미사를 뗀다.
            raw_title = clean_text(entry.get("title") or "")
            title = raw_title.rsplit(" - ", 1)[0].strip() if " - " in raw_title else raw_title
            summary = clean_text(entry.get("summary") or entry.get("description") or "")
            link = entry.get("link")
            if not title or not link or link in seen_links:
                continue
            date_str, dt = parse_entry_date(entry)
            if date_str is None or dt is None or dt < cutoff:
                continue

            src = ""
            source_field = entry.get("source")
            if isinstance(source_field, dict):
                src = source_field.get("title") or source_field.get("href") or ""
            if not src and " - " in raw_title:
                src = raw_title.rsplit(" - ", 1)[1].strip()

            haystack = f"{title} {summary}"
            candidates = [
                (company, category)
                for company, category in GLOBAL_COMPANY_CATEGORY.items()
                if company_mentioned(company, haystack) and context_ok(company, haystack)
            ]
            if not candidates:
                continue
            company, category = min(candidates, key=lambda c: (c[0] in CONTEXT_REQUIRED_GLOBAL, -len(c[0])))

            bucket = by_category.setdefault(category, [])
            if any(is_duplicate_title(title, it.get("t", "")) for it in bucket):
                continue
            bucket.append({
                "co": company,
                "ctx": "News",
                "t": title,
                "src": src or "news.google.com",
                "date": date_str,
                "url": link,
                "desc": summary,  # summarize_news.py가 2줄 한국어 요약을 생성할 때 참고(무료 Gemini 우선).
            })
            seen_links.add(link)
        time.sleep(REQUEST_DELAY_SEC)

    print(f"[INFO] 검색 {ok_queries}건 성공/{failed_queries}건 실패, 총 {total_entries}개 기사 훑음", file=sys.stderr)

    if ok_queries == 0:
        print("[WARN] 모든 Google 뉴스 검색 요청이 실패했습니다(네트워크 문제 또는 일시 차단 "
              "가능성) — 기존 news.json을 그대로 두고 종료합니다.", file=sys.stderr)
        sys.exit(0)

    for cat in list(by_category.keys()):
        kept = []
        for c in by_category[cat]:
            dup_idx = next((i for i, k in enumerate(kept) if is_duplicate_title(c["t"], k["t"])), None)
            if dup_idx is None:
                kept.append(c)
        by_category[cat] = sorted(kept, key=lambda x: x.get("date", ""), reverse=True)[:MAX_ITEMS_PER_CATEGORY]

    global_section = [{"cat": cat, "items": by_category.get(cat, [])} for cat in CATEGORY_ORDER]
    total_items = sum(len(g["items"]) for g in global_section)

    source = existing.get("source", "") or ""
    if "Google 뉴스 검색" not in source:
        source = (source + " + Google 뉴스 검색(site: 검색 기반, md 가이드라인 방식, 무료)").strip(" +")

    payload = {
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "domestic": existing_domestic,
        "global": global_section,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} (해외 총 {total_items}건)")


if __name__ == "__main__":
    main()
