#!/usr/bin/env python3
"""
사용자가 새 대화창에 medtech_news_clipping_rules(-ed29020a).md를 붙여넣고 "클리핑 시작"을
입력했을 때 Claude가 직접 하는 방식 — Google 검색(site:도메인 + 기업명 단독 검색) 기반 —
을 Anthropic API의 web_search 도구로 그대로 자동화한다.

배경(2026-08-21 요청): scrape_news_global.py(RSS 기반)는 API 키가 필요 없는 무료 baseline
이지만, RSS 피드가 없거나(massdevice.com/reuters.com/semafor.com/irobotnews.com/중국어
사이트 등) 기사 요약에 회사명이 안 나오는 경우를 놓친다. 사용자가 Claude 채팅창에서 md 파일을
직접 돌렸을 때 나오는 결과보다 수집량이 눈에 띄게 떨어진다고 지적 — 그 방식(Google site:
검색 + 기업명 단독 검색)을 그대로 재현하기 위해 이 스크립트를 추가한다.

- RSS 스크립트를 대체하는 게 아니라 "보강"이다. 이 스크립트는 update-news.yml에서
  scrape_news_global.py **다음**에 실행되며, 이미 기록된 news.json의 global 섹션에
  URL/제목 기준으로 중복되지 않는 기사만 추가로 병합한다(요약이 없어도 RSS 결과는
  그대로 보존됨 — RSS보다 결과가 못해질 일은 없다).
- 도구: Anthropic Messages API의 서버사이드 web_search 도구(web_search_20250305).
  allowed_domains로 검색을 제한하지 않고, 프롬프트 안에서 md 원본 방법 그대로
  "site:도메인" 쿼리와 "기업명 news 날짜" 단독 쿼리를 병행하도록 지시한다 — 카테고리
  전용 사이트만으로 제한하면 md가 명시한 "지정 기업 외 기업이 투자자·파트너로 참여한
  뉴스도 포함" 같은 광범위 검색이 막히기 때문.
- 비용: 공식 문서(2026-08-21 기준, platform.claude.com/docs) 기준 $10 / 1,000회 검색 +
  표준 토큰 요금. 채널당 max_uses로 예산을 제한한다(MedTech 채널 35회, Robotics 채널
  30회) — 평일 1회 실행 시 하루 약 $0.5~0.7 수준으로 추정(실제 사용량은 Anthropic
  콘솔 Usage 페이지에서 확인 가능, 이 스크립트가 정확한 청구액을 보장하지는 않음).
- ANTHROPIC_API_KEY는 summarize_news.py가 이미 쓰는 것과 동일한 시크릿을 재사용한다
  (update-news.yml에 이미 등록돼 있음, 추가 등록 불필요).
- 이 단계는 "보강" 단계이므로 키 누락/API 실패/파싱 실패로 건너뛰어도 sys.exit(0)으로
  종료하고 RSS 결과를 그대로 둔다(워크플로 전체를 실패시키지 않음).

사용법:
    ANTHROPIC_API_KEY=xxx python search_news_global_claude.py --out news.json
    ANTHROPIC_API_KEY=xxx python search_news_global_claude.py --out news.json --debug
"""
import argparse
import datetime
import json
import os
import sys

# scrape_news_global.py와 동일한 카테고리/회사 매핑·수집 윈도우 규칙·중복 판정 로직을
# 그대로 재사용한다(단일 소스 유지 — 두 스크립트의 카테고리/회사명 표기가 어긋나면
# index.html에서 국내외 뉴스가 같은 카테고리로 안 묶이는 버그가 생기기 때문).
from scrape_news_global import (
    CATEGORY_ORDER,
    GLOBAL_COMPANY_CATEGORY,
    COMPANY_SEARCH_ALIASES,
    MAX_ITEMS_PER_CATEGORY,
    recency_hours_for_today,
    is_duplicate_title,
)

MODEL = "claude-sonnet-5"  # 검색 결과를 사람처럼 판단(관련성/중복/2줄 요약)해야 하므로
                            # 요약 전담인 summarize_news.py의 Haiku보다 상위 모델 사용.
MAX_TOKENS = 8000

# md(medtech_news_clipping_rules-ed29020a.md, 2026-08-18 최신본)의 "3. 뉴스 수집 사이트"
# 그대로. RSS 피드가 없어 scrape_news_global.py가 못 쓰던 사이트(massdevice.com 등)도
# site: 검색 방식이라면 커버 가능하다.
CATEGORY_SITES = {
    "MedTech": [
        "fiercebiotech.com/medtech", "medtechdive.com",
        "massdevice.com/massdevice-article-archive",
    ],
    "Surgical Robot": ["surgicalroboticstechnology.com", "medchina.tech"],
    "IVD": ["360dx.com/breaking-news"],
    "Digital Health": ["fiercehealthcare.com/health-tech"],
    "Healthcare Provider": ["fiercehealthcare.com", "healthcaredive.com"],
    "Cash Pay Market": ["dental-tribune.com", "dermatologytimes.com", "theaestheticguide.com"],
    "Humanoid": [
        "therobotreport.com", "roboticstomorrow.com", "reuters.com/technology",
        "techcrunch.com", "semafor.com", "irobotnews.com",
    ],
    "산업용·서비스 로봇": [
        "therobotreport.com", "roboticstomorrow.com", "reuters.com/technology",
        "techcrunch.com", "semafor.com", "irobotnews.com",
    ],
    "로보틱스 밸류체인": [
        "therobotreport.com", "roboticstomorrow.com", "reuters.com/technology",
        "techcrunch.com", "semafor.com", "irobotnews.com",
    ],
}

# md 원문의 정식 회사명(검색 쿼리 품질용) — GLOBAL_COMPANY_CATEGORY의 짧은 표준 키와
# 다른 경우만 여기 등록. "co" 출력 필드는 항상 표준 키(짧은 이름)를 쓴다.
QUERY_NAME_OVERRIDES = {
    "Abbott": "Abbott Laboratories",
    "UnitedHealth": "UnitedHealth Group",
}

CHANNELS = [
    {"name": "MedTech", "categories": ["MedTech", "Surgical Robot", "IVD", "Digital Health",
                                        "Healthcare Provider", "Cash Pay Market"],
     "max_uses": 35, "extra_rules": ""},
    {"name": "Robotics", "categories": ["Humanoid", "산업용·서비스 로봇", "로보틱스 밸류체인"],
     "max_uses": 30,
     "extra_rules": "huggingface.co/papers, paperswithcode.com은 논문 아카이브이므로 완전 제외하세요.\n"},
]

SYSTEM_PROMPT = """당신은 한국 증권사 의료기기/디지털헬스·로보틱스 애널리스트를 위해 해외 뉴스를
수집하는 리서치 보조원입니다. web_search 도구를 적극적으로 사용해 실제 검색 결과에 근거한
기사만 보고하세요. 검색으로 확인하지 못한 URL/제목/날짜를 지어내면 절대 안 됩니다."""


def build_user_prompt(channel, now_kst, cutoff_kst):
    lines = []
    lines.append(
        f"지금 시각은 한국시간(KST) {now_kst.strftime('%Y-%m-%d %H:%M')}입니다. "
        f"아래 시각 이후(KST {cutoff_kst.strftime('%Y-%m-%d %H:%M')} 이후, 즉 최근 "
        f"{int((now_kst - cutoff_kst).total_seconds() // 3600)}시간 이내)에 발행된 기사만 수집하세요. "
        f"이보다 오래된 기사는 제외합니다."
    )
    lines.append("")
    lines.append("검색 방법(각 카테고리마다 반드시 두 종류를 모두 수행):")
    lines.append("1) 카테고리 전용 사이트 각각에 대해 `site:도메인 키워드` 형태로 site: 검색을 수행해 "
                  "그 사이트의 최근 기사를 확인하세요.")
    lines.append("2) 카테고리별로 지정된 기업 각각에 대해 사이트 제한 없이 "
                  "`기업명 news 날짜(예: Aug 20 2026)` 형식으로 개별 검색하세요.")
    lines.append("3) 수집 기간 중 관련 학술대회·전시회가 있으면 `학회명 + 기업명` 조합으로 추가 검색하세요.")
    lines.append("지정 기업이 아니어도 투자자·파트너로 참여한 중요 뉴스는 포함해도 됩니다. "
                 "단순 주가 변동이나 Money Flow(자금 흐름)만 다루는 기사는 제외하세요. "
                 "동일 내용의 중복 기사는 하나만 남기세요.")
    if channel["extra_rules"]:
        lines.append(channel["extra_rules"])
    lines.append("")
    lines.append("카테고리와 사이트/지정 기업 목록:")
    for cat in channel["categories"]:
        sites = ", ".join(CATEGORY_SITES.get(cat, []))
        companies = [c for c, cc in GLOBAL_COMPANY_CATEGORY.items() if cc == cat]
        query_names = [QUERY_NAME_OVERRIDES.get(c, c) for c in companies]
        lines.append(f"- [{cat}] 사이트: {sites} / 지정 기업: {', '.join(query_names)}")
    lines.append("")
    lines.append(
        "결과를 아래 스키마의 JSON 배열로만 답하세요(다른 설명, 머리말, 마크다운 코드블록 없이 "
        "순수 JSON 배열만 — 검색을 아무리 여러 번 해도 최종 답변은 이 JSON 배열 하나만):\n"
        '[{"cat": "카테고리명(위 목록의 표기 그대로)", "co": "이 기사와 가장 관련된 지정 기업의 '
        '표준 표기(위 목록 지정 기업 이름 중 하나, 지정 기업이 아니면 실제 기업/기관명)", '
        '"t": "영문 기사 제목 원문", "url": "실제 검색 결과에서 확인한 기사 URL", '
        '"date": "YYYY-MM-DD(발행일, KST 아닌 원문 발행일 그대로)", '
        '"src": "도메인(예: medtechdive.com)", '
        '"summary": "한국어 2줄 요약, 두 줄은 \\n으로 구분, 각 줄은 25~50자 내외의 완결된 '
        '서술 문장. 기사에 없는 내용 추측 금지. 단순 주가/자금흐름 기사면 '
        '\\"단순 주가/자금흐름 기사\\"라고 그대로 쓸 것"}, ...]\n'
        "해당 기간에 검색으로 확인된 기사가 하나도 없는 카테고리는 그냥 결과에서 생략하세요"
        "(빈 배열 항목을 넣지 말고 통째로 생략). 검색으로 실제 확인하지 못한 기사는 절대 포함하지 마세요."
    )
    return "\n".join(lines)


def extract_final_text(resp):
    """web_search 도구 사용 중간에 낀 텍스트 블록이 있을 수 있으므로, 뒤에서부터
    비어있지 않은 text 블록을 찾아 우선 사용하고, 그걸로 JSON 파싱이 안 되면 전체
    text 블록을 이어붙여 재시도한다."""
    text_blocks = [b.text for b in resp.content if getattr(b, "type", None) == "text" and b.text.strip()]
    return text_blocks


def call_channel(client, channel, now_kst, cutoff_kst, debug=False):
    user_prompt = build_user_prompt(channel, now_kst, cutoff_kst)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": channel["max_uses"],
        }],
        messages=[{"role": "user", "content": user_prompt}],
    )
    if debug:
        stop = getattr(resp, "stop_reason", None)
        n_search = sum(1 for b in resp.content if getattr(b, "type", None) == "server_tool_use")
        print(f"[DEBUG] {channel['name']} 채널: stop_reason={stop}, 서버 도구 호출 {n_search}회", file=sys.stderr)

    text_blocks = extract_final_text(resp)
    if not text_blocks:
        print(f"[WARN] {channel['name']} 채널: 응답에 텍스트가 없습니다.", file=sys.stderr)
        return []

    # sys.path의 summarize_news.py에 있는 파서를 재사용(코드펜스/잘린 JSON까지 방어적으로 처리).
    from summarize_news import parse_json_array

    for candidate in (text_blocks[-1], "\n".join(text_blocks)):
        parsed = parse_json_array(candidate)
        if isinstance(parsed, list) and parsed:
            return parsed
    print(f"[WARN] {channel['name']} 채널: JSON 파싱 실패, 원문 앞 300자: {text_blocks[-1][:300]}",
          file=sys.stderr)
    return []


def normalize_item(raw, valid_categories, debug=False):
    cat = str(raw.get("cat") or "").strip()
    if cat not in valid_categories:
        return None
    co = str(raw.get("co") or "").strip()
    title = str(raw.get("t") or "").strip()
    url = str(raw.get("url") or "").strip()
    date_str = str(raw.get("date") or "").strip()
    src = str(raw.get("src") or "").strip()
    summary = str(raw.get("summary") or "").strip()
    if not (title and url and date_str):
        return None
    try:
        datetime.date.fromisoformat(date_str)
    except ValueError:
        return None
    if not src and "//" in url:
        src = url.split("//", 1)[1].split("/", 1)[0].replace("www.", "")
    return {
        "co": co or "News",
        "ctx": "News",
        "t": title,
        "src": src,
        "date": date_str,
        "url": url,
        "summary": summary,  # 이미 요약까지 생성해뒀으므로 summarize_news.py가 재요약하지 않음.
    }


def merge_into(by_category, new_items, debug=False):
    added = 0
    for it in new_items:
        cat = it["__cat"]
        bucket = by_category.setdefault(cat, [])
        if any(existing.get("url") == it["url"] for existing in bucket):
            continue
        if any(is_duplicate_title(it["t"], existing.get("t", "")) for existing in bucket):
            continue
        bucket.append({k: v for k, v in it.items() if k != "__cat"})
        added += 1
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="news.json")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[WARN] ANTHROPIC_API_KEY가 없어 Claude 검색 보강을 건너뜁니다 "
              "(RSS 수집 결과는 그대로 유지됩니다).", file=sys.stderr)
        sys.exit(0)

    try:
        import anthropic
    except ImportError:
        print("[WARN] anthropic 패키지가 없어 Claude 검색 보강을 건너뜁니다.", file=sys.stderr)
        sys.exit(0)

    try:
        with open(args.out, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[WARN] {args.out} 읽기 실패, Claude 검색 보강을 건너뜁니다: {e}", file=sys.stderr)
        sys.exit(0)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_kst = now_utc.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
    cutoff_hours = recency_hours_for_today(now_kst.date())
    cutoff_kst = now_kst - datetime.timedelta(hours=cutoff_hours)
    print(f"[INFO] Claude 검색 보강: 최근 {cutoff_hours}시간 이내 기사 대상", file=sys.stderr)

    by_category = {}
    for g in data.get("global", []):
        by_category[g.get("cat")] = list(g.get("items", []))

    client = anthropic.Anthropic(api_key=api_key)
    total_added = 0
    any_call_ok = False

    for channel in CHANNELS:
        try:
            raw_items = call_channel(client, channel, now_kst, cutoff_kst, debug=args.debug)
            any_call_ok = True
        except Exception as e:
            print(f"[WARN] {channel['name']} 채널 검색 호출 실패: {e}", file=sys.stderr)
            continue

        valid_categories = set(channel["categories"])
        normalized = []
        for raw in raw_items:
            item = normalize_item(raw, valid_categories, debug=args.debug)
            if item is None:
                continue
            item["__cat"] = str(raw.get("cat") or "").strip()
            normalized.append(item)

        added = merge_into(by_category, normalized, debug=args.debug)
        total_added += added
        print(f"[INFO] {channel['name']} 채널: 검색 결과 {len(raw_items)}건 중 {added}건 신규 병합", file=sys.stderr)

    if not any_call_ok:
        print("[WARN] 모든 채널 호출이 실패해 기존 news.json을 그대로 둡니다.", file=sys.stderr)
        sys.exit(0)

    global_section = []
    for cat in CATEGORY_ORDER:
        items = sorted(by_category.get(cat, []), key=lambda x: x.get("date", ""), reverse=True)
        items = items[:MAX_ITEMS_PER_CATEGORY]
        global_section.append({"cat": cat, "items": items})
    data["global"] = global_section

    source = data.get("source", "") or ""
    if "Claude 웹검색" not in source:
        data["source"] = (source + " + Claude 웹검색(site: 검색 기반, md 가이드라인 방식) 보강").strip(" +")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    total_items = sum(len(g["items"]) for g in global_section)
    print(f"저장 완료: {args.out} (Claude 검색으로 {total_added}건 신규 병합, 해외 총 {total_items}건)")


if __name__ == "__main__":
    main()
