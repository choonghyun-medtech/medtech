#!/usr/bin/env python3
"""
해외 팔로업 기업(scripts/calendar_ir_sources.json, 18개사)의 분기 실적발표 일정을
yfinance로 수집해 calendar_events.json의 "global" 항목으로 저장한다.

- 통합 공개 API가 없는 "IR 페이지 공시"를 매번 직접 파싱하는 대신, 이미 이 레포에서
  주가 데이터 수집에 쓰고 있는 무료 라이브러리 yfinance(Yahoo Finance 스크래핑, API 키
  불필요)의 get_earnings_dates()를 사용한다. 유료 LLM 웹서치는 쓰지 않는다(비용 지침).
- 출처(source_url/source_name)는 실제로 일정을 가져온 Yahoo Finance가 아니라, 사용자
  요청대로 해당 기업의 공식 IR 페이지로 표기한다 — 실적 날짜 자체는 각 회사가 IR
  페이지에서 공지하는 것과 동일한 사실이고, 대시보드 이용자가 원문을 확인하러 갈
  곳은 IR 페이지가 맞기 때문.
- get_earnings_dates()가 돌려주는 각 행은 "Reported EPS"가 NaN이면 아직 발표 전(예정),
  값이 있으면 이미 발표된 회차 — 이 값의 유무로 estimated(예정) 여부를 판단한다.

사용법:
    python scrape_calendar_global.py --out calendar_events.json
    python scrape_calendar_global.py --insecure   # 사내망 SSL 프록시로 인증서 검증이
                                                    # 실패하는 로컬 환경에서만 사용
                                                    # (GitHub Actions에서는 쓰지 않음)
"""
import argparse
import datetime
import json
import sys
import time

IR_SOURCES_FILE = "scripts/calendar_ir_sources.json"

# 오늘 기준 이 범위 밖 이벤트는 파일 크기/캘린더 가독성을 위해 잘라낸다.
DAYS_BACK = 120   # 과거 백필(7월 데이터 확인 등)에 충분한 여유
DAYS_FORWARD = 400


def load_ir_sources(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fetch_earnings_events(ticker, name, ir_url, session, today):
    import yfinance as yf

    events = []
    win_start = today - datetime.timedelta(days=DAYS_BACK)
    win_end = today + datetime.timedelta(days=DAYS_FORWARD)
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
        if d < win_start or d > win_end:
            continue
        estimated = bool(row.get("Reported EPS") != row.get("Reported EPS"))  # NaN check
        events.append({
            "date": d.isoformat(),
            "region": "global",
            "type": "earn",
            "ticker": ticker,
            "company": name,
            "title": f"{name} 분기 실적발표" + ("(예정)" if estimated else ""),
            "source_name": f"{name} IR",
            "source_url": ir_url,
            "estimated": estimated,
        })
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ir-sources", default=IR_SOURCES_FILE)
    ap.add_argument("--out", default="calendar_events.json")
    ap.add_argument("--insecure", action="store_true",
                     help="TLS 인증서 검증을 건너뛴다 (사내망 SSL 프록시 환경 전용, 기본 꺼짐)")
    args = ap.parse_args()

    sources = load_ir_sources(args.ir_sources)
    today = datetime.date.today()

    session = None
    if args.insecure:
        from curl_cffi import requests as cr
        session = cr.Session(impersonate="chrome", verify=False)
        print("[WARN] --insecure: TLS 인증서 검증을 건너뜁니다 (로컬 전용).", file=sys.stderr)

    all_events = []
    for src in sources:
        ev = fetch_earnings_events(src["ticker"], src["name"], src["ir_url"], session, today)
        print(f"[INFO] {src['ticker']}: {len(ev)}건")
        all_events.extend(ev)
        time.sleep(0.3)  # Yahoo Finance에 부담 주지 않기 위한 대기

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
