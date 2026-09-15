#!/usr/bin/env python3
"""
"주가 Performance" 탭이 추적하는 종목 유니버스(scripts/tickers.json, 145개)를 "글로벌
대시보드" 탭의 모집단(global_dashboard.json, Bloomberg 엑셀 기반)과 동일하게 맞추는
동기화 스크립트(2026-09-16 요청).

- 대시보드의 46개 "산업재 참고 비교군"(엔비디아·화낙·오므론 등)은 제외한다 — 사용자와
  상의 결과, Performance는 "의료기기/헬스케어 주가 추적"이 목적이라 포함하면 취지가
  희석된다고 판단(258개사만 반영).
- tickers.json은 그대로 갱신하지 않고 이 스크립트가 매번 global_dashboard.json에서
  다시 파생시킨다 — 즉 앞으로 대시보드 쪽 데이터가 바뀌면(회사 추가/삭제, 국적 수정 등)
  이 스크립트를 다시 돌리면 Performance 쪽도 그대로 따라간다.
- "섹터" 필드는 기존 8개 카테고리(MedTech/로보틱스/미용/치과/IVD/디지털헬스/생명공학/
  Healthcare Provider)로 매핑한다 — 이 값을 수급 판독기 섹터 필터, 기업 스냅샷 드롭다운
  등 다른 화면에서 그대로 참조하고 있어서, 대시보드의 38개 세분 섹터를 그대로 쓰면
  그 화면들이 깨진다. 한 기업이 여러 세분 섹터를 가지면 SECTOR_PRIORITY 순서대로 첫
  매치를 쓴다(구체적인 의료기기 세부 분류를 뷰티/디지털헬스/생명공학보다 우선).
- "티커" 표기 변환(블룸버그 -> Yahoo Finance): 국가가 아니라 블룸버그 티커 안의 거래소
  코드로 판단한다(중국 A주냐 홍콩이냐가 "국적"만으로는 안 갈려서 — 상하이/선전에 상장된
  중국 기업도 홍콩 상장 중국 기업도 국적이 둘 다 "중국"으로 찍혀 있음). 한국(KS)은
  블룸버그가 코스피/코스닥을 구분 안 하고 전부 "KS"로만 표기해서 이 방식이 안 통해 —
  기존 tickers.json에 이미 있는 종목은 그 값을 그대로 재사용하고(사람이 검증해둔 값),
  새로 편입되는 종목만 KR_MARKET_OVERRIDES에 웹 검색으로 확인한 값을 수동으로 채워뒀다
  (2026-09-16 확인). 그 외 국가는 블룸버그 거래소 코드가 시장을 명확히 알려줘서
  기계적으로 변환 가능하다.
- 알려진 예외: 로슈(Roche Holding)는 대시보드 원본 엑셀이 옛날 티커("ROG SW Equity")를
  그대로 쓰고 있다 — 2026-03-17 SIX 스위스거래소에서 "ROG"(Genussschein)가 "ROP"
  (Participation Certificate)로 1:1 교환·재상장됐음을 웹 검색으로 확인(2026-09-16)해서
  TICKER_OVERRIDES로 "ROP.SW"를 강제한다. 대시보드 쪽 엑셀도 다음 갱신 때 "ROP SW
  Equity"로 고쳐야 한다.

사용법:
    python scripts/sync_ticker_universe.py --dashboard global_dashboard.json --out scripts/tickers.json
"""
import argparse
import json

# 세분 섹터 -> 기존 8개 카테고리. 앞쪽일수록 우선순위 높음(첫 매치 채택).
SECTOR_PRIORITY = [
    ('체외진단', 'IVD'),
    ('치과', '치과'),
    ('메디컬 에스테틱', '미용'),
    ('뷰티', '미용'),
    # 로보틱스/수술로봇보다 구체적인 MedTech 세부군을 먼저 검사한다 — 존슨앤드존슨처럼
    # "수술로봇" 태그가 하나 섞여 있어도 관절·순환계·안과가 본업인 종합 의료기기 회사는
    # 로보틱스 전문회사(인튜이티브 서지컬 등)와 묶이면 안 된다(2026-09-16 확인).
    ('당뇨', 'MedTech'),
    ('순환계', 'MedTech'),
    ('관절', 'MedTech'),
    ('안과', 'MedTech'),
    ('영상진단', 'MedTech'),
    ('인체조직', 'MedTech'),
    ('혁신치료', 'MedTech'),
    ('건강관리 장비', 'MedTech'),
    ('ODM', 'MedTech'),
    ('로보틱스', '로보틱스'),
    ('수술로봇', '로보틱스'),
    ('디지털헬스', '디지털헬스'),
    ('생명공학', '생명공학'),
    ('생명공학 서비스', '생명공학'),
    ('생명공학장비', '생명공학'),
    ('CDMO', '생명공학'),
    ('CRO', '생명공학'),
    ('의료서비스', 'Healthcare Provider'),
    ('건강보험', 'Healthcare Provider'),
]


def map_sector(sectors):
    for tag, category in SECTOR_PRIORITY:
        if tag in sectors:
            return category
    return 'MedTech'  # 매칭 안 되는 새 섹터가 생기면 우선 MedTech로(수동 재검토 필요)


# 블룸버그 티커의 거래소 코드(마지막에서 두 번째 토큰) -> (Yahoo 접미사, market 코드).
# 코드만으로 안 갈리는 두 곳은 별도 처리: KS(한국, 코스피/코스닥 구분 불가) · CH(중국
# A주, 상해/심천 구분은 종목코드 앞자리로 판단) · HK(홍콩, 4자리 zero-pad 필요).
EXCHANGE_MAP = {
    'US': ('', 'US'), 'UN': ('', 'US'), 'UW': ('', 'US'),
    'JP': ('.T', 'JP'), 'JT': ('.T', 'JP'),
    'GR': ('.DE', 'DE'), 'GY': ('.DE', 'DE'),
    'FP': ('.PA', 'FR'),
    'LN': ('.L', 'GB'),
    'SW': ('.SW', 'CH'), 'SE': ('.SW', 'CH'),  # 'SE'는 스웨덴이 아니라 스위스 상장 표기(론자 등)
    'SS': ('.ST', 'SE'),  # 스톡홀름(스웨덴) — Yahoo의 상하이 '.SS'와 글자만 같을 뿐 다른 의미
    'IM': ('.MI', 'IT'),
    'IT': ('.TA', 'IL'),  # 블룸버그 'IT'는 이탈리아가 아니라 텔아비브(이스라엘) 코드
    'TT': ('.TW', 'TW'),
    'BB': ('.BR', 'BE'),
}

# 로슈: 원본 엑셀이 2026-03-17 폐지된 옛 티커(ROG)를 그대로 쓰고 있어 최신 티커로 강제.
TICKER_OVERRIDES = {
    'rog sw equity': ('ROP.SW', 'CH'),
    # 엘렉타(Elekta AB) — 스웨덴 B주는 Yahoo에서 클래스 문자 앞에 하이픈이 필요한데
    # (EKTA-B.ST), 블룸버그 표기(EKTAB SS Equity)엔 하이픈이 없어 기계적 변환으로는
    # "EKTAB.ST"가 나와 실패했다(2026-09-16, Update stock performance 워크플로 오류로 발견).
    'ektab ss equity': ('EKTA-B.ST', 'SE'),
}

# 한국 신규 편입 종목의 코스피/코스닥 구분(2026-09-16 웹 검색으로 확인) — 기존
# tickers.json에 이미 있는 종목은 여기 안 거치고 원래 값을 그대로 재사용한다.
KR_MARKET_OVERRIDES = {
    '090430': 'KS',  # 아모레퍼시픽
    '051900': 'KS',  # LG생활건강
    '192820': 'KS',  # 코스맥스
    '161890': 'KS',  # 한국콜마
    '207940': 'KS',  # 삼성바이오로직스
    '078520': 'KS',  # 에이블씨엔씨 (2011년 코스닥→코스피 이전)
    '036220': 'KQ',  # 오상헬스케어
    '084650': 'KQ',  # 랩지노믹스
    '461030': 'KQ',  # 아이엠비디엑스
    '228670': 'KQ',  # 레이
    '086450': 'KQ',  # 동국제약
    '450950': 'KQ',  # 아스테라시스
    '149980': 'KQ',  # 하이로닉
    '240550': 'KQ',  # 동방메디컬
    '246710': 'KQ',  # 티앤알바이오팹
    '018290': 'KQ',  # 브이티
    '237880': 'KQ',  # 클리오
    '114840': 'KQ',  # 아이패밀리에스씨
    '439090': 'KQ',  # 마녀공장
    '214420': 'KQ',  # 토니모리
    '337930': 'KQ',  # 젝시믹스
    '119610': 'KQ',  # 인터로조
    '065510': 'KQ',  # 휴비츠
    '179290': 'KQ',  # 엠아이텍
    '049950': 'KQ',  # 미래컴퍼니
    '338220': 'KQ',  # 뷰노
    '384470': 'KQ',  # 코어라인소프트
}


def china_yahoo_suffix(code):
    return '.SS' if code.startswith('6') else '.SZ'


def convert_ticker(row, existing_kr_by_code):
    raw = (row.get('ticker') or '').strip()
    raw_lower = raw.lower()
    if raw_lower in TICKER_OVERRIDES:
        symbol, market = TICKER_OVERRIDES[raw_lower]
        return symbol, market

    parts = raw.split()
    code = parts[0] if parts else raw
    exch = parts[-2].upper() if len(parts) >= 2 else ''

    if exch == 'KS':
        code_bare = code.upper()
        if code_bare in existing_kr_by_code:
            return existing_kr_by_code[code_bare], 'KR'
        suffix = KR_MARKET_OVERRIDES.get(code_bare)
        if suffix is None:
            print(f'[WARN] 코스피/코스닥 확인 안 된 신규 한국 종목: {row.get("name")} ({raw}) — 우선 KQ로 처리, 확인 필요', flush=True)
            suffix = 'KQ'
        return f'{code_bare}.{suffix}', 'KR'

    if exch == 'CH':
        return f'{code.upper()}{china_yahoo_suffix(code)}', 'CN'

    if exch == 'HK':
        return f'{code.zfill(4)}.HK', 'HK'

    if exch in EXCHANGE_MAP:
        suffix, market = EXCHANGE_MAP[exch]
        return f'{code.upper()}{suffix}', market

    print(f'[WARN] 알 수 없는 거래소 코드 "{exch}": {row.get("name")} ({raw}) — 그대로 둠, 수동 확인 필요', flush=True)
    return code.upper(), row.get('country', '')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dashboard', default='global_dashboard.json')
    ap.add_argument('--existing', default='scripts/tickers.json',
                     help='한국 종목 코스피/코스닥 구분 등 이미 검증된 값을 재사용할 기존 tickers.json 경로')
    ap.add_argument('--out', default='scripts/tickers.json')
    args = ap.parse_args()

    with open(args.dashboard, encoding='utf-8') as f:
        dash = json.load(f)

    try:
        with open(args.existing, encoding='utf-8') as f:
            existing = json.load(f)
    except FileNotFoundError:
        existing = []
    existing_kr_by_code = {
        t['ticker'].split('.')[0].upper(): t['ticker']
        for t in existing if t.get('market') == 'KR'
    }

    rows = [r for r in dash['rows'] if not r.get('misc')]
    out = []
    seen_tickers = set()
    for r in rows:
        ticker, market = convert_ticker(r, existing_kr_by_code)
        if ticker in seen_tickers:
            continue  # 안전망(정상 데이터라면 안 걸릴 것)
        seen_tickers.add(ticker)
        out.append({
            'ticker': ticker,
            'name': r['name'],
            'sector': map_sector(r.get('sectors') or []),
            'market': market,
        })

    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f'완료: {args.out} ({len(existing)}개 -> {len(out)}개)')


if __name__ == '__main__':
    main()
