#!/usr/bin/env python3
"""
scrape_news.py / scrape_news_global.py가 만든 news.json의 각 기사에 대해
LLM으로 뉴스클리핑 가이드라인 형식의 요약을 생성해 붙인다.

- 국내(domestic): 뉴스클리핑_가이드라인.md의 "- [맥락] ~했음." 형식 —
  대괄호 맥락 태그(예: [실적],[수주],[리포트],[IR행사],[학회발표],[공시],[인허가] 등)
  + 한 문장 "~했음." 요약. 결과는 각 기사의 "ctx"/"summary" 필드로 저장된다.
- 해외(global): medtech_news_clipping_rules.md의 "2줄 내용 요약" 형식 —
  대괄호 태그 없이, 영문 기사 내용을 한국어 2줄로 요약. "summary" 필드(줄바꿈 "\n" 포함)로 저장.

- 요약 provider(둘 중 하나만 설정하면 됨, 둘 다 있으면 Gemini 우선):
  · GEMINI_API_KEY  — Google AI Studio 무료 티어(비용 $0). 신용카드 등록 불필요.
    발급: https://aistudio.google.com/apikey
    참고: 무료 티어로 보낸 내용은 구글이 모델 개선에 활용할 수 있음(공식 정책,
    유료 티어로 전환하면 이 항목이 "No"로 바뀜). 여기서 보내는 내용은 이미 공개된
    뉴스 기사 제목/요약 수준이라 민감도는 낮지만, 참고로 밝혀둔다.
  · ANTHROPIC_API_KEY — Claude(Haiku), 유료(사용량 과금, 저비용).
- 이 단계는 "보강" 단계다 — 핵심 기사 데이터(news.json)는 이미 scrape_news*.py가
  저장을 마친 뒤에 실행되므로, 이 스크립트가 키 누락/API 실패/파싱 실패로 건너뛰어도
  기사 목록 자체(제목/링크/날짜)는 그대로 남는다. 그래서 실패해도 sys.exit(1)로
  워크플로를 실패시키지 않는다(경고 로그만 남기고 0으로 종료) — 요약은 없어도 되지만
  기사 목록이 사라지면 안 되기 때문.

사용법:
    GEMINI_API_KEY=xxx python summarize_news.py --out news.json
    (또는 ANTHROPIC_API_KEY=xxx python summarize_news.py --out news.json)
"""
import argparse
import json
import os
import sys

MAX_ITEMS_PER_CALL = 40  # 한 번의 API 호출에 담는 기사 수 상한(과금/타임아웃 방지)

GEMINI_MODEL = "gemini-3.6-flash"  # gemini-2.5-flash가 신규 API 키에는 404(단종)로 막혀 교체.
# 2026-08-20 기준 ai.google.dev/gemini-api/docs/models 공식 문서에서 무료 티어(Free of charge)로
# 확인된 안정(stable) 모델. 추후 또 막히면 https://ai.google.dev/gemini-api/docs/pricing 에서
# "Free Tier"가 표시되는 최신 stable 모델로 교체하면 된다.
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

DOMESTIC_CTX_EXAMPLES = (
    "정기주주총회, IR행사, 학회발표, 리포트, 공시, 인터뷰, 실적, 수주, 계약, "
    "파트너십, 인허가, 임상, 신제품, 해외진출, M&A, 투자유치, 주가"
)

DOMESTIC_SYSTEM = f"""당신은 국내 의료기기/디지털헬스 증권 애널리스트를 위한 뉴스 요약 보조원입니다.
아래 규칙을 반드시 지켜 JSON 배열만 출력하세요(다른 설명, 마크다운 코드블록 없이 순수 JSON만).

각 기사에 대해:
1. "ctx": 기사 내용에 맞는 짧은 맥락 태그(2~6글자, 대괄호 없이). 예시 어휘: {DOMESTIC_CTX_EXAMPLES}.
   위 예시에 맞는 게 없으면 내용에 맞는 다른 짧은 명사형 태그를 새로 만들어도 됩니다.
2. "summary": 기사 제목과 설명만 근거로, 한 문장으로 핵심을 요약하고 "~했음." 또는 "~함."으로
   끝나는 서술체 문장. 기사에 없는 내용을 추측하거나 지어내지 마세요. 20~60자 내외로 간결하게.
   단순 주가 등락만 언급하고 이유가 없는 기사면 summary에 "이유 설명 없는 단순 주가 등락"이라고
   있는 그대로 쓰세요(추측 금지).

출력 형식: [{{"i": 0, "ctx": "...", "summary": "...했음."}}, {{"i": 1, ...}}, ...]
입력된 기사 개수와 순서(i)를 정확히 맞춰서 모두 답하세요."""

GLOBAL_SYSTEM = """당신은 한국 증권사 애널리스트를 위한 해외 의료기기/헬스케어 뉴스 요약 보조원입니다.
아래 규칙을 반드시 지켜 JSON 배열만 출력하세요(다른 설명, 마크다운 코드블록 없이 순수 JSON만).

각 기사에 대해 "summary"를 작성하세요: 영문 제목/설명만 근거로 한국어 2줄 요약.
두 줄은 "\\n"(개행문자)로 구분하고, 각 줄은 완결된 한 문장(25~50자 내외)이어야 합니다.
기사에 없는 내용을 추측하거나 지어내지 마세요. 단순 주가/자금 흐름만 언급하는 기사는
summary에 "단순 주가/자금흐름 기사"라고 있는 그대로 쓰세요(추측 금지).

출력 형식: [{"i": 0, "summary": "첫째 줄.\\n둘째 줄."}, {"i": 1, ...}, ...]
입력된 기사 개수와 순서(i)를 정확히 맞춰서 모두 답하세요."""


def parse_json_array(text):
    """모델이 배열 뒤에 군더더기를 붙이는 경우까지 방어적으로 파싱."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# provider 어댑터: call(system, user_content, max_tokens) -> 원문 텍스트(JSON 배열이어야 함)
# ---------------------------------------------------------------------------

class GeminiProvider:
    """Google AI Studio 무료 티어. response_mime_type=application/json으로 구조화 출력을 강제한다."""

    name = "gemini"

    def __init__(self, api_key):
        from google import genai
        self._genai = genai
        self.client = genai.Client(api_key=api_key)

    def call(self, system, user_content, max_tokens):
        from google.genai import types
        resp = self.client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
            ),
        )
        return resp.text


class AnthropicProvider:
    """Claude(Haiku), 유료(저비용). assistant 메시지를 "["로 프리필해서 JSON 시작을 강제한다."""

    name = "anthropic"

    def __init__(self, api_key):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)

    def call(self, system, user_content, max_tokens):
        resp = self.client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            temperature=0,
            system=system,
            messages=[
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": "["},
            ],
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        return "[" + text


def build_provider():
    """GEMINI_API_KEY(무료)를 우선 사용하고, 없으면 ANTHROPIC_API_KEY(유료)로 대체.
    둘 다 없거나 해당 패키지가 설치되어 있지 않으면 None을 반환(요약 단계 스킵)."""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            return GeminiProvider(gemini_key)
        except ImportError:
            print("[WARN] google-genai 패키지가 없어 Gemini를 사용할 수 없습니다.", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Gemini 클라이언트 초기화 실패 ({e})", file=sys.stderr)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            return AnthropicProvider(anthropic_key)
        except ImportError:
            print("[WARN] anthropic 패키지가 없어 Claude를 사용할 수 없습니다.", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Anthropic 클라이언트 초기화 실패 ({e})", file=sys.stderr)

    print("[WARN] GEMINI_API_KEY / ANTHROPIC_API_KEY 둘 다 없어 요약 생성을 건너뜁니다 "
          "(기사 목록/제목/링크는 그대로 유지됩니다).", file=sys.stderr)
    return None


def summarize_batch(provider, items, system, max_tokens, build_payload_fn, apply_result_fn, debug=False):
    """items를 MAX_ITEMS_PER_CALL 단위로 나눠 호출. 실패한 배치는 건너뛰고 다음 배치는 계속 시도."""
    for start in range(0, len(items), MAX_ITEMS_PER_CALL):
        chunk = items[start:start + MAX_ITEMS_PER_CALL]
        payload = [build_payload_fn(idx, it) for idx, it in enumerate(chunk)]
        user_content = (
            "다음은 기사 목록입니다(JSON). 각 항목의 i, co, title, desc를 참고해 규칙에 맞는 "
            "JSON 배열로만 답하세요.\n\n" + json.dumps(payload, ensure_ascii=False)
        )
        try:
            raw = provider.call(system, user_content, max_tokens)
        except Exception as e:
            print(f"[WARN] {provider.name} 요약 API 호출 실패(항목 {start}~{start+len(chunk)-1}): {e}", file=sys.stderr)
            continue
        results = parse_json_array(raw)
        if results is None or not isinstance(results, list):
            print(f"[WARN] 요약 응답 JSON 파싱 실패(항목 {start}~{start+len(chunk)-1}), 원본 유지", file=sys.stderr)
            if debug:
                print(f"[DEBUG] 응답 원문 앞 300자: {raw[:300]}", file=sys.stderr)
            continue
        by_i = {r.get("i"): r for r in results if isinstance(r, dict)}
        applied = 0
        for idx, it in enumerate(chunk):
            r = by_i.get(idx)
            if not r:
                continue
            apply_result_fn(it, r)
            applied += 1
        if debug:
            print(f"[DEBUG] 배치 {start}~{start+len(chunk)-1}: {applied}/{len(chunk)}건 요약 적용", file=sys.stderr)


def summarize_domestic(provider, items, debug=False):
    if not items:
        return

    def build_payload(idx, it):
        return {"i": idx, "co": it.get("co", ""), "title": it.get("t", ""), "desc": it.get("desc", "")}

    def apply_result(it, r):
        ctx = str(r.get("ctx") or "").strip()
        summary = str(r.get("summary") or "").strip()
        if ctx:
            it["ctx"] = ctx[:12]
        if summary:
            it["summary"] = summary[:200]

    max_tokens = min(8000, 400 + 120 * min(len(items), MAX_ITEMS_PER_CALL))
    summarize_batch(provider, items, DOMESTIC_SYSTEM, max_tokens, build_payload, apply_result, debug=debug)


def summarize_global(provider, items, debug=False):
    if not items:
        return

    def build_payload(idx, it):
        return {"i": idx, "co": it.get("co", ""), "title": it.get("t", ""), "desc": it.get("desc", "")}

    def apply_result(it, r):
        summary = str(r.get("summary") or "").strip()
        if summary:
            it["summary"] = summary[:300]

    max_tokens = min(8000, 400 + 150 * min(len(items), MAX_ITEMS_PER_CALL))
    summarize_batch(provider, items, GLOBAL_SYSTEM, max_tokens, build_payload, apply_result, debug=debug)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="news.json")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.out, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[WARN] {args.out} 읽기 실패, 요약 생성을 건너뜁니다 ({e})", file=sys.stderr)
        sys.exit(0)

    domestic_items = [it for g in data.get("domestic", []) for it in g.get("items", [])]
    global_items = [it for g in data.get("global", []) for it in g.get("items", [])]

    provider = build_provider()
    if provider is not None:
        try:
            summarize_domestic(provider, domestic_items, debug=args.debug)
        except Exception as e:
            print(f"[WARN] 국내 요약 생성 중 예외 발생, 원본 유지 ({e})", file=sys.stderr)

        try:
            summarize_global(provider, global_items, debug=args.debug)
        except Exception as e:
            print(f"[WARN] 해외 요약 생성 중 예외 발생, 원본 유지 ({e})", file=sys.stderr)

    # desc는 요약 생성용 내부 필드였으므로(요약이 생성됐든 안 됐든) 화면 노출용
    # 최종 파일에는 남기지 않는다.
    for it in domestic_items + global_items:
        it.pop("desc", None)

    n_summarized = sum(1 for it in domestic_items + global_items if it.get("summary"))
    if n_summarized > 0 and data.get("source") and "요약" not in data["source"]:
        provider_label = f"Claude(Haiku)" if provider is not None and provider.name == "anthropic" else "Gemini"
        data["source"] = data["source"] + f" · 요약: {provider_label} 자동 생성"

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(f"요약 단계 완료: 국내 {len(domestic_items)}건 / 해외 {len(global_items)}건 중 "
          f"{n_summarized}건에 요약 적용")


if __name__ == "__main__":
    main()
