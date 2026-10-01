#!/usr/bin/env python3
"""
해외 트래킹 기업의 최근 뉴스를 medtech/헬스케어/로보틱스 전문매체 RSS 피드에서 가져와
news.json의 "global" 섹션을 자동 갱신한다. "domestic"(국내) 섹션은 scrape_news.py가
관리하므로 이 스크립트는 건드리지 않고 그대로 보존한다.

- API 키가 필요 없다: 아래 매체들이 공개 RSS 피드를 제공하며 실제 접속으로 확인했다.
  [MedTech 채널]
    · MedTech Dive            https://www.medtechdive.com/feeds/news/
    · Fierce Biotech(Medtech) https://www.fiercebiotech.com/rss/medtech/xml
    · Fierce Healthcare       https://www.fiercehealthcare.com/rss/xml
    · Healthcare Dive         https://www.healthcaredive.com/feeds/news/
      (Healthcare Provider/Digital Health 카테고리 보강용으로 추가)
    · MedCity News            https://medcitynews.com/feed/
      (2026-08-20 추가 — 하루 10건 이상 올라오는 고빈도 헬스테크 매체. MedTech/Digital
      Health/Healthcare Provider 전반의 원천 기사 풀을 넓히기 위해 추가.)
  [Digital Health 채널] — 이 카테고리가 유독 매칭 건수가 적어(Dexcom/RadNet/iRhythm/
  Hims&Hers/Teladoc 5개사) 전담 고빈도 소스를 추가로 보강했다.
    · MobiHealthNews          https://www.mobihealthnews.com/rss.xml
      (2026-08-20 추가 — 하루 여러 건씩 올라오는 디지털헬스 전문 매체.)
  [로보틱스(수술로봇) 채널] — 기존에는 이 카테고리를 전담하는 소스가 없어 수집량이 0에
  가까웠다. 신규 추가로 보강. (2026-09-17: Intuitive Surgical 등은 카테고리가 "수술로봇"
  →"로보틱스"로 재배치됐지만, 이 소스가 주로 다루는 주제 자체는 그대로라 이름만 갱신)
    · Surgical Robotics Technology https://www.surgicalroboticstechnology.com/feed/
  [치과/미용 채널] — InMode/Align/Straumann 등 미용·자비부담 시장 뉴스 보강용(2026-09-17:
  이 3사는 카테고리가 "비급여시장"→치과/미용으로 재배치됐다).
    · Dermatology Times       https://www.dermatologytimes.com/rss.xml
  [Robotics 채널] — medtech_news_clipping_rules.md(2026-08-18 최신본)에서 신규 추가된
  휴머노이드/산업용·서비스 로봇/로보틱스 밸류체인 3개 카테고리를 커버하기 위해 추가.
    · The Robot Report        https://www.therobotreport.com/feed
    · Robotics Tomorrow        https://www.roboticstomorrow.com/rss/news
    · TechCrunch(Robotics)     https://techcrunch.com/category/robotics/feed
    · Robotics & Automation News https://roboticsandautomationnews.com/feed/
      (2026-08-20 추가 — 하루 여러 건씩 올라오고, FANUC/ABB/KUKA/Schaeffler 등
      산업용·서비스 로봇/로보틱스 밸류체인 카테고리 기업이 실제로 자주 언급되는 것을
      확인했다. 예: "Hexagon starts training AEON humanoid robots at Schaeffler
      factories" 기사가 실제로 수집 테스트에서 확인됨.)
    · IEEE Spectrum(Robotics) https://spectrum.ieee.org/feeds/topic/robotics.rss
      (2026-08-20 추가 — 게재 빈도는 낮지만(주 2~3건) 휴머노이드 관련 심층 기사 위주라
      Humanoid 카테고리 보강용으로 추가.)
  (md가 언급한 massdevice.com/reuters.com/semafor.com/irobotnews.com/중국어 사이트는
  RSS가 없거나 접속 확인이 안 돼 제외 — massdevice는 이전에도 빈 응답이라 제외했었다
  (2026-08-20 재확인해도 여전히 빈 응답). dental-tribune.com은 피드가 2021년 테스트
  글 1건뿐이라 사실상 방치된 피드로 판단해 제외, 360dx.com은 rss.xml/공식 피드 URL
  모두 빈 응답이라 제외, theaestheticguide.com은 피드 응답이 비어 있어(확인 불가)
  제외했다. prnewswire.com의 "All Health" 피드도 확인했으나 반려동물 사료/치과의원
  마케팅 등 무관 보도자료가 대부분이고(회사명 매칭 필터가 있어 오탐 위험은 없지만)
  우리 41개 추적 기업의 실제 적중률이 낮아 보류했다.
  Google 뉴스 RSS 검색(news.google.com/rss/search, 기업명별 쿼리)도 검토했으나 이
  환경의 웹 접근 정책상 접속 확인이 불가능해(빈 응답) 검증하지 못했다 — 회사명별
  실시간 검색이라 커버리지가 가장 넓을 것으로 예상되는 방법이었던 만큼, 필요하면
  나중에 별도로 재검토할 수 있다.
  Google site: 검색 기반 수집(md의 원래 방법)은 자동화 스크립트로 안정적으로 재현하기
  어려워, 검증된 RSS 피드가 있는 소스만 사용한다.)
- 회사명 매칭은 단순 substring이 아니라 단어 경계 정규식으로 한다(예: "ABB"가 다른 영단어
  안에 우연히 포함되는 경우 방지).
- COMPANY_SEARCH_ALIASES: 모기업/제품명이 함께 언급되는 회사(예: Tesla(Optimus),
  KUKA(Midea Group)) — 별칭 중 하나라도 있으면 매칭.
- CONTEXT_REQUIRED_GLOBAL: Tesla/Bosch/Magna/Schaeffler처럼 초대형 기업이라 회사명만으로는
  자동차·일반 산업재 등 무관 뉴스가 압도적으로 많이 잡히는 경우, 로봇 관련 키워드가 함께
  있어야만 채택한다(국내 스크립트의 디오/현대차그룹 안전장치와 동일한 원리).
- 중복 처리: 제목 단어 겹침이 높은 기사는 하나만 남긴다(신디케이션/보도자료 재배포 방지).
- 매체마다 pubDate 포맷이 달라(RFC822 vs 'Aug 18, 2026 7:53am' 커스텀 포맷) feedparser의
  기본 파서로 안 되는 경우 직접 포맷을 하나 더 시도한다.
- 수집 윈도우는 scrape_news.py와 동일 규칙(평일 24시간 / 월요일 72시간, 공휴일 미반영).
  수집 결과가 0건이면 기존 news.json을 보존한다.

사용법:
    python scrape_news_global.py --out news.json
"""
import argparse
import datetime
import html
import json
import re
import sys

import feedparser
import requests

TAG_RE = re.compile(r"<[^>]+>")


def clean_text(s: str) -> str:
    return html.unescape(TAG_RE.sub("", s or "")).strip()


FEEDS = [
    # MedTech 채널
    "https://www.medtechdive.com/feeds/news/",
    "https://www.fiercebiotech.com/rss/medtech/xml",
    "https://www.fiercehealthcare.com/rss/xml",
    "https://www.healthcaredive.com/feeds/news/",
    "https://medcitynews.com/feed/",
    # Digital Health 채널 보강 (2026-08-20)
    "https://www.mobihealthnews.com/rss.xml",
    # Surgical Robot 채널 (기존에 전담 소스가 없었음)
    "https://www.surgicalroboticstechnology.com/feed/",
    # Aesthetics/Cash Pay 채널 보강
    "https://www.dermatologytimes.com/rss.xml",
    # Robotics 채널 (휴머노이드/산업용·서비스 로봇/로보틱스 밸류체인 공용 소스)
    "https://www.therobotreport.com/feed",
    "https://www.roboticstomorrow.com/rss/news",
    "https://techcrunch.com/category/robotics/feed",
    "https://roboticsandautomationnews.com/feed/",
    "https://spectrum.ieee.org/feeds/topic/robotics.rss",
]

MAX_ITEMS_PER_CATEGORY = 6
REQUEST_TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# 해외 기업명 -> 뉴스 카테고리. medtech_news_clipping_rules.md(2026-08-18 최신본)의
# 공식 기업 리스트를 기반으로, 2026-09-17에 tickers.json 마스터 섹터에 맞춰 카테고리를
# 재배치했다(아래 GLOBAL_COMPANY_CATEGORY 상단 주석 참고). 로보틱스 채널(휴머노이드 등)은
# 신규 추가됐던 부분 그대로 유지.
GLOBAL_COMPANY_CATEGORY = {
    # 2026-09-17: tickers.json에 있는 실제 메드텍 기업은 주가 Performance/기업 스냅샷과
    # 같은 마스터 섹터로 재배치했다(티커 기준으로 확인 — 예: Abbott은 회사명만 보면
    # 종합 의료기기 같지만 tickers.json엔 "체외진단"으로 분류돼 있어 그대로 따랐다).
    # "수술로봇"·"비급여시장" 카테고리는 소속 기업이 전부 다른 마스터 섹터로 옮겨가며
    # 빈 카테고리가 돼 폐지했다(Intuitive Surgical→로보틱스, Align/Straumann→치과,
    # InMode→미용 등). 휴머노이드/산업용·서비스 로봇/로보틱스 밸류체인은 tickers.json에
    # 없는 로보틱스 비교군이라 그대로 둔다.
    # [의료기기 채널]
    # 1. 의료기기
    "Stryker": "의료기기",
    "Medtronic": "의료기기",
    "Boston Scientific": "의료기기",
    "Edwards Lifesciences": "의료기기",
    "Edge Medical": "의료기기",
    "Microport MedBot": "의료기기",
    "Dexcom": "의료기기",
    "RadNet": "의료기기",
    # 2. 로보틱스
    "Intuitive Surgical": "로보틱스",
    # 3. 체외진단
    "Abbott": "체외진단",
    "Thermo Fisher": "체외진단",
    # 4. 생명공학
    "Natera": "생명공학",
    "Guardant Health": "생명공학",
    "Tempus AI": "생명공학",
    # 5. 디지털헬스
    "iRhythm": "디지털헬스",
    "Hims & Hers": "디지털헬스",
    "Hims and Hers": "디지털헬스",
    "Teladoc": "디지털헬스",
    # 6. 의료서비스
    "UnitedHealth": "의료서비스",
    # 7. 치과
    "Align Technology": "치과",
    "Straumann": "치과",
    # 8. 미용
    "InMode": "미용",
    # [로보틱스 채널]
    # 9. 휴머노이드 (2026-09-17 국내/해외/index.html 표기를 전부 한글로 통일 — 예전엔
    # 국내 md가 "Humanoid"(영문), 해외가 "휴머노이드"(한글)로 갈려있어 국내 md 표기를
    # 기준으로 영문을 썼었는데, 이제 셋 다 "휴머노이드"로 맞췄다)
    "Tesla": "휴머노이드",
    "Figure AI": "휴머노이드",
    "Agility Robotics": "휴머노이드",
    "Boston Dynamics": "휴머노이드",
    "Unitree": "휴머노이드",
    "AgiBot": "휴머노이드",
    "UBTECH": "휴머노이드",
    "Leju": "휴머노이드",
    # 10. 산업용·서비스 로봇 (국내/해외 md 공통 한글 표기)
    "FANUC": "산업용·서비스 로봇",
    "ABB": "산업용·서비스 로봇",
    "KUKA": "산업용·서비스 로봇",
    "Yaskawa": "산업용·서비스 로봇",
    "Universal Robots": "산업용·서비스 로봇",
    "Estun": "산업용·서비스 로봇",
    "Inovance": "산업용·서비스 로봇",
    # 11. 로보틱스 밸류체인 (국내/해외 md 공통 한글 표기)
    "Harmonic Drive Systems": "로보틱스 밸류체인",
    "Nabtesco": "로보틱스 밸류체인",
    "Schaeffler": "로보틱스 밸류체인",
    "Bosch": "로보틱스 밸류체인",
    "Magna": "로보틱스 밸류체인",
}

CATEGORY_ORDER = [
    "의료기기", "로보틱스", "체외진단", "생명공학", "디지털헬스", "의료서비스", "치과", "미용",
    "휴머노이드", "산업용·서비스 로봇", "로보틱스 밸류체인",
]

# 모기업/제품명이 함께 언급되는 회사 — 별칭 중 하나라도 있으면 매칭으로 인정.
COMPANY_SEARCH_ALIASES = {
    "Tesla": ["Tesla", "Optimus"],
    "KUKA": ["KUKA", "Midea Group"],
    "Universal Robots": ["Universal Robots", "Teradyne"],
}

# 초대형 기업이라 회사명만으로는 무관 뉴스(자동차/반도체/일반 산업재 등)가 압도적으로 많이
# 잡히는 경우, 로봇 관련 키워드가 함께 있어야만 채택한다.
CONTEXT_REQUIRED_GLOBAL = {
    "Tesla": ["optimus", "humanoid", "robot"],
    "Bosch": ["robot", "actuator", "automation"],
    "Magna": ["robot", "actuator"],
    "Schaeffler": ["robot", "actuator", "harmonic drive", "gearbox"],
}

# --- 콘텐츠 품질 필터 (2026-09-14 추가) ---------------------------------------------
# scrape_news.py(국내)는 회사명 매칭 뒤에도 "단순 종목 나열/시황 칼럼/랭킹형/홍보성" 기사를
# 통째로 거르는 is_excluded_article_type()이 있는데, 이 스크립트(해외)는 그동안 회사명
# 매칭만 하고 이런 필터가 전혀 없었다(사용자 지적, 2026-09-14) — 그래서 미국 증권 집단소송
# 로펌이 뿌리는 "OO 주주는 연락하라"류 보도자료(PR Newswire 등으로 대량 배포돼 회사명
# 매칭에 자주 걸림, 실제로 "ROSEN, NATIONAL TRIAL COUNSEL, Encourages Hims & Hers" 사례로
# 확인됨)나 "Top 10 stocks" 식 랭킹 기사가 그대로 섞여 들어왔다. 국내처럼 카테고리
# 화이트리스트 전체를 영문으로 포팅하는 대신, 가장 흔하고 명확한 두 가지 스팸 유형만
# 통째로 제외한다(단순 주가/자금흐름 기사 자체는 summarize_news.py가 요약 후 라벨로 걸러냄).
LAW_FIRM_SPAM_KEYWORDS = [
    "class action", "investor alert", "shareholder alert", "shareholder rights",
    "national trial counsel", "encourages investors", "encourages shareholders",
    "securities fraud", "lead plaintiff", "rosen law", "pomerantz", "bragar eagel",
    "kahn swick", "levi & korsinsky", "glancy prongay", "halper sadeh", "schall law",
    "faruqi", "johnson fistel", "law firm",
    # 2026-10-01: 제목이 잘린 채 들어와 위 키워드를 피해간 로펌 보도자료. 원문 확인 결과
    # (Bronstein의 Stryker 건) 9월 한 달에만 같은 내용이 거의 이틀마다 재배포됐고, 인용하는
    # 사건(공급 차질·사이버공격)은 이미 일반 기사로 수집된 과거 이벤트였다.
    "bronstein", "gewirtz", "kaplan fox", "hagens berman", "hbss", "deadline alert",
    "leading national firm", "with losses", "leadership role",
]
RANKING_TITLE_PATTERNS_GLOBAL = [
    # "top 10"만 보고 걸렀더니 "Thermo Fisher Junior Innovators Challenge Top 300
    # Qualifier"(청소년 과학경진대회 기사)까지 오탐되어(2026-09-14 실측), "stocks"가 함께
    # 있을 때만 매칭하도록 좁혔다.
    re.compile(r"\btop\s*\d+\s+(\w+\s+){0,2}stocks?\b", re.IGNORECASE),
    re.compile(r"\bbest\s+\d*\s*stocks?\b", re.IGNORECASE),
    re.compile(r"\bstocks?\s+to\s+(buy|watch|sell)\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+(\w+\s+){0,2}stocks?\s+(to|that|for)\b", re.IGNORECASE),
]


# --- 비사업성 기사 필터 (2026-10-01 추가) --------------------------------------------
# 사용자 검토(2026-10-01)에서 "주가 등락에 유의미한 영향이 없는" 기사가 다수 지적됐다 —
# 은퇴 임원 부고, 동명 고교 스포츠팀 경기 결과, 히스패닉 유산의 달 기념, 로타리 기부 등.
# 누적 아카이브 전체를 훑어보니 같은 유형이 반복돼서 제목 패턴으로 통째로 제외한다
# (제목 기준 — Google 뉴스 RSS는 desc가 사실상 제목+매체명이라 제목만 봐도 충분).
NON_BUSINESS_TITLE_PATTERNS_GLOBAL = [
    # 부고
    re.compile(r"\bobituary\b|\bdie[sd] at \d+|\bpassed away\b|\bfuneral\b", re.IGNORECASE),
    # 스포츠(동명 학교/선수/경기장 — "Stryker Volleyball", "Dexcom Stadium" 등)
    re.compile(r"\b(volleyball|football|basketball|baseball|softball|rugby|varsity|stadium)\b"
               r"|\blive score\b|\bplayer stats\b|\bstat leader", re.IGNORECASE),
    # CSR/기념행사/학생 경진대회
    re.compile(r"\bheritage month\b|\brotary\b|\bdonat(e|es|ed|ion)\b|\bjunior innovators\b"
               r"|\bscience fair\b", re.IGNORECASE),
    # 시세·데이터 페이지(토큰화 주식, 환율 변환기, 파생/채권 시세표)
    re.compile(r"\btokeni[sz]ed\b|\bxstock\b|\brstock\b|\bmexc\b|\bconvert(er)?\b"
               r"|\bprice today\b|\bbond rates\b|\bbtic\b", re.IGNORECASE),
    # "... Revenue Breakdown – HAM:FUC"처럼 제목 끝에 거래소:코드가 붙는 데이터 페이지
    re.compile(r"[–—-]\s*[A-Z]{2,5}:[A-Z0-9.]+\s*$"),
    # 랜덤 ID가 붙은 스팸 페이지("... (UCVSyQVw74)", "... - View pvlhGZiwl")
    re.compile(r"\([A-Za-z0-9]{10}\)|\bView [A-Za-z0-9]{9}\s*$"),
    # 쇼핑/굿즈
    re.compile(r"\bebay\b|\bpreorder\b|\bmodel kit\b", re.IGNORECASE),
]

# --- 새 이벤트 없는 주가/지분 단신 필터 (2026-10-01 추가) --------------------------------
# 기준: "주가를 움직이는 새 이벤트가 있는가". 아래 유형은 원문을 표본 확인한 결과 모두
# 이벤트의 '결과'를 틀에 맞춰 자동 생성한 글이고, 원인 이벤트는 일반 기사로 이미 수집된다.
#  · ad-hoc-news.de "X stock holds/trades steady as ..." — 같은 제목 틀로 며칠마다 반복,
#    본문은 지난 분기(심지어 2024 연간) 실적 재탕
#  · MarketWatch 자동 기사 "stock underperforms Tuesday when compared to competitors" — 일일
#    등락률·거래량 나열
#  · MarketBeat 13F "OO Has $7.6M Position in Stryker $SYK" — 이미 끝난 분기 보유현황
#  · 세금 원천징수/주식보상 지급 등 비재량적 내부자 신고, Form 4/144 공시 목록 페이지
#    (반면 임원의 재량적 대규모 매도는 신호가 될 수 있어 남겨둔다)
#  · Zacks "$1000 invested 10 years ago", 밸류에이션 서사(fair value/undervalued), 종목 비교형
#    칼럼, 시장조사 보고서 판매 보도자료("... Market 2026-2030 Featuring Profiles of ...")
STOCK_FILLER_TITLE_PATTERNS_GLOBAL = [
    re.compile(r"\bstock (holds|trades|steadies|steady|stays|stabilizes|finds support|digests|reports)\b",
               re.IGNORECASE),
    re.compile(r"\bstock (gains|rises|falls|slips|eases|edges \w+|dips|advances)( modestly| slightly)?"
               r"( [\d.]+ percent)? as\b", re.IGNORECASE),
    re.compile(r"\b(under|out)performs?\b.*\b(competitors|market)\b"
               r"|\b(under|out)performing the (nasdaq|dow|s&p|market)\b", re.IGNORECASE),
    re.compile(r"\$[A-Z]{1,5}\s*$"),  # MarketBeat 13F 제목 형식 "... Corporation $SYK"
    re.compile(r"\bholding history\b|\bbuys new \$[\d.]+[MB]? stake\b", re.IGNORECASE),
    re.compile(r"^form (3|4|144)\b|\bschedule 13d\b|\binitial statement of beneficial ownership\b"
               r"|\btax[- ]withholding\b|\bto cover (the )?(exercise price|taxes)\b|\bcover taxes\b"
               r"|\bstock units\b|\bstock awards?\b|\bmatrimonial\b|\bemployee stock plan\b",
               re.IGNORECASE),
    re.compile(r"\binvest\w*\b.*\byears ago\b|\breturns [\d.]+% annually\b", re.IGNORECASE),
    re.compile(r"\bfair value\b|\bundervalued\b|\bovervalued\b|\bbargain\b|\bgf score\b|\bgf val",
               re.IGNORECASE),
    re.compile(r"\btop research reports\b|\bfinal trades\b|\banalyst blog highlights\b"
               r"|\bbrokers suggest investing\b|\bwall street bulls look optimistic\b"
               r"|\battracting investor attention\b|\binvestors heavily search\b|\btoo late to buy\b"
               r"|\bshould you buy\b|\breasons to retain\b|\bbetter buy\b|\brevisiting stock picks\b"
               r"|\blatest stock news\b", re.IGNORECASE),
    re.compile(r"\bvs\.?\b.*\bwhich\b", re.IGNORECASE),  # "Abbott vs. DexCom: Which CGM Stock ..."
    re.compile(r"\bmarket (report|outlook|global report)\b|\bfeaturing profiles\b|\bkey players\b"
               r"|\bmarket,? \d{4}\s*-\s*\d{4}\b", re.IGNORECASE),
]

# 회사명이 흔한 성(姓)/단어/다른 브랜드와 겹쳐 오탐이 반복 확인된 경우의 제외 키워드
# (haystack 소문자 substring). 이 키워드가 있으면 "그 회사" 매칭만 무효 처리한다.
COMPANY_NEGATIVE_KEYWORDS = {
    # "Optimus" 별칭이 트랜스포머 옵티머스 프라임/샌디스크 SSD 기사에 걸림
    "Tesla": ["optimus prime", "transformers", "hasbro", "peter cullen", "sandisk"],
    # 인물(Tad/Susan Stryker), 학교, 미 육군 스트라이커 장갑차(General Dynamics)
    "Stryker": ["tad stryker", "susan stryker", "mary anne stryker", "stryker berglund",
                "stryker schools", "general dynamics", "dvha", "stryker brigade"],
    # 호주 통신사(ASX:ABB), 아제르바이잔 은행, 볼리비아 축구팀, 블록딜(ABB=accelerated bookbuild)
    "ABB": ["aussie broadband", "asx:abb", "abb bank", "davr bank", "real oruro",
            "bookrunner", "bookbuild", "han:abb"],
    "Dexcom": ["dexcom stadium"],
    "Natera": ["samy natera", "victor ray natera"],
    "KUKA": ["kuka home"],  # 가구 브랜드
    "Edge Medical": ["cutting-edge medical", "cutting edge medical"],
    "Abbott": ["greg abbott", "tony abbott"],
}

# 이름이 짧아 본문(desc)에만 우연히 등장하는 오탐이 많은 회사 — 제목에 있어야만 인정.
# (ABB: 파키스탄 정치·인도네시아 산불 기사 등이 desc 매칭으로 딸려 들어온 사례 확인)
# 단, 매체명(src)이 회사명과 정확히 같으면(= ABB 자사 뉴스룸) 제목에 없어도 인정한다 —
# ABB 뉴스룸은 "How direct current is rewiring power infrastructure"처럼 제목에 자사명을
# 안 쓰는 경우가 많다(2026-10-01 news.json 이력에서 src="ABB" 3건 확인).
TITLE_MATCH_REQUIRED = {"ABB"}


def is_excluded_article_type_global(title: str, desc: str) -> bool:
    """소송 유치 스팸/랭킹형 리스티클/비사업성 기사인지 판별(회사명 매칭과 무관하게 제외).
    scrape_news.py의 is_excluded_article_type()과 동일한 역할의 영문 버전."""
    hay = f"{title} {desc}".lower()
    if any(kw in hay for kw in LAW_FIRM_SPAM_KEYWORDS):
        return True
    if any(p.search(title) for p in RANKING_TITLE_PATTERNS_GLOBAL):
        return True
    if any(p.search(title) for p in NON_BUSINESS_TITLE_PATTERNS_GLOBAL):
        return True
    if any(p.search(title) for p in STOCK_FILLER_TITLE_PATTERNS_GLOBAL):
        return True
    return False


CUSTOM_DATE_FMT = "%b %d, %Y %I:%M%p"  # Fierce 계열 매체가 쓰는 'Aug 18, 2026 7:53am' 형식


def recency_hours_for_today(today: datetime.date) -> int:
    """scrape_news.py와 동일한 단순화 규칙: 월요일(KST 기준 실행일)은 72시간,
    그 외 평일은 24시간. 이 스크립트는 KST 평일에만 실행되므로(cron 0-4=일~목 UTC),
    한국 공휴일 반영은 아직 없다 — medtech_news_clipping_rules.md가 경고하는
    '해외는 토요일에도 기사가 나올 수 있다'는 점은 실행 요일이 아니라 실행 간격(주말 공백)
    문제라 이 규칙으로 충분히 커버된다."""
    if today.weekday() == 0:  # Monday
        return 72
    return 24


def parse_entry_date(entry):
    if entry.get("published_parsed"):
        import time
        return time.strftime("%Y-%m-%d", entry.published_parsed), datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc)
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None, None
    try:
        dt = datetime.datetime.strptime(raw.strip(), CUSTOM_DATE_FMT)
        return dt.strftime("%Y-%m-%d"), dt.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None, None


def _word_boundary_pattern(term: str):
    """단순 substring이 아니라 앞뒤로 영문/숫자가 붙어있지 않은 경우만 매칭
    (예: "ABB"가 다른 영단어 일부로 우연히 포함되는 것 방지). 대소문자 무시."""
    return re.compile(rf"(?<![0-9A-Za-z]){re.escape(term)}(?![0-9A-Za-z])", re.IGNORECASE)


def company_mentioned(company: str, haystack: str) -> bool:
    for alias in COMPANY_SEARCH_ALIASES.get(company, [company]):
        if _word_boundary_pattern(alias).search(haystack):
            return True
    return False


def context_ok(company: str, haystack: str) -> bool:
    required = CONTEXT_REQUIRED_GLOBAL.get(company)
    if not required:
        return True
    hay = haystack.lower()
    return any(kw.lower() in hay for kw in required)


def has_negative_keyword(company: str, haystack: str) -> bool:
    hay = haystack.lower()
    return any(kw in hay for kw in COMPANY_NEGATIVE_KEYWORDS.get(company, []))


def _title_match_ok(company: str, title: str, src: str) -> bool:
    return company_mentioned(company, title) or src.strip() == company


def match_company(title: str, summary: str, src: str = ""):
    """기사에 해당하는 (회사, 카테고리)를 고른다. 없으면 None.
    scrape_news_global_gsearch.py도 이 함수를 그대로 써서 두 수집 경로의 기준을 맞춘다."""
    haystack = f"{title} {summary}"
    candidates = [
        (company, category)
        for company, category in GLOBAL_COMPANY_CATEGORY.items()
        if (_title_match_ok(company, title, src) if company in TITLE_MATCH_REQUIRED
            else company_mentioned(company, haystack))
        and context_ok(company, haystack)
        and not has_negative_keyword(company, haystack)
    ]
    if not candidates:
        return None
    # 한 기사에 여러 회사명이 동시에 매칭되면(예: "Agility Robotics ... Tesla's
    # backyard"), 안전장치가 걸린(초대형 대기업) 회사보다 그렇지 않은·이름이 더
    # 구체적인(긴) 회사를 우선한다 — 그래야 이 기사가 Tesla로 오귀속되지 않고
    # 실제 주인공인 Agility Robotics로 붙는다.
    return min(candidates, key=lambda c: (c[0] in CONTEXT_REQUIRED_GLOBAL, -len(c[0])))


def _title_tokens(title: str):
    return set(re.sub(r"[^0-9A-Za-z\s]", " ", title).lower().split())


def is_duplicate_title(a: str, b: str) -> bool:
    """제목 단어 집합의 겹침 비율이 높으면 동일 내용 기사로 간주(신디케이션 등)."""
    wa, wb = _title_tokens(a), _title_tokens(b)
    if not wa or not wb:
        return False
    overlap = len(wa & wb) / max(1, min(len(wa), len(wb)))
    return overlap >= 0.6


def fetch_feed(url: str, debug=False):
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    if debug:
        print(f"[DEBUG] {url}: HTTP {resp.status_code}, entries={len(parsed.entries)}", file=sys.stderr)
        if parsed.bozo:
            print(f"[DEBUG] {url}: feedparser bozo(파싱 경고)={parsed.bozo_exception}", file=sys.stderr)
    return parsed.entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="news.json")
    args = ap.parse_args()

    try:
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    existing_domestic = existing.get("domestic", [])

    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff_hours = recency_hours_for_today(now.astimezone().date())
    cutoff = now - datetime.timedelta(hours=cutoff_hours)
    print(f"[INFO] 오늘 기준 수집 윈도우: 최근 {cutoff_hours}시간 이내", file=sys.stderr)

    by_category = {cat: [] for cat in CATEGORY_ORDER}
    seen_links = set()
    total_entries = 0
    debug_budget = 3

    for feed_url in FEEDS:
        try:
            entries = fetch_feed(feed_url, debug=debug_budget > 0)
            debug_budget -= 1
        except Exception as e:
            print(f"[WARN] {feed_url}: 피드 수집 실패 ({e})", file=sys.stderr)
            continue
        total_entries += len(entries)

        for entry in entries:
            title = clean_text(entry.get("title") or "")
            summary = clean_text(entry.get("summary") or entry.get("description") or "")
            link = entry.get("link")
            if not title or not link or link in seen_links:
                continue
            date_str, dt = parse_entry_date(entry)
            if date_str is None or dt is None or dt < cutoff:
                continue

            if is_excluded_article_type_global(title, summary):
                continue

            matched = match_company(title, summary)
            if matched is None:
                continue
            company, category = matched
            by_category.setdefault(category, []).append({
                "co": company,
                "ctx": "News",
                "t": title,
                "src": feed_url.split("/")[2].replace("www.", ""),
                "date": date_str,
                "url": link,
                "desc": summary,  # summarize_news.py가 2줄 한글 요약을 생성할 때 참고용.
                                  # 최종 화면에는 노출하지 않는 내부 필드.
            })
            seen_links.add(link)

    # 카테고리별로 동일 내용(신디케이션 등) 기사 중복 제거
    for cat in list(by_category.keys()):
        kept = []
        for c in by_category[cat]:
            dup_idx = next((i for i, k in enumerate(kept) if is_duplicate_title(c["t"], k["t"])), None)
            if dup_idx is None:
                kept.append(c)
        by_category[cat] = kept

    ok_categories = sum(1 for items in by_category.values() if items)
    if ok_categories == 0:
        # 2026-08-21: 이전에는 sys.exit(1)로 처리해서 "0건 매칭"이 곧 워크플로 실패(빨간
        # X)로 보였는데, 실제 실행 로그를 보니(272개 기사 훑었지만 0건 매칭) 이건 대부분
        # 스크래퍼 고장이 아니라 그날 RSS에 41개 추적 기업 중 하나도 언급이 안 된, 그냥
        # 조용한 뉴스일(quiet day)이었을 뿐이었다 — MedTech Dive/FierceHealthcare 등은
        # FDA 인사·정책 뉴스처럼 특정 기업명이 안 나오는 기사 비중이 원래 높다. 이걸
        # 매번 "실패"로 표시하면 실제 문제(피드 자체가 깨진 경우)와 구분이 안 돼 알림
        # 피로만 커지므로, sys.exit(0)으로 낮추고 경고 로그만 남긴다. 기존 news.json
        # 보존 동작은 그대로 유지(global_section을 아예 안 만들고 여기서 종료).
        print(f"[WARN] 총 {total_entries}개 기사를 훑었지만 매칭된 회사가 0개입니다(그날 언급이 "
              f"없었을 가능성이 높음 — 실제 스크래퍼 고장이면 [DEBUG] HTTP 상태/entries 로그를 "
              f"확인). 기존 news.json의 해외 섹션을 그대로 보존하고 종료합니다.", file=sys.stderr)
        sys.exit(0)

    global_section = []
    for cat in CATEGORY_ORDER:
        items = sorted(by_category.get(cat, []), key=lambda x: x["date"], reverse=True)[:MAX_ITEMS_PER_CATEGORY]
        global_section.append({"cat": cat, "items": items})

    payload = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": existing.get("source", "") or "네이버 뉴스검색 API(국내, 자동) + medtech/헬스케어/로보틱스 매체 RSS(해외, 자동) · 뉴스클리핑 가이드라인 카테고리 기준",
        "domestic": existing_domestic,
        "global": global_section,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    total_items = sum(len(g["items"]) for g in global_section)
    print(f"저장 완료: {args.out} (해외 {ok_categories}개 카테고리, {total_items}건 / 국내는 기존 값 {len(existing_domestic)}개 카테고리 유지)")


if __name__ == "__main__":
    main()
