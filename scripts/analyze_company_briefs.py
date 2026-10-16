#!/usr/bin/env python3
"""
산업·기업 뉴스 › 브리핑 › "기업 브리핑" 생성 (2026-10-16 사수 피드백).

- 섹터 브리핑(analyze_news_trend.py)은 섹터 단위 종합이라 기업 하나의 흐름은 보기 어렵다는 피드백.
  처음엔 "검색 후 분석 버튼을 누르면 그때 AI가 분석"하는 방식도 검토했지만, 이 사이트는 서버 없는
  정적 페이지(GitHub Pages)라 버튼에서 AI를 부르려면 API 키를 페이지에 노출해야 해서 채택하지 않았다.
  대신 주 1회 기업별 정리본을 미리 만들어 두고, 화면에서 기업을 고르면 바로 보여준다.
- 입력: news_history.jsonl(누적 클리핑) 중 최근 WINDOW_DAYS일. 기사마다 이미 AI 2줄 요약(summary)과
  맥락 태그(ctx)가 있어 원문을 다시 읽지 않고 그 요약들을 모아 정리한다(토큰이 적게 든다).
- 대상: 그 기간 기사 MIN_ARTICLES건 이상인 기업(co 필드, 쉼표로 여러 기업). 그보다 적으면 화면에서
  기사 목록만 보여준다(정리할 거리가 부족).
- 호출량: 기업 BATCH_SIZE곳을 한 번에 묶어 1회 호출(약 70개 기업 → 7회 안팎). 기사 구성이 지난번과
  같은 기업(sig — 기사 URL 목록 해시)은 다시 만들지 않는다. 주말(토요일)에 돌려 평일 뉴스 요약과
  하루 한도를 나눠 쓰지 않는다.
- Gemini 키: GEMINI_API_KEY_CONCALL → GEMINI_API_KEY_EARNINGS → GEMINI_API_KEY 순서(쿼터 소진·과부하 시
  뉴스 요약과 같은 자동 전환 — summarize_news.GeminiFallbackProvider). 하루 한도가 다 차면 만든 데까지
  저장하고 끝낸다(남은 기업은 다음 실행에서).

출력: company_briefs.json
  {updated, window_days, from, to, companies: {기업키: {region, cat, n, from, to, sig, headline, points[],
   watch, generated_at, model}}}

사용법:
    GEMINI_API_KEY_CONCALL=xxx python scripts/analyze_company_briefs.py
    python scripts/analyze_company_briefs.py --force          # 기사 구성이 같아도 전부 다시
    python scripts/analyze_company_briefs.py --only 씨어스     # 한 기업만
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import sys
import time

from summarize_news import (GEMINI_MODEL, GeminiFallbackProvider, GeminiProvider, ServerBusy,
                            is_daily_quota_exhausted)

HISTORY = "news_history.jsonl"
OUT = "company_briefs.json"
WINDOW_DAYS = 30
MIN_ARTICLES = 3
MAX_ITEMS_PER_COMPANY = 20   # 프롬프트에 넣는 기사 수 상한(최신순)
BATCH_SIZE = 10
MAX_OUTPUT_TOKENS = 8000
PACE_SECONDS = 13
KEY_ENVS = ("GEMINI_API_KEY_CONCALL", "GEMINI_API_KEY_EARNINGS", "GEMINI_API_KEY")

SYSTEM_PROMPT = """당신은 한국 증권사 의료기기·헬스케어·로보틱스 애널리스트를 돕는 뉴스 정리 보조원입니다.
기업별로 최근 30일 기사 요약 목록을 받아, 그 기업에 무슨 일이 있었는지 한국어로 간결하게 정리합니다.
JSON 객체 하나만 출력하세요(설명·코드블록 없이).

출력: {"companies": [{"key": "입력의 기업 키 그대로", "headline": "이 기간 가장 중요한 흐름 한 문장",
  "points": ["핵심 사실 3~5개, 각 1문장 '~함.'/'~임.' 종결, 날짜(M/D)를 앞에 붙이면 좋음"],
  "watch": "향후 확인할 일정·변수 1문장(기사에 근거가 없으면 빈 문자열)"}]}

규칙:
- 입력 기사에 있는 사실만 쓰고, 수치·날짜·고유명사는 그대로 옮기세요. 추측·전망을 지어내지 마세요.
- 같은 사건을 다룬 기사 여러 개는 하나로 합치세요.
- 업계 일반 기사에 기업이 스치듯 언급된 경우는 비중을 낮추고, 그 기업이 주체인 사건(실적·수주·인허가·
  임상·M&A·신제품·공시 등)을 우선하세요.
- 입력의 모든 기업 키에 대해 하나씩 출력하세요."""

try:
    from company_names import glossary_text, normalize_text
    SYSTEM_PROMPT += glossary_text()
except Exception:
    normalize_text = lambda s: s


def load_history():
    rows = []
    with open(HISTORY, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def group_by_company(rows, since):
    groups = collections.defaultdict(list)
    for r in rows:
        if (r.get("date") or "") < since:
            continue
        for co in (r.get("co") or "").split(","):
            co = co.strip()
            if co:
                groups[co].append(r)
    return groups


def sig_of(items):
    return hashlib.sha1("|".join(sorted(r.get("url") or r.get("t", "") for r in items)).encode("utf-8")).hexdigest()[:12]


def company_block(key, items):
    items = sorted(items, key=lambda r: r.get("date") or "", reverse=True)[:MAX_ITEMS_PER_COMPANY]
    lines = []
    for r in items:
        d = r.get("date") or ""
        md = f"{int(d[5:7])}/{int(d[8:10])}" if len(d) >= 10 else d
        body = (r.get("summary") or r.get("t") or "").replace("\n", " ")
        lines.append(f"- {md} [{r.get('ctx') or '뉴스'}] {body}")
    return f"### {key}\n" + "\n".join(lines)


def parse_response(raw):
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
    data = json.loads(text)
    return {c["key"]: c for c in data.get("companies", []) if isinstance(c, dict) and c.get("key")}


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


def save(out):
    out["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    rows = load_history()
    if not rows:
        print("news_history.jsonl이 비어 있습니다.")
        return
    last = max(r.get("date") or "" for r in rows)
    since = (datetime.date.fromisoformat(last) - datetime.timedelta(days=WINDOW_DAYS - 1)).isoformat()
    groups = group_by_company(rows, since)

    try:
        with open(OUT, encoding="utf-8") as f:
            prev = json.load(f).get("companies", {})
    except (OSError, json.JSONDecodeError):
        prev = {}

    out = {"window_days": WINDOW_DAYS, "from": since, "to": last, "companies": {}}
    todo = []
    for key, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(items) < MIN_ARTICLES or (args.only and key != args.only):
            continue
        meta = {
            "region": collections.Counter(r.get("region") for r in items).most_common(1)[0][0],
            "cat": collections.Counter(r.get("cat") for r in items).most_common(1)[0][0],
            "n": len(items),
            "from": min(r["date"] for r in items),
            "to": max(r["date"] for r in items),
            "sig": sig_of(items),
        }
        old = prev.get(key)
        # sample: 로컬 미리보기용으로 손으로 넣은 견본 — 실제 실행에선 항상 새로 만든다
        if old and old.get("sig") == meta["sig"] and old.get("headline") and not old.get("sample") and not args.force:
            out["companies"][key] = old  # 기사 구성이 같으면 지난 정리본 그대로
            continue
        out["companies"][key] = {**(old or {}), **meta, "stale": bool(old)}
        todo.append((key, items))

    print(f"[INFO] 기간 {since} ~ {last} · 대상 기업 {len(out['companies'])}곳 · 새로 정리 {len(todo)}곳", file=sys.stderr)
    if not todo:
        save(out)
        return
    provider = build_provider()
    if provider is None:
        print(f"[WARN] Gemini 키({' / '.join(KEY_ENVS)})가 없어 정리를 건너뜁니다.", file=sys.stderr)
        save(out)
        return

    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        user = "\n\n".join(company_block(k, items) for k, items in batch)
        try:
            parsed = parse_response(provider.call(SYSTEM_PROMPT, user, MAX_OUTPUT_TOKENS))
        except ServerBusy:
            print("[WARN] Gemini 과부하 지속 — 만든 데까지 저장하고 종료(남은 기업은 다음 실행에서).", file=sys.stderr)
            break
        except Exception as e:
            if is_daily_quota_exhausted(e):
                print("[WARN] Gemini 하루 쿼터 소진 — 만든 데까지 저장하고 종료.", file=sys.stderr)
                break
            print(f"[WARN] 묶음 {i // BATCH_SIZE + 1} 정리 실패: {str(e)[:300]}", file=sys.stderr)
            time.sleep(PACE_SECONDS)
            continue
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for k, _ in batch:
            c = parsed.get(k)
            if not c:
                continue
            out["companies"][k].update({
                "headline": normalize_text(c.get("headline") or ""),
                "points": [normalize_text(p) for p in (c.get("points") or [])][:5],
                "watch": normalize_text(c.get("watch") or ""),
                "generated_at": now,
                "model": getattr(provider, "last_model", None) or GEMINI_MODEL,
                "stale": False,
            })
        print(f"[OK] 묶음 {i // BATCH_SIZE + 1}: {sum(1 for k, _ in batch if k in parsed)}/{len(batch)}곳", file=sys.stderr)
        time.sleep(PACE_SECONDS)

    save(out)
    done = sum(1 for c in out["companies"].values() if c.get("headline") and not c.get("stale"))
    print(f"완료: {OUT} (정리본 {done}곳 / 대상 {len(out['companies'])}곳)")


if __name__ == "__main__":
    main()
