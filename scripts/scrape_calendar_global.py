#!/usr/bin/env python3
"""
해외 팔로업 기업(scripts/calendar_ir_sources.json, 18개사)의 분기 실적발표 일정을
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
   전부 직접 테스트해본 결과 대부분(16개)이 봇 차단(403)이거나 응답 자체가 없어
   (타임아웃) 단순 크롤링이 불가능했고, 딱 2곳(Medtronic, Boston Scientific)만
   막힘 없이 접근 가능하면서 미래 확정 일정을 그대로 내려준다는 걸 확인했다:
     - Medtronic: 페이지 뒤의 JSON API(index.php?ajax=ajax&op=list)를 그대로 호출
     - Boston Scientific: events-and-presentations 페이지 안 <table>에 바로 있음
   나머지 16개사는 회사가 공식 발표하기 전까지는 미래 일정을 아예 채우지 않는다
   (추정치를 넣느니 비워두는 쪽을 사용자가 명시적으로 선택함, 2026-08-28).
   추후 나머지 회사들의 크롤링 방법을 찾으면 여기에 scraper를 추가하면 된다.
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

# 회사 IR 페이지에서 긁은 이벤트 제목 중 "실적발표"로 볼 것만 채택한다(컨퍼런스
# 발표 참석 등 다른 IR 이벤트는 제외) — 대소문자 무관.
EARNINGS_TITLE_RE = re.compile(r"earnings|(?:quarter|quarterly).{0,20}(?:results|financial)", re.I)


def load_ir_sources(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def make_event(d, ticker, name, ir_url, title):
    return {
        "date": d.isoformat(),
        "region": "global",
        "type": "earn",
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
        if not EARNINGS_TITLE_RE.search(title):
            continue
        try:
            d = datetime.datetime.strptime(date_m.group(1).strip(), "%A, %B %d, %Y").date()
        except ValueError:
            continue
        if d < today or d > today + datetime.timedelta(days=DAYS_FORWARD):
            continue
        events.append(make_event(d, ticker, name, ir_url, title))
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
        if not EARNINGS_TITLE_RE.search(title):
            continue
        try:
            d = datetime.datetime.strptime(date_str, "%B %d, %Y").date()
        except ValueError:
            continue
        # 이 테이블엔 이미 지난 분기 발표(과거)도 같이 나열되어 있다 — 과거분은
        # fetch_past_earnings(yfinance)가 이미 채워주므로 중복을 막기 위해 미래분만 취한다.
        if d < today or d > today + datetime.timedelta(days=DAYS_FORWARD):
            continue
        events.append(make_event(d, ticker, name, ir_url, title))
    return events


SCRAPERS = {
    "mdt_wd": scrape_mdt_wd,
    "bsx_table": scrape_bsx_table,
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
