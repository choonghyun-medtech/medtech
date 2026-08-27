#!/usr/bin/env python3
"""
한국관광 데이터랩(datalab.visitkorea.or.kr)의 "전체 방한 외래관광객" 화면
(getForTourForm.do, NAT_CD="999"=글로벌)에서 방한 외국인 월별 총 방문자수와, 고정 4개국
(중국·일본·대만·미국)별 월별 방문자수를 가져와 visitor_stats.json으로 저장한다.
산업데이터 탭의 "방한 외국인 월별 추이 / 국가별 방한 외국인" 차트가 이 파일을 읽는다.

- API: scrape_medical_tour.py와 동일하게, 데이터랩 사이트 자신이 화면을 그릴 때 쓰는 내부
  엔드포인트를 그대로 호출한다(로그인/세션 쿠키 불필요, 무료).
    POST https://datalab.visitkorea.or.kr/visualize/getTempleteData.do
    파라미터: qid, NAT_CD, BASE_YM1/BASE_YM2(YYYYMM), srchAreaDate="1"(월간)
  qid 2종(2026-08-27 실측 확인 — nattourform.js 및 실제 API 응답으로 직접 검증):
    NAT_08_01_004 : 방한 외래관광객 "월별" 추이 — BASE_YM1~BASE_YM2 구간 안의 달마다 1 row
                    (BASE_DATE, PSON_NUM=방문자수)를 준다. **주의**: NAT_CD를 특정 국가
                    코드로 바꿔도 응답이 전혀 달라지지 않는다(실측 확인 — 이 페이지의 실제
                    UI 로직도 natCd가 "999"가 아니면 이 qid를 쓰지 않고 다른 화면
                    getForTourDashForm.do로 이동해버린다). 그래서 총 방문자수(글로벌)만
                    이 qid로 조회한다.
    NAT_08_01_012 : 방한여행 요약(국적별) — 이것도 NAT_08_01_011(대륙별)과 같은 "기간 전체
                    합산 스냅샷"이라, 국가별 "월별" 10년치가 필요하면 달마다
                    (BASE_YM1=BASE_YM2=그 달) 하나씩 호출해야 한다. 응답은 그 기간의
                    **상위 5개국만**(NAT_NM/NAT_CD/TOU_NUM) 준다 — 국가 개수를 고를 수
                    없고 항상 5개까지만 나온다.

- 고정 4개국 선정(2026-08-27 확정): NAT_08_01_012(그 달 상위 5개국 스냅샷)를 2019~2026년
  여러 시점으로 직접 조회해 검증한 결과, 중국·일본·대만·미국은 거의 항상 상위 5위 안에
  들지만(10년 119개월 중 이탈 0~33개월, 25% 미만) "5번째 자리"는 홍콩으로 고정해도(72개월
  이탈, 60%) 태국으로 고정해도(100개월 이탈, 84%) 매우 자주 빠졌다 — 특정 국가의 문제가
  아니라 5위 자체가 여러 나라가 계속 자리를 바꾸는 경계 순위라서, 이 API(상위 5개국까지만
  주는 스냅샷)로는 "고정된 5번째 국가"를 안정적으로 얻을 수 없다는 구조적 한계다. 그래서
  안정적으로 거의 항상 잡히는 4개국(중국/일본/대만/미국)만 고정하고, 그 외 전부(5위 밖
  국가는 물론, 애초에 그 달 top5에 든 다른 나라까지)를 "기타"로 묶는다.
  → 이 스크립트는 고정 4개국(china/japan/taiwan/usa)의 NAT_CD가 그 달의 응답에 들어있으면
    그 값을 쓰고, 없으면(극히 드물게 4위 밖으로 밀린 달) 0으로 둔다. "기타"는 전체
    (NAT_CD=999, NAT_08_01_004) - 지정 4개국 합으로 계산하므로, 어느 경우든 총합(4개국 +
    기타)은 항상 정확히 전체 방문자수와 일치한다.

- 조회 범위·호출 횟수 절약: 총 방문자수(NAT_08_01_004)는 매번 120개월 전체를 한 번의
  요청으로 새로 받는다(가볍다). 국가별(NAT_08_01_012)은 달마다 개별 호출이 필요해 매번
  120번을 다시 부르면 낭비이므로, 이미 저장된 과거 달은 그대로 재사용하고 "최근
  COUNTRY_REFRESH_MONTHS개월"만 매번 다시 받아 최신 반영분을 갱신한다(반영 시차 대비,
  대륙별 수집 때와 동일한 패턴). 처음 실행할 때만 120번을 다 호출해 10년치를 백필한다.

- 인증 불필요 · 무료. 이 단계도 "보강" 단계라, 총 방문자수 호출이 실패하면 기존
  visitor_stats.json을 그대로 보존하고 경고만 남긴 채 종료한다(scrape_medical_tour.py와 동일
  패턴). 국가별은 개별 달 단위로 실패할 수 있어, 실패한 달은 기존에 저장돼 있던 값이 있으면
  그걸 유지하고 없으면 그냥 빠진 채로 둔다.

사용법:
    python scrape_visitor_stats.py --out visitor_stats.json
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
REQUEST_DELAY_SEC = 0.15
LOOKBACK_MONTHS = 120  # 10년치 월별
COUNTRY_REFRESH_MONTHS = 3  # 반영 시차 대비 매번 다시 받아오는 최근 개월수

NAT_CD_GLOBAL = "999"
QID_TOTAL_MONTHLY = "NAT_08_01_004"
QID_COUNTRY_SNAPSHOT = "NAT_08_01_012"

# 방한 외국인 고정 4개국(2026-08-27 확정 — 실측 검증 결과 거의 항상 상위 5위 안에 드는
# 국가만 선정, 위 docstring 참고) — natCd는 ISO 3166-1 numeric(scrape_medical_tour.py의
# 중국/일본/미국 코드와 동일 체계).
TOP_COUNTRIES = [
    {"key": "china", "label": "중국", "natCd": "156"},
    {"key": "japan", "label": "일본", "natCd": "392"},
    {"key": "taiwan", "label": "대만", "natCd": "158"},
    {"key": "usa", "label": "미국", "natCd": "840"},
]
TOP_COUNTRY_LABELS = [c["label"] for c in TOP_COUNTRIES]

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://datalab.visitkorea.or.kr/datalab/portal/nat/getForTourForm.do",
    "User-Agent": "Mozilla/5.0 (compatible; medtech-dashboard-bot/1.0)",
}


def yyyymm_add_months(yyyymm: str, months: int) -> str:
    y, m = int(yyyymm[:4]), int(yyyymm[4:])
    total = y * 12 + (m - 1) + months
    return f"{total // 12:04d}{(total % 12) + 1:02d}"


def api_post(qid, nat_cd, base_ym1, base_ym2, debug=False):
    """실패하면 (None, True) 반환 — "데이터 없음"과 "호출 실패"를 구분한다."""
    params = {
        "qid": qid,
        "NAT_CD": nat_cd,
        "SGG_CD": "",
        "SGG_NM": "",
        "BASE_YM1": base_ym1,
        "BASE_YM2": base_ym2,
        "srchAreaDate": "1",
    }
    for attempt in range(REQUEST_RETRIES):
        try:
            resp = requests.post(API_URL, data=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            return data.get("list", []), False
        except (requests.RequestException, ValueError) as e:
            if debug or attempt == REQUEST_RETRIES - 1:
                print(f"[WARN] {qid} ({base_ym1}~{base_ym2}) 호출 실패(시도 {attempt+1}/{REQUEST_RETRIES}): {e}",
                      file=sys.stderr)
            if attempt < REQUEST_RETRIES - 1:
                time.sleep(REQUEST_RETRY_BACKOFF_SEC * (attempt + 1))
    return None, True


def fetch_total_monthly(base_ym1, base_ym2, debug=False):
    rows, failed = api_post(QID_TOTAL_MONTHLY, NAT_CD_GLOBAL, base_ym1, base_ym2, debug=debug)
    if failed or not rows:
        return None
    monthly = []
    for row in rows:
        ym = row.get("BASE_DATE", "")
        if len(ym) != 6:
            continue
        monthly.append({"ym": f"{ym[:4]}-{ym[4:]}", "visitors": round(row.get("PSON_NUM", 0))})
    monthly.sort(key=lambda r: r["ym"])
    return monthly


def fetch_country_month(yyyymm, debug=False):
    """그 달의 상위 5개국(NAT_NM/NAT_CD/TOU_NUM) 스냅샷에서 TOP_COUNTRIES에 해당하는
    국가만 {label: 방문자수}로 뽑아 반환한다. 지정 5개국 중 그 달 상위 5위 밖으로 밀린
    국가는 값이 없으므로 결과 dict에서 빠진다(호출 쪽에서 0으로 채운다)."""
    rows, failed = api_post(QID_COUNTRY_SNAPSHOT, NAT_CD_GLOBAL, yyyymm, yyyymm, debug=debug)
    if failed or rows is None:
        return None
    by_cd = {row.get("NAT_CD"): round(row.get("TOU_NUM", 0)) for row in rows}
    return {c["label"]: by_cd[c["natCd"]] for c in TOP_COUNTRIES if c["natCd"] in by_cd}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="visitor_stats.json")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    existing_countries = {r.get("ym"): r.get("countries", {}) for r in existing.get("countryMonthly", [])}

    now = datetime.datetime.now(datetime.timezone.utc)
    this_ym = now.strftime("%Y%m")
    start_ym = yyyymm_add_months(this_ym, -(LOOKBACK_MONTHS - 1))

    print("[INFO] 전체 방한 외래관광객 월별 추이 조회 시작", file=sys.stderr)
    monthly = fetch_total_monthly(start_ym, this_ym, debug=args.debug)
    if monthly is None:
        print("[WARN] 월별 추이 조회 실패 — 기존 값을 유지합니다.", file=sys.stderr)
        monthly = existing.get("monthly", [])
    else:
        print(f"[INFO] 월별 추이 {len(monthly)}개월치 확보", file=sys.stderr)
    total_by_ym = {r["ym"]: r["visitors"] for r in monthly}

    target_yms = []
    yyyymm = start_ym
    while yyyymm <= this_ym:
        target_yms.append(yyyymm)
        yyyymm = yyyymm_add_months(yyyymm, 1)
    refresh_set = set(target_yms[-COUNTRY_REFRESH_MONTHS:])

    countries_by_ym = {}
    fetched, reused, failed_count = 0, 0, 0
    for yyyymm in target_yms:
        ym_fmt = f"{yyyymm[:4]}-{yyyymm[4:]}"
        need_fetch = yyyymm in refresh_set or ym_fmt not in existing_countries
        if not need_fetch:
            countries_by_ym[ym_fmt] = existing_countries[ym_fmt]
            reused += 1
            continue
        result = fetch_country_month(yyyymm, debug=args.debug)
        time.sleep(REQUEST_DELAY_SEC)
        if result is not None:
            countries_by_ym[ym_fmt] = result
            fetched += 1
        elif ym_fmt in existing_countries:
            countries_by_ym[ym_fmt] = existing_countries[ym_fmt]
            reused += 1
        else:
            failed_count += 1

    print(f"[INFO] 국가별: 신규/갱신 {fetched}개월, 기존 재사용 {reused}개월, 실패 {failed_count}개월",
          file=sys.stderr)

    country_monthly = []
    for ym in sorted(total_by_ym):
        found = countries_by_ym.get(ym, {})
        countries = {label: found.get(label, 0) for label in TOP_COUNTRY_LABELS}
        top5_sum = sum(countries.values())
        countries["기타"] = max(total_by_ym[ym] - top5_sum, 0)
        country_monthly.append({"ym": ym, "countries": countries})

    if not monthly and not country_monthly:
        print("[ERROR] 월별/국가별 데이터를 하나도 확보하지 못해 기존 파일을 보존하고 종료합니다.",
              file=sys.stderr)
        sys.exit(1)

    payload = {
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "한국관광 데이터랩(datalab.visitkorea.or.kr) 방한 외래관광객 통계 · "
                  "getTempleteData.do 실시간 연동(NAT_08_01_004 월별 / NAT_08_01_012 국적별 상위5개국) · "
                  "기타 = 전체 - 4개국(중국·일본·대만·미국) 합",
        "monthly": monthly,
        "countryMonthly": country_monthly,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} (월별 {len(monthly)}개월, 국가별 {len(country_monthly)}개월)")


if __name__ == "__main__":
    main()
