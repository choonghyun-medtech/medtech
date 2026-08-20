#!/usr/bin/env python3
"""
네이버 검색 API(뉴스)로 국내 트래킹 기업의 최근 뉴스를 가져와 news.json의
"domestic" 섹션을 자동 갱신한다. "global"(해외) 섹션은 scrape_news_global.py가
관리하므로 이 스크립트는 건드리지 않고 그대로 보존한다.

- 인증: 환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (GitHub Actions 시크릿으로 주입,
  이 스크립트나 워크플로 파일에 실제 키 값을 하드코딩하지 않는다).
- 엔드포인트: https://openapi.naver.com/v1/search/news.json
  (공식 문서: https://developers.naver.com/docs/serviceapi/search/news/news.md)
- 회사명 -> 카테고리 매핑은 NEWS_COMPANY_CATEGORY에서 관리. 뉴스클리핑_가이드라인.md
  (2026-08-18 사용자 제공 최신본)의 10개 카테고리를 그대로 반영:
  Digital Health / Aesthetics / Robotics / Humanoid / 산업용·서비스 로봇 /
  로보틱스 밸류체인 / Therapeutics / IVD / Bio-Processing / Dental.
  md가 갱신되면 NEWS_COMPANY_CATEGORY/CATEGORY_ORDER만 그대로 옮겨 적으면 된다.
- 수집 기간은 뉴스클리핑_가이드라인.md 규칙(직전 영업일 마감 이후~현재)을 최대한 따른다:
  평일(화~금) 실행이면 24시간 이내, 월요일 실행이면 72시간 이내(주말 포함). 다만 공휴일까지
  반영한 완전한 "직전 영업일" 계산(예: 월요일+직전 금요일 공휴일=96시간)은 한국 공휴일 달력이
  필요해 이 스크립트에는 아직 없다 — 필요하면 연도별 공휴일 목록을 하드코딩해서 추가하면 된다.
- 회사명 매칭은 단순 substring이 아니라 단어 경계 정규식으로 한다("스튜디오"/"오디오" 안에
  우연히 들어있는 "디오" 같은 오매칭 방지).
- COMPANY_SEARCH_ALIASES: 사명 변경 등으로 검색어를 여러 개 시도해야 하는 회사(예: GC메디아이
  ← 유비케어).
- CONTEXT_REQUIRED: 회사명이 흔한 단어/인명/초대형 지주사명과 겹쳐 무관한 기사가 섞이는 경우,
  업종 관련 키워드가 함께 있어야만 채택하도록 하는 안전장치. 예: "디오"(임플란트 회사)는
  아이돌 D.O.(도경수)의 애칭 표기와 같아 무관한 연예 기사가 섞여 들어오는 걸 사용자가 실제로
  확인했다("제주대출신 이주성 작곡가", "도경수 콘서트" 등) — 임플란트/치과 등 키워드가 있어야
  채택. "현대차그룹"/"삼성전자"/"HL홀딩스"는 회사명만으로는 자동차·반도체 등 무관 뉴스가
  압도적으로 많이 잡혀서 로봇 관련 키워드가 있어야 채택한다.
- 중복 처리: 뉴스클리핑_가이드라인.md의 "동일 내용 기사는 가장 신뢰도 높은 매체 1개만 선택"
  규칙을 반영해, 제목이 유사한 기사는 하나만 남기고(TRUSTED_SOURCE_DOMAINS에 있는 매체를
  우선) 나머지는 버린다.
- 수집 결과가 0건이면 기존 news.json을 그대로 보존하고 실패로 종료한다(빈 데이터로 덮어쓰지 않음).

사용법:
    NAVER_CLIENT_ID=xxx NAVER_CLIENT_SECRET=yyy python scrape_news.py --out news.json
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
MAX_ITEMS_PER_COMPANY = 3  # 회사 하나당 최대 채택 기사 수
MAX_ITEMS_PER_CATEGORY = 6 # 카테고리 하나당 최종 표시 기사 수 상한
REQUEST_TIMEOUT = 15

# 회사명 -> 뉴스 카테고리. 뉴스클리핑_가이드라인.md의 "분류 체계 및 트래킹 기업" 표를 그대로 반영
# (2026-08-18 사용자 제공 최신본, 로보틱스 3개 카테고리 신규 추가).
NEWS_COMPANY_CATEGORY = {
    # 1. Digital Health
    "씨어스": "Digital Health",
    "메쥬": "Digital Health",
    "메디아나": "Digital Health",
    "아이센스": "Digital Health",
    "루닛": "Digital Health",
    "인바디": "Digital Health",
    "뉴로핏": "Digital Health",
    "GC메디아이": "Digital Health",  # 구 유비케어, COMPANY_SEARCH_ALIASES에서 옛 이름도 함께 검색
    "에어스메디컬": "Digital Health",
    "스카이랩스": "Digital Health",
    "카카오헬스케어": "Digital Health",
    # 2. Aesthetics
    "클래시스": "Aesthetics",
    "휴젤": "Aesthetics",
    "파마리서치": "Aesthetics",
    "시지메드텍": "Aesthetics",
    "한스바이오메드": "Aesthetics",
    "엘앤씨바이오": "Aesthetics",
    "에이피알": "Aesthetics",
    "메디톡스": "Aesthetics",
    "리센스메디컬": "Aesthetics",
    # 3. Robotics
    "고영": "Robotics",
    "큐렉소": "Robotics",
    "리브스메드": "Robotics",
    # 4. Humanoid
    "현대차그룹": "Humanoid",
    "레인보우로보틱스": "Humanoid",
    "삼성전자": "Humanoid",
    # 5. 산업용·서비스 로봇
    "두산로보틱스": "산업용·서비스 로봇",
    "HL홀딩스": "산업용·서비스 로봇",
    # 6. 로보틱스 밸류체인
    "로보티즈": "로보틱스 밸류체인",
    "에스비비테크": "로보틱스 밸류체인",
    "에스피지": "로보틱스 밸류체인",
    "나우로보틱스": "로보틱스 밸류체인",
    # 7. Therapeutics
    "넥스트바이오메디컬": "Therapeutics",
    "로킷헬스케어": "Therapeutics",
    "시지바이오": "Therapeutics",
    # 8. IVD
    "씨젠": "IVD",
    "에스디바이오센서": "IVD",
    "지노믹트리": "IVD",
    "바이오다인": "IVD",
    "바디텍메드": "IVD",
    # 9. Bio-Processing
    "큐리오시스": "Bio-Processing",
    "토모큐브": "Bio-Processing",
    "큐리옥스바이오시스템즈": "Bio-Processing",
    "뷰웍스": "Bio-Processing",
    # 10. Dental
    "그래피": "Dental",
    "바텍": "Dental",
    "디오": "Dental",
    "덴티움": "Dental",
}

CATEGORY_ORDER = [
    "Digital Health", "Aesthetics", "Robotics", "Humanoid",
    "산업용·서비스 로봇", "로보틱스 밸류체인", "Therapeutics", "IVD",
    "Bio-Processing", "Dental",
]

# 사명 변경 등으로 검색어를 여러 개 시도해야 하는 회사. 표시는 NEWS_COMPANY_CATEGORY의
# 키(새 이름)로 하되, 검색과 회사명 매칭은 별칭 전부를 시도한다.
COMPANY_SEARCH_ALIASES = {
    "GC메디아이": ["GC메디아이", "유비케어"],
    "현대차그룹": ["현대차그룹", "보스턴다이내믹스"],
}

# 회사명이 흔한 단어·인명·초대형 지주사명과 겹쳐 무관한 기사가 섞이는 걸 막는 안전장치.
# 아래 회사는 회사명이 매칭돼도 이 키워드 중 하나 이상이 함께 있어야만 채택한다.
CONTEXT_REQUIRED = {
    "디오": ["임플란트", "치과", "덴탈", "디오임플란트", "코스닥", "공시", "실적", "주가", "수주", "계약", "식약처"],
    "현대차그룹": ["로봇", "보스턴다이내믹스", "휴머노이드", "아틀라스", "Atlas"],
    "삼성전자": ["로봇", "휴머노이드"],
    "HL홀딩스": ["로봇"],
}

# 뉴스클리핑_가이드라인.md의 "주요 매체" 예시 목록 — 중복 기사가 있을 때 이 매체를 우선한다
# (완전한 화이트리스트 필터는 아님. md 자체가 "등"으로 열어둔 예시 목록이라, 이 목록에 없는
# 매체라도 기사 자체는 채택하고 dedup 시 우선순위로만 사용한다).
TRUSTED_SOURCE_DOMAINS = {
    "edaily.co.kr": "이데일리",
    "hankyung.com": "한국경제",
    "mk.co.kr": "매일경제",
    "yna.co.kr": "연합뉴스",
    "heraldcorp.com": "헤럴드경제",
    "asiae.co.kr": "아시아경제",
    "etnews.com": "전자신문",
    "medigatenews.com": "메디게이트뉴스",
    "medifonews.com": "메디포뉴스",
}

TAG_RE = re.compile(r"<[^>]+>")
NON_WORD_RE = re.compile(r"[^0-9A-Za-z가-힣\s]")


def clean_text(s: str) -> str:
    return html.unescape(TAG_RE.sub("", s or "")).strip()


def recency_hours_for_today(today: datetime.date) -> int:
    """뉴스클리핑_가이드라인.md의 기준시간 규칙(단순화 버전).
    월요일(주말 포함)은 72시간, 그 외 평일은 24시간.
    공휴일까지 반영한 '직전 영업일' 계산은 하지 않는다 — 한국 공휴일 달력이 필요하고
    해마다 갱신해야 해서 아직 하드코딩하지 않았다(연휴 낀 주는 과소 수집될 수 있음)."""
    if today.weekday() == 0:  # Monday
        return 72
    return 24


def parse_pubdate(pub_date: str):
    """RFC822 형식('Tue, 18 Aug 2026 09:00:00 +0900')을 YYYY-MM-DD로 변환."""
    try:
        dt = parsedate_to_datetime(pub_date)
        return dt.strftime("%Y-%m-%d"), dt
    except (TypeError, ValueError):
        return None, None


def _word_boundary_pattern(term: str):
    """단순 substring이 아니라 앞뒤로 한글/영문/숫자가 붙어있지 않은 경우만 매칭.
    "스튜디오"/"오디오" 안의 "디오", "그래피티" 안의 "그래피" 같은 오매칭을 막는다."""
    return re.compile(rf"(?<![0-9A-Za-z가-힣]){re.escape(term)}(?![0-9A-Za-z가-힣])")


def company_mentioned(company: str, title: str, desc: str) -> bool:
    for alias in COMPANY_SEARCH_ALIASES.get(company, [company]):
        pat = _word_boundary_pattern(alias)
        if pat.search(title) or pat.search(desc):
            return True
    return False


def context_ok(company: str, title: str, desc: str) -> bool:
    required = CONTEXT_REQUIRED.get(company)
    if not required:
        return True
    hay = f"{title} {desc}".lower()
    return any(kw.lower() in hay for kw in required)


def domain_of(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def source_label(url: str):
    """(표시용 매체명, 신뢰매체 여부) 반환. 모르는 도메인이면 도메인 자체를 표시명으로 쓴다
    (예전처럼 무조건 '네이버뉴스'라고 부정확하게 표기하지 않는다)."""
    dom = domain_of(url)
    for known, label in TRUSTED_SOURCE_DOMAINS.items():
        if dom == known or dom.endswith("." + known):
            return label, True
    return (dom or "네이버뉴스"), False


def _title_tokens(title: str):
    return set(NON_WORD_RE.sub(" ", title).split())


def is_duplicate_title(a: str, b: str) -> bool:
    """제목 단어 집합의 겹침 비율이 높으면 동일 내용 기사로 간주(신디케이션/보도자료 재배포 등)."""
    wa, wb = _title_tokens(a), _title_tokens(b)
    if not wa or not wb:
        return False
    overlap = len(wa & wb) / max(1, min(len(wa), len(wb)))
    return overlap >= 0.6


def fetch_news_for_company(client_id: str, client_secret: str, company: str, cutoff_hours: int, debug=False):
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(hours=cutoff_hours)

    seen_urls = set()
    candidates = []
    for query in COMPANY_SEARCH_ALIASES.get(company, [company]):
        params = {"query": query, "display": 10, "start": 1, "sort": "date"}
        try:
            resp = requests.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
            if debug:
                print(f"[DEBUG] {company}(검색어={query}): HTTP {resp.status_code}", file=sys.stderr)
            resp.raise_for_status()
        except Exception as e:
            print(f"[WARN] {company}(검색어={query}): API 호출 실패 ({e})", file=sys.stderr)
            continue

        for it in resp.json().get("items", []):
            title = clean_text(it.get("title", ""))
            desc = clean_text(it.get("description", ""))
            date_str, dt = parse_pubdate(it.get("pubDate", ""))
            if date_str is None or dt is None or dt < cutoff:
                continue
            # 검색어가 실제로 제목/요약에 "단어" 단위로 포함된 것만 채택 — 네이버 뉴스 API는
            # 종종 관련성 낮은 결과도 섞어 주고, 짧은 회사명은 다른 단어 안에 우연히 포함되거나
            # (스튜디오/오디오 안의 "디오") 동명이인·동명 지주사와 겹칠 수 있어 이중으로 거른다.
            if not company_mentioned(company, title, desc):
                continue
            if not context_ok(company, title, desc):
                continue
            url = it.get("originallink") or it.get("link")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            candidates.append({"date_str": date_str, "dt": dt, "title": title, "desc": desc, "url": url})

    candidates.sort(key=lambda c: c["dt"], reverse=True)

    # 동일 내용 기사(신디케이션/보도자료 재배포 등) 중복 제거 — 신뢰 매체를 우선하고,
    # 그 외에는 먼저 나온(=더 최신) 기사를 유지한다.
    kept = []
    for c in candidates:
        dup_idx = next((i for i, k in enumerate(kept) if is_duplicate_title(c["title"], k["title"])), None)
        if dup_idx is None:
            kept.append(c)
            continue
        _, c_trusted = source_label(c["url"])
        _, k_trusted = source_label(kept[dup_idx]["url"])
        if c_trusted and not k_trusted:
            kept[dup_idx] = c

    out = []
    for c in kept[:MAX_ITEMS_PER_COMPANY]:
        src, _ = source_label(c["url"])
        out.append({
            "co": company,
            "ctx": "뉴스",
            "t": c["title"],
            "src": src,
            "date": c["date_str"],
            "url": c["url"],
            "desc": c["desc"],  # summarize_news.py가 [맥락] ~했음. 요약을 생성할 때 참고용.
                                # 최종 화면에는 노출하지 않는 내부 필드.
        })
    return out


def merge_cross_company_duplicates(items):
    """서로 다른 회사 검색에서 각각 채택됐지만 실은 같은 기사(예: "K바이오 M&A 지형도" 같은
    업종 전체를 다루는 기사가 클래시스 검색에서도, 휴젤 검색에서도 걸리는 경우)를 하나로 합친다.
    URL이 같으면 무조건 동일 기사, URL이 달라도 제목이 유사하면(신디케이션 등) 동일 기사로 보고
    co 필드를 "클래시스, 휴젤"처럼 콤마로 합쳐서 하나의 항목으로 표시한다."""
    merged = []
    for it in items:
        dup_idx = next(
            (i for i, m in enumerate(merged)
             if m["url"] == it["url"] or is_duplicate_title(it["t"], m["t"])),
            None,
        )
        if dup_idx is None:
            merged.append(dict(it))
            continue
        existing = merged[dup_idx]
        cos = [c.strip() for c in existing["co"].split(",")]
        if it["co"] not in cos:
            cos.append(it["co"])
            existing["co"] = ", ".join(cos)
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="news.json")
    args = ap.parse_args()

    client_id = os.environ.get("NAVER_CLIENT_ID")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("[ERROR] NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)

    # 기존 파일 로드 — global(해외) 섹션은 이 스크립트가 건드리지 않고 그대로 보존한다.
    try:
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    existing_global = existing.get("global", [])

    cutoff_hours = recency_hours_for_today(datetime.datetime.now(datetime.timezone.utc).astimezone().date())
    print(f"[INFO] 오늘 기준 수집 윈도우: 최근 {cutoff_hours}시간 이내", file=sys.stderr)

    by_category = {cat: [] for cat in CATEGORY_ORDER}
    ok_companies = 0
    debug_budget = 3
    for company, category in NEWS_COMPANY_CATEGORY.items():
        try:
            items = fetch_news_for_company(client_id, client_secret, company, cutoff_hours, debug=debug_budget > 0)
            debug_budget -= 1
        except Exception as e:
            print(f"[WARN] {company}: 수집 실패 ({e})", file=sys.stderr)
            continue
        if items:
            ok_companies += 1
            by_category.setdefault(category, []).extend(items)
            print(f"{company} ({category}): {len(items)}건")
        else:
            print(f"[INFO] {company}: 최근 {cutoff_hours}시간 내 관련 기사 없음", file=sys.stderr)

    if ok_companies == 0:
        print("[ERROR] 수집된 회사가 0개라 기존 news.json을 보존하고 종료합니다.", file=sys.stderr)
        sys.exit(1)

    domestic = []
    for cat in CATEGORY_ORDER:
        items = sorted(by_category.get(cat, []), key=lambda x: x["date"], reverse=True)
        items = merge_cross_company_duplicates(items)
        domestic.append({"cat": cat, "items": items[:MAX_ITEMS_PER_CATEGORY]})

    payload = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "네이버 뉴스검색 API(국내, 자동) + medtech/헬스케어/로보틱스 매체 RSS(해외, 자동) · 뉴스클리핑 가이드라인 카테고리 기준",
        "domestic": domestic,
        "global": existing_global,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    total = sum(len(g["items"]) for g in domestic)
    print(f"저장 완료: {args.out} (국내 {ok_companies}개 기업, {total}건 / 해외는 기존 값 {len(existing_global)}개 카테고리 유지)")


if __name__ == "__main__":
    main()
