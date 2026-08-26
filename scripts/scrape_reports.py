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
    python scrape_reports.py --authors "서미화,김승민" --out reports_bio.json
                                                     # 여러 작성자를 한 파일에 합쳐서 저장
                                                     # (각 행에 "author" 필드가 붙는다 —
                                                     # index.html '바이오' 드롭다운이 이 필드로 필터링)
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

# 브라우저처럼 보이도록 세션/헤더를 구성한다.
# (requests의 기본 요청은 쿠키·Referer·Accept류 헤더가 없어서, 실제 브라우저로
#  들어가면 검색이 되는데 스크립트로는 0건이 나오는 문제가 있었다 — 자동수집 방지
#  로직이 "브라우저가 아닌 것 같은" 요청을 걸러내는 것으로 추정.)
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://securities.miraeasset.com/bbs/board/message/list.do?categoryId=1521",
}


def make_session() -> requests.Session:
    """검색 전에 게시판 목록 페이지를 한 번 방문해 쿠키를 확보한 세션을 만든다."""
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    warm_url = f"{BASE_URL}?categoryId={CATEGORY_ID}"
    try:
        resp = session.get(warm_url, timeout=20)
        print(
            f"[DEBUG] 워밍업 요청 HTTP {resp.status_code}, 쿠키 {list(session.cookies.keys())}",
            file=sys.stderr,
        )
    except requests.RequestException as e:
        print(f"[DEBUG] 워밍업 요청 실패(무시하고 진행): {e}", file=sys.stderr)
    return session


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


def fetch_page(session: requests.Session, author: str, curpage: int) -> str:
    url = build_url(author, curpage)
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    if curpage == 1:
        print(f"[DEBUG] HTTP {resp.status_code}, 응답 길이 {len(resp.content)} bytes", file=sys.stderr)
    return resp.content.decode("cp949", errors="replace")


def get_total_count(html: str) -> int:
    # 실제 마크업은 "전체건수 :<span>79</span>건"처럼 숫자 앞에 <span> 태그가 낀다.
    # \s*만으로는 태그를 건너뛰지 못해 항상 0으로 오판했던 버그를 수정.
    m = re.search(r"전체건수[^\d]*(\d+)", html)
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
            elif inner.isdigit():
                code, opinion = inner, ""
            else:
                # "헬스케어 산업(비중확대)"처럼 괄호 안이 종목코드가 아니라 업종 의견
                # 하나만 있는 산업 리포트 — 코드가 아니라 투자의견으로 취급한다
                # (2026-08-26 전까지는 code 칸에 "비중확대"가 잘못 들어가고 있었음).
                code, opinion = "", inner
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


# ---- 복수저자(팀/산업) 리포트 보완 스캔 ----
# 2026-08-26 확인: "헬스케어 산업(비중확대)"처럼 여러 애널리스트가 공동 작성한 산업
# 리포트는 게시판 저자란에 "김충현, CF..."처럼 표시되지만(다른 공동저자와 합쳐진 문자열이
# 잘려 보임), 작성자 검색(searchType=5)은 저자 필드 완전일치라서 이런 복수저자 리포트가
# 영원히 검색에서 빠진다. 이를 보완하기 위해 최근 N일치 전체 게시판(저자 필터 없음)을
# 따로 훑어, 행별 저자란 텍스트에 대상 애널리스트 이름이 부분 일치하면 결과에 합친다.
RECENT_TEAM_SCAN_DAYS = 30


def build_general_url(curpage: int, start_date: datetime.date, end_date: datetime.date) -> str:
    params = {
        "categoryId": CATEGORY_ID,
        "searchStartYear": str(start_date.year),
        "searchStartMonth": f"{start_date.month:02d}",
        "searchStartDay": f"{start_date.day:02d}",
        "searchEndYear": str(end_date.year),
        "searchEndMonth": f"{end_date.month:02d}",
        "searchEndDay": f"{end_date.day:02d}",
        "listType": "1",
        "startId": "zzzzz~",
        "startPage": "1",
        "curPage": str(curpage),
        "direction": "1",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{BASE_URL}?{query}"


def parse_rows_with_author(html: str):
    """parse_rows와 동일한 필드를 뽑되, 저자란(마지막 td) 텍스트도 함께 반환한다.
    저자 필터 없이 전체 게시판을 훑을 때 행별로 부분 일치 여부를 판단하기 위함."""
    rows = parse_rows(html)
    soup = BeautifulSoup(html, "html.parser")
    trs = [tr for tr in soup.select("table tr") if tr.select_one('a[href^="javascript:view"]')]
    for row, tr in zip(rows, trs):
        tds = tr.find_all("td")
        row["_author_text"] = tds[-1].get_text(strip=True) if tds else ""
    return rows


def scrape_recent_team_reports(session: requests.Session, author_hint: str, days: int = RECENT_TEAM_SCAN_DAYS):
    """author_hint(예: "김충현")가 저자란에 부분 일치하는, 최근 days일 이내 전체
    게시판(저자 필터 없음) 리포트를 모아 반환한다. 사이트 접근 실패는 조용히 빈 목록으로
    처리 — 이 보완 스캔이 실패해도 본 검색 결과(reports.json)에는 영향 없어야 한다."""
    end_date = datetime.datetime.utcnow().date()
    start_date = end_date - datetime.timedelta(days=days)
    all_rows = []
    curpage = 1
    total_pages = 1
    while curpage <= total_pages:
        url = build_general_url(curpage, start_date, end_date)
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[WARN] 팀 리포트 보완 스캔 실패(page={curpage}): {e}", file=sys.stderr)
            break
        html = resp.content.decode("cp949", errors="replace")
        if curpage == 1:
            total = get_total_count(html)
            if total == 0:
                break
            total_pages = max(1, -(-total // PER_PAGE))
        all_rows.extend(parse_rows_with_author(html))
        curpage += 1
        if curpage <= total_pages:
            time.sleep(REQUEST_DELAY_SEC)

    matched = [r for r in all_rows if author_hint in r.get("_author_text", "")]
    for r in matched:
        r.pop("_author_text", None)
    return matched


def scrape(author: str):
    session = make_session()
    first_html = fetch_page(session, author, 1)
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
        html = fetch_page(session, author, page)
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

    # 복수저자(팀/산업) 리포트 보완 — 저자 완전일치 검색이라 놓치는 리포트를 추가로 합친다.
    author_hint = author.split(",")[0].strip()
    if author_hint:
        try:
            extra_rows = scrape_recent_team_reports(session, author_hint)
        except Exception as e:
            print(f"[WARN] 팀 리포트 보완 스캔 중 오류(무시하고 진행): {e}", file=sys.stderr)
            extra_rows = []
        existing_pdfs = {r["pdf"] for r in unique_rows if r.get("pdf")}
        added = 0
        for r in extra_rows:
            if r.get("pdf") and r["pdf"] not in existing_pdfs:
                unique_rows.append(r)
                existing_pdfs.add(r["pdf"])
                added += 1
        if added:
            print(f"[INFO] 팀/산업 리포트 보완 스캔으로 {added}건 추가", file=sys.stderr)

    unique_rows.sort(key=lambda r: r["d"], reverse=True)
    return unique_rows


def scrape_multi(authors):
    """여러 작성자를 순서대로 검색해 한 리스트로 합친다. 각 행에 "author" 필드를 붙여
    나중에 index.html이 작성자별로 필터링할 수 있게 한다. 작성자 한 명이 0건이어도
    (예: 최근 발간이 뜸한 애널리스트) 나머지 작성자는 계속 진행 — 전체가 0건일 때만 실패."""
    combined = []
    any_success = False
    for author in authors:
        print(f"[INFO] 작성자 '{author}' 검색 시작", file=sys.stderr)
        rows = scrape(author)
        if rows is None or len(rows) == 0:
            print(f"[WARN] 작성자 '{author}': 검색 결과 0건, 건너뜁니다", file=sys.stderr)
            continue
        any_success = True
        for r in rows:
            combined.append({**r, "author": author})
        time.sleep(REQUEST_DELAY_SEC)
    if not any_success:
        return None
    combined.sort(key=lambda r: r["d"], reverse=True)
    return combined


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--author", default="김충현, CFA", help="검색할 작성자명 (게시판 표기와 정확히 일치해야 함)")
    ap.add_argument("--authors", default=None, help="콤마로 구분된 여러 작성자명. 지정하면 --author는 무시되고, 각 행에 author 필드가 붙는다")
    ap.add_argument("--out", default="reports.json", help="출력 JSON 파일 경로")
    args = ap.parse_args()

    if args.authors:
        authors = [a.strip() for a in args.authors.split(",") if a.strip()]
        rows = scrape_multi(authors)
        source_label = f"securities.miraeasset.com 리서치 리포트 게시판 · 작성자: {', '.join(authors)}"
        err_label = args.out
    else:
        rows = scrape(args.author)
        source_label = f"securities.miraeasset.com 리서치 리포트 게시판 · 작성자: {args.author}"
        err_label = args.out

    if rows is None or len(rows) == 0:
        # 검색 결과 0건 = 사이트 차단/구조 변경 등 이상 상황일 가능성이 높음.
        # 기존 파일을 빈 데이터로 덮어쓰지 않도록 건드리지 않고 실패로 종료한다.
        print(f"[ERROR] 검색 결과 0건이라 기존 {err_label}을(를) 보존하고 종료합니다 (파일 미변경).", file=sys.stderr)
        sys.exit(1)

    payload = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source_label,
        "count": len(rows),
        "reports": rows,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"저장 완료: {args.out} ({len(rows)}건)")


if __name__ == "__main__":
    main()
