#!/usr/bin/env python3
"""
글로벌 헬스케어 종목 유니버스(tickers.json)의 주가 퍼포먼스(1일/5일/1개월/3개월/6개월/1년/YTD)와
시가총액(조원 환산)을 계산해 stock_performance.json으로 저장한다.

요구사항 출처: stock_performance_requirements.md
- 변화율 = 로컬 통화 기준, (어제 종가 - 기준일 종가) / 기준일 종가 * 100, 소수점 1자리
- 기간은 거래일수 기준: 5일/21일(1개월)/66일(3개월)/132일(6개월)/220일(1년), YTD는 올해 첫 거래일 대비
- 시가총액은 KRW 환산 조원 단위 (환산에만 환율 적용, 변화율에는 미적용)
- 1차 소스: yfinance, 한국 시총은 Daum Finance API 우선 시도 후 yfinance fallback
- 국내(KR) 종목의 외국인 지분율은 api.stock.naver.com 차트 API에서 가져온다.
  [2026-09-30] 국내 종가는 yfinance(KRX 공식 종가)를 1순위로 변경 — 네이버 종가가 NXT 애프터마켓
  포함 통합 시세라 블룸버그와 달랐음(_fetch_close_series 주석 참고). 예전에 네이버로 옮긴 이유였던
  "yfinance 짧은 히스토리" 문제는 재확인 결과 짧은 5종목 모두 실제 최근 상장 종목이었다(네이버와
  행 수 동일). 야후 실패 시 네이버 종가로 폴백한다.
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
  [2026-09-30] 이 대기·재조회는 제거했다. 대신 장중(마감 전) 일봉을 버리는
  drop_unfinished_session()이 들어가, 몇 시에 실행하든 1일 변화율 = "수집 시점 기준 가장 최근
  마감 종가 vs 그 전 거래일 종가"가 된다.

사용법:
    python scrape_stock_performance.py --out stock_performance.json
"""
import argparse
import concurrent.futures
import datetime
import json
import re
import sys

import pandas as pd
import requests
import yfinance as yf

NAVER_CHART_URL = "https://api.stock.naver.com/chart/domestic/item/{code}/day"
NAVER_HISTORY_DAYS = 400  # 260 거래일 확보를 위한 여유 캘린더일

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
    "SEK": 135,
    "TWD": 43,
    "ILS": 370,
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
    # 아래 5개 시장은 매핑이 빠져 USD로 간주되던 탓에 현지통화 시총에 달러 환율이 곱해져
    # 원화 시총이 과대 계산됐다(예: 엘렉타 SEK, 타이보 과기 TWD) — 2026-09-28 추가.
    "IT": "EUR",
    "BE": "EUR",
    "SE": "SEK",
    "TW": "TWD",
    "IL": "ILS",
}

# 시장별 (거래소 시간대, 정규장 마감 시각). 네이버/yfinance 모두 장중에는 "오늘 날짜의 진행 중인
# 일봉(=현재가)"을 최신 행으로 돌려주기 때문에, 그대로 쓰면 1일 변화율이 전일 종가 대비가 아니라
# 장중 등락률이 된다(2026-09-30 발견: GitHub 예약 지연으로 한국 장 시작 후 실행되며 국내 종목이
# 장중 가격으로 계산됨 — 같은 날 삼성바이오로직스 d1이 수집 시각에 따라 -1.4/-0.5/+1.2로 바뀜).
# 수집 시점에 그 시장의 오늘 일봉이 마감+CLOSE_BUFFER 전이면 버리고 마지막 마감 종가를 쓴다.
MARKET_SESSION = {
    "KR": ("Asia/Seoul", 15, 30),
    "JP": ("Asia/Tokyo", 15, 30),
    "CN": ("Asia/Shanghai", 15, 0),
    "HK": ("Asia/Hong_Kong", 16, 10),  # 종가 단일가(CAS) 포함
    "TW": ("Asia/Taipei", 13, 30),
    "US": ("America/New_York", 16, 0),
    "GB": ("Europe/London", 16, 35),
    "DE": ("Europe/Berlin", 17, 35),
    "FR": ("Europe/Paris", 17, 35),
    "IT": ("Europe/Rome", 17, 35),
    "BE": ("Europe/Brussels", 17, 35),
    "CH": ("Europe/Zurich", 17, 30),
    "SE": ("Europe/Stockholm", 17, 30),
    "IL": ("Asia/Jerusalem", 17, 25),
}
# 네이버 국내 일봉은 대체거래소 NXT(08:00~20:00)까지 합친 통합 시세라, 오늘 일봉이 20:00까지
# 계속 바뀐다. 네이버 폴백을 쓸 때만 국내 마감 기준을 20:00으로 본다.
NAVER_KR_SESSION = ("Asia/Seoul", 20, 0)
# 마감 직후 종가 확정·API 반영까지의 여유(2026-09-30: 30분 일괄 → 소스별로 조정).
# - yfinance/야후(국내 포함): 거래소에 따라 시세가 15~20분 지연되고 미국 공식 종가·홍콩/유럽 마감
#   동시호가 확정에도 몇 분 걸림 → 20분
# - 네이버(국내 폴백): 실시간 시세 → 10분
CLOSE_BUFFER_NAVER = datetime.timedelta(minutes=10)
CLOSE_BUFFER_DEFAULT = datetime.timedelta(minutes=20)


def drop_unfinished_session(close, market, source="yfinance"):
    """close(오름차순 종가 시리즈)의 마지막 행이 아직 마감 전인 '오늘' 일봉이면 제거해 반환.
    source: 종가를 가져온 소스("yfinance" | "naver") — 마감 기준 시각·여유 시간이 다르다."""
    session = NAVER_KR_SESSION if source == "naver" else MARKET_SESSION.get(market)
    if close is None or close.empty or session is None:
        return close
    tz, hh, mm = session
    now_local = pd.Timestamp.now(tz=tz)
    today_local = now_local.date()
    buffer = CLOSE_BUFFER_NAVER if source == "naver" else CLOSE_BUFFER_DEFAULT
    close_at = now_local.normalize() + pd.Timedelta(hours=hh, minutes=mm) + buffer
    last_date = close.index[-1].date()  # 네이버는 naive(현지 날짜), yfinance는 거래소 tz-aware
    if last_date > today_local or (last_date == today_local and now_local < close_at):
        return close.iloc[:-1]
    return close

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
    반환: (close 시리즈(pd.Series, DatetimeIndex, 오름차순), foreign_ratio dict{date_str: float},
           halted_dates set{datetime.date} — 거래량 0인 날(거래정지, 네이버는 전일 종가를 그대로 채움))
    실패 시 (None, None, None).
    """
    m = re.match(r"^([0-9A-Za-z]{6})\.(KS|KQ)$", ticker)
    if not m:
        return None, None, None
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
        return None, None, None
    dates = [datetime.datetime.strptime(r["localDate"], "%Y%m%d") for r in rows]
    halted_dates = {d.date() for d, r in zip(dates, rows) if r.get("accumulatedTradingVolume") == 0}
    closes = [float(r["closePrice"]) for r in rows]
    close = pd.Series(closes, index=pd.DatetimeIndex(dates)).dropna()
    foreign_ratio = {
        str(datetime.datetime.strptime(r["localDate"], "%Y%m%d").date()): r.get("foreignRetentionRate")
        for r in rows
        if r.get("foreignRetentionRate") is not None
    }
    return close, foreign_ratio, halted_dates


# 야후와 네이버 종가가 이 비율 이상 어긋나면 야후 값을 버린다. 평소 차이는 NXT 애프터마켓
# 때문에 몇 % 수준이고, KRX 가격제한폭(±30%)보다 큰 괴리는 정상 거래로 나올 수 없다.
KR_SOURCE_MISMATCH_RATIO = 0.3


def reconcile_kr_close(yf_close, naver_close, halted_dates, ticker=""):
    """[2026-10-07] 야후 국내 종가를 네이버 일봉으로 검증해 오류 구간을 네이버 값으로 바꾼다.
    시지메드텍(056090)이 9/10부터 거래정지 중인데 야후가 10/6 종가를 정지 직전 1,190원의
    정확히 5배인 5,950원으로 줘서(주식병합 기준가만 먼저 반영, 과거 주가는 미조정으로 추정)
    1일/1주/1개월 수익률이 모두 +400%로 찍혔다(사용자 리포트). 그래서
      1) 네이버 기준 거래량 0인 날(거래정지)은 야후의 "정지 직전 정상 거래일 종가"를 그대로
         이어 쓴다. 네이버 값을 쓰지 않는 이유: 야후와 네이버는 수정주가 기준이 달라(예:
         삼성바이오로직스 2025-10-30~11-21 분할 정지 구간 야후 1,877,331 vs 네이버 1,783,167)
         정지 구간만 네이버 값으로 바꾸면 차트에 가짜 계단이 생긴다. 직전 정상일이 조회 범위에
         없으면(시리즈가 정지 중에 시작) 야후 값을 그대로 둔다.
      2) 정상 거래일인데 두 소스가 KR_SOURCE_MISMATCH_RATIO 이상 어긋나면 네이버 값을 쓴다.
    네이버에 없는 날짜(조회 기간 밖 등)는 야후 값을 그대로 둔다."""
    if yf_close is None or naver_close is None or naver_close.empty:
        return yf_close
    naver_by_date = {idx.date(): float(v) for idx, v in naver_close.items()}
    halted_dates = halted_dates or set()
    fixed = yf_close.copy()
    replaced = []
    last_traded = None  # 직전 정상 거래일의 (교정 후) 종가
    for idx, yv in yf_close.items():
        d, yv = idx.date(), float(yv)
        if d in halted_dates:
            new = last_traded if last_traded is not None else yv
        else:
            nv = naver_by_date.get(d)
            new = nv if nv and abs(yv / nv - 1) >= KR_SOURCE_MISMATCH_RATIO else yv
            last_traded = new
        if abs(new - yv) > 0.5:
            replaced.append(f"{d} {yv:,.0f}->{new:,.0f}")
            fixed[idx] = new
    if replaced:
        print(f"[WARN] {ticker}: 야후 종가 {len(replaced)}일 교정(거래정지 중 전일 종가 유지 / 네이버와 큰 괴리) — "
              + ", ".join(replaced[-5:]), file=sys.stderr)
    return fixed


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


def _yf_close(symbol):
    t = yf.Ticker(symbol)
    hist = t.history(period="15mo", auto_adjust=False)
    if hist is None or hist.empty:
        return None, t
    close = hist["Close"].dropna()
    return (close if not close.empty else None), t


def _fetch_close_series(item):
    """단일 종목의 종가 시리즈를 가져온다.
    반환: (close 시리즈|None, foreign_ratio_map|None, yf.Ticker|None, source "yfinance"|"naver").

    [2026-09-30] 국내(KR) 종가도 yfinance(야후)를 1순위로 바꿨다. 네이버 일봉/일별시세의 종가는
    KRX 정규장 종가가 아니라 대체거래소 NXT(15:30~20:00 애프터마켓 포함) 통합 시세의 마지막
    가격이라, 블룸버그 CHG_PCT_1D(KRX 공식 종가 기준)와 값이 달랐다(예: 뷰노 9/29 블룸버그·야후
    +22.0% vs 네이버 +14.9% — 9/28 KRX 종가 6,820원 vs NXT 마감가 7,240원). 야후는 KRX 공식
    종가와 일치함을 10종목으로 확인. 외국인 지분율은 야후에 없어 계속 네이버에서 가져오고,
    야후 조회가 실패하면 네이버 종가로 폴백한다.
    tickers.json의 코스닥 13종목이 .KS로 잘못 적혀 있던 걸 2026-09-30에 .KQ로 정정했다(네이버는
    접미사를 안 봐서 그동안 드러나지 않았음). 새 종목이 잘못 들어올 때를 대비해, 야후 조회가
    비면 .KS <-> .KQ를 바꿔 한 번 더 시도하는 안전장치는 남겨둔다.
    """
    ticker = item["ticker"]
    foreign_ratio_map = None

    if item["market"] == "KR":
        naver_close, halted_dates = None, None
        try:
            naver_close, foreign_ratio_map, halted_dates = fetch_naver_kr_series(ticker)
        except Exception:
            naver_close, foreign_ratio_map, halted_dates = None, None, None
        close, t = None, None
        try:
            close, t = _yf_close(ticker)
            if close is None and re.search(r"\.(KS|KQ)$", ticker):
                alt = ticker[:-2] + ("KQ" if ticker.endswith("KS") else "KS")
                close, t = _yf_close(alt)
        except Exception:
            close = None
        if close is not None:
            close = reconcile_kr_close(close, naver_close, halted_dates, ticker)
            return close, foreign_ratio_map, t, "yfinance"
        if naver_close is not None and not naver_close.empty:
            return naver_close, foreign_ratio_map, None, "naver"
        return None, foreign_ratio_map, t, "yfinance"

    close, t = _yf_close(ticker)
    return close, None, t, "yfinance"


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
        "price_source": None,  # 종가 소스 "yfinance"(KRX 공식 종가 포함) | "naver"(국내 폴백, NXT 통합 시세)
        "price_history": None,  # {"dates":[...], "close":[...]} 최근 약 12개월(거래일 기준) 종가
        "foreign_ratio": None,  # 외국인 지분율(%) 최신값 (KR 종목만)
        "foreign_ratio_history": None,  # {"dates":[...], "values":[...]} (KR 종목만)
        "error": None,
    }
    try:
        close, foreign_ratio_map, t, source = _fetch_close_series(item)
        result["price_source"] = source
        close = drop_unfinished_session(close, item["market"], source)
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

    # [2026-09-30] 예전엔 as_of가 직전 실행과 같은 종목을 모아 RETRY_WAIT_SECONDS(300초) 대기 후
    # 재조회했다. 이제 drop_unfinished_session()이 마감+여유(국내 10분/해외 20분) 전 일봉을 버리므로 "종가 미반영"
    # 위험은 그쪽에서 막고, 휴장일·장중 수동 실행처럼 정상적으로 as_of가 같은 경우에도 매번
    # 5분씩 기다리던 대기는 없앴다. 참고용 로그만 남긴다.
    stale_count = sum(
        1 for item in items
        if results_by_ticker[item["ticker"]]["error"] is None
        and results_by_ticker[item["ticker"]]["as_of"] is not None
        and results_by_ticker[item["ticker"]]["as_of"] == prev_as_of_by_ticker.get(item["ticker"])
    )
    if stale_count:
        print(f"[참고] {stale_count}개 종목의 as_of가 직전 실행과 동일(휴장일이거나 새 종가 없음) — 재조회 없이 진행",
              file=sys.stderr)

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
