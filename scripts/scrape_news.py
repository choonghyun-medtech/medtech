#!/usr/bin/env python3
"""
네이버 검색 API(뉴스)로 국내 트래킹 기업의 최근 뉴스를 가져와 news.json의
"domestic" 섹션을 자동 갱신한다. "global"(해외) 섹션은 이 API로 커버되지 않으므로
건드리지 않고 기존 값을 그대로 보존한다 — 해외는 별도 API(NewsAPI.org 등) 연동 전까지
대화창 리서치로 수동 갱신해야 한다.

- 인증: 환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (GitHub Actions 시크릿으로 주입,
  이 스크립트나 워크플로 파일에 실제 키 값을 하드코딩하지 않는다).
- 엔드포인트: https://openapi.naver.com/v1/search/news.json
  (공식 문서: https://developers.naver.com/docs/serviceapi/search/news/news.md)
- 회사명 -> 카테고리 매핑은 NEWS_COMPANY_CATEGORY에서 관리 (뉴스클리핑_가이드라인.md의
  7개 카테고리: Digital Health / Aesthetics / Robotics / Therapeutics / IVD /
  Bio-Processing / Dental 기준).
- 최근 RECENCY_DAYS일 이내 발행된 기사만, 회사명이 제목/요약에 실제로 포함된 것만 채택
  (검색어와 무관한 결과가 섞이는 것을 방지).
- 수집 결과가 0건이면 기존 news.json을 그대로 보존하고 실패로 종료한다(빈 데이터로 덮어쓰지 않음).

사용법:
    NAVER_CLIENT_ID=xxx NAVER_CLIENT_SECRET=yyy python scrape_news.py --out news.json
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
from email.utils import parsedate_to_datetime

import requests

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
RECENCY_DAYS = 10          # 이 기간 이내 발행된 기사만 채택
MAX_ITEMS_PER_COMPANY = 3  # 회사 하나당 최대 채택 기사 수
MAX_ITEMS_PER_CATEGORY = 6 # 카테고리 하나당 최종 표시 기사 수 상한
REQUEST_TIMEOUT = 15

# 회사명 -> 뉴스 카테고리. 리서치보고서 탭 CATEGORIES(9개 드롭다운)와는 다른 별도 분류로,
# 뉴스클리핑_가이드라인.md의 7개 카테고리를 그대로 따른다.
NEWS_COMPANY_CATEGORY = {
    "씨어스": "Digital Health",
    "인바디": "Digital Health",
    "아이센스": "Digital Health",
    "루닛": "Digital Health",
    "뉴로핏": "Digital Health",
    "파마리서치": "Aesthetics",
    "클래시스": "Aesthetics",
    "휴젤": "Aesthetics",
    "리브스메드": "Robotics",
    "고영": "Robotics",
    "큐렉소": "Robotics",
    "넥스트바이오메디컬": "Therapeutics",
    "씨젠": "IVD",
    "바이오다인": "IVD",
    "큐리오시스": "Bio-Processing",
    "덴티움": "Dental",
    "바텍": "Dental",
    "디오": "Dental",
    "그래피": "Dental",
}

CATEGORY_ORDER = ["Digital Health", "Aesthetics", "Robotics", "Therapeutics", "IVD", "Bio-Processing", "Dental"]

TAG_RE = re.compile(r"<[^>]+>")


def clean_text(s: str) -> str:
    return html.unescape(TAG_RE.sub("", s or "")).strip()


def parse_pubdate(pub_date: str):
    """RFC822 형식('Tue, 18 Aug 2026 09:00:00 +0900')을 YYYY-MM-DD로 변환."""
    try:
        dt = parsedate_to_datetime(pub_date)
        return dt.strftime("%Y-%m-%d"), dt
    except (TypeError, ValueError):
        return None, None


def fetch_news_for_company(client_id: str, client_secret: str, company: str, debug=False):
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {"query": company, "display": 10, "start": 1, "sort": "date"}
    resp = requests.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    if debug:
        print(f"[DEBUG] {company}: HTTP {resp.status_code}", file=sys.stderr)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])

    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=RECENCY_DAYS)

    out = []
    for it in items:
        title = clean_text(it.get("title", ""))
        desc = clean_text(it.get("description", ""))
        date_str, dt = parse_pubdate(it.get("pubDate", ""))
        if date_str is None or dt is None:
            continue
        if dt < cutoff:
            continue
        # 검색어(회사명)가 실제로 제목/요약에 포함된 것만 채택 — 네이버 뉴스 API는 종종
        # 관련성 낮은 결과도 섞어 주기 때문에 이 필터가 중요하다.
        if company not in title and company not in desc:
            continue
        url = it.get("originallink") or it.get("link")
        out.append({
            "co": company,
            "ctx": "뉴스",
            "t": title,
            "src": "네이버뉴스",
            "date": date_str,
            "url": url,
        })
        if len(out) >= MAX_ITEMS_PER_COMPANY:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="news.json")
    args = ap.parse_args()

    client_id = os.environ.get("NAVER_CLIENT_ID")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("[ERROR] NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)

    # 기존 파일 로드 — global(해외) 섹션은 이 스크립트가 건드리지 않고 그대로 보존한다.
    try:
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    existing_global = existing.get("global", [])

    by_category = {cat: [] for cat in CATEGORY_ORDER}
    ok_companies = 0
    debug_budget = 3
    for company, category in NEWS_COMPANY_CATEGORY.items():
        try:
            items = fetch_news_for_company(client_id, client_secret, company, debug=debug_budget > 0)
            debug_budget -= 1
        except Exception as e:
            print(f"[WARN] {company}: 수집 실패 ({e})", file=sys.stderr)
            continue
        if items:
            ok_companies += 1
            by_category.setdefault(category, []).extend(items)
            print(f"{company} ({category}): {len(items)}건")
        else:
            print(f"[INFO] {company}: 최근 {RECENCY_DAYS}일 내 관련 기사 없음", file=sys.stderr)

    if ok_companies == 0:
        print("[ERROR] 수집된 회사가 0개라 기존 news.json을 보존하고 종료합니다.", file=sys.stderr)
        sys.exit(1)

    domestic = []
    for cat in CATEGORY_ORDER:
        items = sorted(by_category.get(cat, []), key=lambda x: x["date"], reverse=True)[:MAX_ITEMS_PER_CATEGORY]
        domestic.append({"cat": cat, "items": items})

    payload = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "네이버 뉴스검색 API(국내, 자동) + 웹서치 기반 수동 리서치(해외, 미자동화) · 뉴스클리핑 가이드라인 카테고리 기준",
        "domestic": domestic,
        "global": existing_global,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    total = sum(len(g["items"]) for g in domestic)
    print(f"저장 완료: {args.out} (국내 {ok_companies}개 기업, {total}건 / 해외는 기존 값 {len(existing_global)}개 카테고리 유지)")


if __name__ == "__main__":
    main()
