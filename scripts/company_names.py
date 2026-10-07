#!/usr/bin/env python3
"""
뉴스 요약·브리핑(AI 생성 텍스트)의 해외 기업 한글 표기를 대시보드 표기로 통일한다(2026-10-07).

- 배경: AI가 영문 기사를 한글로 옮기며 기업명을 제각각 음역했다(Dexcom을 "덱스콤"으로 —
  대시보드·주가 탭은 "덱스컴"). 브리핑 기업 검색도 표기가 어긋나면 못 찾는다.
- 기준: scripts/company_names_ko.json. ticker가 tickers.json에 있으면 그 name(대시보드 표기)이
  표준이고, 없으면 json의 ko를 쓴다 — 엑셀 쪽 사명이 바뀌어 tickers.json이 갱신되면 지시문도
  자동으로 따라간다.
- 두 겹으로 적용한다: (1) 지시문에 표준 표기 목록을 넣어 처음부터 맞게 쓰게 하고(glossary_text),
  (2) 그래도 어긋난 알려진 표기는 생성 결과에서 기계적으로 바꾼다(normalize_text). 새로 어긋난
  표기를 발견하면 company_names_ko.json의 variants에 한 줄 추가하면 된다.
- 표준 라이브러리만 쓴다 — process-news 워크플로는 AI 라이브러리만 설치하므로.
"""
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NAMES_FILE = os.path.join(SCRIPT_DIR, "company_names_ko.json")
TICKERS_FILE = os.path.join(SCRIPT_DIR, "tickers.json")

_cache = None


def _has_hangul(s):
    return bool(re.search(r"[가-힣]", s or ""))


def load_companies():
    """[{en, ko, short[], variants{}}]. 파일을 못 읽으면 빈 목록(교정 없이 원문 유지)."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(NAMES_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[WARN] {NAMES_FILE}을 읽지 못해 기업명 표기 통일을 건너뜁니다: {e}", file=sys.stderr)
        _cache = []
        return _cache
    try:
        with open(TICKERS_FILE, encoding="utf-8") as f:
            ticker_names = {t["ticker"]: t["name"] for t in json.load(f)}
    except (OSError, json.JSONDecodeError):
        ticker_names = {}

    out = []
    for c in data.get("companies", []):
        # tickers.json 이름이 한글이면 그게 표준(대시보드 표기). 영문(Guardant Health Inc 등)이면 json의 ko.
        tname = ticker_names.get(c.get("ticker"))
        ko = tname if _has_hangul(tname) else c.get("ko")
        if not ko:
            continue
        out.append({
            "en": c["en"],
            "ko": ko,
            "short": [s for s in c.get("short", []) if s != ko],
            "variants": c.get("variants", {}),
        })
    _cache = out
    return out


def glossary_text():
    """AI 지시문 끝에 붙이는 표준 표기 안내. 목록이 비면 빈 문자열."""
    rows = []
    for c in load_companies():
        extra = f" (문장 안에서는 줄여서 {', '.join(c['short'])} 가능)" if c["short"] else ""
        rows.append(f"- {c['en']} → {c['ko']}{extra}")
    if not rows:
        return ""
    return ("\n\n기업명 표기 규칙: 아래 기업을 한글로 쓸 때는 반드시 이 표기를 그대로 쓰세요(다른 음역 금지 —"
            " 예: Dexcom은 '덱스콤'이 아니라 '덱스컴'). 목록에 없는 기업은 통용되는 한글 표기를 쓰세요.\n"
            + "\n".join(rows))


_variant_re = None
_variant_map = None


def normalize_text(text):
    """알려진 잘못된 표기를 표준 표기로 바꾼다. 긴 표기부터 한 번에 치환해 겹침을 막는다."""
    global _variant_re, _variant_map
    if not text:
        return text
    if _variant_re is None:
        _variant_map = {}
        for c in load_companies():
            _variant_map.update(c["variants"])
        keys = sorted(_variant_map, key=len, reverse=True)
        _variant_re = re.compile("|".join(re.escape(k) for k in keys)) if keys else False
    if not _variant_re:
        return text
    return _variant_re.sub(lambda m: _variant_map[m.group(0)], text)


if __name__ == "__main__":
    # 점검용: 표준 표기 목록과, 변환 대상 표기가 표준/약칭과 어긋나 있지 않은지 출력.
    for c in load_companies():
        allowed = {c["ko"], *c["short"]}
        bad = [f"{k}->{v}" for k, v in c["variants"].items() if not any(v in a or a in v for a in allowed)]
        print(f"{c['en']:24s} {c['ko']}" + (f"  [WARN 변환 대상이 표준과 다름: {bad}]" if bad else ""))
