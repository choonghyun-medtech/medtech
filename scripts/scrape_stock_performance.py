#!/usr/bin/env python3
"""
글로벌 헬스케어 종목 유니버스(tickers.json)의 주가 퍼포먼스(1일/5일/1개월/3개월/6개월/1년/YTD)와
시가총액(조원 환산)을 계산해 stock_performance.json으로 저장한다.

요구사항 출처: stock_performance_requirements.md
- 변화율 = 로컬 통화 기준, (어제 종가 - 기준일 종가) / 기준일 종가 * 100, 소수점 1자리
- 기간은 거래일수 기준: 5일/21일(1개월)/66일(3개월)/132일(6개월)/220일(1년), YTD는 올해 첫 거래일 대비
- 시가총액은 KRW 환산 조원 단위 (환산에만 환율 적용, 변화율에는 미적용)
- 1차 소스: yfinance, 한국 시총은 Daum Finance API 우선 시도 후 yfinance fallback
- 국내(KR) 종목의 주가 히스토리·외국인 지분율은 api.stock.naver.com 차트 API에서 가져온다
  (yfinance가 일부 KRX 종목에서 짧은 히스토리를 반환하는 문제 회피 + 외국인보유율 필드 제공).
  실패 시 yfinance 히스토리로 폴백한다.
- 병렬 수집 ThreadPoolExecutor(max_workers=10), 실패 종목은 이전 실행에서 정상 수집된 데이터가
  있으면 그걸 그대로 보존하고, 처음부터 한 번도 성공한 적 없는 종목만 null로 남긴다(다른
  scrape_*.py 스크립트들과 동일한 "보강" 패턴, 2026-08-31 추가 — 이전엔 실패 시 무조건 null로
  덮어써서, yfinance가 일시적으로 막히면 기존에 정상 저장돼 있던 해외 종목 데이터까지 통째로
  날아가는 문제가 있었다. 로컬 네트워크에서 해외 107종목이 한꺼번에 실패하며 실제로 겪음).
- 종가 반영 지연 검증(2026-09-22 추가, 2026-09-23 배치 방식으로 수정): API가 아직 전날
  종가만 주고 당일 종가를 안 준 경우 yfinance/네이버 모두 에러 없이 "가장 최근 데이터"를
  그대로 반환하기 때문에 이전엔 조용히 하루 묵은 데이터를 최신인 것처럼 저장했다. 이를
  잡기 위해 1차 조회 후 as_of(최신 종가 거래일)가 직전 실행 결과와 동일한 종목들만 모아,
  RETRY_WAIT_SECONDS만큼 "한 번만" 대기한 뒤 그 종목들만 일괄 재조회한다. 재조회 후에도
  그대로면(휴장일 등 정상적으로 갱신이 없는 경우 포함) 1차 값을 그대로 쓴다 — 무한 재시도는
  하지 않는다. [2026-09-23] 처음엔 종목마다 개별적으로 대기 후 재조회했는데, 워커 풀
  크기(max_workers)에 막혀 동시에 여러 종목이 걸리면 (걸린 종목 수 / max_workers) *
  RETRY_WAIT_SECONDS만큼 전체 실행 시간이 불어나는 문제가 실사용 중 발견됐다(대부분의 KR
  종목이 동시에 걸려 워크플로가 끝나지 않음). "동일 판정된 종목을 모아 딱 한 번만 대기 후
  일괄 재조회"하는 현재 방식으로 변경해 전체 실행 시간 증가분을 RETRY_WAIT_SECONDS 한 번으로
  고정했다.

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

import pandas as pd
import requests
import yfinance as yf

NAVER_CHART_URL = "https://api.stock.naver.com/chart/domestic/item/{code}/day"
NAVER_HISTORY_DAYS = 400  # 260 거래일 확보를 위한 여유 캘린더일
RETRY_WAIT_SECONDS = 300  # as_of가 직전 실행과 동일(=종가 미반영 의심)할 때 재조회 전 대기 시간

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
    m = re.match(r"^([0-9A-Za-z]{6})\.(KS|KQ)$", ticker)
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


def fetch_naver_kr_series(ticker: str):
    """국내 종목의 일별 종가+외국인보유율을 api.stock.naver.com에서 가져온다.
    반환: (close 시리즈(pd.Series, DatetimeIndex, 오름차순), foreign_ratio dict{date_str: float})
    실패 시 (None, None).
    """
    m = re.match(r"^([0-9A-Za-z]{6})\.(KS|KQ)$", ticker)
    if not m:
        return None, None
    code = m.group(1)
    end = datetime.date.today()
    start = end - datetime.timedelta(days=NAVER_HISTORY_DAYS)
    url = NAVER_CHART_URL.format(code=code)
    params = {"startDateTime": start.strftime("%Y%m%d"), "endDateTime": end.strftime("%Y%m%d")}
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None, None
    dates = [datetime.datetime.strptime(r["localDate"], "%Y%m%d") for r in rows]
    closes = [float(r["closePrice"]) for r in rows]
    close = pd.Series(closes, index=pd.DatetimeIndex(dates)).dropna()
    foreign_ratio = {
        str(datetime.datetime.strptime(r["localDate"], "%Y%m%d").date()): r.get("foreignRetentionRate")
        for r in rows
        if r.get("foreignRetentionRate") is not None
    }
    return close, foreign_ratio


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


def _fetch_close_series(item):
    """단일 종목의 종가 시리즈를 가져온다 (KR은 네이버 우선, 그 외/실패 시 yfinance).
    반환: (close 시리즈|None, foreign_ratio_map|None, yf.Ticker|None).
    """
    ticker = item["ticker"]
    t = None
    close = None
    foreign_ratio_map = None

    if item["market"] == "KR":
        try:
            close, foreign_ratio_map = fetch_naver_kr_series(ticker)
        except Exception:
            close, foreign_ratio_map = None, None

    if close is None or close.empty:
        t = yf.Ticker(ticker)
        hist = t.history(period="15mo", auto_adjust=False)
        if hist is not None and not hist.empty:
            close = hist["Close"].dropna()
            foreign_ratio_map = None
        else:
            close = None

    return close, foreign_ratio_map, t


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
        "foreign_ratio": None,  # 외국인 지분율(%) 최신값 (KR 종목만)
        "foreign_ratio_history": None,  # {"dates":[...], "values":[...]} (KR 종목만)
        "error": None,
    }
    try:
        close, foreign_ratio_map, t = _fetch_close_series(item)
        if close is None or close.empty:
            result["error"] = "no price history"
            return result

        if len(close) > 0:
            result["as_of"] = str(close.index[-1].date())
            recent = close.tail(260)  # 약 12개월치 거래일
            recent_dates = [str(d.date()) for d in recent.index]
            result["price_history"] = {
                "dates": recent_dates,
                "close": [round(float(v), 2) for v in recent.values],
            }
            if foreign_ratio_map:
                fr_values = [foreign_ratio_map.get(d) for d in recent_dates]
                if any(v is not None for v in fr_values):
                    result["foreign_ratio_history"] = {"dates": recent_dates, "values": fr_values}
                    last_fr = next((v for v in reversed(fr_values) if v is not None), None)
                    result["foreign_ratio"] = last_fr

        for key, offset in PERIOD_OFFSETS.items():
            result["returns"][key] = pct_change(close, offset)
        result["returns"]["ytd"] = ytd_change(close)

        cap_local = None
        if item["market"] == "KR":
            cap_local = daum_market_cap(ticker)
        if cap_local is None:
            if t is None:
                t = yf.Ticker(ticker)
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

    try:
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    existing_by_ticker = {s.get("ticker"): s for s in existing.get("stocks", [])}
    # 직전 실행에서 정상 수집된(error 없는) 종목의 as_of만 재조회 판단 기준으로 쓴다.
    prev_as_of_by_ticker = {
        ticker: s.get("as_of")
        for ticker, s in existing_by_ticker.items()
        if s.get("error") is None
    }

    def run_batch(batch_items, label):
        batch_results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as ex:
            futures = {ex.submit(fetch_one, item): item for item in batch_items}
            done = 0
            for fut in concurrent.futures.as_completed(futures):
                r = fut.result()
                done += 1
                batch_results[r["ticker"]] = r
                status = "OK" if r["error"] is None else f"FAIL ({r['error']})"
                print(f"[{label} {done}/{len(batch_items)}] {r['ticker']}: {status}")
        return batch_results

    print(f"fetching {len(items)} tickers with {args.max_workers} workers...")
    results_by_ticker = run_batch(items, "1차")

    # as_of가 직전 실행과 동일한 종목을 한데 모아 "딱 한 번"만 재조회한다.
    # (종목마다 개별적으로 대기하면 워커 풀 크기에 막혀 전체 실행 시간이
    # (걸린 종목 수 / max_workers) * RETRY_WAIT_SECONDS 만큼 불어난다 — 2026-09-23
    # 실사용 중 KR 종목 다수가 동시에 이 조건에 걸려 워크플로가 끝나지 않는 문제로 발견,
    # 배치 단위 재조회로 변경. as_of가 같다는 게 항상 오류는 아니다(휴장일 등 정상적으로
    # 새 종가가 없는 경우도 동일하게 걸리므로, 재조회해도 같은 값이면 그대로 채택한다).
    stale_tickers = [
        item for item in items
        if results_by_ticker[item["ticker"]]["error"] is None
        and results_by_ticker[item["ticker"]]["as_of"] is not None
        and results_by_ticker[item["ticker"]]["as_of"] == prev_as_of_by_ticker.get(item["ticker"])
    ]
    if stale_tickers:
        print(f"{len(stale_tickers)}개 종목의 as_of가 직전 실행과 동일 -> "
              f"{RETRY_WAIT_SECONDS}초 대기 후 해당 종목만 일괄 재조회", file=sys.stderr)
        time.sleep(RETRY_WAIT_SECONDS)
        retry_results = run_batch(stale_tickers, "재조회")
        for ticker, rr in retry_results.items():
            if rr["error"] is None and rr["as_of"] is not None:
                results_by_ticker[ticker] = rr

    results = []
    for r in results_by_ticker.values():
        if r["error"] is not None:
            prev = existing_by_ticker.get(r["ticker"])
            if prev is not None and prev.get("error") is None:
                print(f"{r['ticker']}: FAIL ({r['error']}) — 이전 정상 데이터 보존", file=sys.stderr)
                r = prev
        results.append(r)

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
