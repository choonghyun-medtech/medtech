#!/usr/bin/env python3
"""
한국관광 데이터랩(datalab.visitkorea.or.kr)의 "의료관광 현황 > 진료과목별 의료소비 추이 /
국가별 의료소비 추이" 탭에서 외국인 의료 소비액·소비건수(전체 + 중국/일본/미국/태국 4개국)와
진료과목별 비율 추이를 가져와 medical_tour.json으로 저장한다. 산업데이터 탭의 "방한 외국인 ·
의료관광객 데이터" 섹션이 이 파일을 읽는다.

- API: 별도 공공데이터포털 API가 아니라, 데이터랩 사이트 자신이 화면을 그릴 때 쓰는 내부
  엔드포인트를 그대로 호출한다(2026-08-25 확인 — 로그인/세션 쿠키 없이도 200 OK로 정상
  JSON을 준다. Referer 헤더도 필수는 아니었지만 정상적인 브라우저 요청처럼 보이도록 유지).
    POST https://datalab.visitkorea.or.kr/visualize/getTempleteData.do
    파라미터: qid, NAT_CD(국가코드, 전체="000"), BASE_YM1/BASE_YM2(YYYYMM), srchAreaDate="1"(월간), tabDiv
  qid는 화면 탭마다 다르다(medical_tour.js의 chart_init_* 함수들에서 확인):
    전체(tabDiv=2, NAT_CD=000)  : 진료과목별 소비액 비율 BY_TH_MEDIC_002_002_AMT / 소비건수 비율 BY_TH_MEDIC_002_002_CNT
    국가별(tabDiv=3, NAT_CD=국가코드) : 진료과목별 소비액 비율 BY_TH_MEDIC_003_001_AMT / 소비건수 비율 BY_TH_MEDIC_003_001_CNT

- ⚠️ 월별 총액·총건수는 반드시 "전체 추이" 전용 qid(소비액: BY_TH_MEDIC_002_001_AMT /
  BY_TH_MEDIC_003_003_AMT, 소비건수: BY_TH_MEDIC_002_001_CNT / BY_TH_MEDIC_003_003_CNT)에서
  뽑는다 — 진료과목별 비율 qid(002_002/003_001)가 함께 주는 월별 합계 필드(MCLS_SUM_AMT,
  CARD_COMPT_AMT_TOT 등)는 쓰지 않는다. 두 가지를 실측 비교해서 확인한 이유:
    1) 소비건수: 비율 qid의 합계 필드는 소수점이 섞인 근사치였다(예: 중국 202407이
       18148.3) — 전용 qid는 정수 18148.0을 준다(2026-08-25 사용자 지적으로 발견).
       데이터랩 사이트 자신도 "외국인 소비건수(전체) 추이" 화면(callBackChart_03_02,
       medical_tour_chart.js)에 전용 qid를 쓴다.
    2) 소비액: 처음엔 전용 qid가 비율 qid 합계의 정확히 1/1000 값을 줘서 "단위 버그"로
       오판했으나(예: 002_001_AMT가 82,842,742를 줄 때 002_002_AMT 합계는
       82,842,742,272.7), 실제로는 버그가 아니라 데이터랩 사이트가 이 전용 qid의 값을
       처음부터 "천원" 단위로 설계해 그대로 쓰기 때문이다(medical_tour_chart.js의
       callBackChart_03_01/callBackChart_11이 CARD_COMPT_AMT(_TOT)를 unit:'천원'으로
       그대로 표시 — 2026-08-25 사이트 JS 재확인으로 정정). 그래서 monthly.amt는 원(₩)이
       아니라 "천원" 단위로 저장하고, index.html도 억원 환산 없이 그대로
       "1,234,567,890천원" 형식으로 보여준다(2026-08-25 사용자 요청 — 사이트 표기와 통일).

- 조회 범위: "소비액·소비건수 추이"(monthly)와 "진료과목별 비율 추이"(deptAmt/deptCnt)는
  둘 다 최대 10개년(120개월)을 요청한다(2026-08-25 monthly 최초 확대 → 2026-08-31
  deptAmt/deptCnt도 동일 범위로 확대, 피부과 탭 장기 추이 차트 신설 요청). 실제 데이터는
  2026-08-25 확인 기준 2018-01부터만 있어(그 이전은 API가 빈 응답) 결과적으로 약 8.5년치가
  저장된다 — 데이터가 더 쌓이면 자동으로 늘어난다.
    · index.html의 "진료과목별 비율 추이"(전체 8개 과목 누적 막대) 차트는 저장된
      deptAmt/deptCnt 중 최신 12개월만 그린다(현행 유지, 2026-08-25 요청 — 비율 차트만
      1개년 유지).
    · "피부과" 탭의 소비액/소비건수/ASP 차트는 deptAmt/deptCnt에서 dept === '피부과'만
      뽑아 저장된 전체 기간(최대 10개년)을 그대로 그린다.
  ⚠️ deptAmt/deptCnt의 amt는 monthly.amt와 단위가 다르다 — monthly.amt는 "천원"이지만
  deptAmt.amt/deptCnt 쪽 qid(BY_TH_MEDIC_002_002_AMT 등, "진료과목별 비율" 전용)는 "원"
  단위로 내려온다(2026-08-31 실측 확인 — 같은 달 deptAmt 8개 과목 합계가 monthly.amt*1000과
  일치). index.html에서 deptAmt.amt를 표시할 때 이 차이를 놓치면 축 단위가 1000배 어긋난다.

- 인증 불필요 · 무료. 이 단계도 "보강" 단계라, 호출이 전부 실패하면 기존 medical_tour.json을
  그대로 보존하고 경고만 남긴 채 0으로 종료한다(export_data.json 스크립트와 동일한 패턴).

사용법:
    python scrape_medical_tour.py --out medical_tour.json
"""
import argparse
import datetime
import json
import sys
import time

import requests

API_URL = "https://datalab.visitkorea.or.kr/visualize/getTempleteData.do"
REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3
REQUEST_RETRY_BACKOFF_SEC = 3
REQUEST_DELAY_SEC = 0.2
LOOKBACK_MONTHS_RATIO = 120  # 진료과목별 비율(deptAmt/deptCnt) 조회 범위 — 10개년(2026-08-31 확대, 피부과 탭 장기 추이용). index.html의 진료과목별 비율 차트는 이 중 최신 12개월만 표시, 피부과 탭은 전체 기간 표시
LOOKBACK_MONTHS_TREND = 120  # 소비액·소비건수 추이(monthly) 조회 범위 — 10개년(2026-08-25 요청). 실제 데이터는 2018-01부터만 있음

# key/label/natCd — natCd="000"은 전체(글로벌). 나머지는 selectComNatList.do로 확인한 코드
# (2026-08-25): 중국=156, 일본=392, 미국=840, 태국=764. 대만=158(scrape_visitor_stats.py의
# TOP_COUNTRIES와 동일 체계) — 2026-08-31 "국가별 의료관광 소비 침투율" 차트에서 대만 방한객
# 데이터(visitor_stats.json)와 짝지을 분자 데이터가 필요해 추가.
TABS = [
    {"key": "all", "label": "전체", "natCd": "000"},
    {"key": "china", "label": "중국", "natCd": "156"},
    {"key": "japan", "label": "일본", "natCd": "392"},
    {"key": "usa", "label": "미국", "natCd": "840"},
    {"key": "thailand", "label": "태국", "natCd": "764"},
    {"key": "taiwan", "label": "대만", "natCd": "158"},
]

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://datalab.visitkorea.or.kr/datalab/portal/theme/getMedicalTourSearch.do",
    "User-Agent": "Mozilla/5.0 (compatible; medtech-dashboard-bot/1.0)",
}


def yyyymm_add_months(yyyymm: str, months: int) -> str:
    y, m = int(yyyymm[:4]), int(yyyymm[4:])
    total = y * 12 + (m - 1) + months
    return f"{total // 12:04d}{(total % 12) + 1:02d}"


def api_post(qid, nat_cd, base_ym1, base_ym2, tab_div, debug=False):
    """실패하면 (None, True)를 반환 — "이 기간에 데이터가 없다"와 "호출 자체가 실패했다"를
    구분해야 기존 파일 보존 여부를 제대로 판단할 수 있다(scrape_export_data.py와 동일 패턴)."""
    params = {
        "qid": qid,
        "NAT_CD": nat_cd,
        "SGG_CD": "",
        "SGG_NM": "",
        "BASE_YM1": base_ym1,
        "BASE_YM2": base_ym2,
        "srchAreaDate": "1",
        "tabDiv": tab_div,
    }
    for attempt in range(REQUEST_RETRIES):
        try:
            resp = requests.post(API_URL, data=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            return data.get("list", []), False
        except (requests.RequestException, ValueError) as e:
            if debug or attempt == REQUEST_RETRIES - 1:
                print(f"[WARN] {qid} (NAT_CD={nat_cd}) 호출 실패(시도 {attempt+1}/{REQUEST_RETRIES}): {e}",
                      file=sys.stderr)
            if attempt < REQUEST_RETRIES - 1:
                time.sleep(REQUEST_RETRY_BACKOFF_SEC * (attempt + 1))
    return None, True


def fetch_tab(tab, ratio_ym1, trend_ym1, base_ym2, debug=False):
    """진료과목별 비율(AMT/CNT) qid 두 번(ratio_ym1~base_ym2, 10개년) + "전체 추이"
    전용 qid(소비액/소비건수) 두 번(trend_ym1~base_ym2, 10개년), 총 네 번 호출한다. 전체
    (natCd=000)는 tabDiv=2 + 002_002(비율)/002_001(전용) qid, 국가별은 tabDiv=3 +
    003_001(비율)/003_003(전용) qid — 응답 필드명이 서로 달라(MCLS_AMT_RATE vs RATE_AMT 등)
    아래에서 흡수한다. 월별 총액·총건수(monthly)는 반드시 전용 qid에서만 뽑는다(모듈
    docstring의 ⚠️ 참고 — 진료과목 비율 qid의 합계 필드는 총건수는 근사치이고 총액은 단위가
    다르다: 비율 qid는 "원", 전용 qid는 "천원"). 진료과목별 비율(dept_amt/dept_cnt)은
    비율 qid에서 그대로 뽑는다(amt/cnt는 "원" 단위 그대로 저장 — index.html에서 백만원
    환산 시 1,000,000으로 나눠야 한다).

    ratio_ym1과 trend_ym1은 2026-08-31부터 동일한 10개년 범위를 쓰지만(둘 다
    LOOKBACK_MONTHS=120), qid 자체가 다르고 응답 실패 여부도 따로 판단해야 하므로 여전히
    두 번씩 따로 호출한다."""
    is_all = tab["natCd"] == "000"
    tab_div = "2" if is_all else "3"
    qid_amt = "BY_TH_MEDIC_002_002_AMT" if is_all else "BY_TH_MEDIC_003_001_AMT"
    qid_cnt = "BY_TH_MEDIC_002_002_CNT" if is_all else "BY_TH_MEDIC_003_001_CNT"
    qid_amt_total = "BY_TH_MEDIC_002_001_AMT" if is_all else "BY_TH_MEDIC_003_003_AMT"
    qid_cnt_total = "BY_TH_MEDIC_002_001_CNT" if is_all else "BY_TH_MEDIC_003_003_CNT"

    rows_amt, failed_amt = api_post(qid_amt, tab["natCd"], ratio_ym1, base_ym2, tab_div, debug=debug)
    time.sleep(REQUEST_DELAY_SEC)
    rows_cnt, failed_cnt = api_post(qid_cnt, tab["natCd"], ratio_ym1, base_ym2, tab_div, debug=debug)
    time.sleep(REQUEST_DELAY_SEC)
    rows_amt_total, failed_amt_total = api_post(qid_amt_total, tab["natCd"], trend_ym1, base_ym2, tab_div, debug=debug)
    time.sleep(REQUEST_DELAY_SEC)
    rows_cnt_total, failed_cnt_total = api_post(qid_cnt_total, tab["natCd"], trend_ym1, base_ym2, tab_div, debug=debug)
    time.sleep(REQUEST_DELAY_SEC)

    if (failed_amt or failed_cnt or failed_amt_total or failed_cnt_total
            or not rows_amt or not rows_cnt or not rows_amt_total or not rows_cnt_total):
        return None

    amt_field = "CARD_COMPT_AMT" if is_all else "CARD_COMPT_AMT_SUM"
    amt_rate_field = "MCLS_AMT_RATE" if is_all else "RATE_AMT"
    amt_total_field = "CARD_COMPT_AMT" if is_all else "CARD_COMPT_AMT_TOT"
    cnt_field = "CARD_COMPT_CNT" if is_all else "CARD_COMPT_CNT_SUM"
    cnt_rate_field = "MCLS_CNT_RATE" if is_all else "RATE_CNT"
    cnt_total_field = "CARD_COMPT_CNT" if is_all else "CARD_COMPT_CNT_TOT"

    dept_amt = []
    for row in rows_amt:
        ym = row.get("BASE_YM", "")
        if len(ym) != 6:
            continue
        ym_fmt = f"{ym[:4]}-{ym[4:]}"
        dept_amt.append({
            "ym": ym_fmt,
            "dept": row.get("KTO_CATE_SCLS_NM", ""),
            "amt": round(row.get(amt_field, 0)),
            "ratio": row.get(amt_rate_field, 0),
            "rank": int(row.get("RN_AMT") or 0),
        })

    dept_cnt = []
    for row in rows_cnt:
        ym = row.get("BASE_YM", "")
        if len(ym) != 6:
            continue
        ym_fmt = f"{ym[:4]}-{ym[4:]}"
        dept_cnt.append({
            "ym": ym_fmt,
            "dept": row.get("KTO_CATE_SCLS_NM", ""),
            "cnt": round(row.get(cnt_field, 0)),
            "ratio": row.get(cnt_rate_field, 0),
            "rank": int(row.get("RN_CNT") or 0),
        })

    # monthly.amt는 "천원" 단위(데이터랩 사이트 표시와 동일), monthly.cnt는 건수 그대로.
    monthly_amt_tot = {}
    for row in rows_amt_total:
        ym = row.get("BASE_YM", "")
        if len(ym) != 6:
            continue
        ym_fmt = f"{ym[:4]}-{ym[4:]}"
        monthly_amt_tot[ym_fmt] = round(row.get(amt_total_field, 0))

    monthly_cnt_tot = {}
    for row in rows_cnt_total:
        ym = row.get("BASE_YM", "")
        if len(ym) != 6:
            continue
        ym_fmt = f"{ym[:4]}-{ym[4:]}"
        monthly_cnt_tot[ym_fmt] = round(row.get(cnt_total_field, 0))

    all_yms = sorted(set(monthly_amt_tot) | set(monthly_cnt_tot))
    monthly = [
        {"ym": ym, "amt": monthly_amt_tot.get(ym, 0), "cnt": monthly_cnt_tot.get(ym, 0)}
        for ym in all_yms
    ]
    dept_amt.sort(key=lambda r: (r["ym"], r["rank"]))
    dept_cnt.sort(key=lambda r: (r["ym"], r["rank"]))

    return {
        "key": tab["key"],
        "label": tab["label"],
        "natCd": tab["natCd"],
        "monthly": monthly,
        "deptAmt": dept_amt,
        "deptCnt": dept_cnt,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="medical_tour.json")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    existing_tabs = {t.get("key"): t for t in existing.get("tabs", [])}

    now = datetime.datetime.now(datetime.timezone.utc)
    this_ym = now.strftime("%Y%m")
    ratio_ym1 = yyyymm_add_months(this_ym, -(LOOKBACK_MONTHS_RATIO - 1))
    trend_ym1 = yyyymm_add_months(this_ym, -(LOOKBACK_MONTHS_TREND - 1))
    base_ym2 = this_ym

    tabs_out = []
    for tab in TABS:
        print(f"[INFO] {tab['label']}(NAT_CD={tab['natCd']}) 조회 시작", file=sys.stderr)
        result = fetch_tab(tab, ratio_ym1, trend_ym1, base_ym2, debug=args.debug)
        if result is None:
            print(f"[WARN] {tab['label']}: 데이터를 가져오지 못했습니다 — 이전 데이터를 유지합니다.",
                  file=sys.stderr)
            prev = existing_tabs.get(tab["key"])
            if prev:
                tabs_out.append(prev)
            continue
        tabs_out.append(result)
        print(f"[INFO] {tab['label']}: 월별 {len(result['monthly'])}개월치 확보", file=sys.stderr)

    if not tabs_out:
        print("[ERROR] 모든 탭 조회에 실패해 기존 medical_tour.json을 보존하고 종료합니다.", file=sys.stderr)
        sys.exit(1)

    payload = {
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "한국관광 데이터랩(datalab.visitkorea.or.kr) 외국인 의료관광 소비 통계 · "
                  "신한카드 외국인 카드소비 기준 · getTempleteData.do 실시간 연동",
        "monthlyAmtUnit": "천원",  # monthly[].amt 단위 — 데이터랩 사이트 표시와 동일(원 아님)
        "tabs": tabs_out,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} ({len(tabs_out)}개 탭)")


if __name__ == "__main__":
    main()
