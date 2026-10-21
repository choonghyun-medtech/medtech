#!/usr/bin/env python3
"""
뉴스 기사 선택 규칙 — 국내(scrape_news.py)·해외(scrape_news_global*.py)·같은 사안 묶기(group_news.py) 공통
(2026-10-21 사용자와 확정).

전체 흐름:
  수집  제목 유사도(60%) 1차 중복 제거 → 후보(pool): 회사당 최대 COMPANY_POOL건 · 카테고리당 최대 CATEGORY_POOL건
        + 바로 보여줄 기본 목록(items): 회사당 COMPANY_SHOW건 · 카테고리당 CATEGORY_SHOW건(fallback_show)
  묶기  group_news.py가 Gemini로 "제목은 다르지만 같은 사안"인 기사를 묶어 items를 다시 만든다(select_grouped)
        — 회사당 "사안" COMPANY_SHOW개 · 카테고리당 CATEGORY_SHOW건, 나머지 같은 사안 기사는 대표 기사의 dups로 접힘.
        묶기가 실패하면(쿼터 등) 수집 단계의 기본 목록이 그대로 남는다.
  고르는 순서는 어디서나 "매체 등급(tier, 작을수록 우선) → 같은 등급이면 최신순(ts)".

수집 단계 후보를 넉넉히(회사 5 · 카테고리 12) 두는 이유: 묶기 전에 회사 3건·카테고리 6건으로 자르면
같은 사안 기사(예: 합의금 기사 3건)가 칸을 다 채워 다른 사안(실적·컨콜)이 수집 단계에서 잘려 나간다.
회사당 5건 상한은 한 회사 기사가 쏟아지는 날(실적 발표 등) 다른 회사 몫을 남기려고 — 아카이브상
한 회사가 하루 6건 이상인 경우는 해외 약 3%, 국내 1% 미만이고 대부분 한 사안에 기사가 몰린 날이다.
"""

COMPANY_POOL = 5
CATEGORY_POOL = 12
COMPANY_SHOW = 3
CATEGORY_SHOW = 6

# 접힌 같은 사안 기사에 남기는 필드(화면에 제목·매체·날짜·링크만 보여준다)
DUP_FIELDS = ("t", "src", "url", "date")


def order(items):
    """매체 등급 → 같은 등급이면 최신순. ts(ISO 시각)가 없으면 date로."""
    by_time = sorted(items, key=lambda x: x.get("ts") or x.get("date") or "", reverse=True)
    return sorted(by_time, key=lambda x: x.get("tier", 4))


def primary_company(it):
    """여러 회사가 같이 나오는 기사("클래시스, 휴젤")는 첫 회사 기준으로 센다."""
    return (it.get("co") or "").split(",")[0].strip()


def cap_per_company(items, n):
    out, cnt = [], {}
    for it in order(items):
        c = primary_company(it)
        if cnt.get(c, 0) < n:
            out.append(it)
            cnt[c] = cnt.get(c, 0) + 1
    return out


def by_date_desc(items):
    return sorted(items, key=lambda x: x.get("ts") or x.get("date") or "", reverse=True)


def build_pool(items):
    """수집 단계 후보 — (pool, 상한 때문에 빠진 건수)."""
    pool = order(cap_per_company(items, COMPANY_POOL))[:CATEGORY_POOL]
    return by_date_desc(pool), len(items) - len(pool)


def fallback_show(pool):
    """묶기 전·묶기 실패 시 보여줄 기본 목록 — 회사당 3건 · 카테고리 6건(화면은 최신순)."""
    return by_date_desc(order(cap_per_company(pool, COMPANY_SHOW))[:CATEGORY_SHOW])


def select_grouped(pool, group_of):
    """group_of(item) -> 사안 라벨. 같은 회사·같은 라벨 기사를 한 사안으로 묶어 대표 1건(매체 등급 → 최신)을 남기고
    나머지는 대표의 dups로 접는다. 사안 순서는 기사 수(많이 보도된 사안) → 대표 매체 등급 → 최신.
    회사당 사안 COMPANY_SHOW개 · 카테고리당 CATEGORY_SHOW건. 반환: (items, 접힌 기사 수, 상한으로 빠진 사안 수)."""
    groups = {}
    for it in pool:
        groups.setdefault((primary_company(it), group_of(it)), []).append(it)
    reps = []
    for members in groups.values():
        ms = order(members)
        rep = dict(ms[0])
        rep["dups"] = [{k: m.get(k, "") for k in DUP_FIELDS} for m in ms[1:]]
        if not rep["dups"]:
            rep.pop("dups")
        rep["_n"] = len(ms)
        reps.append(rep)
    ranked = sorted(order(reps), key=lambda r: -r["_n"])
    per_co, kept = {}, []
    for r in ranked:
        c = primary_company(r)
        if per_co.get(c, 0) < COMPANY_SHOW:
            kept.append(r)
            per_co[c] = per_co.get(c, 0) + 1
    kept = kept[:CATEGORY_SHOW]
    folded = sum(r["_n"] - 1 for r in kept)
    dropped = len(reps) - len(kept)
    for r in kept:
        r.pop("_n", None)
    return by_date_desc(kept), folded, dropped
