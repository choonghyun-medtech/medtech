#!/usr/bin/env python3
"""
해외기업 컨콜 스크립트(TIKR 원문) 한국어 정리·시계열 분석 (2026-10-15 신설).

- 배경(사수 피드백): 해외기업 컨콜 탭을 IR 보도자료 요약에서 한 단계 더 — 컨콜 원문을
  (1) 실적발표(prepared remarks)와 Q&A로 나눠 한국어로 정리하고, Q&A는 비슷한 질문끼리 묶고,
  (2) 지난 분기 대비 달라진 점·가이던스 변화·지난 분기에 제시한 마일스톤 진행 상황을 추적한다.
- 구조: IR 보도자료 요약(scripts/scrape_earnings_ir.py, earnings_ir.json)은 "발표 당일 숫자 요약"
  으로 그대로 두고, TIKR 원문(concall_transcripts/, extract_concall_transcripts.py로 수동 추가)이
  들어온 분기에만 이 스크립트가 심층 분석을 만들어 concall_analysis/에 원문과 같은 파일명으로
  저장한다. 화면은 IR 카드 아래에 붙인다. 원문은 2026년 2분기부터만 있어 그 이전 분기 시계열은
  IR 요약으로 대신한다.
- 전문 번역이 아니라 "번역 정리본"이다(2026-10-15 결정) — 영어 원문 전체는 기존
  "컨콜 스크립트 보기" 팝업으로 본다. 한 편당 Gemini 호출 1번(정리 + 지난 분기 비교를 한 번에).
- 실적발표/Q&A 경계는 AI에 맡기지 않고 원문 JSON의 qa_start(Q&A가 시작되는 발언 순번)로 자른다.
- 시계열: 같은 회사의 직전 분기 분석(가이던스·마일스톤·핵심 요약)을 함께 넣어 비교시킨다. 직전 분기
  원문 분석이 없으면(2분기 = 첫 원문) earnings_ir.json의 직전 분기 보도자료 요약을 대신 넣는다.
  그래서 처리 순서는 컨콜 날짜 오름차순이고, 같은 회사의 앞 분기가 아직 분석 전이면 뒤 분기는 미룬다.
- Gemini 키: GEMINI_API_KEY_CONCALL(이 작업 전용 무료 키, 별도 구글 프로젝트) → 없거나 하루 쿼터가
  다 떨어지면 GEMINI_API_KEY_EARNINGS로 이어 쓴다. 키·예비 모델 자동 전환은 뉴스 요약
  (summarize_news.py)의 GeminiFallbackProvider를 그대로 쓴다. 하루 쿼터가 다 떨어지면 남은 원문은
  다음 실행으로 넘긴다(워크플로를 실패시키지 않음).
- 원문이 나중에 고쳐지면(파일 내용 해시가 바뀌면) 다시 분석한다. 직전 분기가 다시 분석되면 그 뒤
  분기 비교도 낡으므로 --redo-after로 수동 재분석할 수 있다.

사용법:
    GEMINI_API_KEY_CONCALL=xxx python scripts/analyze_concall_transcripts.py
    python scripts/analyze_concall_transcripts.py --only UNH_2026Q3 --force   # 한 편만 다시
    python scripts/analyze_concall_transcripts.py --max 3                      # 이번 실행 최대 3편
"""
import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import sys
import time

from summarize_news import (GEMINI_MODEL, GeminiFallbackProvider, GeminiProvider, ServerBusy,
                            is_daily_quota_exhausted)

TRANSCRIPT_DIR = "concall_transcripts"
OUT_DIR = "concall_analysis"
EARNINGS_IR_PATH = "earnings_ir.json"
KEY_ENVS = ("GEMINI_API_KEY_CONCALL", "GEMINI_API_KEY_EARNINGS")
MAX_OUTPUT_TOKENS = 16000
DEFAULT_MAX_PER_RUN = 10
PACE_SECONDS = 13  # 무료 티어 분당 5회 제한

SYSTEM_PROMPT = """당신은 한국 증권사 헬스케어/의료기기 애널리스트를 돕는 해외기업 실적 컨퍼런스콜 분석 보조원입니다.
영문 컨콜 스크립트를 읽고, 애널리스트가 원문을 다 읽지 않아도 핵심을 파악할 수 있는 한국어 "번역 정리본"을 만듭니다.
JSON 객체 하나만 출력하세요(설명·코드블록 없이).

입력:
- [회사]/[분기]/[컨콜 날짜]
- [직전 분기 정리] — 같은 회사 직전 분기의 가이던스·마일스톤·핵심 요약(없으면 "없음"). 직전 분기가 컨콜 원문이 아니라
  보도자료 요약일 수도 있다(그 경우 마일스톤 정보는 거의 없다).
- [실적발표] — 경영진 발표(prepared remarks) 원문
- [Q&A] — 질의응답 원문(Operator가 다음 질문자 이름·소속을 소개함)

출력 스키마:
{
  "headline": ["이번 분기 컨콜 핵심 3~5개. 각 1문장, '~함.'/'~임.' 종결"],
  "changes": ["직전 분기 대비 달라진 점 2~5개(가이던스 조정, 새로 등장하거나 사라진 화제, 톤 변화 등). 직전 분기 정리가 없으면 빈 배열"],
  "prepared": {
    "summary": ["실적발표 전체 요약 3~5문장"],
    "sections": [{"title": "사업부/지역/주제명(한국어)", "points": ["핵심 수치와 코멘트 1~3개"]}]
  },
  "guidance": [{
    "item": "가이던스 항목(예: FY26 유기적 매출 성장률, FY26 조정 EPS)",
    "value": "이번 분기 제시 값(원문 단위 그대로, 예: 8~10%, $6.10~6.20)",
    "prev": "직전 분기 정리에 같은 항목이 있으면 그 값, 없으면 빈 문자열",
    "change": "상향|유지|하향|신규|철회|언급 없음|비교불가 중 하나",
    "quote": "근거가 된 원문 영어 문장 1개(그대로 인용, 언급 없음이면 빈 문자열)"
  }],
  "milestones": [{
    "item": "경영진이 제시한 구체적 목표·일정(예: X 제품 FDA 승인 예상, 신공장 가동)",
    "target": "제시된 시기(예: 2026년 하반기, 2027년 초). 없으면 빈 문자열",
    "status": "신규|진행 중|달성|지연|철회|언급 없음 중 하나",
    "note": "상태 판단 근거 1문장(한국어)",
    "quote": "근거 원문 영어 문장 1개(이번 분기 언급이 없으면 빈 문자열)"
  }],
  "qa": [{
    "theme": "질문 주제(한국어, 짧게)",
    "analysts": ["질문자 이름 (소속)"],
    "question": "질문 요지 1~2문장(비슷한 질문 여러 개면 묶어서)",
    "answer": "경영진 답변 요지 1~3문장"
  }]
}

규칙:
- 숫자·단위·기간(FY/분기)은 원문 그대로 옮기고 계산하거나 지어내지 마세요. 원문에 없는 내용은 쓰지 마세요.
- milestones: 직전 분기 정리에 있던 마일스톤은 이번 분기 언급 여부와 관계없이 **하나도 빠짐없이** 넣고, item은 직전 분기
  표기를 글자 그대로 쓰세요. 이번 분기 원문에서 그 항목을 직접 언급한 문장이 있을 때만 진행 상황을 판정하고, 없으면
  status를 "언급 없음", quote를 빈 문자열로 두세요. 비슷하지만 다른 이니셔티브의 수치로 대신 판정하지 마세요.
  이번 분기 새로 제시된 것은 "신규"로 추가하세요. 일반적 포부("성장을 지속")는 넣지 마세요.
- guidance: 회사가 명시한 수치형 가이던스만. 직전 분기 정리에 있던 가이던스 항목도 **하나도 빠짐없이** 넣고(item은 직전
  분기 표기 그대로), prev를 채운 뒤 change를 판정하세요. 이번 분기에 다시 언급되지 않았으면 change="언급 없음", value는 빈 문자열.
  가이던스가 아니라 이미 집행한 실적(예: 3분기까지 자사주 50억 달러 매입)은 value에 넣지 마세요.
- 직전 분기 정리에 쓰인 한국어 표기(사업부명·용어)가 있으면 그 표기를 그대로 따라 분기 간 표기를 통일하세요.
- qa: 같은 주제의 질문은 하나로 묶으세요(질문자 여러 명이면 analysts에 모두). 3~10개 주제. 중요한 순서로.
- 회사·제품·인물 이름은 아래 표기 목록이 있으면 그 표기를 따르고, 없으면 영어 그대로 두세요.
- 문장은 간결한 한국어 보고서체로."""

try:
    from company_names import glossary_text
    SYSTEM_PROMPT += glossary_text()
except Exception:
    pass


def file_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:12]


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def transcript_text(tr):
    """원문 segments를 [실적발표]/[Q&A] 두 덩어리 텍스트로 만든다(qa_start 기준)."""
    qa_start = tr.get("qa_start")
    segs = tr.get("segments", [])
    if qa_start is None:
        qa_start = len(segs)

    def block(items):
        return "\n\n".join(f"{s.get('speaker') or '(발언자 미상)'}: " + "\n".join(s.get("paras", [])) for s in items)

    return block(segs[:qa_start]), block(segs[qa_start:])


def prior_context(ticker, call_date, analyses, earnings_ir, quarter=""):
    """같은 회사의 직전 분기 정리 — 원문 분석이 있으면 그것, 없으면 IR 보도자료 요약. (텍스트, 출처 라벨)
    같은 분기 것은 제외한다(보도자료가 컨콜 전날 나오는 회사가 있어 날짜만 보면 같은 분기가 걸림, 예: RDNT)."""
    prev = [a for a in analyses.values() if a.get("ticker") == ticker and (a.get("call_date") or "") < call_date
            and a.get("quarter") != quarter]
    if prev:
        p = max(prev, key=lambda a: a.get("call_date") or "")
        ctx = {
            "분기": p.get("quarter"),
            "핵심": p.get("headline", []),
            "가이던스": [{k: g.get(k) for k in ("item", "value")} for g in p.get("guidance", [])],
            "마일스톤": [{k: m.get(k) for k in ("item", "target", "status")} for m in p.get("milestones", [])
                      if m.get("status") not in ("달성", "철회")],
        }
        return json.dumps(ctx, ensure_ascii=False, indent=1), f"{p.get('quarter')} 컨콜 분석", p
    irs = [c for c in earnings_ir.get("companies", []) if c.get("ticker") == ticker and (c.get("date") or "") < call_date
           and c.get("quarter") != quarter]
    if irs:
        c = max(irs, key=lambda c: c.get("date") or "")
        ctx = {"분기": c.get("quarter"), "보도자료 지표": c.get("metrics", []), "보도자료 요약": c.get("summary_ko", [])}
        return json.dumps(ctx, ensure_ascii=False, indent=1), f"{c.get('quarter')} IR 보도자료 요약", None
    return "없음", "", None


def carry_over_prior(parsed, prior):
    """직전 분기 분석의 가이던스·마일스톤 중 응답에서 빠진 것을 "언급 없음"으로 채운다(시계열이 끊기지 않게).
    모델에 빠짐없이 넣으라고 지시하지만, 실제로 빠뜨린 사례가 있어(2026-10-15 UNH 3Q 샘플) 코드로 한 번 더 막는다."""
    if not prior:
        return 0
    # 모델이 항목명을 조금 바꿔 쓰는 경우가 있어(예: "부채 대 자본 비율 개선" → "… 40% 도달") 이름이 비슷하면 같은 항목으로 본다
    norm = lambda s: re.sub(r"[\s()\[\]·,~\-]", "", (s or "").lower())
    def present(item, have):
        n = norm(item)
        return any(n == h or difflib.SequenceMatcher(None, n, h).ratio() >= 0.6 for h in have)
    added = 0
    have = {norm(g.get("item")) for g in parsed.get("guidance", [])}
    for g in prior.get("guidance", []):
        if g.get("change") in ("철회",) or present(g.get("item"), have):
            continue
        parsed["guidance"].append({"item": g.get("item"), "value": "", "prev": g.get("value") or g.get("prev") or "",
                                   "change": "언급 없음", "quote": ""})
        added += 1
    have = {norm(m.get("item")) for m in parsed.get("milestones", [])}
    for m in prior.get("milestones", []):
        if m.get("status") in ("달성", "철회") or present(m.get("item"), have):
            continue
        parsed["milestones"].append({"item": m.get("item"), "target": m.get("target") or "", "status": "언급 없음",
                                     "note": "이번 분기 컨콜에서 직접 언급되지 않음", "quote": ""})
        added += 1
    return added


def parse_response(raw):
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict) or not data.get("headline"):
        raise ValueError("응답에 headline이 없음")
    for key, default in (("changes", []), ("prepared", {}), ("guidance", []), ("milestones", []), ("qa", [])):
        data.setdefault(key, default)
    return data


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
    print(f"[INFO] Gemini 키 {len(keys)}개 · 기본 모델 {GEMINI_MODEL}", file=sys.stderr)
    return provider


def write_index(analyses):
    rows = sorted(({k: a.get(k) for k in ("ticker", "quarter", "call_date", "file", "generated_at")}
                   for a in analyses.values()), key=lambda r: (r["ticker"] or "", r["call_date"] or ""))
    with open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(), "analyses": rows},
                  f, ensure_ascii=False, indent=1)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=DEFAULT_MAX_PER_RUN, help="이번 실행에서 분석할 최대 편수")
    ap.add_argument("--only", default="", help="이 파일(확장자 제외, 예: UNH_2026Q3)만 처리")
    ap.add_argument("--force", action="store_true", help="이미 분석된 것도 다시 분석")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    index = load_json(os.path.join(TRANSCRIPT_DIR, "index.json"), {}).get("transcripts", [])
    earnings_ir = load_json(EARNINGS_IR_PATH, {})

    # 이미 있는 분석 — 파일명 기준
    analyses = {}
    for name in os.listdir(OUT_DIR):
        if name.endswith(".json") and name != "index.json":
            a = load_json(os.path.join(OUT_DIR, name), None)
            if a:
                analyses[name] = a

    todo = []
    for t in sorted(index, key=lambda t: (t.get("call_date") or "", t.get("ticker") or "")):
        fname = t["file"]
        if args.only and os.path.splitext(fname)[0] != args.only:
            continue
        src = os.path.join(TRANSCRIPT_DIR, fname)
        if not os.path.exists(src):
            continue
        h = file_hash(src)
        done = analyses.get(fname)
        if done and done.get("source_hash") == h and not args.force:
            continue
        todo.append((t, src, h))

    if not todo:
        print("새로 분석할 컨콜 원문이 없습니다.")
        write_index(analyses)
        return

    provider = build_provider()
    if provider is None:
        print(f"[WARN] Gemini 키({' / '.join(KEY_ENVS)})가 없어 분석을 건너뜁니다. 대기 {len(todo)}편.", file=sys.stderr)
        write_index(analyses)
        return

    pending_tickers = set()  # 앞 분기가 이번 실행에서 실패/미뤄진 회사 — 뒤 분기는 비교가 틀어지니 미룬다
    done_count = 0
    for t, src, h in todo:
        fname, ticker = t["file"], t["ticker"]
        if done_count >= args.max:
            print(f"[INFO] 이번 실행 최대 {args.max}편 도달 — 나머지는 다음 실행에서.", file=sys.stderr)
            break
        if ticker in pending_tickers:
            print(f"[INFO] {fname}: 같은 회사 앞 분기가 아직이라 다음 실행으로 미룸", file=sys.stderr)
            continue
        tr = load_json(src, None)
        if not tr:
            continue
        prepared, qa = transcript_text(tr)
        prior, prior_label, prior_analysis = prior_context(ticker, tr.get("call_date") or t.get("call_date") or "",
                                                           analyses, earnings_ir, tr.get("quarter") or t.get("quarter") or "")
        user = (f"[회사] {tr.get('company')} ({ticker})\n[분기] {tr.get('quarter')}\n[컨콜 날짜] {tr.get('call_date')}\n\n"
                f"[직전 분기 정리]\n{prior}\n\n[실적발표]\n{prepared}\n\n[Q&A]\n{qa or '(Q&A 없음)'}")
        print(f"[INFO] {fname} 분석 중 (원문 약 {len(user):,}자, 직전 분기: {prior_label or '없음'})", file=sys.stderr)

        parsed = None
        for attempt in range(2):  # JSON이 깨진 경우만 1번 더(호출 1번 = 하루 쿼터 1회)
            try:
                raw = provider.call(SYSTEM_PROMPT, user, MAX_OUTPUT_TOKENS)
                parsed = parse_response(raw)
                break
            except ServerBusy:
                print("[WARN] Gemini 과부하 지속 — 이번 실행은 여기까지, 나머지는 다음 실행에서.", file=sys.stderr)
                write_index(analyses)
                return
            except Exception as e:
                if is_daily_quota_exhausted(e):
                    print("[WARN] Gemini 하루 쿼터 소진 — 나머지는 다음 실행(09:00 KST 이후)에서.", file=sys.stderr)
                    write_index(analyses)
                    return
                print(f"[WARN] {fname} 분석 실패({attempt + 1}/2): {str(e)[:300]}", file=sys.stderr)
            finally:
                time.sleep(PACE_SECONDS)
        if parsed is None:
            pending_tickers.add(ticker)
            continue
        carried = carry_over_prior(parsed, prior_analysis)
        if carried:
            print(f"[INFO] {fname}: 응답에서 빠진 직전 분기 항목 {carried}개를 '언급 없음'으로 채움", file=sys.stderr)

        out = {
            "ticker": ticker,
            "company": tr.get("company"),
            "quarter": tr.get("quarter"),
            "call_date": tr.get("call_date"),
            "file": fname,
            "source_hash": h,
            "prior_basis": prior_label,
            "model": GEMINI_MODEL,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            **{k: parsed[k] for k in ("headline", "changes", "prepared", "guidance", "milestones", "qa")},
        }
        with open(os.path.join(OUT_DIR, fname), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
            f.write("\n")
        analyses[fname] = out
        done_count += 1
        print(f"[OK] {fname}: 핵심 {len(out['headline'])} · 가이던스 {len(out['guidance'])} · "
              f"마일스톤 {len(out['milestones'])} · Q&A 주제 {len(out['qa'])}", file=sys.stderr)

    write_index(analyses)
    left = len(todo) - done_count
    print(f"완료: 이번 실행 {done_count}편 분석" + (f", 남은 {left}편은 다음 실행에서" if left > 0 else ""))


if __name__ == "__main__":
    main()
