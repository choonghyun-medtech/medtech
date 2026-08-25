#!/usr/bin/env python3
"""
한국관광 데이터랩(datalab.visitkorea.or.kr)의 "전체 방한 외래관광객" 화면
(getForTourForm.do, NAT_CD="999"=글로벌)에서 방한 외국인 월별 총 방문자수와 대륙별 방문자수를
가져와 visitor_stats.json으로 저장한다. 산업데이터 탭의 "방한 외국인 월별 추이 / 대륙별 방한
외국인" 차트가 이 파일을 읽는다.

- API: scrape_medical_tour.py와 동일하게, 데이터랩 사이트 자신이 화면을 그릴 때 쓰는 내부
  엔드포인트를 그대로 호출한다(로그인/세션 쿠키 불필요, 무료).
    POST https://datalab.visitkorea.or.kr/visualize/getTempleteData.do
    파라미터: qid, NAT_CD="999"(글로벌), BASE_YM1/BASE_YM2(YYYYMM), srchAreaDate="1"(월간)
  qid 2종(2026-08-25 실측 확인 — nattourform.js/nattourform_chart.js):
    NAT_08_01_004 : 방한 전체 외래관광객 "월별" 추이 — BASE_YM1~BASE_YM2 구간 안의 달마다
                    1 row(BASE_DATE, PSON_NUM=방문자수)를 준다. 기간 제한이 딱히 없어 보여서
                    120개월(10년)을 한 번의 요청으로 다 받아올 수 있었다.
    NAT_08_01_011 : 방한여행 요약 "대륙별" — 이건 월별 시계열이 아니라 BASE_YM1~BASE_YM2
                    구간 "전체 합산" 스냅샷 1건만 대륙마다 준다(CTNN_NM, TOU_NUM). 그래서
                    대륙별 "월별" 10년치가 필요하면 이 qid를 달마다(BASE_YM1=BASE_YM2=그 달)
                    하나씩 120번 호출해야 한다 — 단일 요청으로는 안 됨.

- 대륙 매핑: NAT_08_01_011 응답의 CTNN_NM은 아시아/아메리카/유럽/오세아니아/아프리카/교포/기타
  7개 그대로 온다(국가별 qid에는 대륙 코드가 안 실려 있어 국가→대륙 자체 매핑이 필요 없다).
  원본 7개 카테고리를 그대로 저장하고, 작은 카테고리(아프리카/교포/기타)를 하나로 묶어
  보여주는 건 index.html 쪽에서 한다(원본 데이터는 그대로 보존).

- 조회 범위·호출 횟수 절약: 총 방문자수(NAT_08_01_004)는 매번 120개월 전체를 한 번의 요청으로
  새로 받는다(가볍다). 대륙별(NAT_08_01_011)은 달마다 개별 호출이 필요해 매번 120번을 다시
  부르면 낭비이므로, 이미 저장된 과거 달은 그대로 재사용하고 "최근 CONTINENT_REFRESH_MONTHS
  개월"만 매번 다시 받아 최신 반영분을 갱신한다(카드/집계 반영 시차 대비). 처음 실행할 때만
  120번을 다 호출해 10년치를 백필한다.

- 인증 불필요 · 무료. 이 단계도 "보강" 단계라, 총 방문자수 호출이 실패하면 기존
  visitor_stats.json을 그대로 보존하고 경고만 남긴 채 종료한다(scrape_medical_tour.py와 동일
  패턴). 대륙별은 개별 달 단위로 실패할 수 있어, 실패한 달은 기존에 저장돼 있던 값이 있으면
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
CONTINENT_REFRESH_MONTHS = 3  # 반영 시차 대비 매번 다시 받아오는 최근 개월수

NAT_CD_GLOBAL = "999"
QID_TOTAL_MONTHLY = "NAT_08_01_004"
QID_CONTINENT_SNAPSHOT = "NAT_08_01_011"

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


def api_post(qid, base_ym1, base_ym2, debug=False):
    """실패하면 (None, True) 반환 — "데이터 없음"과 "호출 실패"를 구분한다."""
    params = {
        "qid": qid,
        "NAT_CD": NAT_CD_GLOBAL,
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
    rows, failed = api_post(QID_TOTAL_MONTHLY, base_ym1, base_ym2, debug=debug)
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


def fetch_continent_month(yyyymm, debug=False):
    rows, failed = api_post(QID_CONTINENT_SNAPSHOT, yyyymm, yyyymm, debug=debug)
    if failed or not rows:
        return None
    continents = {}
    for row in rows:
        name = row.get("CTNN_NM", "")
        if not name:
            continue
        continents[name] = round(row.get("TOU_NUM", 0))
    return continents or None


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
    existing_continent = {r.get("ym"): r.get("continents", {}) for r in existing.get("continentMonthly", [])}

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

    target_yms = []
    yyyymm = start_ym
    while yyyymm <= this_ym:
        target_yms.append(yyyymm)
        yyyymm = yyyymm_add_months(yyyymm, 1)
    refresh_set = set(target_yms[-CONTINENT_REFRESH_MONTHS:])

    continent_by_ym = {}
    fetched, reused, failed_count = 0, 0, 0
    for yyyymm in target_yms:
        ym_fmt = f"{yyyymm[:4]}-{yyyymm[4:]}"
        need_fetch = yyyymm in refresh_set or ym_fmt not in existing_continent
        if not need_fetch:
            continent_by_ym[ym_fmt] = existing_continent[ym_fmt]
            reused += 1
            continue
        result = fetch_continent_month(yyyymm, debug=args.debug)
        time.sleep(REQUEST_DELAY_SEC)
        if result is not None:
            continent_by_ym[ym_fmt] = result
            fetched += 1
        elif ym_fmt in existing_continent:
            continent_by_ym[ym_fmt] = existing_continent[ym_fmt]
            reused += 1
        else:
            failed_count += 1

    print(f"[INFO] 대륙별: 신규/갱신 {fetched}개월, 기존 재사용 {reused}개월, 실패 {failed_count}개월",
          file=sys.stderr)

    continent_monthly = [
        {"ym": ym, "continents": continent_by_ym[ym]}
        for ym in sorted(continent_by_ym)
    ]

    if not monthly and not continent_monthly:
        print("[ERROR] 월별/대륙별 데이터를 하나도 확보하지 못해 기존 파일을 보존하고 종료합니다.",
              file=sys.stderr)
        sys.exit(1)

    payload = {
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "한국관광 데이터랩(datalab.visitkorea.or.kr) 방한 외래관광객 통계 · "
                  "getTempleteData.do 실시간 연동(NAT_08_01_004 월별 / NAT_08_01_011 대륙별)",
        "monthly": monthly,
        "continentMonthly": continent_monthly,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} (월별 {len(monthly)}개월, 대륙별 {len(continent_monthly)}개월)")


if __name__ == "__main__":
    main()
