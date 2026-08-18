#!/usr/bin/env python3
"""
reports.json에 있는 각 기업의 "가장 최신" 리포트 PDF에서
실적 추정 테이블(매출액/영업이익/순이익 등 연도별 추정치)과 목표주가를 추출해
report_estimates.json으로 저장한다.

- 입력: reports.json (scrape_reports.py가 만든 파일, 기업별 최신 pdf 첨부 id 포함)
- PDF 원문 안의 "실적 추정"(예: 표 3~4, "분기별 실적전망표") 표는 리포트마다 위치·표 번호·
  행/열 방향이 조금씩 다를 수 있어, "매출액"과 "영업이익"이 함께 들어있는 표를 찾는 방식으로
  위치를 특정한다. 항목이 행으로 나열된 표와, 분기별 실적전망표처럼 기간이 행이고 항목이 열로
  나열된 표(전치된 형태) 둘 다 지원한다. 선 기반(테두리) 표 추출로 못 찾으면 테두리 없는
  텍스트 정렬 기반 표 추출도 재시도한다.
- 목표주가는 본문 텍스트에서 "목표주가" 근처 숫자를 정규식으로 찾는다.
- 이 방식은 실제 사이트 접근이 막힌 샌드박스 환경에서는 검증하지 못했고, 로컬에서
  직접 만든 샘플 PDF로만 로직을 검증했다 — 실제 리포트 PDF 구조와 다를 수 있으므로
  첫 실행 시 Actions 로그에서 결과를 반드시 확인해야 한다.
- 종목별로 추출에 실패해도 다른 종목 처리는 계속하고, 기존 report_estimates.json에
  있던 값은 유지한다(부분 실패가 전체를 덮어쓰지 않도록 병합 저장).

사용법:
    python scrape_report_estimates.py --reports reports.json --out report_estimates.json
"""
import argparse
import datetime
import json
import re
import sys

import requests

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

PDF_URL_TMPL = "https://securities.miraeasset.com/bbs/download/{pdf}.pdf?attachmentId={pdf}"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://securities.miraeasset.com/bbs/board/message/list.do?categoryId=1521",
}

# 실적 추정 표를 찾을 때 헤더/행 라벨로 쓰는 키워드 (표 형태가 조금 달라도 이 두 라벨이
# 같은 표 안에 함께 있으면 실적 추정 표로 간주한다).
REQUIRED_ROW_LABELS = ["매출액", "영업이익"]

# "목표주가"라는 글자 사이에 줄바꿈/공백이 끼는 좁은 박스 레이아웃 대비 \s*로 유연하게 매치.
TARGET_PRICE_PATTERNS = [
    r"목\s*표\s*주\s*가[^\d]{0,12}([\d,]{4,9})\s*원",
    r"목\s*표\s*주\s*가\s*\(\s*원\s*\)[^\d]{0,6}([\d,]{4,9})",
    r"T\s*P[^\d가-힣]{0,10}([\d,]{4,9})\s*원",
]


def fetch_pdf_bytes(session: requests.Session, pdf_id: str) -> bytes:
    url = PDF_URL_TMPL.format(pdf=pdf_id)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.content


def _clean(c):
    return str(c).replace("\n", " ").strip() if c else ""


def _try_match_table(table):
    """표 하나를 놓고 두 방향(항목=행 / 항목=열) 모두 시도해 매출액+영업이익이 같이
    있는 쪽을 찾는다. 매치되면 {"headers":[...], "rows":[{"label","values"}]} 반환,
    아니면 None. 항목=열(분기별 실적전망표처럼 기간이 행, 지표가 열인 표)도 지원한다.
    """
    if not table or len(table) < 2:
        return None

    # 방향 A: 첫 열(row[0])이 항목명(매출액/영업이익/...), 나머지 열이 기간별 값
    row_labels = [_clean(row[0]) if row else "" for row in table]
    if all(any(lbl in cell for cell in row_labels) for lbl in REQUIRED_ROW_LABELS):
        headers = [_clean(c) for c in table[0]]
        rows = []
        for row in table[1:]:
            label = _clean(row[0]) if row else ""
            values = [_clean(c) for c in row[1:]]
            if label:
                rows.append({"label": label, "values": values})
        return {"headers": headers, "rows": rows, "orientation": "row"}

    # 방향 B: 첫 행(table[0])이 항목명(매출액/영업이익/...), 나머지 행이 기간(분기/연도)별 값
    # — "분기별 실적전망표"처럼 기간이 행으로, 지표가 열로 나열된 표.
    header_cells = [_clean(c) for c in table[0]]
    if all(any(lbl in cell for cell in header_cells) for lbl in REQUIRED_ROW_LABELS):
        period_labels = [_clean(row[0]) if row else "" for row in table[1:]]
        rows = []
        for col_idx, metric in enumerate(header_cells):
            if col_idx == 0 or not metric:
                continue
            values = [_clean(row[col_idx]) if row and col_idx < len(row) else "" for row in table[1:]]
            rows.append({"label": metric, "values": values})
        return {"headers": [header_cells[0]] + period_labels, "rows": rows, "orientation": "col"}

    return None


def find_estimate_table(pdf, debug=False):
    """모든 페이지의 표를 훑어 매출액/영업이익이 함께 있는 표를 찾는다(행/열 방향 모두 시도).
    기본(선 기반) 추출로 못 찾으면 테두리 없는 표에 대비해 텍스트 정렬 기반 추출도 재시도한다.
    반환: (page_no, {"headers","rows"}) 또는 None
    """
    all_tables_seen = []  # 디버그용: (page_no, strategy, table 첫 행/첫 열 미리보기)

    strategies = [None, {"vertical_strategy": "text", "horizontal_strategy": "text"}]
    for strategy in strategies:
        for page_no, page in enumerate(pdf.pages, start=1):
            try:
                tables = page.extract_tables(table_settings=strategy) if strategy else page.extract_tables()
            except Exception:
                continue
            for table in tables:
                if not table or len(table) < 2:
                    continue
                if debug and len(all_tables_seen) < 25:
                    all_tables_seen.append({
                        "page": page_no,
                        "strategy": "text" if strategy else "lines(기본)",
                        "row_count": len(table),
                        "col_count": len(table[0]) if table[0] else 0,
                        "first_row": [_clean(c) for c in table[0]][:6],
                        "first_col": [_clean(row[0]) if row else "" for row in table[:6]],
                    })
                matched = _try_match_table(table)
                if matched:
                    return page_no, matched
        if strategy is None and debug:
            # 기본 전략으로 못 찾았을 때만 텍스트 전략도 시도 — 진행 상황 로그
            print(f"[DEBUG] 선 기반 추출로 미발견, 텍스트 정렬 기반 추출 재시도 중... (지금까지 발견한 표 {len(all_tables_seen)}개)", file=sys.stderr)

    if debug:
        print(f"[DEBUG] 두 전략 모두 실패. 문서 전체에서 발견된 표(최대 25개) 미리보기:", file=sys.stderr)
        for t in all_tables_seen:
            print(f"  page={t['page']} strategy={t['strategy']} {t['row_count']}x{t['col_count']} "
                  f"첫행={t['first_row']} 첫열={t['first_col']}", file=sys.stderr)
    return None


def find_target_price(full_text: str, debug=False):
    for pat in TARGET_PRICE_PATTERNS:
        m = re.search(pat, full_text)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                continue
    if debug:
        # "목표" 글자가 어디에 어떤 형태로 나오는지 문맥을 보여준다 (정규식이 실제 표기와
        # 다를 때 다음 라운드에서 바로 패턴을 고칠 수 있도록).
        hits = [m.start() for m in re.finditer("목표", full_text)][:5]
        if hits:
            print(f"[DEBUG] '목표' 문자열은 {len(hits)}곳(최대5개 표시)에서 발견됨:", file=sys.stderr)
            for pos in hits:
                ctx = full_text[max(0, pos-15):pos+40].replace("\n", "\\n")
                print(f"  ...{ctx}...", file=sys.stderr)
        else:
            print("[DEBUG] 문서 전체에 '목표'라는 글자 자체가 없음 — 목표주가가 이미지(그래픽)로 렌더링됐을 가능성이 있음", file=sys.stderr)
    return None


def debug_page_text_density(pdf, co: str, max_pages: int = 5):
    """페이지별 실제 추출 가능한 글자 수 vs 삽입된 이미지가 페이지 면적에서 차지하는 비율을 찍는다.
    글자 수는 적은데 이미지 비율이 크면 그 페이지는 표/텍스트가 이미지(그래픽)로 박혀 있어서
    지금 방식(텍스트 레이어 추출)으로는 원천적으로 못 읽는다는 뜻 — 이 경우 OCR이 필요하다.
    """
    print(f"[DEBUG] {co}: 페이지별 텍스트/이미지 밀도 (최대 {max_pages}페이지)", file=sys.stderr)
    for page in pdf.pages[:max_pages]:
        area = (page.width or 1) * (page.height or 1)
        img_area = 0.0
        for im in page.images:
            w = max(0.0, (im.get("x1", 0) - im.get("x0", 0)))
            h = max(0.0, (im.get("y1", 0) - im.get("y0", 0)))
            img_area += w * h
        ratio = min(1.0, img_area / area) if area else 0.0
        print(
            f"  page={page.page_number} 글자수={len(page.chars)} 이미지개수={len(page.images)} "
            f"이미지면적비율={ratio:.0%}",
            file=sys.stderr,
        )


def process_report(session: requests.Session, co: str, report_date: str, pdf_id: str, debug_first: bool):
    if pdfplumber is None:
        print("[ERROR] pdfplumber가 설치되어 있지 않습니다.", file=sys.stderr)
        return None
    try:
        pdf_bytes = fetch_pdf_bytes(session, pdf_id)
    except Exception as e:
        print(f"[WARN] {co}: PDF 다운로드 실패 ({e})", file=sys.stderr)
        return None

    import io
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            full_text = "\n".join((p.extract_text() or "") for p in pdf.pages)
            target_price = find_target_price(full_text, debug=debug_first)
            found = find_estimate_table(pdf, debug=debug_first)
            if debug_first:
                print(f"[DEBUG] {co}: PDF 페이지수={len(pdf.pages)}, 텍스트 길이={len(full_text)}, 목표주가 파싱={target_price}", file=sys.stderr)
                if found is not None:
                    print(f"[DEBUG] {co}: 표 발견 page={found[0]}, orientation={found[1]['orientation']}, headers={found[1]['headers']}, rows={found[1]['rows']}", file=sys.stderr)
                else:
                    debug_page_text_density(pdf, co)
    except Exception as e:
        print(f"[WARN] {co}: PDF 파싱 실패 ({e})", file=sys.stderr)
        return None

    if found is None:
        print(f"[WARN] {co}: 실적 추정 표를 찾지 못해 이번 회차는 건너뜁니다 (목표주가만 있으면 그것만 반영).", file=sys.stderr)
        table_payload = None
    else:
        page_no, matched = found
        table_payload = {"headers": matched["headers"], "rows": matched["rows"], "source_page": page_no}

    if table_payload is None and target_price is None:
        return None

    return {
        "report_date": report_date,
        "target_price_krw": target_price,
        "table": table_payload,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", default="reports.json")
    ap.add_argument("--out", default="report_estimates.json")
    args = ap.parse_args()

    with open(args.reports, encoding="utf-8") as f:
        reports_data = json.load(f)
    rows = reports_data.get("reports", [])

    # 기업별 최신(날짜 기준) pdf 첨부가 있는 리포트만 추림
    latest_by_co = {}
    for r in rows:
        if not r.get("pdf"):
            continue
        co = r["co"]
        if co not in latest_by_co or r["d"] > latest_by_co[co]["d"]:
            latest_by_co[co] = r

    print(f"PDF 첨부가 있는 기업 {len(latest_by_co)}개 대상으로 실적 추정 추출 시작")

    # 기존 파일 로드 (부분 실패 시 값 유지를 위해)
    try:
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f).get("stocks", {})
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    result = dict(existing)
    ok = 0
    debug_budget = 2  # 처음 N개 기업만 상세 디버그 로그(표 전체 미리보기 등)를 남긴다 — 로그가 너무 길어지지 않게
    for co, r in latest_by_co.items():
        # 이미 같은 날짜 보고서로 추출된 값이 있으면 재다운로드 생략 (API 부담 감소)
        if co in existing and existing[co].get("report_date") == r["d"]:
            continue
        debug_this = debug_budget > 0
        parsed = process_report(session, co, r["d"], r["pdf"], debug_this)
        if debug_this:
            debug_budget -= 1
        if parsed is not None:
            result[co] = parsed
            ok += 1
            print(f"{co}: {r['d']} 보고서 기준 추출 완료 (목표주가={parsed['target_price_krw']}, 표={'O' if parsed['table'] else 'X'})")

    if ok == 0 and not existing:
        print("[ERROR] 추출된 기업이 0개이고 기존 데이터도 없어 파일을 생성하지 않습니다.", file=sys.stderr)
        sys.exit(1)

    payload = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "미래에셋증권 리포트 PDF · 실적 추정 표(매출액/영업이익 포함 표 자동 탐지) + 목표주가 텍스트 추출",
        "count": len(result),
        "stocks": result,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} ({ok}개 신규/갱신, 총 {len(result)}개)")


if __name__ == "__main__":
    main()
