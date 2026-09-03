#!/usr/bin/env python3
"""
건강보험심사평가원 보건의료빅데이터개방시스템(opendata.hira.or.kr)의 "진료행위(검사/수술 등)
통계" 화면에서 EX871(심전도 감시[원격심박기술에 의한 감시][1일당], 입원)과
E6547(24시간혈압측정검사[1일당], 외래) 두 개 진료행위코드의 월별 총사용량·진료금액을 가져와
hira_procedure.json으로 저장한다. 산업데이터 탭의 "진료행위 통계" 섹션이 이 파일을 읽는다.

- 데이터 위치: https://opendata.hira.or.kr/op/opc/olapDiagBhvInfoTab1.do (사용자 안내 링크)
  이 화면은 별도 공공 API가 아니라 자체 서버 렌더링 화면이라, 화면이 그리는 검색폼을 그대로
  POST 요청으로 재현해서 결과 HTML 테이블을 파싱한다(2026-09-03 확인 — 로그인/세션 쿠키
  없이도 200 OK로 정상 데이터를 준다. Akamai 등 봇차단 없음 · curl_cffi 불필요).
    1) 코드→내부코드 조회: POST /op/opc/olapDiagBhvPList.do (searchWrd1=코드, flag=A)
       응답 JSON의 st5Cd(예: "1EX871")가 실제 조회 폼에 넣는 olapCd 값(앞에 "1" 접두).
    2) 조회 가능 최소/최대 진료년월: GET /op/opc/getDiagBhvYmList.do?olapCd=1EX871&tabGubun=Tab1
       → {"minym":"201001","maxym":"202601"} 형태. "진료년월별 자료는 최근 8개월 전까지의
       자료가 조회됩니다"라고 화면에 안내되어 있음(2026-09-03 기준 maxym=202601 확인 — 사용자가
       말한 "8개월 후행"과 일치).
    3) 실제 데이터 조회: POST /op/opc/olapDiagBhvInfoTab1.do
       파라미터: searchWrd, olapCd(=1EX871), olapCdNm, tabGubun=Tab1, gubun=D(진료년월 기준),
       sDiagYm/eDiagYm/sYm/eYm(조회 기간). 클라이언트 측 유효성 검사(medStcCommon.js
       formCheck())가 기간을 최대 36개월로 제한하므로(초과 시 사이트 자체가 막음이 아니라
       클라이언트 JS 체크라 서버가 실제로 몇 개월까지 받아주는지는 검증 안 했음 — 안전하게
       24개월 단위로 나눠서 요청), 이 스크립트도 CHUNK_MONTHS(24개월)씩 나눠 요청한다.
  응답은 성별(계/남/여) × 입원외래구분(계/소계/입원/외래) 조합의 월별 표(HTML table)다.
  "계" 성별은 소계 없이 전체 합계 한 줄만 나오고 입원/외래 구분이 없어서, 코드별로 필요한
  입원외래구분(EX871=입원, E6547=외래) 값은 남/외래(또는 입원) + 여/외래(또는 입원) 두 줄을
  더해서 만든다(2026-09-03 실측으로 테이블 구조 확인).

- 인증 불필요 · 무료(정부 공공데이터 포털, API 키 없이 화면 자체 엔드포인트 사용).

사용법:
    python scrape_hira_procedure.py --out hira_procedure.json
"""
import argparse
import datetime
import json
import re
import sys
import time

import requests

BASE_URL = "https://opendata.hira.or.kr"
POPUP_URL = BASE_URL + "/op/opc/olapDiagBhvPList.do"
YM_RANGE_URL = BASE_URL + "/op/opc/getDiagBhvYmList.do"
SEARCH_URL = BASE_URL + "/op/opc/olapDiagBhvInfoTab1.do"

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3
REQUEST_RETRY_BACKOFF_SEC = 3
REQUEST_DELAY_SEC = 0.3
CHUNK_MONTHS = 24  # 한 번에 조회할 최대 개월 수(폼 유효성 제한 36개월보다 여유있게)
START_YM = "202301"  # 수집 시작 진료년월(사용자 지정: 2023년 1월부터)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; medtech-dashboard-bot/1.0)",
    "X-Requested-With": "XMLHttpRequest",
}

# code -> 조회할 입원외래구분("입원" 또는 "외래")
CODES = [
    {"code": "EX871", "admission": "입원"},
    {"code": "E6547", "admission": "외래"},
]


def yyyymm_add_months(yyyymm: str, months: int) -> str:
    y, m = int(yyyymm[:4]), int(yyyymm[4:])
    total = y * 12 + (m - 1) + months
    return f"{total // 12:04d}{(total % 12) + 1:02d}"


def _request(method, url, debug=False, **kwargs):
    for attempt in range(REQUEST_RETRIES):
        try:
            resp = requests.request(method, url, headers=HEADERS, timeout=REQUEST_TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if debug or attempt == REQUEST_RETRIES - 1:
                print(f"[WARN] {url} 호출 실패(시도 {attempt+1}/{REQUEST_RETRIES}): {e}", file=sys.stderr)
            if attempt < REQUEST_RETRIES - 1:
                time.sleep(REQUEST_RETRY_BACKOFF_SEC * (attempt + 1))
    return None


def resolve_code(code, debug=False):
    """진료행위코드(예: EX871) -> (내부 olapCd(예: 1EX871), 항목명). 실패 시 (None, None)."""
    resp = _request("post", POPUP_URL, debug=debug, data={"searchWrd1": code, "flag": "A"})
    if resp is None:
        return None, None
    try:
        rows = resp.json()
    except ValueError:
        return None, None
    for row in rows:
        if row.get("st5Cd2") == code and row.get("st5Cd"):
            return row["st5Cd"], row.get("st5CdNm", code)
    return None, None


def fetch_ym_range(olap_cd, debug=False):
    """(minym, maxym) yyyymm 문자열 튜플. 실패 시 (None, None)."""
    resp = _request("get", YM_RANGE_URL, debug=debug, params={"olapCd": olap_cd, "tabGubun": "Tab1"})
    if resp is None or not resp.text.strip():
        return None, None
    try:
        data = resp.json()
    except ValueError:
        return None, None
    return data.get("minym"), data.get("maxym")


ROW_TH_RE = re.compile(r'<th(?![^>]*class="sky")[^>]*>\s*([^<]*?)\s*</th>')
TD_RE = re.compile(r'<td>\s*([^<]*?)\s*</td>')
MONTH_TH_RE = re.compile(r'<th scope="row" colspan="3">\s*(\d{4})년\s*(\d{2})월\s*</th>')


def parse_table(html, admission):
    """결과 HTML에서 (월라벨 목록, {ym: (usage, amount)}) 반환. admission별 남+여 합산."""
    anchor = html.find("tblType02 data webScroll")
    if anchor == -1:
        return []
    thead_start = html.find("<thead>", anchor)
    thead_end = html.find("</thead>", thead_start)
    tbody_start = html.find("<tbody>", thead_end)
    tbody_end = html.find("</tbody>", tbody_start)
    if -1 in (thead_start, thead_end, tbody_start, tbody_end):
        return []

    months = [f"{y}-{m}" for y, m in MONTH_TH_RE.findall(html[thead_start:thead_end])]
    if not months:
        return []

    tbody_html = html[tbody_start:tbody_end]
    monthly_sum = {ym: [0, 0] for ym in months}

    current_gender = None
    for tr_html in tbody_html.split("<tr>")[1:]:
        ths = [t for t in ROW_TH_RE.findall(tr_html) if t]
        if len(ths) >= 2:
            current_gender = ths[0]
            row_admission = ths[1]
        elif len(ths) == 1:
            row_admission = ths[0]
        else:
            continue
        if current_gender not in ("남", "여") or row_admission != admission:
            continue

        vals = TD_RE.findall(tr_html)
        for i, ym in enumerate(months):
            base = i * 3
            if base + 2 >= len(vals):
                break
            usage_s = vals[base + 1].replace(",", "").strip()
            amount_s = vals[base + 2].replace(",", "").strip()
            usage = int(usage_s) if usage_s else 0
            amount = int(amount_s) if amount_s else 0
            monthly_sum[ym][0] += usage
            monthly_sum[ym][1] += amount

    return [{"ym": ym, "usage": v[0], "amount": v[1]} for ym, v in monthly_sum.items()]


def fetch_code(code_cfg, end_ym, debug=False):
    code = code_cfg["code"]
    admission = code_cfg["admission"]

    olap_cd, name = resolve_code(code, debug=debug)
    if not olap_cd:
        print(f"[WARN] {code}: 내부코드 조회 실패", file=sys.stderr)
        return None
    time.sleep(REQUEST_DELAY_SEC)

    min_ym, max_ym = fetch_ym_range(olap_cd, debug=debug)
    if not max_ym:
        print(f"[WARN] {code}: 조회 가능 기간 확인 실패", file=sys.stderr)
        return None
    time.sleep(REQUEST_DELAY_SEC)

    start_ym = START_YM
    if min_ym and min_ym > start_ym:
        start_ym = min_ym
    last_ym = min(end_ym, max_ym)
    if start_ym > last_ym:
        print(f"[WARN] {code}: 조회 가능한 기간이 없습니다(start={start_ym}, max={max_ym})", file=sys.stderr)
        return None

    monthly = []
    chunk_start = start_ym
    while chunk_start <= last_ym:
        chunk_end = min(yyyymm_add_months(chunk_start, CHUNK_MONTHS - 1), last_ym)
        s_disp = f"{chunk_start[:4]}-{chunk_start[4:]}"
        e_disp = f"{chunk_end[:4]}-{chunk_end[4:]}"
        payload = {
            "searchWrd": name,
            "olapCd": olap_cd,
            "olapCdNm": name,
            "tabGubun": "Tab1",
            "gubun": "D",
            "sDiagYm": chunk_start,
            "eDiagYm": chunk_end,
            "sYm": s_disp,
            "eYm": e_disp,
        }
        resp = _request("post", SEARCH_URL, debug=debug, data=payload)
        if resp is None:
            print(f"[WARN] {code}: {s_disp}~{e_disp} 구간 조회 실패", file=sys.stderr)
            return None
        rows = parse_table(resp.text, admission)
        if not rows:
            print(f"[WARN] {code}: {s_disp}~{e_disp} 구간 파싱 결과 없음", file=sys.stderr)
            return None
        monthly.extend(rows)
        time.sleep(REQUEST_DELAY_SEC)
        chunk_start = yyyymm_add_months(chunk_end, 1)

    monthly.sort(key=lambda r: r["ym"])
    return {
        "code": code,
        "name": name,
        "admission": admission,
        "monthly": monthly,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="hira_procedure.json")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    existing_codes = {c.get("code"): c for c in existing.get("codes", [])}

    now = datetime.datetime.now(datetime.timezone.utc)
    end_ym = now.strftime("%Y%m")

    codes_out = []
    for code_cfg in CODES:
        print(f"[INFO] {code_cfg['code']}({code_cfg['admission']}) 조회 시작", file=sys.stderr)
        result = fetch_code(code_cfg, end_ym, debug=args.debug)
        if result is None:
            print(f"[WARN] {code_cfg['code']}: 데이터를 가져오지 못했습니다 — 이전 데이터를 유지합니다.",
                  file=sys.stderr)
            prev = existing_codes.get(code_cfg["code"])
            if prev:
                codes_out.append(prev)
            continue
        codes_out.append(result)
        print(f"[INFO] {code_cfg['code']}: 월별 {len(result['monthly'])}개월치 확보", file=sys.stderr)

    if not codes_out:
        print("[ERROR] 모든 코드 조회에 실패해 기존 hira_procedure.json을 보존하고 종료합니다.", file=sys.stderr)
        sys.exit(1)

    payload = {
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "건강보험심사평가원 보건의료빅데이터개방시스템(opendata.hira.or.kr) "
                  "진료행위(검사/수술 등) 통계 · olapDiagBhvInfoTab1.do 실시간 연동",
        "unit": {"usage": "총사용량(건)", "amount": "진료금액(천원)"},
        "codes": codes_out,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} ({len(codes_out)}개 코드)")


if __name__ == "__main__":
    main()
