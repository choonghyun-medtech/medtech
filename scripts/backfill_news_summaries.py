#!/usr/bin/env python3
"""
뉴스 아카이브(news_history.jsonl)에서 요약이 빠진 기사를 나중에 채운다 (2026-10-16).

- 배경: 매일 뉴스 요약(summarize_news.py)은 "그날 수집한 news.json"만 처리한다. 하루 한도 소진·과부하로
  그날 요약을 못 하면, 다음 날 news.json이 새 기사로 덮어써지면서 요약 없는 기사가 그대로 아카이브에
  남고 다시 처리할 장치가 없었다(10/8·10/12·10/13에 약 145건). 이 스크립트가 그 빈칸을 채운다.
- 요약 재료: 매일 요약은 제목 + 기사 첫머리 소개글(desc, RSS)을 쓰는데 소개글은 아카이브에 남지 않는다.
  · 기사 링크가 언론사 원문이면 페이지의 소개글(meta og:description/description)을 다시 읽어 평소처럼 요약.
  · 해외 기사 대부분은 구글 뉴스 중계 링크(news.google.com)라 원문 주소를 안정적으로 풀 수 없어
    제목만으로 요약한다(사용자 결정 — 비워두는 것보다 낫다). 이 경우 summary_src="title"로 표시해
    화면에서 "제목 기반 요약"임을 알린다.
- 요약·맥락 태그는 매일 요약과 같은 함수(summarize_domestic/summarize_global)·같은 지시문으로 만든다.
  "단순 주가 등락" 기사로 판정되면 매일 요약처럼 아카이브에서 뺀다.
- Gemini 키: GEMINI_API_KEY_CONCALL → GEMINI_API_KEY_EARNINGS → GEMINI_API_KEY(매일 뉴스용 키는 마지막).
  한도가 다 차면 채운 데까지 저장한다(다시 실행하면 남은 것만 이어서).

사용법:
    python scripts/backfill_news_summaries.py --since 2026-10-01
    python scripts/backfill_news_summaries.py --since 2026-10-01 --dry-run   # 대상·소개글 수집만 확인
"""
import argparse
import json
import os
import sys
import time

import requests
from bs4 import BeautifulSoup

from company_names import normalize_text
from summarize_news import (GEMINI_MODEL, DailyQuotaExhausted, GeminiFallbackProvider, GeminiProvider,
                            ServerBusy, summarize_domestic, summarize_global)

HISTORY = "news_history.jsonl"
KEY_ENVS = ("GEMINI_API_KEY_CONCALL", "GEMINI_API_KEY_EARNINGS", "GEMINI_API_KEY")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}


def fetch_description(url):
    """기사 페이지의 소개글(meta og:description → description). 못 읽으면 빈 문자열."""
    if not url or "news.google.com" in url:
        return ""
    try:
        r = requests.get(url, headers=UA, timeout=10)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or r.encoding  # 국내 언론사 일부는 EUC-KR
        soup = BeautifulSoup(r.text, "html.parser")
        for attrs in ({"property": "og:description"}, {"name": "description"}, {"name": "twitter:description"}):
            tag = soup.find("meta", attrs=attrs)
            if tag and (tag.get("content") or "").strip():
                return " ".join(tag["content"].split())[:500]
    except Exception:
        pass
    return ""


def build_provider():
    keys = []
    for env in KEY_ENVS:
        k = (os.environ.get(env) or "").strip()
        if k and k not in keys:
            keys.append(k)
    if not keys:
        return None
    provider = GeminiFallbackProvider(GeminiProvider, keys)
    try:
        provider._provider(0, GEMINI_MODEL)
    except Exception as e:
        print(f"[WARN] Gemini 클라이언트 초기화 실패 ({e})", file=sys.stderr)
        return None
    return provider


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-10-01", help="이 날짜(YYYY-MM-DD) 이후 기사만 채운다")
    ap.add_argument("--dry-run", action="store_true", help="AI 호출 없이 대상·소개글 수집만 확인")
    args = ap.parse_args()

    with open(HISTORY, encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]
    targets = [r for r in rows if (r.get("date") or "") >= args.since and not (r.get("summary") or "").strip()]
    dom = [r for r in targets if r.get("region") == "domestic"]
    glo = [r for r in targets if r.get("region") != "domestic"]
    print(f"[INFO] {args.since} 이후 요약 없는 기사 {len(targets)}건(국내 {len(dom)} · 해외 {len(glo)}) — 소개글 수집 중", file=sys.stderr)
    if not targets:
        return

    for r in targets:
        r["desc"] = fetch_description(r.get("url"))
        time.sleep(0.2)
    got = sum(1 for r in targets if r["desc"])
    print(f"[INFO] 소개글 확보 {got}/{len(targets)}건(나머지는 제목만으로 요약)", file=sys.stderr)
    if args.dry_run:
        for r in targets[:5]:
            print(f"  {r['date']} {r['region']} {r['t'][:50]} → {r['desc'][:80] or '(제목만)'}")
        return

    provider = build_provider()
    if provider is None:
        print(f"[WARN] Gemini 키({' / '.join(KEY_ENVS)})가 없어 건너뜁니다.", file=sys.stderr)
        return
    for name, fn, items in (("국내", summarize_domestic, dom), ("해외", summarize_global, glo)):
        if not items:
            continue
        try:
            fn(provider, items)
        except (DailyQuotaExhausted, ServerBusy):
            print(f"[WARN] 한도 소진·과부하로 {name} 이후 중단 — 채운 데까지 저장(다시 실행하면 이어서).", file=sys.stderr)
            break

    filled = title_only = 0
    for r in targets:
        had_desc = bool(r.pop("desc", ""))
        if (r.get("summary") or "").strip():
            r["summary"] = normalize_text(r["summary"])
            filled += 1
            if not had_desc:
                r["summary_src"] = "title"
                title_only += 1
    # 매일 요약과 같은 기준으로 "단순 주가 등락" 기사는 아카이브에서 뺀다
    before = len(rows)
    rows = [r for r in rows if "단순 주가" not in (r.get("summary") or "")]
    with open(HISTORY, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"완료: {filled}/{len(targets)}건 요약 채움(그중 제목 기반 {title_only}건) · 단순 주가 기사 {before - len(rows)}건 제외")


if __name__ == "__main__":
    main()
