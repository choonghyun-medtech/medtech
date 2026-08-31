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
  국가별 세부 브레이크다운(최근 구간만, 호출량 절약, 2026-08-21부터 카테고리마다
  다른 국가 목록을 조회 — CATEGORIES의 "countries" 필드 참고)에는 같은 기관의
  "관세청_품목별 국가별 수출입실적(GW)"을 함께 쓴다.
    엔드포인트: https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList
    (cntyCd 필수라 국가마다 별도 호출해야 함 — 그래서 최근 구간 + 카테고리별 지정
    국가만 조회)
  지역별(시군구) 세부 브레이크다운에는 "관세청_시군구별 품목별 수출입실적"(데이터셋
  id 15134343)을 쓴다.
    엔드포인트: https://apis.data.go.kr/1220000/sigunguperprlstperacrs/getSigunguPerPrlstPerAcrs
    (sidoCd 필수 — 시군구 코드가 아니라 시도코드만 넣으면 그 시도 관내 전체 시군구
    데이터를 배열로 돌려줌, 응답의 sggNm에서 원하는 시군구만 걸러 씀. 2026-08-21에
    사용자가 실제 서비스키로 라이브 호출해 엔드포인트/파라미터/응답 구조를 확인함 —
    이 API는 제목에 "(GW)" 표시가 없어 처음엔 Itemtrade 계열과 다른 서비스 그룹으로
    추정했으나, 실제로는 같은 1220000 그룹 소속이었음. 시도코드는 일반 행정표준코드와
    달라(예: 강원은 42가 아니라 51=강원특별자치도) 사용자가 공식 코드표
    관세청조회코드_v1.3.xlsx를 data.go.kr에서 받아 공유해줘서 확인함 — 아래
    SIDO_CD_BY_NAME 주석 참고. 응답은 월별(priodTitle "2024.01" 형식)이라 국가별
    수준으로 촘촘함.)
  모두 무료(비용부과 없음)·이용허락범위 제한 없음·개발단계 자동승인(일 10,000건)임을
  공공데이터포털 페이지에서 확인했다(2026-08-20). serviceKey는 이 스크립트를 쓰는
  사람이 data.go.kr에 직접 가입해 위 API들을 "활용신청"한 뒤 발급받아야 한다(계정
  생성은 본인이 해야 하는 일이라 이 스크립트가 대신할 수 없다). 신청은 개발계정
  기준 보통 즉시~수 분 내 자동승인되지만, 승인 직후 첫 호출은 실제 게이트웨이에 키가
  반영되기까지 시차가 있어 SERVICE_KEY_IS_NOT_REGISTERED_ERROR가 잠깐 날 수 있다(이번에
  실제로 겪음 — 몇 분~길게는 1~2시간 뒤 재시도하면 해결됨). 참고로 data.go.kr의
  "일반 인증키"는 계정 하나에 여러 API를 활용신청해도 키 값 자체는 계정 공용이지만,
  각 API 활용신청 승인 상태는 API별로 따로 관리된다(마이페이지 > Open API 활용신청
  현황에서 확인 가능) — 이번에 쓴 계정은 시군구별 API만 승인돼 있고 Itemtrade 등
  기존 3개 API는 활용신청이 안 돼 있었음(=GitHub Actions 시크릿의 키와는 다른 계정으로
  보임). 이 스크립트를 실제로 돌리려면 DATA_GO_KR_SERVICE_KEY 하나가 아래 4개 API를
  전부 활용신청해서 승인된 계정의 키여야 한다: 관세청_품목별 수출입실적(GW),
  관세청_품목별 국가별 수출입실적(GW), 관세청_시군구별 품목별 수출입실적 — 이 3개는
  현재 GitHub Actions 시크릿 계정과 이번에 시군구 API 테스트에 쓴 계정이 다를 수 있어
  재확인 필요.

- HS코드 매핑: 관세청/K-stat이 카테고리명으로 직접 분류를 제공하지 않아, 이 카테고리들을
  전문적으로 다루는 투자리서치 블로그 "머니레시피"(moneyrecipe.blog, HS코드 기반 상장사
  수출 추정을 전문으로 하는 매체, 2026-08-11/2026-08-20 게시물 기준)가 실제 신고 사례로
  검증해 공개한 코드를 가져다 썼다. 관세청이 공식으로 "이 카테고리 = 이 HS코드"라고
  못박은 자료가 아니라 리서치 매체의 추정 매핑이라는 점을 감안할 것 — 실제 신고 관행이
  달라지거나(회사별로 일부 다른 코드를 쓸 수 있음) 다른 품목이 같은 코드에 섞여 잡힐
  가능성이 있다. 특히 "의료용 미용기기"(HS 9018.90)는 미용기기 외 다른 의료기기도
  일부 섞여 잡힐 수 있는 넓은 코드라 진폭이 과장될 수 있다는 점에 유의.
    · 톡신(보툴리눔) : 3002491000 (2026-08-21: index.html EXPORT_CATEGORY_CONFIG 기준으로
      단일 코드 확정 — 이전엔 3002909000도 합산했으나 사용자가 제외 확인)
    · 미용기기(의료용) : 901890 (6자리 — 클래시스/원텍/루트로닉/레이저옵텍 등)
    · 임플란트(치과) : 902129 (6자리, 2026-08-21 확정 — 이전 10자리 9021290000에서
      상위 6자리로 넓힘. 오스템/덴티움 등 — 정형외과용 인공관절(9021.31)과는 다른
      코드이니 혼동 주의)
    · 필러·리쥬란류(기타화장품) : 3304999000 (휴젤 필러, 파마리서치 리쥬란 등이 여기 포함)
    · 지혈제(지혈재) : 3006104000 (2026-08-21 추가 — 넥스트바이오메디컬 내시경용 지혈재)
    · 치과영상장비 : 902213 (6자리, 2026-08-21 확정 — 이전 10자리 9022120000에서
      상위 6자리로 넓힘. 제노레이 덴탈 CBCT, 다른 종목은 미확인)
    · 체외진단 PCR : 3822192020 (2026-08-21 신규 추가 — index.html 스펙, 상장사 미확인)
    · 면역진단 : 3822191000 (2026-08-21 신규 추가 — index.html 스펙, 상장사 미확인)
  홈뷰터(가정용 미용기기, HS 8543702020)는 2026-08-21에 사용자 확인으로 카테고리
  목록에서 제외됨(index.html EXPORT_CATEGORY_CONFIG의 8개 품목에 없었음).
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
# 시군구별 브레이크다운용 엔드포인트(SIGUNGU_ITEMTRADE_URL)는 아래 CATEGORIES 이후
# 섹션에 정의돼 있다 — 2026-08-21에 사용자가 실제 서비스키로 확인한 값. (예전엔 시도
# 단위 SIDOITEMTRADE_URL을 썼는데, CATEGORIES의 regions가 전부 시군구 단위라 안 맞아서
# 시군구 단위 API로 교체함 — 자세한 경위는 아래 주석 참고.)

START_YYMM = "201501"  # "최대한 길게" 요청에 따른 기본 시작월(2015-01) — 필요시 조정 가능
REQUEST_DELAY_SEC = 0.15
REQUEST_TIMEOUT = 30  # data.go.kr가 해외 리전(GitHub Actions 러너)에서 느릴 때가 있어 20→30초로 상향
REQUEST_RETRIES = 3  # 타임아웃/연결오류 시 재시도 횟수(최초 시도 포함)
REQUEST_RETRY_BACKOFF_SEC = 3  # 재시도 간 대기(시도 횟수에 비례해 증가)
MAX_CONSECUTIVE_EMPTY_YEARS = 2  # 이 횟수만큼 연달아 빈 연도가 나오면 그 이전은 그만 조회

# 카테고리 정의 — 라벨/HS코드(합산 대상 복수 가능)/참고 종목/국가별·지역별 조회 대상.
# 2026-08-21 인수인계: 사용자가 index.html EXPORT_CATEGORY_CONFIG에 지정한 8개
# 품목·HS코드·국가/지역 범위를 스크래퍼 쪽에도 그대로 반영(사용자 확인: "셋 다 지금
# 업데이트된 그대로 유지" — home_device 제외, 톡신 HS 단일화, 임플란트/치과CBCT 6자리
# 코드 확정). 기존 CATEGORIES와의 차이점은 아래 각 항목 주석 참고.
# 출처(HS코드): moneyrecipe.blog "HS코드 + 수출 데이터로 실적 추정하기 좋은 기업은?"
# (2025-11-17, 2025-11-30 수정) 및 "26년 8월 수출 잠정치 분석: K-뷰티/헬스케어"
# (2026-08-11) 게시물의 "품목 수출통계 검색에 사용된 HS코드" 표. 위 스크립트 상단
# docstring 참고. countries/regions는 index.html EXPORT_CATEGORY_CONFIG와 동일(그
# 목록의 근거/출처는 index.html 쪽에서 사용자가 직접 지정한 것이라 이 스크립트에는
# 별도 출처 기록 없음).
CATEGORIES = [
    {
        "key": "toxin",
        "label": "톡신(보툴리눔)",
        # 기존엔 3002491000 + 3002909000 두 코드 합산이었으나, 사용자 확정 스펙은
        # 3002491000 하나만(2026-08-21 사용자 확인).
        "hsCodes": ["3002491000"],
        "companies": "휴젤·메디톡스·대웅제약·휴온스글로벌",
        "countries": [("US", "미국"), ("CN", "중국"), ("BR", "브라질"), ("TH", "태국"), ("JP", "일본")],
        "regions": [],
    },
    {
        "key": "device_medical",
        "label": "미용기기(의료용)",
        "hsCodes": ["901890"],
        "companies": "클래시스·루트로닉·원텍·레이저옵텍",
        "countries": [("US", "미국"), ("BR", "브라질"), ("TH", "태국"), ("CN", "중국"), ("JP", "일본")],
        "regions": ["경기 고양시", "서울 강남구", "서울 금천구", "대전 유성구"],
    },
    {
        "key": "implant_dental",
        "label": "임플란트(치과)",
        # 기존 9021290000(10자리) → 사용자 확정 스펙 902129(6자리)로 변경(2026-08-21
        # 사용자 확인 — 상위 6자리 기준으로 넓게 잡음).
        "hsCodes": ["902129"],
        "companies": "오스템임플란트·덴티움",
        "countries": [("RU", "러시아 연방"), ("CN", "중국"), ("US", "미국")],
        "regions": ["서울 강서구", "경기 수원시", "부산 해운대구"],
    },
    {
        "key": "filler",
        "label": "필러·리쥬란류(기타화장품)",
        "hsCodes": ["3304999000"],
        "companies": "휴젤·파마리서치·휴메딕스",
        "countries": [],
        "regions": ["강원 강릉시"],
    },
    {
        # 출처: moneyrecipe.blog 위 게시물의 "헬스케어·의료기기" 표 — "넥스트바이오메디컬
        # 내시경용 지혈재 3006.10.4000"으로 명시. 상장사(232830)가 뚜렷이 매핑된 코드.
        "key": "hemostat",
        "label": "지혈제(지혈재)",
        "hsCodes": ["3006104000"],
        "companies": "넥스트바이오메디컬",
        "countries": [],
        "regions": [],
    },
    {
        # 출처: 위 게시물 "헬스케어·의료기기" 표 — "제노레이 덴탈 CBCT 9022.12.0000"
        # (메디컬 C-ARM·Mammography는 9022.14.1090으로 별도 표기돼 있어 치과 전용
        # 코드만 채택). 기존 9022120000(10자리) → 사용자 확정 스펙 902213(6자리)로
        # 변경(2026-08-21 사용자 확인). 바텍 등 다른 치과영상장비 상장사도 동일
        # HS코드군을 쓸 가능성이 높지만 개별 확인은 못했다 — companies는 확인된
        # 제노레이만 우선 기재.
        "key": "dental_imaging",
        "label": "치과영상장비",
        "hsCodes": ["902213"],
        "companies": "제노레이(추가 종목 확인 필요)",
        "countries": [("CN", "중국")],
        "regions": ["경기 화성시", "경기 성남시"],
    },
    {
        # 2026-08-21 신규 추가(index.html EXPORT_CATEGORY_CONFIG 기준). HS코드는
        # 사용자가 지정한 값 그대로 사용 — 이 스크립트 작성 시점엔 별도 상장사 리서치를
        # 못 해 companies는 비워둠(추후 확인 필요).
        "key": "pcr_diagnostic",
        "label": "체외진단 PCR",
        "hsCodes": ["3822192020"],
        "companies": "",  # TODO: 관련 상장사 확인 필요
        "countries": [],
        "regions": ["서울 송파구"],
    },
    {
        "key": "immuno_diagnostic",
        "label": "면역진단",
        "hsCodes": ["3822191000"],
        "companies": "",  # TODO: 관련 상장사 확인 필요
        "countries": [],
        "regions": ["경기 수원시"],
    },
]
# home_device(홈뷰터, HS 8543702020)는 2026-08-21 사용자 확인으로 목록에서 제외됨
# (index.html EXPORT_CATEGORY_CONFIG의 8개 품목에 없었음 — "셋 다 지금 업데이트된
# 그대로 유지" 답변으로 확정).

# 국가별은 최근 132개월(11년)만 롤링 수집(호출량 절약). index.html이 표시 구간 전체(최근
# 10년=120개월)에 걸쳐 YoY를 그리려면 뒤쪽 12개월치도 전년동기 비교 대상이 수집 범위 안에
# 있어야 하므로, "수집 11년 → 표시 10년"이 되도록 표시 기간(120개월)에 12개월 여유를 더했다
# (2026-08-31 요청 — 기존 36개월/24개월 표시에서 확장. 이전에 24개월 표시였을 때도 같은
# 이유로 수집을 36개월로 늘렸던 적이 있음, 2026-08-24). index.html의
# EXPORT_BREAKDOWN_DISPLAY_MONTHS(120)와 exportDisplayFromLabel()가 이 여유분(앞 12개월)을
# YoY 기준선으로만 쓰고 화면엔 뒤 120개월만 보여준다. 아직 132개월치가 안 쌓인 카테고리는
# 있는 만큼만 쌓이다가 시간이 지나며 자연히 채워진다(그동안 삭제 없이 계속 롤링 백필됨).
COUNTRY_BREAKDOWN_MONTHS = 132

# 지역별(시군구) 브레이크다운 — 국내 지역 수출량으로 특정 기업의 실적을 추정하는 용도
# (aesthetic-web의 "강릉=파마리서치·리쥬란" 프록시 방식과 동일한 아이디어).
#
# 2026-08-21 갱신: 기존엔 시도(광역, 예: "서울특별시") 단위 API(sidoitemtrade)만 있었는데,
# CATEGORIES의 regions가 전부 시군구(구/시, 예: "서울 강남구") 단위라 애초에 안 맞았다
# (시도 단위로는 "강남구"처럼 세분화된 값을 못 얻음). 사용자가 공공데이터포털에서
# "관세청_시군구별 품목별 수출입실적"(데이터셋 id 15134343) API를 별도로 활용신청하고
# 실제 서비스키로 라이브 호출해서 엔드포인트/파라미터/응답 구조를 확인해줬다 — 그 결과로
# 아래 SIGUNGU_ITEMTRADE_URL과 fetch_sigungu_breakdown()을 구현함(기존 시도 단위
# fetch_sido_breakdown()/SIDOITEMTRADE_URL/SIDO_CODES_CONFIRMED는 이제 안 쓰므로 제거 —
# 필요하면 git 히스토리에서 복원 가능).
#
# 이 API는 시군구 코드를 직접 넣는 게 아니라 sidoCd(시도코드)만 필수로 받고, 그 시도
# 관내 전체 시군구의 데이터를 한 번에 배열로 돌려준다(응답 필드 sggNm이 "서울특별시
# 강남구"처럼 시도 전체명+시군구명). 그래서 우리는 필요한 시도만 호출한 뒤 응답에서
# 원하는 시군구명만 걸러 쓴다. 응답은 월별(priodTitle "2024.01" 형식)이라 기존 시도
# API(연 단위로만 집계되는 것으로 보였음)보다 더 촘촘하다.
SIGUNGU_ITEMTRADE_URL = "https://apis.data.go.kr/1220000/sigunguperprlstperacrs/getSigunguPerPrlstPerAcrs"

# 시도코드 — 2026-08-21에 사용자가 data.go.kr에서 다운로드해 공유해준 공식 코드표
# (관세청조회코드_v1.3.xlsx의 "시도코드" 시트)에서 확인. 일반 행정표준코드(41=경기 등)와
# 다른 관세청 자체 코드라 이 표 없이는 추측으로 못 맞춘다(강원은 42가 아니라
# 51=강원특별자치도, 2023년 개편명 반영). CATEGORIES의 regions에서 실제로 쓰는 시도만
# 등록해뒀다 — 새 지역이 추가되면 이 표에서 추가로 찾아 넣어야 함.
SIDO_CD_BY_NAME = {
    "서울": "11",
    "경기": "41",
    "대전": "30",
    "부산": "26",
    "강원": "51",
}
SIGUNGU_BREAKDOWN_MONTHS = 132  # 국가별과 동일한 이유로 132개월(11년)로 늘림(위 COUNTRY_BREAKDOWN_MONTHS 주석 참고)


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
    """카테고리(여러 HS코드 합산)의 전국 총계 월별 시계열. 최근 연도부터 거꾸로(최신→과거)
    훑다가 빈 응답이 MAX_CONSECUTIVE_EMPTY_YEARS번 연속되면 그 이전은 자료가 없다고 보고
    중단한다.

    ⚠️ 2026-08-21 버그 수정: 원래 이 루프가 청크를 오래된 연도(start_yymm)부터 최신
    순서로(오름차순) 돌면서 "2개 청크 연속 빈 응답이면 중단"했는데, 이러면 정작 가장
    오래된 연도(2015~2016)에 데이터가 없는 카테고리는 최신 연도(실제로는 데이터가 있는
    구간)까지 가보지도 못하고 맨 처음에 중단돼버리는 버그가 있었다 — 톡신 HS코드를
    3002491000 하나로 단순화한 뒤(2026-08-21) 이 코드가 2015~2016년엔 신고 실적이
    없다는 게 드러나면서 실제로 "전국 시계열을 하나도 못 가져옴"으로 나타남. 청크
    순서를 최신→과거로 뒤집어서, "최근 데이터는 다 챙기고 나서 과거로 갈수록 없으면
    그때 가서 중단"하도록 고쳤다(모아진 monthly는 마지막에 ym 기준으로 다시 정렬하므로
    순회 순서 자체는 최종 결과에 영향 없음)."""
    monthly = {}  # ym(YYYY-MM) -> {"expDlr": int, "expWgt": int}
    chunks = list(reversed(yymm_range_chunks(start_yymm, end_yymm)))  # 최신 청크부터
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


def fetch_country_breakdown(service_key, hs_codes, countries, start_yymm, end_yymm, debug=False):
    """최근 구간만 카테고리별로 지정된 국가에 대해서만 조회(호출량 절약). 국가마다
    개별 호출 필요. countries는 [(cntyCd, 국가명), ...] — 카테고리마다 다르다
    (2026-08-21부터: 예전엔 전 카테고리 공통 TOP_COUNTRIES 8개국 고정이었으나,
    index.html EXPORT_CATEGORY_CONFIG에 카테고리별로 지정된 국가만 조회하도록 변경).

    ⚠️ 2026-08-21 버그 수정: 이 API도 fetch_national_series의 Itemtrade와 마찬가지로
    "조회기간은 1년 이내만 가능"(API 오류 코드 99) 제한이 있는데, 이 함수는 원래
    start_yymm~end_yymm(COUNTRY_BREAKDOWN_MONTHS=24개월)을 청크 없이 한 번에 요청하고
    있었다 — 실제 서비스키로 처음 돌려보고서야 이 제한에 걸려 매번 오류 99만 받고
    국가별 데이터가 전부 0건으로 나오는 게 드러났다. yymm_range_chunks로 1년 단위
    청크로 쪼개서 호출하도록 수정."""
    by_country = {}
    chunks = yymm_range_chunks(start_yymm, end_yymm)
    for cnty_cd, cnty_name in countries:
        monthly = {}
        for hs in hs_codes:
            for chunk_start, chunk_end in chunks:
                params = {
                    "serviceKey": service_key,
                    "strtYymm": chunk_start,
                    "endYymm": chunk_end,
                    "hsSgn": hs,
                    "cntyCd": cnty_cd,
                    "numOfRows": "999",
                    "pageNo": "1",
                }
                items, _, failed = api_get(NITEMTRADE_URL, params, debug=debug)
                time.sleep(REQUEST_DELAY_SEC)
                if failed and debug:
                    print(f"[DEBUG] 국가별 호출 실패(무시하고 계속): {cnty_name}/{hs}/{chunk_start}~{chunk_end}",
                          file=sys.stderr)
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


def fetch_sigungu_breakdown(service_key, hs_codes, regions, start_yymm, end_yymm, debug=False):
    """regions는 CATEGORIES의 "regions" 필드 그대로(예: ["경기 고양시", "서울 강남구"]) —
    "시도 단축명 시군구명" 형식, index.html EXPORT_CATEGORY_CONFIG의 regions와 동일한
    문자열이어야 한다(반환 dict의 키로 그대로 씀 — index.html이 이 키로 조회하므로 형식이
    어긋나면 화면에 안 뜬다).

    이 API는 시군구 코드를 직접 못 넣고 sidoCd(시도)만 필수라, 필요한 시도만 호출해서
    그 시도 관내 전체 시군구 응답을 받은 뒤 원하는 시군구명(sggNm에서 시도 전체명을 뗀
    나머지)만 걸러 쓴다. 같은 시도에 지정 지역이 여러 개면(예: 경기 고양시+경기 수원시)
    그 시도는 HS코드당 한 번만 호출하고 응답에서 둘 다 걸러낸다(중복 호출 방지).

    실패해도(failed=True) 이 카테고리 전체를 죽이지 않고 그 시도만 건너뛴다.

    ⚠️ 2026-08-21 버그 수정: 이 API도 "조회기간 1년 이내" 제한이 있는데(fetch_national_series/
    fetch_country_breakdown과 동일), 원래 이 함수는 start_yymm~end_yymm
    (SIGUNGU_BREAKDOWN_MONTHS=24개월)을 청크 없이 한 번에 요청해서 실제 서비스키로
    처음 돌려보니 오류 코드 99만 받고 지역별 데이터가 전부 0건이었다. yymm_range_chunks로
    1년 단위 청크로 쪼개서 호출하도록 수정."""
    if not regions:
        return {}

    wanted = {}  # region_label(예: "경기 고양시") -> (sido_cd, sigungu_name(예: "고양시"))
    for region in regions:
        parts = region.split(" ", 1)
        if len(parts) != 2:
            print(f"[WARN] 지역 문자열 형식이 예상과 다름(건너뜀): {region!r}", file=sys.stderr)
            continue
        sido_short, sigungu_name = parts
        sido_cd = SIDO_CD_BY_NAME.get(sido_short)
        if not sido_cd:
            print(f"[WARN] {region!r}: 시도 '{sido_short}'의 코드가 SIDO_CD_BY_NAME에 없음(코드표에서 "
                  f"확인 후 추가 필요) — 건너뜀", file=sys.stderr)
            continue
        wanted[region] = (sido_cd, sigungu_name)

    needed_sido_cds = sorted({v[0] for v in wanted.values()})
    monthly_by_region = {region: {} for region in wanted}  # region -> {ym: expUsdAmt}
    chunks = yymm_range_chunks(start_yymm, end_yymm)

    for sido_cd in needed_sido_cds:
        for hs in hs_codes:
            # 시군구별 API(sigunguperprlstperacrs)는 다른 세 API(Itemtrade/nitemtrade)와
            # 달리 품목코드를 반드시 6자리로만 받는다("API 오류 코드 99: 품목코드는 6자리로
            # 입력해야 합니다" — 2026-08-24 실서비스키로 확인). 카테고리 중 필러(3304999000)·
            # 체외진단 PCR(3822192020)·면역진단(3822191000)처럼 10자리 코드를 쓰는 곳은
            # 이 호출에서만 앞 6자리로 잘라서 보낸다(전국 시계열/국가별 호출은 원래 코드 그대로 유지).
            hs_sigungu = hs[:6]
            for chunk_start, chunk_end in chunks:
                params = {
                    "serviceKey": service_key,
                    "strtYymm": chunk_start,
                    "endYymm": chunk_end,
                    "HsSgn": hs_sigungu,
                    "sidoCd": sido_cd,
                    "numOfRows": "999",
                    "pageNo": "1",
                }
                items, _, failed = api_get(SIGUNGU_ITEMTRADE_URL, params, debug=debug)
                time.sleep(REQUEST_DELAY_SEC)
                if failed and debug:
                    print(f"[DEBUG] 시군구별 호출 실패(무시하고 계속): sidoCd={sido_cd}/{hs}/{chunk_start}~{chunk_end}",
                          file=sys.stderr)
                if not items:
                    continue
                for row in items:
                    sgg_nm = row.get("sggNm", "")
                    # sggNm은 "서울특별시 강남구"처럼 시도 전체명+시군구명이 붙어 있음 —
                    # 뒤쪽 시군구명만 떼서 우리가 찾는 지역명과 비교한다.
                    sigungu_part = sgg_nm.split(" ", 1)[1] if " " in sgg_nm else sgg_nm
                    period = row.get("priodTitle", "")
                    if not period or not period[:4].isdigit():
                        continue  # "총계" 등 합계 행 제외
                    ym = period.replace(".", "-")  # "2024.01" -> "2024-01"
                    exp = int((row.get("expUsdAmt") or "0").replace(",", "").strip() or "0")
                    for region, (want_sido, want_sigungu) in wanted.items():
                        if want_sido == sido_cd and want_sigungu == sigungu_part:
                            slot = monthly_by_region[region]
                            slot[ym] = slot.get(ym, 0) + exp

    by_region = {}
    for region, monthly in monthly_by_region.items():
        if monthly:
            by_region[region] = sorted(
                ({"ym": ym, "expDlr": v} for ym, v in monthly.items()), key=lambda r: r["ym"]
            )
    return by_region


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
    sigungu_start_yymm = yymm_add_months(end_yymm, -(SIGUNGU_BREAKDOWN_MONTHS - 1))

    categories_out = []
    for cat in CATEGORIES:
        print(f"[INFO] {cat['label']} 조회 시작 (HS {cat['hsCodes']})", file=sys.stderr)
        monthly = fetch_national_series(service_key, cat["hsCodes"], args.start_yymm, end_yymm, debug=args.debug)
        by_country = (
            fetch_country_breakdown(
                service_key, cat["hsCodes"], cat.get("countries", []), country_start_yymm, end_yymm,
                debug=args.debug,
            )
            if cat.get("countries")
            else {}
        )
        by_region = fetch_sigungu_breakdown(
            service_key, cat["hsCodes"], cat.get("regions", []), sigungu_start_yymm, end_yymm, debug=args.debug
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
            "byRegion": by_region,  # 월별, 이 카테고리가 지정한 시군구(regions)만 포함
        })
        print(f"[INFO] {cat['label']}: 월별 {len(monthly)}개월치, 국가별 {len(by_country)}개국, "
              f"지역별 {len(by_region)}개 시군구 확보", file=sys.stderr)

    if not categories_out:
        print("[ERROR] 모든 카테고리 조회에 실패해 기존 export_data.json을 보존하고 종료합니다.", file=sys.stderr)
        sys.exit(1)

    payload = {
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "관세청 수출입무역통계(공공데이터포털 data.go.kr) · 카테고리별 HS코드 기준",
        "categories": categories_out,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} ({len(categories_out)}개 카테고리)")


if __name__ == "__main__":
    main()
