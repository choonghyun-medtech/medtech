#!/usr/bin/env python3
"""
"글로벌 대시보드" 탭의 Peer Table 데이터 변환 스크립트.

- 배경: 이 데이터는 Bloomberg 엑셀 애드인(BDP/BDH, BEST_PE_RATIO 등 필드명으로 확인됨)으로
  값을 끌어오는 엑셀 파일이 원본이다. Bloomberg 함수는 터미널 라이선스가 있는 로컬 PC의
  로그인된 세션에서만 계산되므로, 다른 *.json(뉴스/공시/주가 등 무료 API 기반)과 달리
  GitHub Actions 같은 클라우드 서버에서 자동으로 값을 갱신할 방법이 없다(2026-09-15 확인).
  그래서 이 스크립트는 스케줄 워크플로가 아니라, 사용자가 엑셀을 새로 저장한 뒤 수동으로
  실행하는 용도다.
- [2026-09-17] 파일 자체가 교체됐다 — 이전엔 프로젝트 루트의 "글로벌 대시보드.xlsx"
  Sheet1(블룸버그 함수가 그대로 걸려있는 시트)을 직접 읽었는데, 이제 사용자가 별도 폴더
  ("D:/★사용자 폴더/Desktop/dashboard files/글로벌 대시보드!!.xlsx")에서 관리하고, 그 안의
  "raw" 탭(블룸버그 함수, 로컬에서 값을 못 읽음)이 아니라 "값복사" 탭(raw를 값으로
  붙여넣은 결과)을 읽어야 한다. 컬럼 배치도 완전히 바뀌어서(실제 데이터는 여전히 4행부터):
  A=미사용, B=국적, C=섹터(콤마로 여러 개 묶임), D=사명, E=시가총액(백만달러),
  F~H=영업이익률(OPM) FY0/1/2, I~K=P/E FY0/1/2, L~N=P/S FY0/1/2, O~Q=EV/EBITDA FY0/1/2,
  R~T=매출(백만달러) FY0/1/2, U~V=매출성장률(%) FY1/2, W=Ticker. 국적/섹터가 이제 앞쪽에
  바로 있어 예전의 "국적(보정)" 열 보정 단계가 필요 없어졌다(국적 오타는 COUNTRY_OVERRIDES로
  여전히 안전망은 둠). Y~AA(영업이익 절대금액 FY0/1/2)는 새로 추가됐지만 사이트가 아직
  영업이익률(%)만 쓰고 절대금액은 안 써서 파싱하지 않는다(2026-09-17 사용자 확인 — 필요해지면
  vals 범위를 W 앞까지가 아니라 Y~AA까지 넓히고 row dict에 op_income 필드를 추가할 것).
  - 반도체·로봇부품 등 비의료기기 참고 비교군(MISC_TICKERS, 예전엔 "엔비디아" 이후 목록)이
    이번 "값복사" 탭 범위(4~281행)엔 아예 없다(2026-09-17 확인, 사용자 요청으로 이번엔 그냥
    비워두고 진행 — 나중에 다시 필요해지면 MISC_TICKERS를 그대로 두었으니 채워 넣으면 됨).
- 원본 자체에 있는 문제 두 가지를 이 스크립트가 보정한다:
  1) 일부 사명/섹터 문자열이 소스에서부터 특정 글자 수(사명은 28자)에서 잘려 들어온다
     (예: "Shanghai MicroPort MedBot Gr" -> "...Group", "산업용 기계, 용품 및" -> "...및 부품").
     2026-09-15 육안 확인으로 찾은 건들만 NAME_FIXES/SECTOR_FIXES에 정리해뒀다 — 엑셀이
     바뀌면 새로 잘린 값이 생길 수 있으니, 이 매핑에 없는 새로운 이상한 값은 수동으로 추가.
  2) MicroPort MedBot Group/Shenzhen Edge Medical 행이 원본에 실수로 완전히 중복
     입력돼 있어(2026-09-15 확인, 2026-09-17 새 파일에서도 재확인) (사명,티커) 기준으로
     중복 제거한다.
  3) 국적(B열) 오타 — 스카이랩스(386380 KS Equity, 코스닥 상장)가 한때 "미국"으로 잘못
     들어있던 걸 발견(2026-09-15), 사용자가 원본 엑셀에서 직접 정정 완료. 혹시 비슷한
     오타가 또 생기면 COUNTRY_OVERRIDES(티커 기준)에 등록해 바로잡을 수 있다(현재는 비어있음).

사용법:
    python scripts/convert_global_dashboard.py --source "D:/★사용자 폴더/Desktop/dashboard files/글로벌 대시보드!!.xlsx" --out global_dashboard.json
"""
import argparse
import datetime
import json
import sys

import openpyxl

COUNTRY_KO = {
    'BELGIUM': '벨기에', 'BRITAIN': '영국', 'CANADA': '캐나다', 'DENMARK': '덴마크',
    'FRANCE': '프랑스', 'GERMANY': '독일', 'HONG KONG': '홍콩', 'IRELAND': '아일랜드',
    'ISRAEL': '이스라엘', 'ITALY': '이탈리아', 'JERSEY': '저지', 'NETHERLANDS': '네덜란드',
    'SWEDEN': '스웨덴', 'SWITZERLAND': '스위스', 'TAIWAN': '대만',
    '대한민국': '대한민국', '미국': '미국', '일본': '일본', '중국': '중국',
}

# 원본 소스 자체에서 잘려 들어온 값 보정(2026-09-15 확인분). ticker 기준 매칭이 더 안전하지만
# 사명은 (사명 원문 그대로) 매칭 — 새 잘림이 또 생기면 여기 추가.
NAME_FIXES = {
    'Shanghai MicroPort MedBot Gr': 'Shanghai MicroPort MedBot Group',
    'Microport Cardioflow Medtech': 'MicroPort CardioFlow Medtech Corp',
    'Shanghai INT Medical Instrum': 'Shanghai INT Medical Instruments Co Ltd',
    'Ping An Healthcare and Techn': 'Ping An Healthcare and Technology Co Ltd',
    'Guangzhou Wondfo Biotech Co': 'Guangzhou Wondfo Biotech Co Ltd',
    '인테그라 라이프사이언시스 홀': '인테그라 라이프사이언시스 홀딩스',
    '찰스 리버 래버러토리스 인터': '찰스 리버 래버러토리스 인터내셔널',
    '산동 웨이가오 그룹 메디컬 폴': '산동 웨이가오 그룹 메디컬 폴리머',
}
SECTOR_FIXES = {
    '산업용 기계, 용품 및': '산업용 기계, 용품 및 부품',
    '기술 하드웨어, 스토리': '기술 하드웨어, 스토리지 및 주변기기',
    '생명 과학 도구 & 서비': '생명 과학 도구 및 서비스',
    '애플리케이션 소프트': '애플리케이션 소프트웨어',
}

# 국적(A열) 오타 정정 — 원본을 못 고치는 경우에 대비한 안전망(티커 기준). 지금은 비어있음
# (2026-09-15 스카이랩스 오타는 사용자가 원본 엑셀에서 직접 정정해 더 이상 필요 없음).
COUNTRY_OVERRIDES = {}

# 엔비디아 이하 반도체·산업자동화·로봇부품 등 의료기기 무관 참고 비교군(2026-09-15 요청)
# — index.html에서 이 티커들은 misc:true로 표시돼 섹터별 하위 탭에는 안 나오고 "전체" 탭에만 노출.
MISC_TICKERS = {t.lower() for t in [
    'NVDA US Equity', 'QCOM US Equity', 'SIE GR Equity', 'SU FP Equity', 'DE US Equity',
    'ABBN SW Equity', '6861 JP Equity', 'CDNS US Equity', 'EMR US EQUITY', 'ADSK US Equity',
    '6503 JP Equity', '2308 TT Equity', 'ROK US Equity', 'MCHP US Equity', '6954 JP Equity',
    'HEXAB SS Equity', '6273 JP Equity', 'PTC US Equity', 'ZBRA US Equity', 'TER US EQUITY',
    'SYM US EQUITY', 'CLS CN EQUITY', '2395 TT EQUITY', 'G1A GR EQUITY', '6383 JP EQUITY',
    '6506 JP EQUITY', 'CGNX US Equity', '6645 JP Equity', 'JBTM US EQUITY', '6841 JP EQUITY',
    'NOVT US EQUITY', '1590 TT EQUITY', 'KGX GR Equity', 'KRN GR EQUITY', 'AMBA US EQUITY',
    'IPGP US Equity', '2049 TT EQUITY', 'RSW LN EQUITY', '454910 KS EQUITY', 'ats cn EQUITY',
    '6324 JP EQUITY', 'AZTA US EQUITY', 'KARN SW EQUITY', '6268 JP EQUITY', '6134 JP EQUITY',
    'JEN GR EQUITY',
]}

# [2026-09-15] El.En./SofWave Medical을 수동으로 추가해야 하는 줄 알았으나, 확인해보니
# 원본 엑셀 85·86행에 이미 국가/섹터/사명/티커가 채워진 채로 들어있었다(재무 지표만 비어
# 있었을 뿐) — 그래서 별도 EXTRA_ROWS 없이도 load_rows()가 자연히 포함한다. 과거에 이
# 스크립트가 둘을 수동으로 또 추가해 완전 중복 행을 만든 적이 있으니, 엑셀에 실제로 없는
# 회사를 추가해야 할 때만 이 자리에 EXTRA_ROWS 리스트를 다시 만들어 쓸 것.


def clean(v):
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s == '' or s.startswith('#'):
            return None
        try:
            return float(s)
        except ValueError:
            return s
    return v


def load_rows(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb['값복사']
    rows = []
    for r in range(4, ws.max_row + 1):
        name = ws.cell(row=r, column=4).value
        country = ws.cell(row=r, column=2).value
        sector = ws.cell(row=r, column=3).value
        if not name and not country:
            continue
        vals = [clean(ws.cell(row=r, column=c).value) for c in range(5, 23)]  # E..V (18개)
        ticker = ws.cell(row=r, column=23).value

        name = NAME_FIXES.get(name, name)
        sector_joined = SECTOR_FIXES.get(sector, sector) if sector else sector
        sectors = [s.strip() for s in (sector_joined or '').split(',') if s.strip()]
        country_ko = COUNTRY_KO.get((country or '').strip(), country)
        ticker_key = (ticker or '').strip().lower()
        if ticker_key in COUNTRY_OVERRIDES:
            country_ko = COUNTRY_OVERRIDES[ticker_key]

        row = {
            'country': country_ko,
            'sectors': sectors,
            'name': name,
            'ticker': ticker,
            'mktcap': vals[0],
            'opm': vals[1:4],
            'per': vals[4:7],
            'ps': vals[7:10],
            'ev_ebitda': vals[10:13],
            'revenue': vals[13:16],
            'rev_growth': vals[16:18],
        }
        if (ticker or '').strip().lower() in MISC_TICKERS:
            row['misc'] = True
        rows.append(row)

    # 원본에 실수로 완전 중복 입력된 행 제거((사명,티커) 기준, 2026-09-15 확인)
    seen = set()
    deduped = []
    for row in rows:
        key = (row['name'].strip().lower(), (row['ticker'] or '').strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


DEFAULT_SOURCE = r'D:\★사용자 폴더\Desktop\dashboard files\글로벌 대시보드!!.xlsx'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default=DEFAULT_SOURCE, help='원본 Bloomberg 엑셀 파일 경로("값복사" 탭을 읽음)')
    ap.add_argument('--out', default='global_dashboard.json')
    args = ap.parse_args()

    try:
        rows = load_rows(args.source)
    except FileNotFoundError:
        print(f'[ERROR] 원본 엑셀을 찾을 수 없습니다: {args.source}', file=sys.stderr)
        sys.exit(1)
    except KeyError:
        print(f'[ERROR] "{args.source}"에 "값복사" 시트가 없습니다 — 탭 이름이 바뀌었는지 확인해주세요.', file=sys.stderr)
        sys.exit(1)

    sectors = sorted({s for row in rows for s in row['sectors']})
    out_data = {
        'updated': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'source': 'Bloomberg 컨센서스(글로벌 대시보드!!.xlsx "값복사" 탭, 로컬 터미널 세션에서 수동 갱신) 기반, 자동 스케줄 갱신 아님',
        'rows': rows,
        'sectors': sectors,
    }
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    print(f'완료: {args.out} (기업 {len(rows)}건, 섹터 {len(sectors)}개)', file=sys.stderr)


if __name__ == '__main__':
    main()
