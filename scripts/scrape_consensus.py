#!/usr/bin/env python3
"""
tickers.json 유니버스 종목의 애널리스트 컨센서스(목표주가, 투자의견, Forward PER, 향후
1개년/1개분기 실적 추정치)를 consensus.json으로 저장한다.

- 국내(KR) 종목: finance.naver.com 종목 메인 페이지(에프앤가이드 제공 컨센서스)를 스크래핑.
  - 목표주가/투자의견: <caption>투자의견</caption> 표의 첫 행.
  - 추정PER(Forward PER)/추정EPS: id="_cns_per"/"_cns_eps" 엘리먼트. 이 값은 최근 3개월간
    추정 증권사가 3개 이상인 경우에만 네이버가 제공하므로, 커버리지가 얕은 종목은 null이 된다
    (추정 증권사 3개 미만이면 trailing PER로 대체하지 않고 그냥 비워 둔다 — trailing 값을
    "컨센서스"라고 잘못 표시하지 않기 위함).
  - 실적 추이(매출액/영업이익/당기순이익/EPS): "기업실적분석" 표를 통째로 가져온다 — 최근
    3개년 연간 실적 + 향후 1개년 추정, 최근 5개분기 실적 + 향후 1개분기 추정("(E)" 표시가
    붙은 마지막 컬럼만 컨센서스 추정치, 나머지는 확정 실적).
- 해외 종목: yfinance의 `Ticker.info`에서 targetMeanPrice/targetHighPrice/targetLowPrice/
  numberOfAnalystOpinions/forwardPE를, `Ticker.income_stmt`/`quarterly_income_stmt`에서 최근
  실제 실적(매출액/순이익/EPS)을, `Ticker.earnings_estimate`/`revenue_estimate`에서
  차년도(+1y)/차분기(+1q) 매출·EPS 컨센서스 평균을 가져와 한 흐름으로 이어붙인다.
- 이전엔 PDF 리포트에서 목표주가/밸류에이션을 정규식으로 추출하려 했으나(scrape_report_estimates.py),
  실제 리포트 PDF가 텍스트 레이어 없이 페이지 전체를 이미지로 렌더링한 형태라 원리적으로 추출이
  불가능해 전면 폐기하고 시장 컨센서스로 대체했다.
- 다른 scrape_*.py와 동일하게, 종목별로 실패해도 이전에 정상 수집된 값이 있으면 보존한다
  (부분 실패가 전체를 덮어쓰지 않도록).

사용법:
    python scrape_consensus.py --tickers tickers.json --out consensus.json
"""
import argparse
import concurrent.futures
import datetime
import json
import re
import sys

import requests
import yfinance as yf
from bs4 import BeautifulSoup

NAVER_MAIN_URL = "https://finance.naver.com/item/main.naver?code={code}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

TICKERS_FILE = "tickers.json"


def _num(s):
    if s is None:
        return None
    s = re.sub(r"[^\d.\-]", "", s)
    if not s or s in ("-", "."):
        return None
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        return None


def fetch_naver_consensus(code: str):
    """finance.naver.com 종목 메인 페이지에서 목표주가/투자의견/추정PER을 파싱한다."""
    resp = requests.get(NAVER_MAIN_URL.format(code=code), headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    out = {
        "opinion": None, "opinion_score": None,
        "target_price": None, "target_price_high": None, "target_price_low": None,
        "per_fwd": None, "eps_fwd": None,
    }

    caption = soup.find("caption", string=lambda s: s and s.strip() == "투자의견")
    if caption:
        table = caption.find_parent("table")
        first_row_ems = table.find_all("tr")[0].find_all("em")
        if len(first_row_ems) >= 2:
            out["opinion_score"] = _num(first_row_ems[0].get_text())
            # "4.00매수" 처럼 <em>4.00</em> 바로 뒤에 의견 텍스트가 붙어 있음
            opinion_span = first_row_ems[0].find_parent("span")
            if opinion_span:
                out["opinion"] = re.sub(r"[\d.]", "", opinion_span.get_text()).strip()
            out["target_price"] = _num(first_row_ems[1].get_text())
        # 두 번째 행: 52주 최고/최저 (목표주가 고/저로 오인하지 않도록 별도 라벨 확인)
        rows = table.find_all("tr")
        if len(rows) >= 2 and "52주" in rows[1].get_text():
            hi_lo = rows[1].find_all("em")
            if len(hi_lo) >= 2:
                out["target_price_high"] = _num(hi_lo[0].get_text())
                out["target_price_low"] = _num(hi_lo[1].get_text())

    cns_per = soup.find(id="_cns_per")
    cns_eps = soup.find(id="_cns_eps")
    if cns_per:
        out["per_fwd"] = _num(cns_per.get_text())
    if cns_eps:
        out["eps_fwd"] = _num(cns_eps.get_text())

    out["earnings"] = fetch_naver_earnings_table(soup)
    return out


def fetch_naver_earnings_table(soup):
    """기업실적분석 표를 있는 그대로 뽑는다: 최근 3개년 실적 + 향후 1개년 추정("(E)" 표시),
    최근 5개분기 실적 + 향후 1개분기 추정. 컨센서스 추정치인 마지막 컬럼만 골라내던 이전
    방식과 달리, 실적 추이를 보여주기 위해 확정 실적 컬럼도 전부 포함한다. 단위: 매출액/
    영업이익/당기순이익은 억원, EPS는 원 (네이버 표기 그대로, 재계산하지 않음).
    """
    caption = soup.find("caption", string=lambda s: s and s.strip() == "기업실적분석 테이블")
    if not caption:
        return None
    table = caption.find_parent("table")
    thead_rows = table.find("thead").find_all("tr")
    if len(thead_rows) < 2:
        return None
    group_ths = thead_rows[0].find_all("th")
    annual_group = next((th for th in group_ths if "연간" in th.get_text()), None)
    if annual_group is None:
        return None
    annual_count = int(annual_group.get("colspan", 0))
    periods = [th.get_text(strip=True) for th in thead_rows[1].find_all("th")]
    if annual_count <= 0 or annual_count > len(periods):
        return None

    def row_values(label):
        for tr in table.find("tbody").find_all("tr"):
            th = tr.find("th")
            strong = th.find("strong") if th else None
            if strong and strong.get_text(strip=True) == label:
                return [_num(td.get_text()) for td in tr.find_all("td")]
        return None

    revenue = row_values("매출액")
    op_income = row_values("영업이익")
    net_income = row_values("당기순이익")
    eps = row_values("EPS(원)")

    def build_range(idx_range):
        out = []
        for i in idx_range:
            # "(E)"는 is_estimate 플래그로 별도 전달하므로 라벨 문자열에서는 떼어낸다
            # (index.html이 is_estimate일 때 배지로 "(E)"를 다시 붙이므로, 여기 남겨두면 중복 표시된다).
            is_est = "(E)" in periods[i]
            period_label = periods[i].replace("(E)", "").strip()
            out.append({
                "period": period_label,
                "is_estimate": is_est,
                "revenue_eok": revenue[i] if revenue and i < len(revenue) else None,
                "operating_income_eok": op_income[i] if op_income and i < len(op_income) else None,
                "net_income_eok": net_income[i] if net_income and i < len(net_income) else None,
                "eps": eps[i] if eps and i < len(eps) else None,
            })
        return out

    annual = build_range(range(0, annual_count))
    quarterly = build_range(range(annual_count, len(periods)))
    if not annual and not quarterly:
        return None
    return {"annual": annual, "quarterly": quarterly}


def _safe_float(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN 체크


def fetch_yfinance_earnings_table(t):
    """최근 실제 연간/분기 실적(매출액/순이익/EPS, income_stmt 기반) 뒤에 향후 1개년/1개분기
    컨센서스 추정(earnings_estimate/revenue_estimate 기반)을 이어붙여 반환한다.
    yfinance 버전에 따라 일부 속성이 없을 수 있어, 구할 수 있는 부분만 채우고 나머지는
    조용히 비운다(다른 필드 수집에 영향 주지 않도록).
    """
    def build_actual(stmt, max_periods):
        if stmt is None or stmt.empty:
            return []
        cols = list(stmt.columns)[:max_periods]  # 최신순으로 들어옴
        cols = list(reversed(cols))  # 오래된 -> 최신 순으로 뒤집음
        rows = []
        for c in cols:
            rev = stmt.loc["Total Revenue", c] if "Total Revenue" in stmt.index else None
            ni = stmt.loc["Net Income", c] if "Net Income" in stmt.index else None
            eps_v = stmt.loc["Diluted EPS", c] if "Diluted EPS" in stmt.index else None
            rows.append({
                "period": str(c.date()) if hasattr(c, "date") else str(c),
                "is_estimate": False,
                "revenue": _safe_float(rev),
                "net_income": _safe_float(ni),
                "eps": _safe_float(eps_v),
            })
        return rows

    try:
        annual = build_actual(t.income_stmt, 3)
    except Exception:
        annual = []
    try:
        quarterly = build_actual(t.quarterly_income_stmt, 4)
    except Exception:
        quarterly = []

    try:
        eps_df = t.earnings_estimate
        rev_df = t.revenue_estimate
    except Exception:
        eps_df = rev_df = None

    def append_estimate(lst, period_key, label):
        if eps_df is None or rev_df is None:
            return
        if period_key not in eps_df.index or period_key not in rev_df.index:
            return
        try:
            eps_avg = eps_df.loc[period_key, "avg"]
            rev_avg = rev_df.loc[period_key, "avg"]
        except Exception:
            return
        lst.append({
            "period": label,
            "is_estimate": True,
            "revenue": _safe_float(rev_avg),
            "net_income": None,
            "eps": _safe_float(eps_avg),
        })

    append_estimate(annual, "+1y", "차년도(+1Y)")
    append_estimate(quarterly, "+1q", "차분기(+1Q)")

    if not annual and not quarterly:
        return None
    return {"annual": annual, "quarterly": quarterly}


def fetch_yfinance_consensus(ticker: str):
    t = yf.Ticker(ticker)
    info = t.info
    return {
        "opinion": info.get("recommendationKey"),
        "opinion_score": info.get("recommendationMean"),
        "target_price": info.get("targetMeanPrice"),
        "target_price_high": info.get("targetHighPrice"),
        "target_price_low": info.get("targetLowPrice"),
        "analyst_count": info.get("numberOfAnalystOpinions"),
        "per_fwd": info.get("forwardPE"),
        "eps_fwd": info.get("forwardEps"),
        "earnings": fetch_yfinance_earnings_table(t),
    }


def fetch_one(item):
    ticker = item["ticker"]
    result = {
        "ticker": ticker,
        "name": item["name"],
        "market": item["market"],
        "source": None,
        "opinion": None,
        "opinion_score": None,
        "target_price": None,
        "target_price_high": None,
        "target_price_low": None,
        "analyst_count": None,
        "per_fwd": None,
        "eps_fwd": None,
        "earnings": None,  # {"annual": [...], "quarterly": [...]} — 실적 추이 + 마지막 1개는 컨센서스 추정("(E)")
        "as_of": str(datetime.date.today()),
        "error": None,
    }
    try:
        m = re.match(r"^([0-9A-Za-z]{6})\.(KS|KQ)$", ticker)
        if item["market"] == "KR" and m:
            result["source"] = "naver"
            result.update(fetch_naver_consensus(m.group(1)))
        else:
            result["source"] = "yfinance"
            result.update(fetch_yfinance_consensus(ticker))
        if result["target_price"] is None and result["per_fwd"] is None:
            result["error"] = "no consensus data found"
    except Exception as e:
        result["error"] = str(e)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=TICKERS_FILE)
    ap.add_argument("--out", default="consensus.json")
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

    print(f"fetching consensus for {len(items)} tickers with {args.max_workers} workers...")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = {ex.submit(fetch_one, item): item for item in items}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            done += 1
            if r["error"] is not None:
                prev = existing_by_ticker.get(r["ticker"])
                if prev is not None and prev.get("error") is None:
                    print(f"[{done}/{len(items)}] {r['ticker']}: FAIL ({r['error']}) — 이전 정상 데이터 보존",
                          file=sys.stderr)
                    results.append(prev)
                    continue
            results.append(r)
            status = "OK" if r["error"] is None else f"FAIL ({r['error']})"
            print(f"[{done}/{len(items)}] {r['ticker']}: {status}")

    order = {item["ticker"]: i for i, item in enumerate(items)}
    results.sort(key=lambda r: order.get(r["ticker"], 0))

    ok = sum(1 for r in results if r["error"] is None)
    payload = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(results),
        "ok_count": ok,
        "stocks": results,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} ({ok}/{len(results)}건 성공)")


if __name__ == "__main__":
    main()
