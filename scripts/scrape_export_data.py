#!/usr/bin/env python3
"""
관세청 수출입무역통계(공공데이터포털 data.go.kr Open API)에서 톡신/미용기기/임플란트/
필러(리쥬란류) 등 카테고리별 월별 수출 실적(HS코드 기준)을 가져와 export_data.json으로
저장한다. 산업데이터 탭의 "수출 데이터" 섹션이 이 파일을 읽는다.

- 참고한 두 사이트의 구성을 합쳤다:
  · https://okirogue.github.io/aesthetic-web/ 의 "수출 데이터" 섹션 — 리쥬란류(HS
    3304999000) 단일 품목을 TRASS(관세청) 무역통계로 월별 추적하는 형식.
  · https://newsbot-3uj.pages.dev/coverage/coverage 의 "수출 데이터" 섹션 — 톡신/
    미용기기/임플란트 등 여러 카테고리를 관세청 확정치로 품목별/국가별/월별·분기별로
    보여주는 구성.
  이 스크립트는 후자처럼 여러 카테고리를 다루되, 전자처럼 카테고리별 시계열을 최대한
  길게(가능한 만큼) 쌓는 것을 목표로 한다.

- API: 공공데이터포털(data.go.kr) "관세청_품목별 수출입실적(GW)"
    엔드포인트: https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList
    (전국 총계, 국가 구분 없음 — 카테고리별 메인 시계열에 사용)
  국가별 세부 브레이크다운(최근 구간만, 호출량 절약)에는 같은 기관의
  "관세청_품목별 국가별 수출입실적(GW)"을 함께 쓴다.
    엔드포인트: https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList
    (cntyCd 필수라 국가마다 별도 호출해야 함 — 그래서 최근 구간 + 주요국만 조회)
  둘 다 무료(비용부과 없음)·이용허락범위 제한 없음·개발단계 자동승인(일 10,000건)임을
  공공데이터포털 페이지에서 확인했다(2026-08-20). serviceKey는 이 스크립트를 쓰는
  사람이 data.go.kr에 직접 가입해 위 두 API를 "활용신청"한 뒤 발급받아야 한다(계정
  생성은 본인이 해야 하는 일이라 이 스크립트가 대신할 수 없다). 신청은 개발계정
  기준 보통 즉시~수 분 내 자동승인된다.

- HS코드 매핑: 관세청/K-stat이 카테고리명으로 직접 분류를 제공하지 않아, 이 카테고리들을
  전문적으로 다루는 투자리서치 블로그 "머니레시피"(moneyrecipe.blog, HS코드 기반 상장사
  수출 추정을 전문으로 하는 매체, 2026-08-11/2026-08-20 게시물 기준)가 실제 신고 사례로
  검증해 공개한 코드를 가져다 썼다. 관세청이 공식으로 "이 카테고리 = 이 HS코드"라고
  못박은 자료가 아니라 리서치 매체의 추정 매핑이라는 점을 감안할 것 — 실제 신고 관행이
  달라지거나(회사별로 일부 다른 코드를 쓸 수 있음) 다른 품목이 같은 코드에 섞여 잡힐
  가능성이 있다. 특히 "의료용 미용기기"(HS 9018.90)는 미용기기 외 다른 의료기기도
  일부 섞여 잡힐 수 있는 넓은 코드라 진폭이 과장될 수 있다는 점에 유의.
    · 톡신(보툴리눔) : 3002491000, 3002909000 (구코드 3002903090 포함 가능성 있어 참고용으로
      같이 시도)
    · 미용기기(의료용) : 901890 (6자리 — 클래시스/원텍/루트로닉/레이저옵텍 등)
    · 임플란트(치과) : 9021290000 (오스템/덴티움 등 — 정형외과용 인공관절(9021.31)과는
      다른 코드이니 혼동 주의)
    · 필러·리쥬란류(기타화장품) : 3304999000 (휴젤 필러, 파마리서치 리쥬란 등이 여기 포함)
  각 카테고리는 여러 HS코드를 합산할 수 있어 CATEGORIES 딕셔너리 값이 리스트다.

- 시계열 길이: "최대한 길게" 요청에 맞춰 기본 시작월을 START_YYMM(2015-01)로 잡았다.
  API 쿼리 기간 제한이 1년이라 연 단위로 끊어서 반복 호출한다. 특정 연도 구간에서
  자료가 없으면(과거로 갈수록 없을 수 있음) 조용히 건너뛰고 그 이전 연도는 시도를
  멈춘다(관세청 자료가 실제로 어디까지 있는지 API가 명시하지 않아 이렇게 탐색한다).
  나중에 사용자가 "더 길게/짧게"를 원하면 --start-yymm으로 조정하면 된다.

- 이 단계도 "보강" 단계다 — DATA_GO_KR_SERVICE_KEY가 없거나 API 실패 시 기존
  export_data.json을 그대로 보존하고 경고만 남긴 채 0으로 종료한다.

사용법:
    DATA_GO_KR_SERVICE_KEY=xxx python scrape_export_data.py --out export_data.json
"""
import argparse
import datetime
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

import requests

ITEMTRADE_URL = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"
NITEMTRADE_URL = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"

START_YYMM = "201501"  # "최대한 길게" 요청에 따른 기본 시작월(2015-01) — 필요시 조정 가능
REQUEST_DELAY_SEC = 0.15
REQUEST_TIMEOUT = 30  # data.go.kr가 해외 리전(GitHub Actions 러너)에서 느릴 때가 있어 20→30초로 상향
REQUEST_RETRIES = 3  # 타임아웃/연결오류 시 재시도 횟수(최초 시도 포함)
REQUEST_RETRY_BACKOFF_SEC = 3  # 재시도 간 대기(시도 횟수에 비례해 증가)
MAX_CONSECUTIVE_EMPTY_YEARS = 2  # 이 횟수만큼 연달아 빈 연도가 나오면 그 이전은 그만 조회

# 카테고리 정의 — 라벨/HS코드(합산 대상 복수 가능)/참고 종목.
# 출처: moneyrecipe.blog "HS코드 + 수출 데이터로 실적 추정하기 좋은 기업은?"(2025-11-17,
# 2025-11-30 수정) 및 "26년 8월 수출 잠정치 분석: K-뷰티/헬스케어"(2026-08-11) 게시물의
# "품목 수출통계 검색에 사용된 HS코드" 표. 위 스크립트 상단 docstring 참고.
CATEGORIES = [
    {
        "key": "toxin",
        "label": "톡신(보툴리눔)",
        "hsCodes": ["3002491000", "3002909000"],
        "companies": "휴젤·메디톡스·대웅제약·휴온스글로벌",
    },
    {
        "key": "device_medical",
        "label": "미용기기(의료용)",
        "hsCodes": ["901890"],
        "companies": "클래시스·루트로닉·원텍·레이저옵텍",
    },
    {
        "key": "implant_dental",
        "label": "임플란트(치과)",
        "hsCodes": ["9021290000"],
        "companies": "오스템임플란트·덴티움",
    },
    {
        "key": "filler",
        "label": "필러·리쥬란류(기타화장품)",
        "hsCodes": ["3304999000"],
        "companies": "휴젤·파마리서치·휴메딕스",
    },
]

# 국가별 세부 브레이크다운에 쓸 주요국(전체 순회하면 호출량이 너무 커져 대표국만).
TOP_COUNTRIES = [
    ("CN", "중국"), ("US", "미국"), ("JP", "일본"), ("VN", "베트남"),
    ("HK", "홍콩"), ("DE", "독일"), ("FR", "프랑스"), ("TH", "태국"),
]
COUNTRY_BREAKDOWN_MONTHS = 24  # 국가별은 최근 24개월만(호출량 절약)


def yymm_add_months(yymm: str, months: int) -> str:
    y, m = int(yymm[:4]), int(yymm[4:])
    total = y * 12 + (m - 1) + months
    return f"{total // 12:04d}{(total % 12) + 1:02d}"


def yymm_range_chunks(start_yymm: str, end_yymm: str):
    """1년(12개월) 이하 제한에 맞춰 [start, end] 구간을 연 단위 청크로 쪼갠다."""
    chunks = []
    cur = start_yymm
    while cur <= end_yymm:
        chunk_end = min(yymm_add_months(cur, 11), end_yymm)
        chunks.append((cur, chunk_end))
        cur = yymm_add_months(chunk_end, 1)
    return chunks


def api_get(url, params, debug=False):
    """data.go.kr는 GitHub Actions 러너(해외 리전)에서 호출할 때 TLS 핸드셰이크가
    느리거나 가끔 타임아웃되는 경우가 실제로 관측됐다(2026-08-21, 첫 실행 로그에서
    ReadTimeoutError 확인). 네트워크 예외를 잡지 않고 그대로 올리면 그 호출 하나 때문에
    스크립트 전체가 죽어버려(exit 1) 이후 카테고리는 아예 시도조차 못 하게 된다 —
    다른 스크립트들과 동일하게 "이 호출만 실패로 처리하고 계속 진행"하도록 재시도 +
    예외 캐치를 추가했다.

    반환값은 (items, raw_text, failed) 3-튜플이다. failed=True는 "이 기간에 데이터가
    없다"가 아니라 "네트워크/파싱/API 오류로 이 호출 자체가 실패했다"는 뜻이라, 호출부에서
    "여기부터는 과거 자료가 없다"는 판단(MAX_CONSECUTIVE_EMPTY_YEARS)에 실패 케이스를
    섞지 않도록 구분해서 쓴다. 그렇게 안 하면 data.go.kr가 일시적으로 불안정할 때
    "자료가 실제로는 있는데 접속이 안 돼서" 조기 중단해버리는 오판을 할 수 있다."""
    for attempt in range(REQUEST_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            if debug or attempt == REQUEST_RETRIES - 1:
                print(f"[WARN] {url} 호출 실패(시도 {attempt+1}/{REQUEST_RETRIES}): {e}", file=sys.stderr)
            if attempt < REQUEST_RETRIES - 1:
                time.sleep(REQUEST_RETRY_BACKOFF_SEC * (attempt + 1))
    else:
        return None, None, True
    text = resp.text
    if debug:
        print(f"[DEBUG] {url} params={ {k:v for k,v in params.items() if k!='serviceKey'} } "
              f"-> HTTP {resp.status_code}, 응답 {len(text)}자", file=sys.stderr)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        print(f"[WARN] XML 파싱 실패: {e} — 응답 앞 300자: {text[:300]}", file=sys.stderr)
        return None, text, True
    result_code_el = root.find(".//resultCode")
    result_code = result_code_el.text if result_code_el is not None else None
    if result_code not in (None, "00"):
        msg_el = root.find(".//resultMsg")
        msg = msg_el.text if msg_el is not None else "?"
        print(f"[WARN] API 오류 코드 {result_code}: {msg}", file=sys.stderr)
        return None, text, True
    items = []
    for item_el in root.findall(".//item"):
        row = {child.tag: (child.text or "").strip() for child in item_el}
        items.append(row)
    return items, text, False


def fetch_national_series(service_key, hs_codes, start_yymm, end_yymm, debug=False):
    """카테고리(여러 HS코드 합산)의 전국 총계 월별 시계열. 오래된 연도부터 빈 응답이
    MAX_CONSECUTIVE_EMPTY_YEARS번 연속되면 그 이전은 자료가 없다고 보고 중단한다."""
    monthly = {}  # ym(YYYY-MM) -> {"expDlr": int, "expWgt": int}
    chunks = yymm_range_chunks(start_yymm, end_yymm)
    consecutive_empty = 0
    failed_calls = 0
    total_calls = 0
    for chunk_start, chunk_end in chunks:
        chunk_had_data = False
        chunk_all_failed = True
        for hs in hs_codes:
            params = {
                "serviceKey": service_key,
                "strtYymm": chunk_start,
                "endYymm": chunk_end,
                "hsSgn": hs,
                "numOfRows": "999",
                "pageNo": "1",
            }
            total_calls += 1
            items, _, failed = api_get(ITEMTRADE_URL, params, debug=debug)
            time.sleep(REQUEST_DELAY_SEC)
            if failed:
                failed_calls += 1
                continue
            chunk_all_failed = False  # 이 청크에서 최소 하나는 실패가 아니라 "확인된 응답"이었음
            if not items:
                continue
            for row in items:
                year = row.get("year", "")
                ym = year.replace(".", "-") if year else None
                if not ym or len(ym) != 7:
                    continue
                exp_dlr = int(row.get("expDlr") or 0)
                exp_wgt = int(row.get("expWgt") or 0)
                slot = monthly.setdefault(ym, {"expDlr": 0, "expWgt": 0})
                slot["expDlr"] += exp_dlr
                slot["expWgt"] += exp_wgt
                chunk_had_data = True
        if chunk_all_failed:
            # 이 청크의 모든 HS코드 호출이 네트워크/API 오류로 실패 — "자료가 없다"는 판단에
            # 넣지 않고 그냥 다음 청크로 넘어간다(조기 중단 카운터를 건드리지 않음).
            continue
        if chunk_had_data:
            consecutive_empty = 0
        else:
            consecutive_empty += 1
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY_YEARS:
                print(f"[INFO] {chunk_start}~ 구간이 {consecutive_empty}개 청크 연속 (확인된) 빈 응답 — "
                      f"이 카테고리는 여기까지가 조회 가능한 과거 한계로 보고 중단", file=sys.stderr)
                break
    if failed_calls:
        print(f"[WARN] 이 카테고리 호출 {total_calls}건 중 {failed_calls}건이 네트워크/API 오류로 실패함 "
              f"— 해당 기간은 실제로 자료가 없는 게 아니라 이번 실행에서 못 가져온 것일 수 있음 "
              f"(다음 실행에서 재시도됨)", file=sys.stderr)
    return sorted(({"ym": ym, **v} for ym, v in monthly.items()), key=lambda r: r["ym"])


def fetch_country_breakdown(service_key, hs_codes, start_yymm, end_yymm, debug=False):
    """최근 구간만 주요국별로 조회(호출량 절약). 국가마다 개별 호출 필요."""
    by_country = {}
    for cnty_cd, cnty_name in TOP_COUNTRIES:
        monthly = {}
        for hs in hs_codes:
            params = {
                "serviceKey": service_key,
                "strtYymm": start_yymm,
                "endYymm": end_yymm,
                "hsSgn": hs,
                "cntyCd": cnty_cd,
                "numOfRows": "999",
                "pageNo": "1",
            }
            items, _, failed = api_get(NITEMTRADE_URL, params, debug=debug)
            time.sleep(REQUEST_DELAY_SEC)
            if failed and debug:
                print(f"[DEBUG] 국가별 호출 실패(무시하고 계속): {cnty_name}/{hs}", file=sys.stderr)
            if not items:
                continue
            for row in items:
                year = row.get("year", "")
                ym = year.replace(".", "-") if year else None
                if not ym or len(ym) != 7:
                    continue
                exp_dlr = int(row.get("expDlr") or 0)
                monthly[ym] = monthly.get(ym, 0) + exp_dlr
        if monthly:
            by_country[cnty_name] = sorted(
                ({"ym": ym, "expDlr": v} for ym, v in monthly.items()), key=lambda r: r["ym"]
            )
    return by_country


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="export_data.json")
    ap.add_argument("--start-yymm", default=START_YYMM)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}

    service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not service_key:
        print("[WARN] DATA_GO_KR_SERVICE_KEY가 없어 수출 데이터 갱신을 건너뜁니다. "
              "data.go.kr에서 '관세청_품목별 수출입실적(GW)'과 '관세청_품목별 국가별 "
              "수출입실적(GW)' 두 API를 활용신청한 뒤 발급받은 서비스키를 이 환경변수에 "
              "넣어주세요(개발계정은 보통 즉시 자동승인).", file=sys.stderr)
        sys.exit(0)

    now = datetime.datetime.now(datetime.timezone.utc)
    # 관세청 자료는 매월 15일경 전월분까지 반영되므로 이번 달은 아직 비어있을 수 있어
    # 조회 종료월을 전월로 잡는다(당월을 넣어도 API가 빈 값을 주므로 결과는 같지만,
    # 불필요한 호출을 줄이기 위해 미리 뺀다).
    end_yymm = yymm_add_months(now.strftime("%Y%m"), -1)
    country_start_yymm = yymm_add_months(end_yymm, -(COUNTRY_BREAKDOWN_MONTHS - 1))

    categories_out = []
    for cat in CATEGORIES:
        print(f"[INFO] {cat['label']} 조회 시작 (HS {cat['hsCodes']})", file=sys.stderr)
        monthly = fetch_national_series(service_key, cat["hsCodes"], args.start_yymm, end_yymm, debug=args.debug)
        by_country = fetch_country_breakdown(
            service_key, cat["hsCodes"], country_start_yymm, end_yymm, debug=args.debug
        )
        if not monthly:
            print(f"[WARN] {cat['label']}: 전국 시계열을 하나도 못 가져왔습니다(API 실패 또는 "
                  f"HS코드 문제 가능성) — 이 카테고리는 이전 데이터를 유지합니다.", file=sys.stderr)
            prev = next((c for c in existing.get("categories", []) if c.get("key") == cat["key"]), None)
            if prev:
                categories_out.append(prev)
            continue
        categories_out.append({
            "key": cat["key"],
            "label": cat["label"],
            "hsCodes": cat["hsCodes"],
            "companies": cat["companies"],
            "monthly": monthly,
            "byCountry": by_country,
        })
        print(f"[INFO] {cat['label']}: 월별 {len(monthly)}개월치, 국가별 {len(by_country)}개국 확보", file=sys.stderr)

    if not categories_out:
        print("[ERROR] 모든 카테고리 조회에 실패해 기존 export_data.json을 보존하고 종료합니다.", file=sys.stderr)
        sys.exit(1)

    payload = {
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "관세청 수출입무역통계(공공데이터포털 data.go.kr) · HS코드 매핑은 "
                  "moneyrecipe.blog 리서치 자료 참고(공식 관세청 분류 아님, 교차검증 권장)",
        "categories": categories_out,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} ({len(categories_out)}개 카테고리)")


if __name__ == "__main__":
    main()
