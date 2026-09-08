#!/usr/bin/env python3
"""
해외기업 실적 발표 IR 공식 자료(보도자료) 기반 "해외기업 컨콜" 데이터 수집.

- 배경: 사용자가 처음 요청한 방식은 TIKR(유료 실적 컨콜 스크립트 사이트)을 스크래핑해
  무료 열람 3개월 제한을 우회, 스크립트 원문을 계속 누적 저장하는 것이었다. 이건 (1) TIKR
  이용약관 위반 소지가 크고 (2) 스크립트가 TIKR 자체 저작물이 아니라 제3자 라이선스 콘텐츠일
  가능성이 높아 재배포 문제가 있어 채택하지 않았다(2026-09-08 검토). 대신 기업이 직접
  공개하는 IR 보도자료(공식 발표자료)를 근거로 한다 — Q&A 전체 스크립트는 없지만, 매출·
  수익성·가이던스·경영진 코멘트 같은 핵심 내용은 보도자료에 이미 담겨 있다.

- [2026-09-08] 방식 통일: 처음엔 회사마다 다른 경로(Q4 IR 플랫폼의 공개 JSONP 피드,
  회사별 뉴스룸 사이트를 <a> 태그 범용 스캔)를 썼다. 하지만 4개사(ABT/ISRG/ALGN/TEM)를
  추가 조사하는 과정에서, 미국 상장사는 실적 발표 시 의무적으로 8-K 공시를 제출하고 그
  안에 보도자료 원문을 "Exhibit 99.x" 첨부문서로 넣는다는 걸 확인했다 — SEC가 이걸
  data.sec.gov 공식 무료 JSON API(키 불필요, 기계 판독 접근을 명시적으로 권장)로 그대로
  공개한다. 이 방식은 (a) 회사가 어떤 IR 플랫폼을 쓰든 상관없이 미국 상장사 전체에 동일하게
  적용 가능하고 (b) 정부 공시 시스템이라 이용약관/저작권 리스크가 사실상 없고 (c) 회사가
  IR 사이트를 개편해도 안 깨진다는 점에서 이전 방식보다 명백히 우월해, 회사별 스크래퍼를
  전부 걷어내고 이 방식 하나로 통일했다(scripts/calendar_ir_sources.json 18개사 중
  17개사가 미국 국내 상장사로 8-K를 제출). CIK(회사 고유 식별번호)는 SEC의
  company_tickers.json(https://www.sec.gov/files/company_tickers.json, 티커→CIK
  공식 매핑)으로 미리 확인해 EDGAR_SOURCES에 하드코딩해뒀다(회사당 한 번만 확인하면 되고
  거의 바뀌지 않는 값이라 매 실행마다 다시 조회할 필요는 없다고 판단).
  - 예외: InMode(INMD)는 이스라엘 기업이라 미국 국내기업용 8-K가 아니라 해외기업용
    6-K를 제출한다. 6-K도 같은 방식(첨부문서에 "Exhibit 99.1"로 보도자료를 넣음)을 실제
    확인해서(2026-09-08, 최근 6-K accession 0001178913-26-003831 안에
    "exhibit_99-1.htm" 존재 확인) 폼 타입만 다르게 주고 같은 함수로 처리한다.
  - 8-K는 실적 발표 외에도 다양한 공시(임원 변경, M&A 등)에 쓰이므로, submissions
    JSON이 주는 "items" 필드에 "2.02"(Results of Operations and Financial Condition)가
    있는 공시를 우선으로 고른다 — 6-K는 items 분류가 없어 이 필터를 못 쓰므로, 그냥
    최근 6-K부터 순서대로 뒤져 99번대 첨부문서가 있는 첫 건을 쓴다(InMode는 6-K를 자주
    내는 편은 아니라 대부분 맞을 것으로 예상, 틀리면 다음 실행 때 다른 게 걸릴 수 있음
    — 완벽하진 않지만 최악의 경우도 "실적 아닌 공시를 실적으로 오인" 정도라 번역 결과를
    보면 바로 눈치챌 수 있음).

- 번역/요약 provider: GEMINI_API_KEY_EARNINGS(이 스크립트 전용 무료 키, 있으면 우선) →
  없으면 GEMINI_API_KEY(뉴스 요약/월간 브리핑과 공유하는 기존 키) → 둘 다 없으면
  ANTHROPIC_API_KEY(Claude, 유료) 순으로 시도한다. 전부 없으면 원문 영어만 metrics
  없이 저장하고 조용히 종료한다(부분 실패로 워크플로를 실패시키지 않음). 전용 키를
  따로 쓰는 이유(2026-09-08): 뉴스 요약·월간 브리핑이 이미 GEMINI_API_KEY의 하루 20회
  무료 쿼터를 쓰고 있어서, 이 스크립트가 같은 키를 쓰면 서로 쿼터를 나눠 쓰다 소진되는
  문제가 실제로 있었다 — 완전히 독립된 무료 키를 하나 더 만들면(aistudio.google.com/apikey,
  신용카드 불필요) 셋이 각자 하루 20회씩 쓸 수 있다.

- 누적: earnings_ir.json은 회사(ticker)당 최근 8개 분기만 유지한다. 이미 수집한 공시인지는
  Exhibit 문서의 URL(회사·공시마다 유일)로 판별해 중복 번역을 막는다.

사용법:
    python scrape_earnings_ir.py --out earnings_ir.json
"""
import argparse
import datetime
import json
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
MAX_QUARTERS_PER_TICKER = 8

# SEC EDGAR 공시 대상 18개사 — https://www.sec.gov/files/company_tickers.json로 확인한
# CIK(10자리 0 패딩). form은 대부분 "8-K"(미국 국내 상장사), InMode만 "6-K"(외국민간발행인).
EDGAR_SOURCES = {
    "SYK":  {"name": "Stryker",              "cik": "0000310764", "form": "8-K"},
    "DXCM": {"name": "Dexcom",                "cik": "0001093557", "form": "8-K"},
    "EW":   {"name": "Edwards Lifesciences",  "cik": "0001099800", "form": "8-K"},
    "TMO":  {"name": "Thermo Fisher Scientific", "cik": "0000097745", "form": "8-K"},
    "HIMS": {"name": "Hims & Hers Health",    "cik": "0001773751", "form": "8-K"},
    "IRTC": {"name": "iRhythm Technologies",  "cik": "0001388658", "form": "8-K"},
    "TDOC": {"name": "Teladoc Health",        "cik": "0001477449", "form": "8-K"},
    "NTRA": {"name": "Natera",                "cik": "0001604821", "form": "8-K"},
    "GH":   {"name": "Guardant Health",       "cik": "0001576280", "form": "8-K"},
    "MDT":  {"name": "Medtronic",             "cik": "0001613103", "form": "8-K"},
    "BSX":  {"name": "Boston Scientific",     "cik": "0000885725", "form": "8-K"},
    "UNH":  {"name": "UnitedHealth Group",    "cik": "0000731766", "form": "8-K"},
    "RDNT": {"name": "RadNet",                "cik": "0000790526", "form": "8-K"},
    "ABT":  {"name": "Abbott",                "cik": "0000001800", "form": "8-K"},
    "ISRG": {"name": "Intuitive Surgical",    "cik": "0001035267", "form": "8-K"},
    "ALGN": {"name": "Align Technology",      "cik": "0001097149", "form": "8-K"},
    "TEM":  {"name": "Tempus AI",             "cik": "0001717115", "form": "8-K"},
    "INMD": {"name": "InMode",                "cik": "0001742692", "form": "6-K"},
}

# SEC는 자동화 요청(특히 data.sec.gov)에 "이름 연락처이메일" 형식의 식별 가능한
# User-Agent를 요청한다(https://www.sec.gov/os/accessing-edgar-data) — 아래는 예시 값이니
# 실제 운영 시 프로젝트 관리자의 실제 연락처로 바꾸는 게 SEC 정책 취지에 더 맞는다.
SEC_HEADERS = {"User-Agent": "medtech-dashboard research contact@example.com"}

# 8-K/6-K 첨부문서 파일명에서 "Exhibit 99.x"(관례적으로 보도자료 원문)를 찾는 패턴.
# 파일명 표기가 회사마다 달라("ex99_1.htm", "ex-99.1.htm", "exhibit_99-1.htm",
# "d38597dex991.htm" 등) "ex"와 "99" 사이 글자 수를 넉넉히 허용한다.
EXHIBIT_99_RE = re.compile(r"ex.{0,10}99", re.I)


def find_earnings_release_via_edgar(ticker, cik, form_type):
    """data.sec.gov 공식 JSON API로 최근 공시를 조회해 실적 보도자료로 보이는 첨부문서를
    찾는다. 8-K는 submissions JSON의 "items"에 "2.02"(Results of Operations and Financial
    Condition)가 있는 것을 우선으로 고르고(6-K는 이 분류 체계가 없어 그냥 최근 것부터
    순서대로 본다), 그 공시 안의 첨부문서 중 EXHIBIT_99_RE에 맞는 파일을 고른다."""
    try:
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=20, headers=SEC_HEADERS)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[WARN] {ticker} EDGAR submissions 조회 실패: {e}", file=sys.stderr)
        return None

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    items_list = recent.get("items", [])
    cik_int = str(int(cik))

    # items="2.02" 필터로도 못 찾으면 계속 과거로 거슬러 올라가며 아무 8-K/6-K나 뒤지게
    # 되는데, 그러다 우연히 "99" 첨부문서가 있는 완전히 엉뚱한 옛날 공시를 집는 사고가
    # 실제로 있었다(2026-09-08, UNH가 2025-10-28짜리, ALGN이 2021-04-28짜리 — 5년 전! —
    # 공시를 "실적 발표"로 오인). 회사는 분기마다 실적을 발표하므로 최신 공시는 항상
    # 100여 일 이내여야 한다는 사실을 이용해, 그보다 오래된 공시는 애초에 후보에서 뺀다.
    today = datetime.date.today()
    MAX_FILING_AGE_DAYS = 120

    def _recent_enough(i):
        try:
            d = datetime.datetime.strptime(dates[i], "%Y-%m-%d").date()
        except (ValueError, IndexError):
            return False
        return (today - d).days <= MAX_FILING_AGE_DAYS

    candidates = [i for i, f in enumerate(forms) if f == form_type and _recent_enough(i)]
    if form_type == "8-K":
        earnings_first = [i for i in candidates if "2.02" in (items_list[i] if i < len(items_list) else "")]
        other = [i for i in candidates if i not in earnings_first]
        candidates = earnings_first + other  # 2.02(실적) 표시된 것부터, 없으면 나머지도 시도

    for i in candidates:
        accession_nodash = accessions[i].replace("-", "")
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/index.json"
        try:
            idx_r = requests.get(index_url, timeout=20, headers=SEC_HEADERS)
            idx_r.raise_for_status()
            idx_data = idx_r.json()
        except Exception as e:
            print(f"[WARN] {ticker} EDGAR filing index 조회 실패({index_url}): {e}", file=sys.stderr)
            continue
        for item in idx_data.get("directory", {}).get("item", []):
            fname = item.get("name", "")
            if EXHIBIT_99_RE.search(fname):
                url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{fname}"
                return {"title": f"{ticker} {form_type} Exhibit 99 ({dates[i]})", "url": url, "date": dates[i]}
    print(f"[INFO] {ticker} 최근 {form_type} 공시 중 실적 보도자료로 보이는 첨부문서를 찾지 못함", file=sys.stderr)
    return None


def fetch_release_text(url):
    """보도자료 상세 페이지에서 본문 텍스트를 최대한 뽑아낸다. 회사마다 컨테이너 클래스가
    달라 범용적으로 본문 후보 컨테이너를 순서대로 시도하고, 다 실패하면 body 전체에서
    짧은 nav/footer 텍스트를 걸러낸 <p> 모음을 쓴다."""
    # sec.gov는 자동화 요청에 식별 가능한 User-Agent를 요구한다(find_earnings_release_via_edgar
    # 주석 참고) — data.sec.gov API뿐 아니라 www.sec.gov/Archives 정적 문서 요청도 마찬가지다.
    req_headers = SEC_HEADERS if "sec.gov" in url else HEADERS
    try:
        r = requests.get(url, timeout=20, headers=req_headers)
        r.raise_for_status()
    except Exception as e:
        print(f"[WARN] 보도자료 본문 요청 실패({url}): {e}", file=sys.stderr)
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    for sel in ["div.module_body", "div.press-release-body", "article", "div#WebsiteContent"]:
        node = soup.select_one(sel)
        if node:
            text = node.get_text("\n", strip=True)
            if len(text) > 200:
                return text[:8000]
    paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    text = "\n".join(p for p in paras if len(p) > 40)
    if len(text) > 200:
        return text[:8000]
    # [2026-09-08] 실제 워크플로 실행에서 SEC EDGAR Exhibit 99 문서 대부분이 여기서
    # 걸러졌다 — SEC 제출용으로 변환된 옛날 스타일 HTML이라 <p> 없이 <table>/<div>/<font>
    # 태그로만 레이아웃을 잡는 경우가 많아 위 두 방식이 실패한다. 최후 수단으로 body
    # 전체 텍스트를 쓴다 — SEC 개별 문서(회사 웹사이트와 달리)는 nav/광고가 없는
    # 본문 전용 정적 파일이라 이 방식이 오히려 잘 맞는다.
    body = soup.find("body") or soup
    text = re.sub(r"\n{3,}", "\n\n", body.get_text("\n", strip=True))
    return text[:8000] if len(text) > 200 else None


# ---------------------------------------------------------------------------
# 번역/요약 provider — summarize_news.py의 GeminiProvider/AnthropicProvider와 동일한
# 어댑터 패턴(각 스크립트가 독립적으로 돌아가야 해서 공용 모듈로 안 빼고 그대로 복제함,
# 이 저장소의 기존 관례).
# ---------------------------------------------------------------------------

GEMINI_MODEL = "gemini-3.6-flash"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """당신은 한국 증권사 애널리스트를 위한 해외 의료기기/헬스케어 기업 실적 발표
보도자료 번역·요약 보조원입니다. 아래 규칙을 지켜 JSON 객체 하나만 출력하세요(설명, 코드블록 없이).

입력은 영문 실적 발표 보도자료 본문입니다. 다음을 뽑아 한국어로 작성하세요:
- "date": 보도자료에 명시된 발표일을 "YYYY-MM-DD" 형식으로(없으면 빈 문자열).
- "quarter": 몇 년도 몇 분기 발표인지("2026년 2분기(Q2)" 형식)
- "metrics": [{"label":"매출액","value":"...","note":"..."}, ...] 형태의 배열. 매출액/영업이익/
  영업이익률/순이익(또는 EPS)/가이던스 중 보도자료에 실제로 있는 항목만 포함하세요(없는 걸
  지어내지 마세요). value에는 숫자와 단위를, note에는 YoY 증감률 등 부연 설명을 넣으세요.
- "summary_ko": 핵심 내용을 3~4개의 완결된 문장으로("~함." 종결) 요약한 배열. 매출/수익성
  실적, 가이던스, 주요 사업 하이라이트 순으로 작성하세요.
- "ceo_quote_ko": 경영진(CEO 등) 인용문이 있으면 한국어로 자연스럽게 번역(없으면 빈 문자열).

이 문서가 실적 발표 보도자료가 아니라 다른 종류의 공시(임원 변경, M&A 발표 등)라면
모든 필드를 비운 채로("quarter":"", "metrics":[], "summary_ko":[], "ceo_quote_ko":"")
그대로 출력하세요(지어내지 마세요).

출력 형식: {"date":"...", "quarter":"...", "metrics":[...], "summary_ko":["...","..."], "ceo_quote_ko":"..."}
보도자료에 없는 내용은 추측하지 말고 비워두세요."""


class GeminiProvider:
    """summarize_news.py가 이미 겪은 문제와 동일 — gemini-3.x는 기본적으로 내부 "thinking"
    (추론) 토큰을 max_output_tokens 예산에서 같이 소모해, 실제 JSON 응답이 중간에 잘리는
    문제가 있다("Unterminated string..." 파싱 에러로 나타남, 2026-09-08 실행에서 IRTC/GH/
    ISRG가 이 문제였다). thinking_budget=0으로 꺼서 예산을 전부 실제 응답에 쓰게 한다 —
    일부 모델 버전은 이 값을 거부(400)하니 그럴 땐 thinking_config 자체를 빼고 재시도."""
    name = "gemini"

    def __init__(self, api_key):
        from google import genai
        self._genai = genai
        self.client = genai.Client(api_key=api_key)

    def _generate(self, user_content, thinking_budget):
        from google.genai import types
        config_kwargs = dict(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=3000,
            response_mime_type="application/json",
        )
        if thinking_budget is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)
        resp = self.client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_content,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        return resp.text

    def call(self, user_content):
        try:
            return self._generate(user_content, thinking_budget=0)
        except Exception as e:
            if "400" in str(e) or "INVALID_ARGUMENT" in str(e):
                return self._generate(user_content, thinking_budget=None)
            raise


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)

    def call(self, user_content):
        resp = self.client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        return resp.content[0].text


def get_provider():
    import os
    # 뉴스 요약(summarize_news.py)/월간 브리핑(analyze_news_trend.py)이 GEMINI_API_KEY를
    # 이미 같이 쓰고 있어서 하루 20회 무료 쿼터를 셋이 나눠 쓰다 소진되는 문제가 있었다
    # (2026-09-08) — 이 스크립트 전용으로 별도 무료 키를 GEMINI_API_KEY_EARNINGS에
    # 등록하면 그 키를 우선 쓰고(완전히 독립된 하루 쿼터), 아직 안 만들었으면 기존
    # GEMINI_API_KEY로 자동 대체한다.
    gemini_key = os.environ.get("GEMINI_API_KEY_EARNINGS") or os.environ.get("GEMINI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if gemini_key:
        try:
            return GeminiProvider(gemini_key)
        except Exception as e:
            print(f"[WARN] Gemini provider 초기화 실패: {e}", file=sys.stderr)
    if anthropic_key:
        try:
            return AnthropicProvider(anthropic_key)
        except Exception as e:
            print(f"[WARN] Anthropic provider 초기화 실패: {e}", file=sys.stderr)
    return None


class DailyQuotaExhausted(Exception):
    """Gemini 무료 티어의 "하루" 단위 쿼터(gemini-3.6-flash 기준 하루 20회, 에러 메시지의
    quotaId에 "PerDay"가 붙음)가 소진됐음을 나타낸다. summarize_news.py가 이미 같은 문제를
    겪어서 쓰는 패턴 그대로다 — 분당 제한과 달리 몇 초~몇십 초 기다린다고 안 풀리고 태평양시
    기준 하루 지나야 풀리므로, 이게 확인되면 재시도 없이 바로 포기하고 이번 실행에서 남은
    회사들도 전부 건너뛴다(2026-09-08, 실제 실행에서 이걸 구분 못 해 회사마다 10~30초씩
    헛되이 재시도하며 시간을 낭비한 걸 확인하고 수정)."""
    pass


def is_daily_quota_exhausted(error):
    return "PerDay" in str(error)


def translate_release(provider, release_text, retries=3):
    """[2026-09-08] 실제 워크플로 실행에서 Gemini가 "503 UNAVAILABLE(현재 수요 급증)"을
    반환해 RDNT/ALGN/TEM 번역이 실패한 적이 있다 — 구글 쪽 일시적 과부하로, 잠깐 쉬었다
    다시 부르면 대개 풀린다. 재시도 없이 바로 포기하던 걸 최대 3회까지 짧게 대기 후
    재시도하도록 고쳤다. 다만 하루 쿼터 초과(DailyQuotaExhausted)는 기다려도 안 풀리니
    재시도하지 않고 바로 포기한다."""
    last_err = None
    for attempt in range(retries):
        try:
            return _parse_translation_response(provider.call(release_text))
        except Exception as e:
            if provider.name == "gemini" and is_daily_quota_exhausted(e):
                raise DailyQuotaExhausted(str(e)) from e
            last_err = e
            if attempt < retries - 1:
                wait = 10 * (attempt + 1)
                print(f"[WARN] 번역 API 호출 실패(재시도 {attempt+1}/{retries}, {wait}초 대기): {e}", file=sys.stderr)
                time.sleep(wait)
    raise last_err


def _parse_translation_response(raw):
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
    return json.loads(text)


def process_release(ticker, name, release, provider, out_data):
    """보도자료 본문을 가져와 번역하고, out_data["companies"]에 항목을 추가한다.
    성공하면 True, 건너뛰면(본문 없음/번역 실패/실적 발표가 아닌 공시로 판정) False를 반환한다."""
    body = fetch_release_text(release["url"])
    if not body:
        print(f"[WARN] {ticker} 보도자료 본문을 가져오지 못함: {release['url']}", file=sys.stderr)
        return False
    try:
        parsed = translate_release(provider, body)
    except DailyQuotaExhausted:
        raise  # main()이 잡아서 이번 실행에서 남은 회사들도 전부 건너뛰도록 위로 전달
    except Exception as e:
        print(f"[WARN] {ticker} 번역 실패: {e}", file=sys.stderr)
        return False
    if not parsed.get("summary_ko"):
        # items="2.02" 필터를 못 쓰는 6-K이거나, 8-K인데도 첨부문서가 실적 발표가 아닌
        # 경우(SYSTEM_PROMPT가 이럴 때 모든 필드를 비워서 응답하도록 지시해뒀다) — 조용히 건너뜀.
        print(f"[INFO] {ticker} 첨부문서가 실적 발표 보도자료가 아닌 것으로 판정되어 건너뜀: {release['url']}", file=sys.stderr)
        return False
    entry = {
        "ticker": ticker,
        "name": name,
        "market": "US",
        "quarter": parsed.get("quarter", ""),
        "date": parsed.get("date") or release.get("date") or "",
        "title": release["title"],
        "source_url": release["url"],
        "metrics": parsed.get("metrics", []),
        "summary_ko": parsed.get("summary_ko", []),
        "ceo_quote_ko": parsed.get("ceo_quote_ko", ""),
    }
    out_data.setdefault("companies", []).append(entry)
    print(f"[OK] {ticker} {entry['date']} 실적 보도자료 번역 완료", file=sys.stderr)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="earnings_ir.json")
    args = ap.parse_args()

    try:
        with open(args.out, encoding="utf-8") as f:
            out_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        out_data = {"updated": None, "source": "각 기업 SEC 공시(8-K/6-K Exhibit 99) 기반 실적 보도자료, 한국어 번역·요약은 Gemini/Claude 자동 처리", "companies": []}

    existing_urls = {c.get("source_url") for c in out_data.get("companies", [])}
    provider = get_provider()
    if provider is None:
        print("[WARN] GEMINI_API_KEY/ANTHROPIC_API_KEY가 없어 번역을 건너뜁니다 — 기존 데이터만 유지합니다.", file=sys.stderr)

    changed = False
    for ticker, info in EDGAR_SOURCES.items():
        release = find_earnings_release_via_edgar(ticker, info["cik"], info["form"])
        if not release:
            continue
        if release["url"] in existing_urls:
            continue
        if provider is None:
            continue
        try:
            if process_release(ticker, info["name"], release, provider, out_data):
                changed = True
        except DailyQuotaExhausted:
            # 오늘 Gemini 무료 티어 하루 쿼터를 다 썼다 — 남은 회사들도 똑같이 실패할 게
            # 뻔하니 헛되이 계속 시도하지 않고 여기서 이번 실행을 마친다(이미 수집한
            # 회사들은 dedup으로 건너뛰니 다음 실행 때 나머지가 자연히 이어서 채워진다).
            print(f"[WARN] Gemini 무료 티어 하루 쿼터 소진 — 이번 실행은 여기까지만 진행하고 남은 회사는 다음 실행 때 이어서 수집합니다.", file=sys.stderr)
            break

    if changed:
        # 같은 분기가 서로 다른 URL로 두 번 들어올 수 있다 — 예: 이전에 회사 자체 웹사이트
        # URL로 수동 시딩해둔 분기를, 이번 실행에서 EDGAR가 같은 분기를 다른(sec.gov) URL로
        # 새로 찾아 중복 추가하는 경우(2026-09-08, 실제 실행에서 RDNT/ALGN/TEM/ABT/ISRG가
        # 이 케이스였음 — source_url 기준 중복 체크만으로는 못 거른다). (ticker, date) 기준으로
        # 합쳐서 나중에 추가된 쪽(=이번에 새로 수집된 EDGAR 항목)을 남긴다.
        dedup = {}
        for c in out_data["companies"]:
            dedup[(c["ticker"], c.get("date"))] = c
        out_data["companies"] = list(dedup.values())

        # 회사별 최근 8개 분기만 유지(오래된 분기는 자연히 정리).
        by_ticker = {}
        for c in out_data["companies"]:
            by_ticker.setdefault(c["ticker"], []).append(c)
        trimmed = []
        for ticker, rows in by_ticker.items():
            rows.sort(key=lambda c: c.get("date") or "")
            trimmed.extend(rows[-MAX_QUARTERS_PER_TICKER:])
        out_data["companies"] = trimmed
        out_data["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    print(f"완료: {args.out} (기업 {len(out_data.get('companies', []))}건)", file=sys.stderr)


if __name__ == "__main__":
    main()
