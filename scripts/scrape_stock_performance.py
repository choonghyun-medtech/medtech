#!/usr/bin/env python3
"""
글로벌 헬스케어 종목 유니버스(tickers.json)의 주가 퍼포먼스(1일/5일/1개월/3개월/6개월/1년/YTD)와
시가총액(조원 환산)을 계산해 stock_performance.json으로 저장한다.

요구사항 출처: stock_performance_requirements.md
- 변화율 = 로컬 통화 기준, (어제 종가 - 기준일 종가) / 기준일 종가 * 100, 소수점 1자리
- 기간은 거래일수 기준: 5일/21일(1개월)/66일(3개월)/132일(6개월)/220일(1년), YTD는 올해 첫 거래일 대비
- 시가총액은 KRW 환산 조원 단위 (환산에만 환율 적용, 변화율에는 미적용)
- 1차 소스: yfinance, 한국 시총은 Daum Finance API 우선 시도 후 yfinance fallback
- 병렬 수집 ThreadPoolExecutor(max_workers=10), 실패 종목은 해당 값만 null 처리하고 계속 진행

사용법:
    python scrape_stock_performance.py --out stock_performance.json
"""
import argparse
import concurrent.futures
import datetime
import json
import re
import sys
import time

import requests
import yfinance as yf

TICKERS_FILE = "tickers.json"

# 근사 환율 (원화 환산용, 시가총액에만 적용) — 필요 시 최신값으로 수정
FX_TO_KRW = {
    "USD": 1370,
    "HKD": 175,
    "CHF": 1540,
    "JPY": 9,
    "CNY": 190,
    "GBP": 1730,
    "EUR": 1540,
    "KRW": 1,
}

MARKET_TO_CURRENCY = {
    "US": "USD",
    "KR": "KRW",
    "HK": "HKD",
    "CH": "CHF",
    "JP": "JPY",
    "CN": "CNY",
    "GB": "GBP",
    "DE": "EUR",
    "FR": "EUR",
}

# 거래일 기준 오프셋 (요구사항 3-2)
PERIOD_OFFSETS = {
    "d1": 1,
    "d5": 5,
    "m1": 21,
    "m3": 66,
    "m6": 132,
    "y1": 220,
}


def daum_market_cap(ticker: str):
    """KR 종목의 한국거래소 시가총액을 Daum Finance API에서 가져온다 (원화, 절대값)."""
    m = re.match(r"^(\d{6})\.(KS|KQ)$", ticker)
    if not m:
        return None
    code = m.group(1)
    url = f"https://finance.daum.net/api/quotes/A{code}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": f"https://finance.daum.net/quotes/A{code}",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        cap = data.get("marketCap")
        return float(cap) if cap else None
    except Exception:
        return None


def yfinance_market_cap(t: yf.Ticker):
    try:
        cap = t.fast_info.get("market_cap")
        if cap:
            return float(cap)
    except Exception:
        pass
    try:
        info = t.info
        cap = info.get("marketCap")
        if cap:
            return float(cap)
    except Exception:
        pass
    return None


def pct_change(hist, offset):
    """hist: 종가 시리즈(오래된 -> 최신). offset 거래일 전 종가 대비 최신 종가 변화율(%)."""
    if hist is None or len(hist) <= offset:
        return None
    latest = hist.iloc[-1]
    base = hist.iloc[-1 - offset]
    if base == 0:
        return None
    return round((latest - base) / base * 100, 1)


def ytd_change(hist_with_dates):
    """올해 첫 거래일 종가 대비 최신 종가 변화율(%)."""
    if hist_with_dates is None or len(hist_with_dates) == 0:
        return None
    this_year = hist_with_dates.index[-1].year
    ytd_rows = hist_with_dates[hist_with_dates.index.year == this_year]
    if len(ytd_rows) == 0:
        return None
    base = ytd_rows.iloc[0]
    latest = hist_with_dates.iloc[-1]
    if base == 0:
        return None
    return round((latest - base) / base * 100, 1)


def fetch_one(item):
    ticker = item["ticker"]
    result = {
        "ticker": ticker,
        "name": item["name"],
        "sector": item["sector"],
        "market": item["market"],
        "currency": MARKET_TO_CURRENCY.get(item["market"], "USD"),
        "market_cap_krw_tril": None,
        "market_cap_krw_eok": None,  # 억원 단위 정밀값 (조원 1자리 반올림 후 재환산 시 발생하는 정밀도 손실 방지용)
        "returns": {"d1": None, "d5": None, "m1": None, "m3": None, "m6": None, "y1": None, "ytd": None},
        "as_of": None,  # 변화율 계산에 쓰인 최신 종가의 거래일 (YYYY-MM-DD, 해당 거래소 현지 날짜)
        "price_history": None,  # {"dates":[...], "close":[...]} 최근 약 12개월(거래일 기준) 종가
        "error": None,
    }
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="15mo", auto_adjust=False)
        if hist is None or hist.empty:
            result["error"] = "no price history"
            return result
        close = hist["Close"].dropna()
        if len(close) > 0:
            result["as_of"] = str(close.index[-1].date())
            recent = close.tail(260)  # 약 12개월치 거래일
            result["price_history"] = {
                "dates": [str(d.date()) for d in recent.index],
                "close": [round(float(v), 2) for v in recent.values],
            }

        for key, offset in PERIOD_OFFSETS.items():
            result["returns"][key] = pct_change(close, offset)
        result["returns"]["ytd"] = ytd_change(close)

        cap_local = None
        if item["market"] == "KR":
            cap_local = daum_market_cap(ticker)
        if cap_local is None:
            cap_local = yfinance_market_cap(t)

        if cap_local is not None:
            fx = FX_TO_KRW.get(result["currency"], 1)
            cap_krw = cap_local * fx
            result["market_cap_krw_tril"] = round(cap_krw / 1e12, 1)
            result["market_cap_krw_eok"] = round(cap_krw / 1e8, 1)
    except Exception as e:
        result["error"] = str(e)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=TICKERS_FILE)
    ap.add_argument("--out", default="stock_performance.json")
    ap.add_argument("--max-workers", type=int, default=10)
    args = ap.parse_args()

    with open(args.tickers, encoding="utf-8") as f:
        items = json.load(f)

    print(f"fetching {len(items)} tickers with {args.max_workers} workers...")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = {ex.submit(fetch_one, item): item for item in items}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            results.append(r)
            done += 1
            status = "OK" if r["error"] is None else f"FAIL ({r['error']})"
            print(f"[{done}/{len(items)}] {r['ticker']}: {status}")

    order = {item["ticker"]: i for i, item in enumerate(items)}
    results.sort(key=lambda r: order.get(r["ticker"], 0))

    ok = sum(1 for r in results if r["error"] is None)
    payload = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fx_to_krw": FX_TO_KRW,
        "count": len(results),
        "ok_count": ok,
        "stocks": results,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} ({ok}/{len(results)}건 성공)")


if __name__ == "__main__":
    main()
