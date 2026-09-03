#!/usr/bin/env python3
"""
해외 팔로업 기업(scripts/calendar_ir_sources.json, 18개사)의 주요 IR 일정을
수집해 calendar_events.json의 "global" 항목으로 저장한다.

수집 방식은 두 갈래다.

1) 과거(이미 발표를 마친) 실적일정 — 18개사 전부, yfinance 사용
   무료 라이브러리 yfinance(Yahoo Finance 스크래핑, API 키 불필요)의
   get_earnings_dates()로 가져온다. "Reported EPS"가 채워진 행(=실제로 발표가
   끝난 회차)만 쓴다 — 이미 벌어진 사실이라 추정치 문제가 없다.

2) 미래(아직 발표 전) 실적일정 — calendar_ir_sources.json에 "scraper" 필드가
   있는 회사만, 회사 공식 IR 페이지를 직접 크롤링
   [2026-08-28 방침 변경] yfinance가 주는 "다음 실적발표 예정일"은 회사가 IR
   페이지에 공식 공지한 날짜가 아니라 Yahoo/제3 데이터공급업체의 추정치였고,
   실제로 회사 공식 공지와 어긋나는 경우가 있어 신뢰할 수 없다고 판단해 걷어냈다
   (yfinance 응답 자체에 확정/추정을 구분하는 필드도 없음). 18개사 IR 페이지를
   전부 직접 테스트해본 결과 대부분이 봇 차단(403)이거나 응답 자체가 없어
   (타임아웃) 단순 크롤링이 불가능했다. 지금까지 확인된 접근 가능 회사:
     - Medtronic: 페이지 뒤의 JSON API(index.php?ajax=ajax&op=list)를 그대로 호출
     - Boston Scientific: events-and-presentations 페이지 안 <table>에 바로 있음
     - UnitedHealth Group: investors.html "Events" 박스 <h4>에 다음 일정 1건이
       "{월 일}: {제목}" 형식으로 서버 렌더링됨(연도 미표기 → 오늘 기준 추정)
     - [2026-09-03] Stryker/Dexcom/Edwards/Thermo Fisher/Hims & Hers/iRhythm/
       Teladoc/Natera/Guardant Health(9개사): 전부 "Q4 Inc."라는 동일한 IR
       웹사이트 위탁 플랫폼을 쓰고 있어서, 각자 페이지가 JS로 호출하는 공개
       JSONP 엔드포인트("{IR도메인}/feed/Event.svc/GetEventList")를 그대로
       호출하면 회사별 HTML 파싱 없이 구조화된 JSON으로 일정을 받는다
       (scrape_q4_feed 함수 하나로 9개사 전부 처리). Align Technology/Tempus
       AI/Abbott/Intuitive Surgical도 같은 플랫폼을 쓰는지는 로컬 사내망에서
       TLS 핸드셰이크가 막혀 있어 확인하지 못했다 — GitHub Actions 등 다른
       네트워크에서 재확인 필요.
   나머지 회사는 회사가 공식 발표하기 전까지는 미래 일정을 아예 채우지 않는다
   (추정치를 넣느니 비워두는 쪽을 사용자가 명시적으로 선택함, 2026-08-28).
   추후 나머지 회사들의 크롤링 방법을 찾으면 여기에 scraper를 추가하면 된다.
   [2026-09-03 방침 변경] 위 3곳(scraper 연결된 회사)에서 크롤링한 미래 일정은
   원래 "실적발표"로 볼 만한 제목만 채택했으나, 국내 스크래퍼(scrape_calendar_
   domestic.py)가 주총/배당/증자 등도 함께 캘린더에 넣는 것과 기준을 맞추기 위해
   GLOBAL_CATEGORY_RULES로 확장했다 — 실적발표(earn) 외에 주주총회(agm), 배당/
   액면분할(exright), 투자자 컨퍼런스(ir)도 제목 키워드로 분류해 채택한다. 과거
   실적일정(yfinance, fetch_past_earnings)은 이 분류와 무관하게 그대로 earn 고정.
- 유료 LLM 웹서치는 쓰지 않는다(비용 지침).
- source_url/source_name은 실제 수집 경로(yfinance/JSON API 등)가 아니라 사용자
  요청대로 해당 기업의 공식 IR 페이지로 표기한다 — 이용자가 원문을 확인하러 갈
  곳은 IR 페이지가 맞기 때문.

사용법:
    python scrape_calendar_global.py --out calendar_events.json
    python scrape_calendar_global.py --insecure   # 사내망 SSL 프록시로 인증서 검증이
                                                    # 실패하는 로컬 환경에서만 사용
                                                    # (GitHub Actions에서는 쓰지 않음)
"""
import argparse
import datetime
import json
import re
import sys
import time
from urllib.parse import urlparse

import requests

IR_SOURCES_FILE = "scripts/calendar_ir_sources.json"

# 오늘 기준 이 범위 밖 과거 이벤트는 파일 크기/캘린더 가독성을 위해 잘라낸다.
DAYS_BACK = 120   # 과거 백필(7월 데이터 확인 등)에 충분한 여유
DAYS_FORWARD = 400  # 미래 확정 이벤트(회사 IR 직접 크롤링분)에 적용하는 여유 범위

# 회사 IR 페이지에서 긁은 이벤트 제목을 국내 스크래퍼(scrape_calendar_domestic.py의
# CATEGORY_RULES)와 같은 기준으로 분류한다. 여기 안 걸리는 제목(M&A/자사주 매입
# 발표 등 예정된 "일정"이 아니라 그때그때의 기업 뉴스에 가까운 것)은 버린다 —
# 화이트리스트 방식. "Conference Call ... Results"는 earn 규칙이 먼저 걸려서
# "conference"의 ir 규칙과 충돌하지 않는다(리스트 순서상 earn을 먼저 검사).
GLOBAL_CATEGORY_RULES = [
    (re.compile(r"earnings|(?:quarter|quarterly).{0,20}(?:results|financial)", re.I), "earn"),
    (re.compile(r"annual(?:\s+general)?\s+meeting|shareholder.{0,20}meeting", re.I), "agm"),
    (re.compile(r"dividend|stock\s+split", re.I), "exright"),
    # 국내의 "IR개최/기업설명회"에 대응 — "~at [행사명] Conference"처럼 투자자
    # 컨퍼런스 발표 참석도 IR 일정으로 포함한다(단 "Conference Call"은 제외).
    (re.compile(r"investor\s+(?:day|event|conference)|\bconference\b(?!\s+call)", re.I), "ir"),
]


def classify_global(title):
    for pattern, ev_type in GLOBAL_CATEGORY_RULES:
        if pattern.search(title):
            return ev_type
    return None


def load_ir_sources(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def make_event(d, ticker, name, ir_url, title, ev_type="earn"):
    return {
        "date": d.isoformat(),
        "region": "global",
        "type": ev_type,
        "ticker": ticker,
        "company": name,
        "title": title,
        "source_name": f"{name} IR",
        "source_url": ir_url,
        "estimated": False,
    }


def fetch_past_earnings(ticker, name, ir_url, session, today):
    """yfinance로 이미 발표가 끝난(=확정된 사실) 회차만 가져온다."""
    import yfinance as yf

    events = []
    win_start = today - datetime.timedelta(days=DAYS_BACK)
    try:
        t = yf.Ticker(ticker, session=session) if session else yf.Ticker(ticker)
        df = t.get_earnings_dates(limit=16)
    except Exception as e:
        print(f"[WARN] {ticker} 실적일정 조회 실패: {e}", file=sys.stderr)
        return events

    if df is None or df.empty:
        return events

    for ts, row in df.iterrows():
        d = ts.date()
        if d < win_start or d > today:
            continue
        reported = row.get("Reported EPS")
        if reported != reported:  # NaN이면 아직 발표 전 -> 제외(과거분만 다루는 함수)
            continue
        events.append(make_event(d, ticker, name, ir_url, "분기 실적발표"))
    return events


def scrape_mdt_wd(scraper_url, ticker, name, ir_url, today):
    """Medtronic IR 사이트(WebDriver 기반 이벤트 캘린더) — 페이지가 쓰는 것과 동일한
    JSON API(index.php?ajax=ajax&op=list&direction=future)를 그대로 호출한다."""
    events = []
    parsed = urlparse(scraper_url)
    api_url = f"{parsed.scheme}://{parsed.netloc}/index.php"
    params = {
        "s": "19",  # 페이지 소스에서 확인한 사이트 섹션 id(고정값)
        "ajax": "ajax",
        "op": "list",
        "direction": "future",
        "limit_date": today.strftime("%Y-%m-%d"),
        "offcnt": "0",
    }
    try:
        r = requests.get(api_url, params=params, timeout=20, headers={
            "User-Agent": "Mozilla/5.0",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": scraper_url,
        })
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[WARN] {ticker} 공식 IR 크롤링 실패(mdt_wd): {e}", file=sys.stderr)
        return events

    for item in data.get("items", []):
        if item.get("type") != "event":
            continue
        html = item.get("content", "")
        date_m = re.search(r'wd_event_date">.*?</span>([^<]+)<', html)
        title_m = re.search(r'wd_title"><a[^>]*>([^<]+)</a>', html)
        if not date_m or not title_m:
            continue
        title = title_m.group(1).strip()
        ev_type = classify_global(title)
        if not ev_type:
            continue
        try:
            d = datetime.datetime.strptime(date_m.group(1).strip(), "%A, %B %d, %Y").date()
        except ValueError:
            continue
        if d < today or d > today + datetime.timedelta(days=DAYS_FORWARD):
            continue
        events.append(make_event(d, ticker, name, ir_url, title, ev_type))
    return events


def scrape_bsx_table(scraper_url, ticker, name, ir_url, today):
    """Boston Scientific IR 이벤트 페이지 — 서버가 내려주는 HTML <table> 안
    eventDate/eventTitle 셀을 정규식으로 바로 파싱한다(JS 렌더링 불필요)."""
    events = []
    try:
        r = requests.get(scraper_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        text = r.text
    except Exception as e:
        print(f"[WARN] {ticker} 공식 IR 크롤링 실패(bsx_table): {e}", file=sys.stderr)
        return events

    pattern = re.compile(
        r'eventDate[^>]*>\s*([A-Za-z]+ \d{1,2}, \d{4})[\s\S]{0,400}?eventTitle[^>]*>\s*([^<]+?)\s*</p>'
    )
    for m in pattern.finditer(text):
        date_str, title = m.group(1).strip(), m.group(2).strip()
        ev_type = classify_global(title)
        if not ev_type:
            continue
        try:
            d = datetime.datetime.strptime(date_str, "%B %d, %Y").date()
        except ValueError:
            continue
        # 이 테이블엔 이미 지난 분기 발표(과거)도 같이 나열되어 있다 — 과거분은
        # fetch_past_earnings(yfinance)가 이미 채워주므로 중복을 막기 위해 미래분만 취한다.
        if d < today or d > today + datetime.timedelta(days=DAYS_FORWARD):
            continue
        events.append(make_event(d, ticker, name, ir_url, title, ev_type))
    return events


def scrape_unh_events(scraper_url, ticker, name, ir_url, today):
    """UnitedHealth Group IR 페이지의 "Events" 박스 — 서버 렌더링 HTML에 다음
    일정 하나만 "<h4>{월 일}: {제목}</h4>" 형태로 들어있다(테이블이 아니라 단건).
    일정이 없으면 "No upcoming events"로 채워진다. 연도가 표기되지 않으므로
    오늘 날짜 기준으로 이미 지난 월/일이면 내년으로 추정한다."""
    events = []
    try:
        r = requests.get(scraper_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        text = r.text
    except Exception as e:
        print(f"[WARN] {ticker} 공식 IR 크롤링 실패(unh_events): {e}", file=sys.stderr)
        return events

    m = re.search(r'class="upcoming-events[^"]*"[\s\S]{0,200}?<h4>\s*([^<]+?)\s*</h4>', text)
    if not m:
        return events

    header = m.group(1).strip()
    date_m = re.match(r'([A-Za-z]+ \d{1,2}):\s*(.+)', header)
    if not date_m:
        return events  # "No upcoming events" 등 날짜가 없는 경우

    date_str, title = date_m.group(1).strip(), date_m.group(2).strip()
    ev_type = classify_global(title)
    if not ev_type:
        return events

    try:
        md = datetime.datetime.strptime(date_str, "%B %d").date()
    except ValueError:
        return events
    d = md.replace(year=today.year)
    if d < today:
        d = d.replace(year=today.year + 1)

    if d > today + datetime.timedelta(days=DAYS_FORWARD):
        return events
    events.append(make_event(d, ticker, name, ir_url, title, ev_type))
    return events


def scrape_q4_feed(scraper_url, ticker, name, ir_url, today):
    """Q4 Inc.(다수의 미국 상장사가 쓰는 IR 웹사이트 위탁 플랫폼) 공개 이벤트 피드.
    회사 IR 페이지가 브라우저에서 JS로 호출하는 "{IR도메인}/feed/Event.svc/
    GetEventList" JSONP 엔드포인트를 그대로 호출한다 — 회사마다 다른 HTML을
    파싱할 필요 없이 이 함수 하나로 Q4 플랫폼을 쓰는 모든 회사를 처리한다.
    eventSelection=1 & eventDateFilter=1이 페이지가 기본으로 쓰는 "다가오는 일정만"
    필터와 동일함을 DXCM 페이지의 위젯 초기화 스크립트(eventSelection: 1)로 확인했다."""
    events = []
    base = scraper_url.rstrip("/")
    params = {
        "pageSize": 25,
        "includeTags": "true",
        "eventSelection": 1,
        "eventDateFilter": 1,
        "sortOperator": 1,
        "excludeSelection": 1,
        "includePressReleases": "true",
        "includePresentations": "true",
        "includeFinancialReports": "true",
        "LanguageId": 1,
        "callback": "?",  # jQuery의 JSONP 콜백 자리표시자 — 응답은 "?(...)" 형태로 온다
    }
    try:
        r = requests.get(f"{base}/feed/Event.svc/GetEventList", params=params, timeout=20,
                          headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        text = r.text.strip()
    except Exception as e:
        print(f"[WARN] {ticker} 공식 IR 크롤링 실패(q4_feed): {e}", file=sys.stderr)
        return events

    m = re.match(r"^\?\((.*)\);?$", text, re.S)
    if not m:
        print(f"[WARN] {ticker} q4_feed 응답 형식을 인식하지 못함", file=sys.stderr)
        return events
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f"[WARN] {ticker} q4_feed JSON 파싱 실패: {e}", file=sys.stderr)
        return events

    for item in data.get("GetEventListResult", []):
        title = (item.get("Title") or "").strip()
        ev_type = classify_global(title)
        if not ev_type:
            continue
        start = item.get("StartDate", "")
        try:
            d = datetime.datetime.strptime(start.split(" ")[0], "%m/%d/%Y").date()
        except ValueError:
            continue
        if d < today or d > today + datetime.timedelta(days=DAYS_FORWARD):
            continue
        events.append(make_event(d, ticker, name, ir_url, title, ev_type))
    return events


SCRAPERS = {
    "mdt_wd": scrape_mdt_wd,
    "bsx_table": scrape_bsx_table,
    "unh_events": scrape_unh_events,
    "q4_feed": scrape_q4_feed,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ir-sources", default=IR_SOURCES_FILE)
    ap.add_argument("--out", default="calendar_events.json")
    ap.add_argument("--insecure", action="store_true",
                     help="TLS 인증서 검증을 건너뛴다 (사내망 SSL 프록시 환경 전용, 기본 꺼짐, yfinance 조회에만 적용)")
    args = ap.parse_args()

    sources = load_ir_sources(args.ir_sources)
    today = datetime.date.today()

    session = None
    if args.insecure:
        from curl_cffi import requests as cr
        session = cr.Session(impersonate="chrome", verify=False)
        print("[WARN] --insecure: TLS 인증서 검증을 건너뜁니다 (로컬 전용, yfinance 조회에만 적용).", file=sys.stderr)

    all_events = []
    for src in sources:
        ticker, name, ir_url = src["ticker"], src["name"], src["ir_url"]

        past_ev = fetch_past_earnings(ticker, name, ir_url, session, today)

        future_ev = []
        scraper_key = src.get("scraper")
        if scraper_key and scraper_key in SCRAPERS:
            future_ev = SCRAPERS[scraper_key](src["scraper_url"], ticker, name, ir_url, today)

        print(f"[INFO] {ticker}: 과거 {len(past_ev)}건 + 확정 미래 {len(future_ev)}건")
        all_events.extend(past_ev)
        all_events.extend(future_ev)
        time.sleep(0.3)  # 대상 서버에 부담 주지 않기 위한 대기

    all_events.sort(key=lambda e: e["date"])

    try:
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}

    existing["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    existing["domestic"] = existing.get("domestic", [])
    existing["global"] = all_events

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"[DONE] global 이벤트 {len(all_events)}건 -> {args.out}")


if __name__ == "__main__":
    main()
