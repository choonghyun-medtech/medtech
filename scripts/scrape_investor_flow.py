#!/usr/bin/env python3
"""
국내(KR) 추적 종목의 투자자별 순매매 동향(기관/외국인/개인)을 네이버 증권에서 가져와
investor_flow.json으로 저장한다.

- 대상: tickers.json 중 market == "KR" 인 종목 전체 (해외 종목은 이 방식의 데이터가 없음)
- 소스: https://stock.naver.com/api/stockSecurity/investor-trend/v2/domestic/{6자리코드}/paged
  ?exchangeType=krx&size=N&index=P
  (신버전 네이버 증권 SPA가 내부적으로 호출하는 JSON API. 일별 기관/외국인/개인 순매매수량(주),
  종가 등을 최신일부터 size일치씩 준다 — index는 0부터 세는 페이지 번호.)
- 순매매대금(억원) = 순매매수량(주) x 해당일 종가 로 근사 환산한다
  (실제 체결가 평균이 아닌 종가 기준 근사치임을 감안할 것).
- 개인 순매매는 API가 individualNetVolume으로 직접 제공한다 (더 이상 -(기관+외국인) 추정이 아님).

배경:
    예전에는 https://finance.naver.com/item/frgn.naver?code={code} 의 HTML 표를 파싱했으나,
    네이버가 이 URL을 새 stock.naver.com SPA로 리다이렉트시키면서 서버 응답에 표가 사라졌다
    (데이터는 브라우저에서 JS가 별도 API를 호출해 채움). 그래서 위 JSON API를 직접 호출하는
    방식으로 교체했다.
    [2026-10-22] 그다음 쓰던 /api/domestic/detail/{code}/trend가 404로 사라져 전 종목 수집이
    실패했다 — 페이지 JS 번들에서 찾은 위 investor-trend/v2로 교체. exchangeType은 소문자
    "krx"만 받고(대문자는 400), size는 200까지만 돼서(260은 400) 페이지를 나눠 받는다.
    종가는 예전 closePrice와 같은 값인 krx.closingPrice를 쓴다(regularMarketClosingPrice는
    정규장 종가라 날에 따라 조금 다르다 — 기존 데이터와 이어지도록 closingPrice 유지).

사용법:
    python scrape_investor_flow.py --tickers scripts/tickers.json --out investor_flow.json
"""
import argparse
import datetime
import json
import re
import sys
import time

import requests

TREND_URL = "https://stock.naver.com/api/stockSecurity/investor-trend/v2/domestic/{code}/paged"
TRADING_DAYS_TO_COVER = 260  # 약 12개월 거래일 (주가/시가총액/외국인지분율 차트와 기간 통일)
PAGE_SIZE = 130  # API가 한 번에 200일까지만 받아서 260일을 두 페이지로 나눈다
REQUEST_DELAY_SEC = 0.3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://stock.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

_debug_dumped = False


def parse_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_trend_page(session: requests.Session, code: str, index: int):
    """index번째 페이지(최신일부터 PAGE_SIZE일씩)의 items와 다음 페이지 유무를 돌려준다."""
    global _debug_dumped
    resp = session.get(
        TREND_URL.format(code=code),
        params={"exchangeType": "krx", "size": PAGE_SIZE, "index": index},
        timeout=15,
    )
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        if not _debug_dumped:
            print(
                f"[DEBUG] {code}: JSON 파싱 실패. 응답 앞부분:\n{resp.text[:2000]}",
                file=sys.stderr,
            )
            _debug_dumped = True
        return [], False

    rows = data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        if not _debug_dumped:
            print(f"[DEBUG] {code}: 예상치 못한 응답 형태: {resp.text[:2000]}", file=sys.stderr)
            _debug_dumped = True
        return [], False
    return rows, bool(data.get("hasNext"))


def fetch_flow_for_code(session: requests.Session, code: str):
    rows = []
    index = 0
    while len(rows) < TRADING_DAYS_TO_COVER:
        page, has_next = fetch_trend_page(session, code, index)
        rows.extend(page)
        if not has_next:
            break
        index += 1
        time.sleep(REQUEST_DELAY_SEC)

    parsed = []
    for r in rows:
        traded_at = r.get("localTradedAt")
        krx = r.get("krx") or {}
        if not traded_at or not re.match(r"^\d{4}-\d{2}-\d{2}$", traded_at):
            continue
        close = parse_int(krx.get("closingPrice"))
        inst_q = parse_int(krx.get("organizationNetVolume"))
        foreign_q = parse_int(krx.get("foreignNetVolume"))
        individual_q = parse_int(krx.get("individualNetVolume"))
        parsed.append(
            {
                "date": traded_at,
                "close": close,
                "inst_q": inst_q,
                "foreign_q": foreign_q,
                "individual_q": individual_q,
            }
        )

    # 날짜 오름차순 정렬 + 중복 제거
    seen = set()
    unique = []
    for r in sorted(parsed, key=lambda r: r["date"]):
        if r["date"] in seen:
            continue
        seen.add(r["date"])
        unique.append(r)
    unique = unique[-TRADING_DAYS_TO_COVER:]

    dates, inst_eok, foreign_eok, retail_eok = [], [], [], []
    for r in unique:
        dates.append(r["date"])
        close = r["close"]
        inst_q = r["inst_q"]
        foreign_q = r["foreign_q"]
        individual_q = r["individual_q"]
        if None in (close, inst_q, foreign_q, individual_q):
            inst_eok.append(None)
            foreign_eok.append(None)
            retail_eok.append(None)
            continue
        inst_eok.append(round(inst_q * close / 1e8, 2))
        foreign_eok.append(round(foreign_q * close / 1e8, 2))
        retail_eok.append(round(individual_q * close / 1e8, 2))

    return {
        "dates": dates,
        "inst_eok": inst_eok,
        "foreign_eok": foreign_eok,
        "retail_eok": retail_eok,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="scripts/tickers.json")
    ap.add_argument("--out", default="investor_flow.json")
    args = ap.parse_args()

    with open(args.tickers, encoding="utf-8") as f:
        items = json.load(f)

    kr_items = [it for it in items if it.get("market") == "KR"]
    print(f"국내 종목 {len(kr_items)}개 대상으로 투자자 수급 수집 시작")

    session = requests.Session()
    session.headers.update(HEADERS)

    result = {}
    ok = 0
    for item in kr_items:
        m = re.match(r"^([0-9A-Za-z]{6})\.(KS|KQ)$", item["ticker"])
        if not m:
            continue
        code = m.group(1)
        try:
            flow = fetch_flow_for_code(session, code)
            if flow["dates"]:
                result[item["ticker"]] = flow
                ok += 1
                print(f"{item['ticker']} ({item['name']}): {len(flow['dates'])}일치 수집")
            else:
                print(f"[WARN] {item['ticker']} ({item['name']}): 데이터 없음", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] {item['ticker']} ({item['name']}) 실패: {e}", file=sys.stderr)
        time.sleep(REQUEST_DELAY_SEC)

    if ok == 0:
        print("[ERROR] 수집된 종목이 0개라 기존 investor_flow.json을 보존하고 종료합니다.", file=sys.stderr)
        sys.exit(1)

    payload = {
        "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "stock.naver.com 종목 상세 투자자동향 API · 기관/외국인/개인 순매매대금(종가 기준 근사)",
        "count": ok,
        "stocks": result,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} ({ok}/{len(kr_items)}종목)")


if __name__ == "__main__":
    main()
