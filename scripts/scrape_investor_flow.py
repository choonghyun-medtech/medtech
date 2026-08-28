#!/usr/bin/env python3
"""
국내(KR) 추적 종목의 투자자별 순매매 동향(기관/외국인)을 네이버 금융에서 가져와
investor_flow.json으로 저장한다.

- 대상: tickers.json 중 market == "KR" 인 종목 전체 (해외 종목은 이 방식의 데이터가 없음)
- 소스: https://finance.naver.com/item/frgn.naver?code={6자리코드}&page={n}
  (일별 기관/외국인 순매매량(주) 표, 페이지당 10행)
- 순매매대금(억원) = 순매매량(주) x 해당일 종가 로 근사 환산한다
  (실제 체결가 평균이 아닌 종가 기준 근사치임을 감안할 것).
- 개인 순매매는 네이버 페이지에 직접 제공되지 않는다. -(기관+외국인)으로 추정하며,
  기타법인 등 다른 투자자 유형이 섞여 있어 근사치다 — 프론트엔드에도 "추정"으로 표시한다.
- 페이지 구조가 예상과 다를 경우를 대비해 첫 종목의 첫 페이지는 원본 HTML을
  로그에 전부 출력한다 (reports.json 스크래퍼와 동일한 디버깅 방식).

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
from bs4 import BeautifulSoup

FRGN_URL = "https://finance.naver.com/item/frgn.naver"
TRADING_DAYS_TO_COVER = 260  # 약 12개월 거래일 (주가/시가총액/외국인지분율 차트와 기간 통일)
ROWS_PER_PAGE = 10
PAGES_NEEDED = -(-TRADING_DAYS_TO_COVER // ROWS_PER_PAGE) + 1  # 여유 1페이지
REQUEST_DELAY_SEC = 0.3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

_debug_dumped = False
_debug_first_request_dumped = False  # 첫 요청은 성공 여부와 무관하게 무조건 한 번 상태를 찍어본다


def make_session() -> requests.Session:
    """네이버 금융 메인 페이지를 먼저 한 번 방문해 쿠키를 확보한 세션을 만든다.
    (reports.json 스크래퍼에서도 같은 패턴을 썼음 — 실제로 필요한지는 아직 검증 전이지만
    비용이 거의 없어 방어적으로 추가. 이게 원인이 아닐 수도 있으니 이번 라운드 로그로 확인 필요.)
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        resp = session.get("https://finance.naver.com/", timeout=15)
        print(f"[DEBUG] 워밍업 요청 HTTP {resp.status_code}, 쿠키 {list(session.cookies.keys())}", file=sys.stderr)
    except requests.RequestException as e:
        print(f"[DEBUG] 워밍업 요청 실패(무시하고 진행): {e}", file=sys.stderr)
    return session


def parse_signed_int(text: str):
    t = text.strip().replace(",", "").replace("+", "")
    if t in ("", "-", "N/A"):
        return None
    try:
        return int(t)
    except ValueError:
        return None


def fetch_page(session: requests.Session, code: str, page: int) -> str:
    global _debug_first_request_dumped
    resp = session.get(FRGN_URL, params={"code": code, "page": page}, timeout=15)
    if not _debug_first_request_dumped:
        print(
            f"[DEBUG] 첫 요청 {code} page{page}: HTTP {resp.status_code}, "
            f"응답 바이트수={len(resp.content)}, 최종 URL={resp.url}",
            file=sys.stderr,
        )
        _debug_first_request_dumped = True
    resp.raise_for_status()
    return resp.content.decode("euc-kr", errors="replace")


def _parse_rows_from_table(table, code: str, page: int, verbose: bool):
    """table 하나에서 날짜 패턴(YYYY.MM.DD)에 매치되는 행만 골라 파싱한다."""
    rows = []
    trs = table.select("tr")
    for tr in trs:
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        cell_texts = [td.get_text(strip=True) for td in tds]
        date_text = cell_texts[0]
        if not re.match(r"^\d{4}\.\d{2}\.\d{2}$", date_text):
            continue

        if verbose:
            print(f"[DEBUG] {code} page{page} 첫 데이터 행 셀 목록: {cell_texts}", file=sys.stderr)

        # 일반적인 frgn.naver 열 순서: 날짜, 종가, 전일비, 등락률, 거래량, 기관순매매량, 외국인순매매량, 보유주수, 보유율
        close = parse_signed_int(cell_texts[1]) if len(cell_texts) > 1 else None
        inst_qty = parse_signed_int(cell_texts[5]) if len(cell_texts) > 5 else None
        foreign_qty = parse_signed_int(cell_texts[6]) if len(cell_texts) > 6 else None

        if verbose:
            print(
                f"[DEBUG] {code} 파싱 결과: date={date_text} close={close} "
                f"inst_qty={inst_qty} foreign_qty={foreign_qty}",
                file=sys.stderr,
            )
            verbose = False  # 같은 테이블 안에서는 첫 행만 자세히 찍는다

        rows.append(
            {
                "date": date_text.replace(".", "-"),
                "close": close,
                "inst_qty": inst_qty,
                "foreign_qty": foreign_qty,
            }
        )
    return rows


def parse_page(html: str, code: str, page: int):
    """반환: [{date, close, inst_qty, foreign_qty}, ...] (최신순 아닐 수 있음, 호출부에서 정렬)

    frgn.naver 페이지에는 class="type2"인 표가 여러 개 있다(예: 상단의 매도상위/매수상위
    증권사 표도 같은 클래스를 씀). select_one으로 첫 번째 table.type2만 집었더니 날짜 컬럼이
    없는 엉뚱한 표를 계속 읽고 있었던 게 실제 원인이었다 — 이제는 모든 table.type2 후보를
    순회하며 날짜 패턴에 매치되는 행이 실제로 나오는 표를 찾는다.
    """
    global _debug_dumped
    soup = BeautifulSoup(html, "html.parser")
    candidates = soup.select("table.type2")
    if not candidates:
        if not _debug_dumped:
            has_type2_string = "type2" in html
            print(
                f"[DEBUG] {code} page{page}: table.type2 자체를 못 찾음 (html 안에 'type2' 문자열 존재={has_type2_string}). "
                f"응답 앞부분:\n{html[:3000]}",
                file=sys.stderr,
            )
            _debug_dumped = True
        return []

    for table in candidates:
        rows = _parse_rows_from_table(table, code, page, verbose=not _debug_dumped)
        if rows:
            _debug_dumped = True
            return rows

    # table.type2 후보가 여러 개 있었는데 그 중 어느 것도 날짜 매치 행이 없는 경우.
    if not _debug_dumped:
        print(
            f"[DEBUG] {code} page{page}: table.type2 후보 {len(candidates)}개 중 날짜 매치 행이 있는 표가 없음. "
            f"각 후보의 앞부분 tr 3개씩:",
            file=sys.stderr,
        )
        for i, table in enumerate(candidates):
            trs_preview = [str(tr)[:250] for tr in table.select("tr")[:3]]
            print(f"  [후보 {i}]\n" + "\n  ---\n".join(trs_preview), file=sys.stderr)
        _debug_dumped = True
    return []


def fetch_flow_for_code(session: requests.Session, code: str):
    all_rows = []
    for page in range(1, PAGES_NEEDED + 1):
        html = fetch_page(session, code, page)
        rows = parse_page(html, code, page)
        if not rows:
            break
        all_rows.extend(rows)
        time.sleep(REQUEST_DELAY_SEC)

    # 날짜 오름차순 정렬 + 중복 제거
    seen = set()
    unique = []
    for r in sorted(all_rows, key=lambda r: r["date"]):
        if r["date"] in seen:
            continue
        seen.add(r["date"])
        unique.append(r)
    unique = unique[-TRADING_DAYS_TO_COVER:]

    dates, inst_eok, foreign_eok, retail_eok_est = [], [], [], []
    for r in unique:
        dates.append(r["date"])
        close = r["close"]
        inst_q = r["inst_qty"]
        foreign_q = r["foreign_qty"]
        if close is None or inst_q is None or foreign_q is None:
            inst_eok.append(None)
            foreign_eok.append(None)
            retail_eok_est.append(None)
            continue
        inst_amt = round(inst_q * close / 1e8, 2)
        foreign_amt = round(foreign_q * close / 1e8, 2)
        inst_eok.append(inst_amt)
        foreign_eok.append(foreign_amt)
        # 개인 추정치 = -(기관+외국인). 기타법인 등이 섞여 있어 근사치.
        retail_eok_est.append(round(-(inst_amt + foreign_amt), 2))

    return {
        "dates": dates,
        "inst_eok": inst_eok,
        "foreign_eok": foreign_eok,
        "retail_eok_est": retail_eok_est,
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

    session = make_session()

    result = {}
    ok = 0
    for item in kr_items:
        m = re.match(r"^(\d{6})\.(KS|KQ)$", item["ticker"])
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

    if ok == 0:
        print("[ERROR] 수집된 종목이 0개라 기존 investor_flow.json을 보존하고 종료합니다.", file=sys.stderr)
        sys.exit(1)

    payload = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "finance.naver.com frgn.naver · 기관/외국인 순매매대금(종가 기준 근사) · 개인은 -(기관+외국인) 추정치",
        "count": ok,
        "stocks": result,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} ({ok}/{len(kr_items)}종목)")


if __name__ == "__main__":
    main()
