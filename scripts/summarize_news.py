#!/usr/bin/env python3
"""
scrape_news.py / scrape_news_global.py가 만든 news.json의 각 기사에 대해
LLM으로 뉴스클리핑 가이드라인 형식의 요약을 생성해 붙인다.

- 국내(domestic): 맥락 태그([실적],[수주],[리포트],[IR행사],[학회발표],[공시],[인허가] 등)는
  뉴스클리핑_가이드라인.md 그대로 유지하되("ctx" 필드), 요약 자체는 2026-08-20 사용자 요청으로
  1줄 → 2줄로 확장했다(해외와 동일하게). "summary" 필드에 줄바꿈("\n")으로 구분된 2줄 저장.
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
import re
import sys
import time

MAX_ITEMS_PER_CALL = 20  # 한 번의 API 호출에 담는 기사 수 상한(과금/타임아웃 방지, 응답 잘림 위험 감소)

GEMINI_MODEL = "gemini-3.6-flash"  # gemini-2.5-flash가 신규 API 키에는 404(단종)로 막혀 교체.
# 2026-08-20 기준 ai.google.dev/gemini-api/docs/models 공식 문서에서 무료 티어(Free of charge)로
# 확인된 안정(stable) 모델. 추후 또 막히면 https://ai.google.dev/gemini-api/docs/pricing 에서
# "Free Tier"가 표시되는 최신 stable 모델로 교체하면 된다.
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

# 2026-09-02: 실제 워크플로 로그에서 확인된 Gemini 무료 티어 제한 —
# "Quota exceeded ... limit: 5, model: gemini-3.6-flash" (분당 5회, 프로젝트·모델 단위).
# 요약/트렌드 스크립트 둘 다 호출 사이 간격 없이 연속으로 쏴서 두 번째 호출부터 바로
# 429가 나던 게 실제 원인이었다 — 그래서 Gemini 호출마다 이만큼 쉬어서 페이싱한다.
# Anthropic(유료)은 이 제한이 훨씬 커서 불필요하게 느려지지 않도록 건너뛴다.
GEMINI_MIN_INTERVAL_SECONDS = 13  # 60/5=12초보다 여유있게


def gemini_pace(provider):
    """Gemini 호출 하나 끝날 때마다 불러서 다음 호출까지 최소 간격을 보장한다."""
    if provider is not None and getattr(provider, "name", None) == "gemini":
        time.sleep(GEMINI_MIN_INTERVAL_SECONDS)


def gemini_backoff_seconds(error, default=30):
    """429 응답 메시지에 담긴 'retry in 29.99...s' 안내를 최대한 파싱해 그만큼(+여유 2초) 쉰다.
    파싱 실패하면 기본값만큼 쉰다."""
    m = re.search(r"retry in ([\d.]+)s", str(error))
    if m:
        try:
            return float(m.group(1)) + 2
        except ValueError:
            pass
    return default


class DailyQuotaExhausted(Exception):
    """Gemini 무료 티어의 '하루' 단위 쿼터(예: limit 20, quotaId에 PerDay가 붙음)가 소진됐음을
    나타낸다. 분당 제한과 달리 몇 초~몇 십 초 기다린다고 풀리지 않고 하루(태평양시 기준으로
    보임) 지나야 풀리므로, 이게 확인되면 재시도/페이싱 없이 바로 포기하고 남은 배치·호출도
    전부 건너뛴다 — 2026-09-03, 실제로 이걸 구분 안 해서 이미 소진된 상태로 워크플로가
    9분 넘게 의미 없는 재시도·대기를 반복한 사례가 있었다."""
    pass


def is_daily_quota_exhausted(error):
    """429 에러 메시지에 'PerDay'가 있으면 일별 쿼터 초과(분당 제한과 다름)로 판단한다."""
    return "PerDay" in str(error)


DOMESTIC_CTX_EXAMPLES = (
    "정기주주총회, IR행사, 학회발표, 리포트, 공시, 인터뷰, 실적, 수주, 계약, "
    "파트너십, 인허가, 임상, 신제품, 해외진출, M&A, 투자유치, 주가"
)

DOMESTIC_SYSTEM = f"""당신은 국내 의료기기/디지털헬스 증권 애널리스트를 위한 뉴스 요약 보조원입니다.
아래 규칙을 반드시 지켜 JSON 배열만 출력하세요(다른 설명, 마크다운 코드블록 없이 순수 JSON만).

각 기사에 대해:
1. "ctx": 기사 내용에 맞는 짧은 맥락 태그(2~6글자, 대괄호 없이). 예시 어휘: {DOMESTIC_CTX_EXAMPLES}.
   위 예시에 맞는 게 없으면 내용에 맞는 다른 짧은 명사형 태그를 새로 만들어도 됩니다.
2. "summary": 기사 제목과 설명만 근거로 한국어 2줄 요약. 두 줄은 "\\n"(개행문자)로 구분하고,
   각 줄은 "~했음." 또는 "~함."으로 끝나는 완결된 서술체 문장(줄당 20~50자 내외)이어야 합니다.
   첫 줄은 핵심 사실, 둘째 줄은 배경/맥락이나 세부 내용으로 나누세요. 기사에 없는 내용을
   추측하거나 지어내지 마세요. 둘째 줄로 나눌 만한 추가 내용이 없으면 같은 사실을 다른
   각도로 보충하지 말고 "추가 세부 내용 없음"이라고 있는 그대로 쓰세요(추측 금지).
   단순 주가 등락만 언급하고 이유가 없는 기사면 summary에 "이유 설명 없는 단순 주가 등락"이라고
   있는 그대로 쓰세요(추측 금지).

출력 형식: [{{"i": 0, "ctx": "...", "summary": "첫째 줄.\\n둘째 줄."}}, {{"i": 1, ...}}, ...]
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
    """모델이 배열 뒤에 군더더기를 붙이거나(마크다운 코드펜스 등), 토큰 한도에 걸려
    배열이 중간에 잘린 경우까지 최대한 방어적으로 파싱한다.
    - 온전한 JSON이면 그대로 파싱.
    - ```json ... ``` 코드펜스로 감싸져 있으면 벗겨내고 재시도.
    - 배열이 끝까지 안 닫힌 채로 잘렸어도(예: 20개 중 14개까지만 응답) 완성된 항목까지는
      살려서 부분 리스트로 반환한다 — 예전에는 배치 전체를 통째로 버려서, 한 배치(최대
      20건) 안의 일부 기사만 요약이 빠지는 원인이 됐었다."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    if start == -1:
        return None

    end = text.rfind("]")
    if end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    # 여기까지 왔으면 배열이 온전히 안 닫혀 있다는 뜻 — 앞에서부터 완성된 객체만 순서대로
    # 최대한 살린다(응답이 잘리기 시작한 지점 이후는 자연히 누락되지만, 그 앞까지는 정상 반영).
    decoder = json.JSONDecoder()
    idx = start + 1
    n = len(text)
    items = []
    while idx < n:
        while idx < n and text[idx] in " \t\n\r,":
            idx += 1
        if idx >= n or text[idx] == "]":
            break
        try:
            obj, end_idx = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        items.append(obj)
        idx = end_idx
    return items if items else None


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

    def _generate(self, system, user_content, max_tokens, thinking_budget):
        from google.genai import types
        config_kwargs = dict(
            system_instruction=system,
            # gemini-3.x는 구글 공식 마이그레이션 가이드가 temperature를 1.0(기본값)에서
            # 낮추지 말라고 권고한다("looping or degraded performance" 우려) — 예전
            # gemini-2.5용으로 넣어뒀던 temperature=0을 그대로 뒀더니 일부 배치에서
            # JSON이 이상하게 잘리는 문제가 있었는데, 이게 원인 중 하나로 보여 제거했다.
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        )
        if thinking_budget is not None:
            # gemini-3.x 계열은 기본으로 "thinking"(내부 추론)을 켜고 도는데, 이 추론
            # 토큰도 max_output_tokens 예산을 같이 잡아먹는다. 이 작업은 정해진 스키마로
            # 요약만 뽑는 단순 작업이라 추론이 전혀 필요 없어서, thinking_budget=0으로
            # 완전히 꺼서 예산을 전부 실제 응답(JSON)에 쓰게 한다 — 일부 기사만 요약이
            # 빠지던 문제(응답이 중간에 잘리던 문제)의 주된 원인으로 추정.
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)
        resp = self.client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_content,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        return resp.text

    def call(self, system, user_content, max_tokens):
        try:
            return self._generate(system, user_content, max_tokens, thinking_budget=0)
        except Exception as e:
            # 2026-09-02: 실제 워크플로 로그에서 thinking_budget=0을 준 첫 호출부터 바로
            # "400 INVALID_ARGUMENT"가 나는 게 확인됐다 — gemini-3.6-flash가 이 값을 거부하는
            # 것으로 추정. thinking_config 자체를 빼고(모델 기본 사고 예산 사용) 한 번 더
            # 시도한다 — 429(쿼터)처럼 재시도해도 소용없는 에러는 그대로 올려서 상위 재시도
            # 로직(요약 배치/트렌드 생성 쪽의 2회 재시도)이 처리하게 둔다.
            if "400" in str(e) or "INVALID_ARGUMENT" in str(e):
                return self._generate(system, user_content, max_tokens, thinking_budget=None)
            raise


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
    """items를 MAX_ITEMS_PER_CALL 단위로 나눠 호출. API 호출 실패/응답 잘림으로 배치 일부가
    비어도 최대 1회 재시도하고, 그래도 안 되는 항목만 건너뛴다(다음 배치는 계속 진행)."""
    for start in range(0, len(items), MAX_ITEMS_PER_CALL):
        chunk = items[start:start + MAX_ITEMS_PER_CALL]
        pending = {idx: it for idx, it in enumerate(chunk)}  # 아직 요약을 못 받은 항목만 추적

        for attempt in range(2):  # 1차 시도 + 실패분 1회 재시도
            if not pending:
                break
            idx_list = sorted(pending.keys())
            payload = [build_payload_fn(idx, pending[idx]) for idx in idx_list]
            user_content = (
                "다음은 기사 목록입니다(JSON). 각 항목의 i, co, title, desc를 참고해 규칙에 맞는 "
                "JSON 배열로만 답하세요.\n\n" + json.dumps(payload, ensure_ascii=False)
            )
            try:
                raw = provider.call(system, user_content, max_tokens)
            except Exception as e:
                if is_daily_quota_exhausted(e):
                    print(f"[WARN] {provider.name} 일별 쿼터 소진 확인(항목 {start}~{start+len(chunk)-1}) — "
                          f"재시도해도 못 풀리므로 이 실행의 남은 요약을 전부 건너뜁니다: {e}", file=sys.stderr)
                    raise DailyQuotaExhausted(str(e)) from e
                tag = "재시도도 " if attempt else ""
                print(f"[WARN] {provider.name} 요약 API 호출 {tag}실패(항목 {start}~{start+len(chunk)-1}): {e}", file=sys.stderr)
                # Gemini 무료 티어는 분당 5회 제한이라 429가 나면 일반 페이싱보다 더 오래 쉬어야
                # 다음 시도가 또 429로 낭비되지 않는다(2026-09-02 실제 로그로 확인된 원인).
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    time.sleep(gemini_backoff_seconds(e))
                else:
                    gemini_pace(provider)
                continue
            gemini_pace(provider)
            results = parse_json_array(raw)
            if results is None or not isinstance(results, list):
                tag = "재시도도 " if attempt else ""
                print(f"[WARN] 요약 응답 JSON 파싱 {tag}실패(항목 {start}~{start+len(chunk)-1}), 원본 유지", file=sys.stderr)
                if debug:
                    print(f"[DEBUG] 응답 원문 앞 300자: {raw[:300]}", file=sys.stderr)
                continue
            by_i = {r.get("i"): r for r in results if isinstance(r, dict)}
            for idx in idx_list:
                r = by_i.get(idx)
                if not r:
                    continue
                apply_result_fn(pending[idx], r)
                del pending[idx]

        applied = len(chunk) - len(pending)
        if debug or pending:
            print(f"[DEBUG] 배치 {start}~{start+len(chunk)-1}: {applied}/{len(chunk)}건 요약 적용"
                  + (f" ({len(pending)}건은 재시도 후에도 실패, 원본 유지)" if pending else ""), file=sys.stderr)


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
            it["summary"] = summary[:300]  # 1줄→2줄로 늘리면서 상한도 global과 동일하게 300자로 상향

    # 2줄 요약으로 늘어난 만큼 항목당 토큰 배분도 global과 동일하게(120→150) 상향.
    max_tokens = min(8000, 400 + 150 * min(len(items), MAX_ITEMS_PER_CALL))
    summarize_batch(provider, items, DOMESTIC_SYSTEM, max_tokens, build_payload, apply_result, debug=debug)


def summarize_global(provider, items, debug=False):
    # search_news_global_claude.py(2026-08-21 추가)가 만든 기사는 검색 그라운딩 상태에서
    # 이미 2줄 한국어 요약을 직접 생성해 "summary" 필드를 채워 넣는다 — 여기서 title/desc
    # 만으로 다시 요약하면 오히려 근거가 약한 요약으로 덮어쓰게 되므로, 이미 summary가
    # 채워진 항목은 건너뛴다(비용 절감 + 품질 유지 둘 다 목적).
    todo = [it for it in items if not (it.get("summary") or "").strip()]
    skipped = len(items) - len(todo)
    if skipped and debug:
        print(f"[DEBUG] global: 이미 요약이 있는 {skipped}건은 재요약 건너뜀", file=sys.stderr)
    if not todo:
        return

    def build_payload(idx, it):
        return {"i": idx, "co": it.get("co", ""), "title": it.get("t", ""), "desc": it.get("desc", "")}

    def apply_result(it, r):
        summary = str(r.get("summary") or "").strip()
        if summary:
            it["summary"] = summary[:300]

    max_tokens = min(8000, 400 + 150 * min(len(todo), MAX_ITEMS_PER_CALL))
    summarize_batch(provider, todo, GLOBAL_SYSTEM, max_tokens, build_payload, apply_result, debug=debug)


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
        quota_exhausted = False
        try:
            summarize_domestic(provider, domestic_items, debug=args.debug)
        except DailyQuotaExhausted:
            quota_exhausted = True
        except Exception as e:
            print(f"[WARN] 국내 요약 생성 중 예외 발생, 원본 유지 ({e})", file=sys.stderr)

        if quota_exhausted:
            print("[WARN] 일별 쿼터가 이미 소진된 상태라 해외 요약도 건너뜁니다(어차피 똑같이 "
                  "실패하므로 시간 낭비 방지).", file=sys.stderr)
        else:
            try:
                summarize_global(provider, global_items, debug=args.debug)
            except DailyQuotaExhausted:
                pass
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
