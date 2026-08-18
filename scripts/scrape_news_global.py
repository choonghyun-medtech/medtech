#!/usr/bin/env python3
"""
해외 트래킹 기업의 최근 뉴스를 medtech/헬스케어/로보틱스 전문매체 RSS 피드에서 가져와
news.json의 "global" 섹션을 자동 갱신한다. "domestic"(국내) 섹션은 scrape_news.py가
관리하므로 이 스크립트는 건드리지 않고 그대로 보존한다.

- API 키가 필요 없다: 아래 매체들이 공개 RSS 피드를 제공하며 실제 접속으로 확인했다.
  [MedTech 채널]
    · MedTech Dive            https://www.medtechdive.com/feeds/news/
    · Fierce Biotech(Medtech) https://www.fiercebiotech.com/rss/medtech/xml
    · Fierce Healthcare       https://www.fiercehealthcare.com/rss/xml
  [Robotics 채널] — medtech_news_clipping_rules.md(2026-08-18 최신본)에서 신규 추가된
  휴머노이드/산업용·서비스 로봇/로보틱스 밸류체인 3개 카테고리를 커버하기 위해 추가.
    · The Robot Report        https://www.therobotreport.com/feed
    · Robotics Tomorrow        https://www.roboticstomorrow.com/rss/news
    · TechCrunch(Robotics)     https://techcrunch.com/category/robotics/feed
  (md가 언급한 massdevice.com/reuters.com/semafor.com/irobotnews.com/중국어 사이트는
  RSS가 없거나 접속 확인이 안 돼 제외 — massdevice는 이전에도 빈 응답이라 제외했었다.
  Google site: 검색 기반 수집(md의 원래 방법)은 자동화 스크립트로 안정적으로 재현하기
  어려워, 검증된 RSS 피드가 있는 소스만 사용한다.)
- 회사명 매칭은 단순 substring이 아니라 단어 경계 정규식으로 한다(예: "ABB"가 다른 영단어
  안에 우연히 포함되는 경우 방지).
- COMPANY_SEARCH_ALIASES: 모기업/제품명이 함께 언급되는 회사(예: Tesla(Optimus),
  KUKA(Midea Group)) — 별칭 중 하나라도 있으면 매칭.
- CONTEXT_REQUIRED_GLOBAL: Tesla/Bosch/Magna/Schaeffler처럼 초대형 기업이라 회사명만으로는
  자동차·일반 산업재 등 무관 뉴스가 압도적으로 많이 잡히는 경우, 로봇 관련 키워드가 함께
  있어야만 채택한다(국내 스크립트의 디오/현대차그룹 안전장치와 동일한 원리).
- 중복 처리: 제목 단어 겹침이 높은 기사는 하나만 남긴다(신디케이션/보도자료 재배포 방지).
- 매체마다 pubDate 포맷이 달라(RFC822 vs 'Aug 18, 2026 7:53am' 커스텀 포맷) feedparser의
  기본 파서로 안 되는 경우 직접 포맷을 하나 더 시도한다.
- 수집 윈도우는 scrape_news.py와 동일 규칙(평일 24시간 / 월요일 72시간, 공휴일 미반영).
  수집 결과가 0건이면 기존 news.json을 보존한다.

사용법:
    python scrape_news_global.py --out news.json
"""
import argparse
import datetime
import html
import json
import re
import sys

import feedparser
import requests

TAG_RE = re.compile(r"<[^>]+>")


def clean_text(s: str) -> str:
    return html.unescape(TAG_RE.sub("", s or "")).strip()


FEEDS = [
    # MedTech 채널
    "https://www.medtechdive.com/feeds/news/",
    "https://www.fiercebiotech.com/rss/medtech/xml",
    "https://www.fiercehealthcare.com/rss/xml",
    # Robotics 채널 (휴머노이드/산업용·서비스 로봇/로보틱스 밸류체인 공용 소스)
    "https://www.therobotreport.com/feed",
    "https://www.roboticstomorrow.com/rss/news",
    "https://techcrunch.com/category/robotics/feed",
]

MAX_ITEMS_PER_CATEGORY = 6
REQUEST_TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# 해외 기업명 -> 뉴스 카테고리. medtech_news_clipping_rules.md(2026-08-18 최신본)의
# 공식 기업 리스트를 그대로 반영. MedTech 채널(1~6)은 기존과 동일, Robotics 채널(7~9)이
# 신규 추가됨.
GLOBAL_COMPANY_CATEGORY = {
    # [MedTech 채널]
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
    # [Robotics 채널]
    # 7. 휴머노이드 — 카테고리명은 "Humanoid"(영문)로 통일한다. 국내 md는 "Humanoid"(영문)로
    # 적혀있고 해외 md는 "휴머노이드"(한글)로 적혀있어 표기가 다른데, index.html에서 국내/해외
    # 뉴스를 같은 카테고리로 묶어 보여주려면(이미 IVD/Digital Health가 이렇게 공유되는 중)
    # 두 스크립트가 정확히 같은 문자열을 써야 한다. 국내 md의 영문 표기를 기준으로 삼았다.
    "Tesla": "Humanoid",
    "Figure AI": "Humanoid",
    "Agility Robotics": "Humanoid",
    "Boston Dynamics": "Humanoid",
    "Unitree": "Humanoid",
    "AgiBot": "Humanoid",
    "UBTECH": "Humanoid",
    "Leju": "Humanoid",
    # 8. 산업용·서비스 로봇 (국내/해외 md 공통 한글 표기)
    "FANUC": "산업용·서비스 로봇",
    "ABB": "산업용·서비스 로봇",
    "KUKA": "산업용·서비스 로봇",
    "Yaskawa": "산업용·서비스 로봇",
    "Universal Robots": "산업용·서비스 로봇",
    "Estun": "산업용·서비스 로봇",
    "Inovance": "산업용·서비스 로봇",
    # 9. 로보틱스 밸류체인 (국내/해외 md 공통 한글 표기)
    "Harmonic Drive Systems": "로보틱스 밸류체인",
    "Nabtesco": "로보틱스 밸류체인",
    "Schaeffler": "로보틱스 밸류체인",
    "Bosch": "로보틱스 밸류체인",
    "Magna": "로보틱스 밸류체인",
}

CATEGORY_ORDER = [
    "MedTech", "Surgical Robot", "IVD", "Digital Health", "Healthcare Provider", "Cash Pay Market",
    "Humanoid", "산업용·서비스 로봇", "로보틱스 밸류체인",
]

# 모기업/제품명이 함께 언급되는 회사 — 별칭 중 하나라도 있으면 매칭으로 인정.
COMPANY_SEARCH_ALIASES = {
    "Tesla": ["Tesla", "Optimus"],
    "KUKA": ["KUKA", "Midea Group"],
    "Universal Robots": ["Universal Robots", "Teradyne"],
}

# 초대형 기업이라 회사명만으로는 무관 뉴스(자동차/반도체/일반 산업재 등)가 압도적으로 많이
# 잡히는 경우, 로봇 관련 키워드가 함께 있어야만 채택한다.
CONTEXT_REQUIRED_GLOBAL = {
    "Tesla": ["optimus", "humanoid", "robot"],
    "Bosch": ["robot", "actuator", "automation"],
    "Magna": ["robot", "actuator"],
    "Schaeffler": ["robot", "actuator", "harmonic drive", "gearbox"],
}

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


def _word_boundary_pattern(term: str):
    """단순 substring이 아니라 앞뒤로 영문/숫자가 붙어있지 않은 경우만 매칭
    (예: "ABB"가 다른 영단어 일부로 우연히 포함되는 것 방지). 대소문자 무시."""
    return re.compile(rf"(?<![0-9A-Za-z]){re.escape(term)}(?![0-9A-Za-z])", re.IGNORECASE)


def company_mentioned(company: str, haystack: str) -> bool:
    for alias in COMPANY_SEARCH_ALIASES.get(company, [company]):
        if _word_boundary_pattern(alias).search(haystack):
            return True
    return False


def context_ok(company: str, haystack: str) -> bool:
    required = CONTEXT_REQUIRED_GLOBAL.get(company)
    if not required:
        return True
    hay = haystack.lower()
    return any(kw.lower() in hay for kw in required)


def _title_tokens(title: str):
    return set(re.sub(r"[^0-9A-Za-z\s]", " ", title).lower().split())


def is_duplicate_title(a: str, b: str) -> bool:
    """제목 단어 집합의 겹침 비율이 높으면 동일 내용 기사로 간주(신디케이션 등)."""
    wa, wb = _title_tokens(a), _title_tokens(b)
    if not wa or not wb:
        return False
    overlap = len(wa & wb) / max(1, min(len(wa), len(wb)))
    return overlap >= 0.6


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
            title = clean_text(entry.get("title") or "")
            summary = clean_text(entry.get("summary") or entry.get("description") or "")
            link = entry.get("link")
            if not title or not link or link in seen_links:
                continue
            date_str, dt = parse_entry_date(entry)
            if date_str is None or dt is None or dt < cutoff:
                continue

            haystack = f"{title} {summary}"
            candidates = [
                (company, category)
                for company, category in GLOBAL_COMPANY_CATEGORY.items()
                if company_mentioned(company, haystack) and context_ok(company, haystack)
            ]
            if not candidates:
                continue
            # 한 기사에 여러 회사명이 동시에 매칭되면(예: "Agility Robotics ... Tesla's
            # backyard"), 안전장치가 걸린(초대형 대기업) 회사보다 그렇지 않은·이름이 더
            # 구체적인(긴) 회사를 우선한다 — 그래야 이 기사가 Tesla로 오귀속되지 않고
            # 실제 주인공인 Agility Robotics로 붙는다.
            company, category = min(candidates, key=lambda c: (c[0] in CONTEXT_REQUIRED_GLOBAL, -len(c[0])))
            by_category.setdefault(category, []).append({
                "co": company,
                "ctx": "News",
                "t": title,
                "src": feed_url.split("/")[2].replace("www.", ""),
                "date": date_str,
                "url": link,
                "desc": summary,  # summarize_news.py가 2줄 한글 요약을 생성할 때 참고용.
                                  # 최종 화면에는 노출하지 않는 내부 필드.
            })
            seen_links.add(link)

    # 카테고리별로 동일 내용(신디케이션 등) 기사 중복 제거
    for cat in list(by_category.keys()):
        kept = []
        for c in by_category[cat]:
            dup_idx = next((i for i, k in enumerate(kept) if is_duplicate_title(c["t"], k["t"])), None)
            if dup_idx is None:
                kept.append(c)
        by_category[cat] = kept

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
        "source": existing.get("source", "") or "네이버 뉴스검색 API(국내, 자동) + medtech/헬스케어/로보틱스 매체 RSS(해외, 자동) · 뉴스클리핑 가이드라인 카테고리 기준",
        "domestic": existing_domestic,
        "global": global_section,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    total_items = sum(len(g["items"]) for g in global_section)
    print(f"저장 완료: {args.out} (해외 {ok_categories}개 카테고리, {total_items}건 / 국내는 기존 값 {len(existing_domestic)}개 카테고리 유지)")


if __name__ == "__main__":
    main()
