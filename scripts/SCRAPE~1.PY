#!/usr/bin/env python3
"""
미래에셋증권 리서치 리포트 게시판에서 특정 작성자의 발간 이력을 긁어와
reports.json으로 저장한다.

- 대상 게시판: https://securities.miraeasset.com/bbs/board/message/list.do?categoryId=1521
- 필터: 구분=작성자, 검색어=AUTHOR (기본값 "김충현, CFA")
- 인코딩: 이 게시판은 EUC-KR(cp949) 기반이라 검색어도 cp949로 percent-encode 해야 한다.
- 로그인 불필요 (공개 게시판, PDF 다운로드도 인증 없이 200 응답 확인됨).

사용법:
    python scrape_reports.py                       # 기본 작성자로 실행
    python scrape_reports.py --author "홍길동"      # 다른 작성자 지정
    python scrape_reports.py --out other.json       # 출력 파일 지정
"""
import argparse
import datetime
import json
import re
import sys
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://securities.miraeasset.com/bbs/board/message/list.do"
CATEGORY_ID = "1521"  # 전체 리서치 리포트
PER_PAGE = 10
REQUEST_DELAY_SEC = 0.5  # 게시판에 부담 주지 않기 위한 페이지 간 대기


def build_url(author: str, curpage: int, start_year: int = 2010) -> str:
    encoded_author = quote(author.encode("cp949"))
    # 사이트의 검색기간 연도 드롭다운은 당해 연도까지만 지원한다(예: 2026년이면 2010~2026).
    # 다음 해(currentYear+1)를 보내면 서버가 유효하지 않은 값으로 보고 검색기간을
    # 알 수 없는 기본값(미래 날짜 1일 구간 등)으로 clamp해버려 0건이 나오는 문제가 있었다.
    end_year = datetime.datetime.utcnow().year
    params = {
        "categoryId": CATEGORY_ID,
        "searchType": "5",  # 5 = 작성자 검색
        "searchText": encoded_author,
        "searchStartYear": str(start_year),
        "searchStartMonth": "01",
        "searchStartDay": "01",
        "searchEndYear": str(end_year),
        "searchEndMonth": "12",
        "searchEndDay": "31",
        "listType": "1",
        "startId": "zzzzz~",
        "startPage": "1",
        "curPage": str(curpage),
        "direction": "1",
    }
    query = "&".join(
        f"{k}={v}" if k == "searchText" else f"{k}={v}" for k, v in params.items()
    )
    return f"{BASE_URL}?{query}"


def fetch_page(author: str, curpage: int) -> str:
    url = build_url(author, curpage)
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    if curpage == 1:
        print(f"[DEBUG] HTTP {resp.status_code}, 응답 길이 {len(resp.content)} bytes", file=sys.stderr)
    return resp.content.decode("cp949", errors="replace")


def get_total_count(html: str) -> int:
    m = re.search(r"전체건수\s*:\s*(\d+)", html)
    return int(m.group(1)) if m else 0


def parse_rows(html: str):
    soup = BeautifulSoup(html, "html.parser")
    rows = [tr for tr in soup.select("table tr") if tr.select_one('a[href^="javascript:view"]')]
    out = []
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        date_text = tds[0].get_text(strip=True)

        title_a = tr.select_one('a[href^="javascript:view"]')
        b_tag = title_a.find("b")
        header = b_tag.get_text(strip=True) if b_tag else title_a.get_text(strip=True)
        full_text = title_a.get_text(separator="\n").strip()
        parts = full_text.split("\n", 1)
        subtitle = parts[1].strip() if len(parts) > 1 else ""

        m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", header)
        if m:
            company = m.group(1).strip()
            inner = m.group(2).strip()
            if "/" in inner:
                code, opinion = inner.split("/", 1)
                code, opinion = code.strip(), opinion.strip()
            else:
                code, opinion = inner.strip(), ""
        else:
            # 종목 코드가 없는 산업 전망 리포트 등
            company, code, opinion = header.strip(), "", ""

        file_a = tr.select_one('a[href^="javascript:downConfirm"]')
        pdf_id = None
        if file_a:
            m2 = re.search(r"attachmentId=(\d+)", file_a.get("href", ""))
            if m2:
                pdf_id = m2.group(1)

        out.append(
            {
                "d": date_text,
                "co": company,
                "code": code,
                "op": opinion,
                "t": subtitle,
                "pdf": pdf_id,
            }
        )
    return out


def scrape(author: str):
    first_html = fetch_page(author, 1)
    total = get_total_count(first_html)
    if total == 0:
        print(f"[WARN] '{author}' 검색 결과가 0건입니다. 검색어 또는 사이트 접근 차단 여부를 확인하세요.", file=sys.stderr)
        print(f"[DEBUG] 요청 URL: {build_url(author, 1)}", file=sys.stderr)
        print(f"[DEBUG] 전체건수 텍스트 포함 여부: {'전체건수' in first_html}", file=sys.stderr)
        print(f"[DEBUG] 전체 응답 (아래부터):\n{'='*60}", file=sys.stderr)
        print(first_html, file=sys.stderr)
        print(f"{'='*60}\n[DEBUG] 응답 끝", file=sys.stderr)
        return None

    total_pages = max(1, -(-total // PER_PAGE))
    all_rows = parse_rows(first_html)
    print(f"page 1/{total_pages}: {len(all_rows)}건 (전체 {total}건)")

    for page in range(2, total_pages + 1):
        time.sleep(REQUEST_DELAY_SEC)
        html = fetch_page(author, page)
        rows = parse_rows(html)
        print(f"page {page}/{total_pages}: {len(rows)}건")
        all_rows.extend(rows)

    # 방어적 중복 제거 (날짜+회사명+제목 기준)
    seen = set()
    unique_rows = []
    for r in all_rows:
        key = (r["d"], r["co"], r["t"])
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(r)

    unique_rows.sort(key=lambda r: r["d"], reverse=True)
    return unique_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--author", default="김충현, CFA", help="검색할 작성자명 (게시판 표기와 정확히 일치해야 함)")
    ap.add_argument("--out", default="reports.json", help="출력 JSON 파일 경로")
    args = ap.parse_args()

    rows = scrape(args.author)
    if rows is None:
        # 검색 결과 0건 = 사이트 차단/구조 변경 등 이상 상황일 가능성이 높음.
        # 기존 reports.json을 빈 데이터로 덮어쓰지 않도록 파일을 건드리지 않고 실패로 종료한다.
        print("[ERROR] 검색 결과 0건이라 기존 reports.json을 보존하고 종료합니다 (파일 미변경).", file=sys.stderr)
        sys.exit(1)
    if len(rows) == 0:
        print("[ERROR] 파싱된 리포트가 0건이라 기존 reports.json을 보존하고 종료합니다 (파일 미변경).", file=sys.stderr)
        sys.exit(1)

    payload = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": f"securities.miraeasset.com 리서치 리포트 게시판 · 작성자: {args.author}",
        "count": len(rows),
        "reports": rows,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"저장 완료: {args.out} ({len(rows)}건)")


if __name__ == "__main__":
    main()
