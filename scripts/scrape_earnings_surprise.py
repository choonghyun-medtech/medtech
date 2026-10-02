#!/usr/bin/env python3
"""
분기 실적 서프라이즈(실제 실적 vs 발표 직전 컨센서스)를 earnings_surprise.json으로 저장한다.
대시보드 "기업 분석 › 실적 서프라이즈" 탭이 쓴다.

- 국내(KR): navercomp.wisereport.co.kr의 어닝서프라이즈 데이터(c1050001_data.aspx?flag=5).
  네이버 종목분석 탭에 그대로 임베드되는 소스로, 로그인/키 불필요. 최근 3개 분기의
  실제 실적·발표일·"발표 직전" 컨센서스와 다음 분기 컨센서스를 매출액/영업이익별로 준다(억원).
- 해외: Yahoo Finance quoteSummary(earningsHistory, earnings, calendarEvents).
  - earningsHistory: 최근 4개 분기 EPS 실제/추정(발표 직전 컨센서스)/서프라이즈.
  - earnings: 분기별 실제 발표일(reportedDate)과 실제 매출.
  - calendarEvents: 다음 실적 발표 예정일과 그 분기 EPS·매출 컨센서스.
  - 매출 서프라이즈: Yahoo는 "과거 분기의 매출 추정치"를 주지 않으므로, 매일 calendarEvents의 다음 분기
    매출 컨센서스를 pending에 기록해 두었다가 그 분기 실적이 나오면 마지막 기록값을 "발표 직전 컨센서스"로
    쓴다. 그래서 매출 서프라이즈는 이 스크립트가 돌기 시작한 뒤 발표되는 분기(3Q26)부터 채워진다.
- 이전 실행의 분기 데이터는 보존·병합한다(소스가 최근 3~4개 분기만 주므로 쌓아 두면 이력이 길어진다).
  실패한 종목도 이전 값을 그대로 유지한다(다른 scrape_*.py와 같은 원칙).

사용법:
    python scripts/scrape_earnings_surprise.py --tickers scripts/tickers.json --out earnings_surprise.json
"""
import argparse
import concurrent.futures
import datetime
import json
import re
import sys
import time
from pathlib import Path

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
WISE_URL = "https://navercomp.wisereport.co.kr/v3/company/ajax/c1050001_data.aspx"
WISE_ACCOUNTS = {"rev": "121000", "op": "121500"}  # 매출액, 영업이익
YAHOO_SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{t}"


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v))
    if not s or s in ("-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def surprise_pct(actual, est):
    """(실제/추정 - 1) × 100. 추정치가 0 이하(적자 예상)면 비율이 의미가 없어 None."""
    if actual is None or est is None or est <= 0:
        return None
    return round((actual / est - 1) * 100, 2)


def ym(s):
    """'2026/06', '2026-06-30' 등 → '2026-06'."""
    m = re.match(r"^(\d{4})[/\-.](\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}" if m else None


# ---------------------------------------------------------------- 국내 (WiseReport)
def fetch_kr(ticker, session, cons_quarterly=None):
    code = ticker.split(".")[0]
    quarters = {}
    nxt = {"period": None, "date": None, "date_confirmed": False}
    referer = f"https://navercomp.wisereport.co.kr/v2/company/c1050001.aspx?cmp_cd={code}"
    today = datetime.date.today().strftime("%Y%m%d")
    for key, acc in WISE_ACCOUNTS.items():
        r = session.get(WISE_URL, params={"flag": "5", "cmp_cd": code, "finGubun": "MAIN", "frq": "1",
                                          "sDT": today, "acc_cd": acc, "chartType": "svg"},
                        headers={**HEADERS, "Referer": referer}, timeout=20)
        r.raise_for_status()
        data = r.json().get("tableData") or {}
        header = (data.get("tableHeaderData") or [{}])[0]
        rows = {row.get("QTR"): row for row in data.get("tableData") or []}
        act, est = rows.get("분기실적(A)") or {}, rows.get("발표직전(E)") or {}
        for col in ("FY_2", "FY_1", "FY0"):
            period = ym(header.get(f"CNS_{col}"))
            if not period:
                continue
            a, e = _num(act.get(col)), _num(est.get(col))
            if a is None and e is None:
                continue
            q = quarters.setdefault(period, {"period": period, "report_date": None})
            date = act.get(f"{col}_S")
            if isinstance(date, str) and re.match(r"^\d{4}/\d{2}/\d{2}$", date):
                q["report_date"] = date.replace("/", "-")
            q[key] = {"actual": a, "est": e, "surprise": surprise_pct(a, e)}
        period = ym(header.get("CNS_FY1"))
        if period:
            nxt["period"] = period
            nxt[f"{key}_est"] = _num(est.get("FY1"))
    # 다음 분기 "발표 직전" 칸은 날짜에 따라 비어 있을 때가 있어(2026-10-02 확인), 매일 갱신되는
    # consensus.json의 분기 추정치(같은 WiseReport 소스)로 보충한다.
    q_est = {ym(q.get("period")): q for q in cons_quarterly or [] if q.get("is_estimate")}
    if nxt["period"] in q_est:
        if nxt.get("rev_est") is None:
            nxt["rev_est"] = q_est[nxt["period"]].get("revenue_eok")
        if nxt.get("op_est") is None:
            nxt["op_est"] = q_est[nxt["period"]].get("operating_income_eok")
    if not quarters and not nxt["period"]:
        raise ValueError("어닝서프라이즈 데이터 없음(컨센서스 미커버 종목)")
    return {"unit": "억원", "quarters": sorted(quarters.values(), key=lambda q: q["period"], reverse=True),
            "next": nxt}


# ---------------------------------------------------------------- 해외 (Yahoo + Nasdaq)
class Yahoo:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.crumb = None

    def refresh(self):
        self.s.get("https://fc.yahoo.com/", timeout=20)  # 쿠키만 받으면 됨(응답은 404가 정상)
        self.crumb = self.s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=20).text.strip()

    def summary(self, ticker, modules):
        for attempt in range(4):
            if not self.crumb:
                self.refresh()
            r = self.s.get(YAHOO_SUMMARY_URL.format(t=ticker),
                           params={"modules": ",".join(modules), "crumb": self.crumb}, timeout=20)
            if r.status_code in (401, 403):
                self.crumb = None
                continue
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            res = (r.json().get("quoteSummary") or {}).get("result") or []
            return res[0] if res else {}
        raise RuntimeError("Yahoo 요청 반복 실패(429/401)")


def _raw(d, k):
    v = (d or {}).get(k)
    return v.get("raw") if isinstance(v, dict) else v


def _ts_ym(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m") if ts else None


def _ts_date(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d") if ts else None


def add_months(period, n):
    y, m = map(int, period.split("-"))
    m += n
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f"{y}-{m:02d}"


def fetch_global(ticker, market, yahoo):
    res = yahoo.summary(ticker, ["earningsHistory", "earnings", "calendarEvents"])
    # earnings 모듈: 분기별 발표일(reportedDate)과 실제 매출. fiscalQuarter 라벨로 매출을, periodEndDate로 기간을 잇는다.
    earn = res.get("earnings") or {}
    dates, fq_period = {}, {}
    for e in (earn.get("earningsChart") or {}).get("quarterly") or []:
        p = _ts_ym(_raw(e, "periodEndDate"))
        if p:
            dates[p] = _ts_date(_raw(e, "reportedDate"))
            fq_period[e.get("fiscalQuarter") or e.get("date")] = p
    revenue = {}
    for f in (earn.get("financialsChart") or {}).get("quarterly") or []:
        p = fq_period.get(f.get("fiscalQuarter") or f.get("date"))
        if p:
            revenue[p] = _raw(f, "revenue")

    quarters = []
    for h in (res.get("earningsHistory") or {}).get("history") or []:
        period = _ts_ym(_raw(h, "quarter"))
        if not period:
            continue
        a, e = _raw(h, "epsActual"), _raw(h, "epsEstimate")
        if a is None and e is None:
            continue
        q = {"period": period, "report_date": dates.get(period),
             "eps": {"actual": a, "est": e, "surprise": surprise_pct(a, e)}}
        if revenue.get(period) is not None:
            q["rev"] = {"actual": revenue[period], "est": None, "surprise": None}
        quarters.append(q)
    quarters.sort(key=lambda q: q["period"], reverse=True)

    cal = ((res.get("calendarEvents") or {}).get("earnings")) or {}
    next_dates = [d.get("fmt") for d in cal.get("earningsDate") or [] if d.get("fmt")]
    nxt = {"period": add_months(quarters[0]["period"], 3) if quarters else None,
           "date": next_dates[0] if next_dates else None,
           "date_confirmed": cal.get("isEarningsDateEstimate") is False,
           "eps_est": _raw(cal, "earningsAverage"), "rev_est": _raw(cal, "revenueAverage")}
    if not quarters and not nxt["date"]:
        raise ValueError("Yahoo 실적 이력 없음(반기 실적만 내는 기업 등)")
    return {"unit": None, "quarters": quarters, "next": nxt}


# ---------------------------------------------------------------- 공통
MAX_QUARTERS = 8


def merge_with_previous(new, old):
    """이전 실행 결과와 병합한다.
    1) 이전에 기록해 둔 "다음 분기 컨센서스·예정일"(old.next)이 이번에 실적으로 나온 분기와 같으면,
       그 값을 발표 직전 컨센서스로 붙인다 — 해외 매출 서프라이즈는 이 방법으로만 계산된다.
    2) 소스가 더 이상 주지 않는 오래된 분기는 이전 파일에서 가져와 이력을 이어 붙인다(최대 8개 분기)."""
    if not old:
        return
    by_period = {q["period"]: q for q in new["quarters"]}
    on = old.get("next") or {}
    q = by_period.get(on.get("period"))
    if q:
        if not q.get("report_date") and on.get("date"):
            q["report_date"] = on["date"]
        rev = q.get("rev")
        if rev and rev.get("est") is None and on.get("rev_est") is not None:
            rev["est"] = on["rev_est"]
            rev["surprise"] = surprise_pct(rev.get("actual"), rev["est"])
    for oq in old.get("quarters") or []:
        cur = by_period.get(oq["period"])
        if cur is None:
            new["quarters"].append(oq)
            continue
        # 이전에 붙여 둔 매출 컨센서스·발표일은 이번 응답에 없으면 유지
        if not cur.get("report_date") and oq.get("report_date"):
            cur["report_date"] = oq["report_date"]
        orev, crev = oq.get("rev") or {}, cur.get("rev")
        if crev and crev.get("est") is None and orev.get("est") is not None:
            crev["est"] = orev["est"]
            crev["surprise"] = surprise_pct(crev.get("actual"), crev["est"])
    new["quarters"] = sorted(new["quarters"], key=lambda q: q["period"], reverse=True)[:MAX_QUARTERS]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="scripts/tickers.json")
    ap.add_argument("--out", default="earnings_surprise.json")
    ap.add_argument("--consensus", default="consensus.json", help="국내 다음 분기 컨센서스 보충용")
    ap.add_argument("--limit", type=int, default=0, help="테스트용: 앞에서 N종목만")
    args = ap.parse_args()

    universe = json.loads(Path(args.tickers).read_text(encoding="utf-8"))
    if args.limit:
        universe = universe[:args.limit]
    out_path = Path(args.out)
    prev = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    prev_stocks = prev.get("stocks") or {}

    cons_path = Path(args.consensus)
    cons = json.loads(cons_path.read_text(encoding="utf-8")).get("stocks") if cons_path.exists() else []
    cons_q = {s.get("ticker"): (s.get("earnings") or {}).get("quarterly") for s in cons or []}

    kr_session = requests.Session()
    yahoo = Yahoo()

    def work(t):
        ticker, market = t["ticker"], t.get("market")
        try:
            data = (fetch_kr(ticker, kr_session, cons_q.get(ticker)) if market == "KR"
                    else fetch_global(ticker, market, yahoo))
            data.update({"name": t.get("name"), "market": market, "error": None})
            merge_with_previous(data, prev_stocks.get(ticker))
            return ticker, data
        except Exception as e:
            old = prev_stocks.get(ticker)
            if old:
                return ticker, {**old, "error": f"이번 수집 실패, 이전 값 유지: {str(e)[:120]}"}
            return ticker, {"name": t.get("name"), "market": market, "quarters": [], "next": {},
                            "error": str(e)[:200]}

    stocks = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for ticker, data in ex.map(work, universe):
            stocks[ticker] = data

    ok = sum(1 for s in stocks.values() if s.get("quarters") and not s.get("error"))
    result = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": "국내: WiseReport 어닝서프라이즈(매출액·영업이익, 억원) · 해외: Yahoo Finance(EPS·매출·발표일)",
        "count": len(stocks), "ok_count": ok, "stocks": stocks,
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[OK] {out_path}: {ok}/{len(stocks)}종목 수집", file=sys.stderr)


if __name__ == "__main__":
    main()
