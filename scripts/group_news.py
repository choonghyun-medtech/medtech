#!/usr/bin/env python3
"""
같은 사안 기사 묶기(2026-10-21) — process-news 워크플로에서 요약(summarize_news.py) 바로 앞에 돈다.

수집 단계의 제목 유사도(60%) 중복 제거는 같은 보도자료 재배포처럼 제목이 거의 같은 경우만 잡는다.
"Intuitive Surgical Stock Slides Despite Earnings Beat as Company Discloses Settlement"(Barron's)와
"Intuitive Surgical drops despite Q3 beats as $450M settlement weighs"(Seeking Alpha)처럼 매체마다 제목을
다르게 쓴 같은 사안은 못 잡아서, 한 회사 같은 사안 기사가 칸을 여러 개 차지했다(사용자 지적).

- 국내·해외 각각 Gemini 1회: 수집 단계 후보(카테고리별 pool, 회사 5 · 카테고리 12)의 회사명·제목·RSS 요약문
  앞부분만 보내 "같은 회사의 같은 사안"끼리 같은 라벨을 받는다(요약을 쓰는 게 아니라 라벨만 받아 응답이 짧다).
  본문은 읽지 않는다 — 유료 기사·접속 차단이 많고, 무료 하루 쿼터(20회) 안에서 기사 수십 건 본문은 무리.
- 라벨대로 news_select.select_grouped로 다시 고른다: 사안마다 대표 1건(매체 등급 → 최신), 회사당 사안 3개,
  카테고리 6건. 같은 사안의 나머지 기사는 대표 기사의 dups(제목·매체·날짜·링크)로 접혀 화면에서 펼쳐 볼 수 있다.
- 실패(키 없음·쿼터 소진·응답 파싱 실패)해도 수집 단계가 만든 기본 목록(items, 회사 3 · 카테고리 6)이 그대로
  남는다 — 뉴스가 비는 일은 없다. 어느 쪽이든 끝나면 pool은 지운다(요약·아카이브는 items만 본다).

사용법: python scripts/group_news.py --out news.json [--debug]
"""
import argparse
import json
import sys

from news_select import primary_company, select_grouped
from summarize_news import build_provider, parse_json_array, DailyQuotaExhausted, ServerBusy

DESC_CHARS = 160  # 요약문은 앞부분만 — 같은 사안인지 판단엔 충분하고 입력 토큰을 아낀다

SYSTEM = """당신은 헬스케어·로보틱스 기업 뉴스 클리핑 편집자입니다.
입력은 기사 목록(JSON 배열)이며 각 기사에 id, co(회사), t(제목), d(요약문 앞부분)가 있습니다.
같은 회사(co)에 대해 "같은 사안(같은 사건·발표·공시·이벤트)"을 다룬 기사끼리 같은 g 값을 주세요.
- 같은 사안 예: 같은 분기 실적 발표를 다룬 서로 다른 매체 기사, 같은 인허가 획득 소식, 같은 합의금 공시와 그에 따른 주가 하락.
- 다른 사안 예: 같은 날 같은 회사라도 "3분기 실적 상회"와 "합의금 공시로 주가 하락"은 다른 사안,
  "실적 발표 예고/프리뷰"와 "실적 발표 결과"도 다른 사안, 컨콜 원문(transcript) 게시는 별도 사안.
- 애매하면 다른 사안으로 두세요(잘못 묶으면 기사가 숨겨집니다).
- 회사가 다르면 무조건 다른 g.
반드시 입력의 모든 id에 대해 [{"id": 0, "g": "짧은 라벨"}, ...] 형식의 JSON 배열만 출력하세요. 다른 말은 쓰지 마세요."""


def group_region(provider, groups, region, debug=False):
    """groups: 지역의 카테고리 목록. 각 카테고리의 pool로 items를 다시 만든다. 반환: 호출 성공 여부."""
    flat = []  # (카테고리 인덱스, 기사)
    for gi, g in enumerate(groups):
        for it in g.get("pool") or []:
            flat.append((gi, it))
    if not flat:
        print(f"[INFO] {region}: 묶을 후보(pool)가 없어 건너뜀(이미 처리됐거나 예전 형식)", file=sys.stderr)
        return True
    # 같은 회사 기사가 붙어 있어야 모델이 비교하기 쉽다
    flat.sort(key=lambda x: (primary_company(x[1]), x[1].get("t", "")))
    payload = [{"id": i, "co": primary_company(it), "t": it.get("t", ""),
                "d": (it.get("desc") or "")[:DESC_CHARS]} for i, (_, it) in enumerate(flat)]
    max_tokens = min(8000, 300 + 30 * len(payload))
    text = provider.call(SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens)
    arr = parse_json_array(text) or []
    label = {}
    for row in arr:
        if isinstance(row, dict) and isinstance(row.get("id"), int) and row.get("g") is not None:
            label[row["id"]] = str(row["g"]).strip()
    missing = len(payload) - len(label)
    if missing:
        print(f"[WARN] {region}: 라벨이 빠진 기사 {missing}건은 각자 단독 사안으로 처리", file=sys.stderr)
    group_of_id = {}
    for i, (_, it) in enumerate(flat):
        group_of_id[id(it)] = label.get(i, f"__solo_{i}")

    total_pool = total_shown = total_folded = total_dropped = 0
    for gi, g in enumerate(groups):
        pool = g.get("pool") or []
        if not pool:
            continue
        items, folded, dropped = select_grouped(pool, lambda it: group_of_id.get(id(it), f"__solo_{id(it)}"))
        g["items"] = items
        total_pool += len(pool)
        total_shown += len(items)
        total_folded += folded
        total_dropped += dropped
        if debug:
            for it in items:
                if it.get("dups"):
                    print(f"[DEBUG] {g.get('cat')} · {it.get('co')}: '{it.get('t')}' 외 같은 사안 {len(it['dups'])}건 접음",
                          file=sys.stderr)
    print(f"{region}: 후보 {total_pool}건 → 표시 {total_shown}건 (같은 사안으로 접힘 {total_folded}건 · "
          f"회사당 사안 3개/카테고리 6건 상한으로 빠진 사안 {total_dropped}개)")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="news.json")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    try:
        with open(args.out, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[WARN] {args.out} 읽기 실패 — 묶기 건너뜀 ({e})", file=sys.stderr)
        sys.exit(0)

    provider = build_provider()
    if provider is None:
        print("[WARN] AI 키가 없어 같은 사안 묶기를 건너뜁니다 — 수집 단계 기본 목록(회사 3·카테고리 6) 유지",
              file=sys.stderr)
    else:
        for region in ("domestic", "global"):
            try:
                group_region(provider, data.get(region, []), region, debug=args.debug)
            except (DailyQuotaExhausted, ServerBusy):
                print(f"[WARN] {region}: 쿼터 소진/과부하로 묶기 중단 — 남은 지역은 기본 목록 유지", file=sys.stderr)
                break
            except Exception as e:
                print(f"[WARN] {region}: 묶기 실패, 기본 목록 유지 ({e})", file=sys.stderr)

    # 성공·실패와 무관하게 후보 목록은 지운다(요약·아카이브·화면은 items만 쓴다)
    for region in ("domestic", "global"):
        for g in data.get(region, []):
            g.pop("pool", None)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
