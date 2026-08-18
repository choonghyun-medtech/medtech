#!/usr/bin/env python3
"""
reports.json에 있는 각 기업의 "가장 최신" 리포트 PDF에서
실적 추정 테이블(매출액/영업이익/순이익 등 연도별 추정치)과 목표주가를 추출해
report_estimates.json으로 저장한다.

- 입력: reports.json (scrape_reports.py가 만든 파일, 기업별 최신 pdf 첨부 id 포함)
- PDF 원문 안의 "실적 추정" 표는 리포트마다 위치·표 번호·행 구성이 조금씩 다를 수 있어,
  "매출액"과 "영업이익" 행이 함께 들어있는 표를 찾는 방식으로 위치를 특정한다
  (Mirae Asset 리포트에서 이 두 항목은 거의 항상 실적 추정 표에 같이 들어간다는 가정).
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

TARGET_PRICE_PATTERNS = [
    r"목표주가[^\d]{0,10}([\d,]{4,9})\s*원",
    r"목표주가\s*\(원\)\s*([\d,]{4,9})",
]


def fetch_pdf_bytes(session: requests.Session, pdf_id: str) -> bytes:
    url = PDF_URL_TMPL.format(pdf=pdf_id)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.content


def find_estimate_table(pdf) -> tuple:
    """모든 페이지의 표를 훑어 매출액/영업이익 행이 함께 있는 표를 찾는다.
    반환: (page_no, headers, rows) 또는 None
    """
    for page_no, page in enumerate(pdf.pages, start=1):
        try:
            tables = page.extract_tables()
        except Exception:
            continue
        for table in tables:
            if not table or len(table) < 2:
                continue
            # 첫 열(라벨 열) 텍스트 모음
            row_labels = [str(row[0]).replace("\n", "") if row and row[0] else "" for row in table]
            if all(any(lbl in cell for cell in row_labels) for lbl in REQUIRED_ROW_LABELS):
                headers = [str(c).replace("\n", "") if c else "" for c in table[0]]
                rows = []
                for row in table[1:]:
                    label = str(row[0]).replace("\n", "") if row and row[0] else ""
                    values = [str(c).replace("\n", "") if c else "" for c in row[1:]]
                    if label:
                        rows.append({"label": label, "values": values})
                return page_no, headers, rows
    return None


def find_target_price(full_text: str):
    for pat in TARGET_PRICE_PATTERNS:
        m = re.search(pat, full_text)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


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
            target_price = find_target_price(full_text)
            found = find_estimate_table(pdf)
            if debug_first:
                print(f"[DEBUG] {co}: PDF 페이지수={len(pdf.pages)}, 목표주가 파싱={target_price}", file=sys.stderr)
                if found is None:
                    print(f"[DEBUG] {co}: 실적 추정 표(매출액/영업이익 포함)를 찾지 못함. 본문 앞부분:\n{full_text[:1500]}", file=sys.stderr)
                else:
                    print(f"[DEBUG] {co}: 표 발견 page={found[0]}, headers={found[1]}, rows={found[2]}", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] {co}: PDF 파싱 실패 ({e})", file=sys.stderr)
        return None

    if found is None:
        print(f"[WARN] {co}: 실적 추정 표를 찾지 못해 이번 회차는 건너뜁니다 (목표주가만 있으면 그것만 반영).", file=sys.stderr)
        table_payload = None
    else:
        page_no, headers, rows = found
        table_payload = {"headers": headers, "rows": rows, "source_page": page_no}

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
    debug_first = True
    for co, r in latest_by_co.items():
        # 이미 같은 날짜 보고서로 추출된 값이 있으면 재다운로드 생략 (API 부담 감소)
        if co in existing and existing[co].get("report_date") == r["d"]:
            continue
        parsed = process_report(session, co, r["d"], r["pdf"], debug_first)
        debug_first = False
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
