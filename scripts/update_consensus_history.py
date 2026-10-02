"""
컨센서스 리비전 추적용 이력 파일(consensus_history.json)을 만든다/갱신한다.

consensus.json은 매일 덮어써지는 "오늘 기준 컨센서스" 스냅샷이라 추정치가 어떻게 바뀌어왔는지
(리비전)는 남지 않는다. 이 스크립트가 매일 그날 스냅샷에서 핵심 값만 뽑아 날짜별 시계열로 누적한다.

- 기본 모드: 현재 consensus.json 한 건을 이력에 추가(같은 날짜가 이미 있으면 덮어씀).
- --backfill: git 히스토리에 커밋된 consensus.json 과거 버전들을 전부 읽어 이력을 처음부터 재구성.
  (consensus.json 일일 커밋이 2026-09-04부터 쌓여 있어, 도입 시점에 약 한 달치를 바로 채울 수 있다.)

저장하는 값(종목별, dates 배열과 같은 길이의 배열, 값이 없으면 null):
- tp: 목표주가 평균(해외 yfinance만 제공), n: 커버 애널리스트 수(해외만)
- eps / rev / op: {"<회계연도>": [...]} — 추정치(is_estimate)만. op(영업이익)는 국내(naver)만 제공.

회계연도 키는 실제 연도로 저장한다. 해외(yfinance)는 기간이 "이번연도(0Y)"/"차년도(+1Y)"처럼
상대 표기라 1월에 기준 연도가 넘어가면 "FY1 대비 변화"가 엉뚱하게 튄다 — 마지막 실적(확정) 연도 + 1을
0Y로 환산해 고정 연도 키로 바꿔 둔다. 국내(naver)는 "2026.12"처럼 결산월이 붙어 있어 앞 4자리를 쓴다.
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

KST = datetime.timezone(datetime.timedelta(hours=9))
REL_OFFSET = {"이번연도(0Y)": 1, "차년도(+1Y)": 2}


def snapshot_date(data):
    """스냅샷 기준일 — updated(UTC ISO)를 KST 날짜로. 없으면 None."""
    upd = data.get("updated")
    if not upd:
        return None
    dt = datetime.datetime.fromisoformat(upd.replace("Z", "+00:00"))
    return dt.astimezone(KST).date().isoformat()


def fiscal_year_key(period, last_actual_year, fallback_year):
    m = re.match(r"^(\d{4})[.\-]", period or "")
    if m:
        return m.group(1)
    if period in REL_OFFSET:
        base = last_actual_year if last_actual_year else fallback_year - 1
        return str(base + REL_OFFSET[period])
    return None


def extract(stock, snap_year):
    """종목 하나에서 이력에 저장할 값만 뽑는다."""
    annual = (stock.get("earnings") or {}).get("annual") or []
    actual_years = [int(m.group(1)) for a in annual if not a.get("is_estimate")
                    for m in [re.match(r"^(\d{4})", a.get("period") or "")] if m]
    last_actual = max(actual_years) if actual_years else None
    out = {"tp": stock.get("target_price"), "n": stock.get("analyst_count"), "eps": {}, "rev": {}, "op": {}}
    for a in annual:
        if not a.get("is_estimate"):
            continue
        fy = fiscal_year_key(a.get("period"), last_actual, snap_year)
        if not fy:
            continue
        rev = a.get("revenue_eok", a.get("revenue"))
        op = a.get("operating_income_eok", a.get("operating_income"))
        if a.get("eps") is not None:
            out["eps"][fy] = a["eps"]
        if rev is not None:
            out["rev"][fy] = rev
        if op is not None:
            out["op"][fy] = op
    return out


def empty_history():
    return {"updated": None, "dates": [], "stocks": {}}


def add_snapshot(hist, data):
    date = snapshot_date(data)
    if not date:
        return False
    snap_year = int(date[:4])
    dates = hist["dates"]
    if date in dates:
        idx = dates.index(date)
    else:
        # 날짜 순서를 유지하며 삽입 — 모든 종목 배열에 같은 위치로 null 슬롯을 만든다.
        idx = next((i for i, d in enumerate(dates) if d > date), len(dates))
        dates.insert(idx, date)
        for s in hist["stocks"].values():
            for key in ("tp", "n"):
                s[key].insert(idx, None)
            for key in ("eps", "rev", "op"):
                for arr in s[key].values():
                    arr.insert(idx, None)
    n = len(dates)
    stocks = data.get("stocks") or {}
    if isinstance(stocks, list):  # 초기(2026-09 초) 버전은 stocks가 dict가 아니라 list였다
        stocks = {s.get("ticker"): s for s in stocks if s.get("ticker")}
    for ticker, stock in stocks.items():
        if stock.get("error"):
            continue
        vals = extract(stock, snap_year)
        s = hist["stocks"].setdefault(ticker, {
            "name": stock.get("name"), "market": stock.get("market"),
            "tp": [None] * n, "n": [None] * n, "eps": {}, "rev": {}, "op": {},
        })
        s["name"] = stock.get("name") or s["name"]
        s["market"] = stock.get("market") or s["market"]
        s["tp"][idx] = vals["tp"]
        s["n"][idx] = vals["n"]
        for key in ("eps", "rev", "op"):
            for fy, v in vals[key].items():
                s[key].setdefault(fy, [None] * n)[idx] = v
    hist["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    return True


def _merge_series(dst, src):
    return [d if d is not None else s for d, s in zip(dst, src)]


def reconcile(hist, universe_path):
    """현재 유니버스(tickers.json) 기준으로 티커를 정리한다.
    - 티커 표기가 바뀐 종목(예: 2026-09-30 코스닥 13종목 .KS→.KQ 정정)은 같은 종목코드끼리 이력을 합친다.
    - 유니버스에서 빠진 종목은 이력에서도 지운다."""
    universe = json.loads(Path(universe_path).read_text(encoding="utf-8"))
    current = {t["ticker"] for t in universe}
    by_code = {t["ticker"].split(".")[0]: t["ticker"] for t in universe if "." in t["ticker"]}
    for old in [t for t in hist["stocks"] if t not in current]:
        s = hist["stocks"].pop(old)
        new = by_code.get(old.split(".")[0]) if "." in old else None
        if not new:
            continue
        d = hist["stocks"].setdefault(new, s)
        if d is s:
            continue
        for key in ("tp", "n"):
            d[key] = _merge_series(d[key], s[key])
        for key in ("eps", "rev", "op"):
            for fy, arr in s[key].items():
                d[key][fy] = _merge_series(d[key][fy], arr) if fy in d[key] else arr


def git_snapshots(path):
    """git 히스토리의 consensus.json 버전을 날짜별 마지막 커밋 하나씩 반환(오래된 순)."""
    log = subprocess.run(["git", "log", "--format=%H", "--", path],
                         capture_output=True, text=True, check=True).stdout.split()
    by_date = {}
    for sha in reversed(log):  # 오래된 순으로 읽어 같은 날짜면 나중 커밋이 이긴다
        raw = subprocess.run(["git", "show", f"{sha}:{path}"], capture_output=True, check=True).stdout
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        d = snapshot_date(data)
        if d:
            by_date[d] = data
    return [by_date[d] for d in sorted(by_date)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--consensus", default="consensus.json")
    ap.add_argument("--out", default="consensus_history.json")
    ap.add_argument("--tickers", default="scripts/tickers.json")
    ap.add_argument("--backfill", action="store_true", help="git 히스토리에서 이력을 처음부터 재구성")
    args = ap.parse_args()

    out = Path(args.out)
    if args.backfill:
        hist = empty_history()
        for data in git_snapshots(args.consensus):
            add_snapshot(hist, data)
    else:
        hist = json.loads(out.read_text(encoding="utf-8")) if out.exists() else empty_history()

    current = json.loads(Path(args.consensus).read_text(encoding="utf-8"))
    add_snapshot(hist, current)
    reconcile(hist, args.tickers)

    out.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[OK] {out}: {len(hist['dates'])}일 ({hist['dates'][0]} ~ {hist['dates'][-1]}), "
          f"{len(hist['stocks'])}종목", file=sys.stderr)


if __name__ == "__main__":
    main()
