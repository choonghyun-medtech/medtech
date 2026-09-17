#!/usr/bin/env python3
"""
"월간 분석" 계열 스크립트(analyze_export_monthly.py / analyze_medtour_monthly.py /
analyze_visitor_monthly.py) 3개가 공통으로 쓰는 유틸리티.

- LLM provider: summarize_news.py의 429/일별쿼터/일시과부하 판별·백오프 로직은 그대로
  재사용하되(중복 구현 방지), Provider 클래스 자체는 새로 둔다 — summarize_news의
  AnthropicProvider는 출력이 항상 JSON "배열"이라고 가정하고 assistant 메시지를 "["로
  프리필하는데, 월간 분석은 출력이 JSON "객체"({"summary": ...})라 그 프리필을 쓰면 안
  깨진다. 그래서 프리필 없는 버전을 여기 별도로 둔다.
- 월별 시계열 통계(전월비/전년동월비/역대 순위/연초누계 vs 전년 연간)를 계산하는
  month_stats()도 여기 둔다 — 세 스크립트가 전부 "그 카테고리/국가/부서가 유의미하게
  변했는지"를 같은 방식으로 판단해야 서로 다른 기준으로 들쭉날쭉해지지 않는다. 숫자
  계산은 전부 여기(파이썬)에서 하고 LLM에는 계산된 사실만 넘긴다 — LLM이 직접 YoY%
  등을 암산하게 하면 숫자가 틀리기 쉽다(환각 방지).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from summarize_news import (  # noqa: E402
    ANTHROPIC_MODEL,
    GEMINI_MODEL,
    DailyQuotaExhausted,
    gemini_backoff_seconds,
    gemini_pace,
    is_daily_quota_exhausted,
    is_transient_server_error,
    transient_backoff_seconds,
)


class GeminiObjectProvider:
    """Google AI Studio 무료 티어. summarize_news.GeminiProvider와 거의 동일하지만
    프리필 없이 JSON 객체 하나를 그대로 받는다."""

    name = "gemini"

    def __init__(self, api_key):
        from google import genai
        self.client = genai.Client(api_key=api_key)

    def _generate(self, system, user_content, max_tokens, thinking_budget):
        from google.genai import types
        config_kwargs = dict(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        )
        if thinking_budget is not None:
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
            if "400" in str(e) or "INVALID_ARGUMENT" in str(e):
                return self._generate(system, user_content, max_tokens, thinking_budget=None)
            raise


class AnthropicObjectProvider:
    """Claude(Haiku), 유료(저비용). 프리필 없이 그대로 JSON 객체 텍스트를 받는다."""

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
            messages=[{"role": "user", "content": user_content}],
        )
        return "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")


def build_object_provider():
    """GEMINI_API_KEY(무료) 우선, 없으면 ANTHROPIC_API_KEY(유료). 둘 다 없으면 None."""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            return GeminiObjectProvider(gemini_key)
        except ImportError:
            print("[WARN] google-genai 패키지가 없어 Gemini를 사용할 수 없습니다.", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Gemini 클라이언트 초기화 실패 ({e})", file=sys.stderr)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            return AnthropicObjectProvider(anthropic_key)
        except ImportError:
            print("[WARN] anthropic 패키지가 없어 Claude를 사용할 수 없습니다.", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Anthropic 클라이언트 초기화 실패 ({e})", file=sys.stderr)

    print("[WARN] GEMINI_API_KEY / ANTHROPIC_API_KEY 둘 다 없어 월간 분석 생성을 건너뜁니다.", file=sys.stderr)
    return None


def call_llm_with_retries(provider, system, user_content, max_tokens, tag, debug=False):
    """analyze_news_trend.generate_trends_batch와 동일한 재시도/백오프 정책(최대 5회,
    429는 안내된 재시도 대기, 503/UNAVAILABLE은 20→40→80→120s 지수 백오프)."""
    for attempt in range(5):
        try:
            raw = provider.call(system, user_content, max_tokens=max_tokens)
        except Exception as e:
            if is_daily_quota_exhausted(e):
                print(f"[WARN] 일별 쿼터 소진 확인({tag}): {e}", file=sys.stderr)
                raise DailyQuotaExhausted(str(e)) from e
            retry = f"재시도({attempt}회차)도 " if attempt else ""
            print(f"[WARN] LLM 호출 {retry}실패({tag}): {e}", file=sys.stderr)
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                time.sleep(gemini_backoff_seconds(e))
            elif is_transient_server_error(e):
                time.sleep(transient_backoff_seconds(attempt))
            else:
                gemini_pace(provider)
            continue
        gemini_pace(provider)
        if debug:
            print(f"[DEBUG] LLM 응답 원문({tag}): {raw[:300]}", file=sys.stderr)
        return raw
    return None


def month_add(ym, delta):
    """'YYYY-MM'에 delta개월을 더한다(음수 가능)."""
    y, m = int(ym[:4]), int(ym[5:7])
    total = y * 12 + (m - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def month_stats(series, value_key, ym_key="ym", min_history_months=13):
    """월별 시계열(list of {ym_key: 'YYYY-MM', value_key: 숫자})에서 최신월 기준 통계를 뽑는다.
    시계열이 비어 있으면 None. 전년동월 비교·역대 순위는 min_history_months(기본 13개월,
    전년동월 비교에 필요한 최소 길이) 미만이면 계산하지 않고 해당 필드를 None으로 둔다
    (예: byRegion처럼 최근 몇 달치만 있는 짧은 시계열에 억지로 YoY를 계산해 근거 없는
    수치를 만들지 않기 위함)."""
    items = sorted(
        (it for it in series if it.get(ym_key) and it.get(value_key) is not None),
        key=lambda x: x[ym_key],
    )
    if not items:
        return None
    by_ym = {it[ym_key]: it[value_key] for it in items}
    latest_ym = items[-1][ym_key]
    latest_value = items[-1][value_key]
    n = len(items)

    prev_value = by_ym.get(month_add(latest_ym, -1))
    mom_pct = ((latest_value - prev_value) / prev_value * 100) if prev_value else None

    yoy_pct = None
    yoy_value = None
    rank = None
    ytd_sum = None
    ytd_months_count = None
    ytd_yoy_pct = None  # 연초~최신월 누계의 전년동기 대비 증감률(예: "1~8월 누계 +52%")
    prior_year_total = None
    ytd_vs_prior_year_pct = None  # 누계가 전년 "연간 총실적" 대비 몇 %인지(예: "연간의 96%")

    if n >= min_history_months:
        yoy_value = by_ym.get(month_add(latest_ym, -12))
        if yoy_value:
            yoy_pct = (latest_value - yoy_value) / yoy_value * 100
        sorted_vals = sorted((it[value_key] for it in items), reverse=True)
        rank = sorted_vals.index(latest_value) + 1

        year = int(latest_ym[:4])
        latest_month_num = int(latest_ym[5:7])
        ytd_months = [it for it in items if it[ym_key].startswith(f"{year}-")]
        ytd_sum = sum(it[value_key] for it in ytd_months)
        ytd_months_count = len(ytd_months)

        # 전년 같은 기간(1월~latest_month_num월) 누계 — 있는 만큼만 더하고, 그 개월수가
        # 이번해 누계 개월수와 같을 때만 YoY를 계산한다(기간을 안 맞추고 비교하면 왜곡됨).
        prior_year_same_period = [
            it for it in items
            if it[ym_key].startswith(f"{year - 1}-") and int(it[ym_key][5:7]) <= latest_month_num
        ]
        if len(prior_year_same_period) == ytd_months_count:
            prior_ytd_sum = sum(it[value_key] for it in prior_year_same_period)
            if prior_ytd_sum:
                ytd_yoy_pct = (ytd_sum - prior_ytd_sum) / prior_ytd_sum * 100

        prior_year_months = [it for it in items if it[ym_key].startswith(f"{year - 1}-")]
        if len(prior_year_months) == 12:
            prior_year_total = sum(it[value_key] for it in prior_year_months)
            if prior_year_total:
                ytd_vs_prior_year_pct = ytd_sum / prior_year_total * 100

    return {
        "latest_ym": latest_ym,
        "latest_value": latest_value,
        "n_months_total": n,
        "mom_pct": mom_pct,
        "yoy_pct": yoy_pct,
        "yoy_value": yoy_value,
        "rank": rank,  # 1 = 역대 최고
        "ytd_sum": ytd_sum,
        "ytd_months_count": ytd_months_count,
        "ytd_yoy_pct": ytd_yoy_pct,
        "prior_year_total": prior_year_total,
        "ytd_vs_prior_year_pct": ytd_vs_prior_year_pct,
    }


def is_notable(stats, yoy_threshold=20.0, rank_threshold=3, ytd_milestone_pct=90.0):
    """month_stats() 결과가 "유의미한 변동/기록"으로 볼 만한지 판단 — 이 기준을 넘는
    항목만 LLM 프롬프트에 넣어(노이즈 제거) 억지로 매달 모든 카테고리를 언급하지 않게 한다."""
    if stats is None:
        return False
    if stats["yoy_pct"] is not None and abs(stats["yoy_pct"]) >= yoy_threshold:
        return True
    if stats["rank"] is not None and stats["rank"] <= rank_threshold:
        return True
    if stats["ytd_vs_prior_year_pct"] is not None and stats["ytd_vs_prior_year_pct"] >= ytd_milestone_pct:
        return True
    return False


def fmt_pct(pct):
    if pct is None:
        return "N/A"
    return f"{pct:+.1f}%"


def fmt_rank(rank, n_total, rank_threshold=3):
    """[2026-09-17] 예전엔 rank가 있기만 하면(예: 40개월 중 15위처럼 전혀 안 두드러지는
    순위여도) 무조건 "역대 N위" 문구를 만들어 LLM에 넘겼다 — LLM은 주어진 사실을 그대로
    옮기라고 지시받으므로(환각 방지), 이 안 두드러지는 순위까지 그대로 받아써서 "역대 n위"
    가 카테고리마다 기계적으로 반복되는 문제가 있었다(사용자 리포트). is_notable()이 항목을
    고를 때 쓰는 rank_threshold(기본 3위 이내)와 동일한 기준으로, 실제로 상위권일 때만
    문구를 만든다 — YoY%나 연초누계 기준으로 notable해진 항목은 순위가 평범해도(예: 15위)
    억지로 순위를 언급하지 않는다."""
    if rank is None or rank > rank_threshold:
        return ""
    if rank == 1:
        return f"역대 최고({n_total}개월 중 1위)"
    return f"역대 {rank}위({n_total}개월 중)"


def load_months_file(path):
    """medtour_monthly_analysis.json처럼 {"months": {"YYYY-MM": {...}}} 형태로 여러
    스크립트(analyze_medtour_monthly.py/analyze_visitor_monthly.py)가 같은 파일의 서로
    다른 키에 나눠 쓰는 파일을 읽는다. 없거나 형식이 깨졌으면 빈 구조로 시작한다."""
    import json
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"months": {}}
    if not isinstance(data, dict) or not isinstance(data.get("months"), dict):
        return {"months": {}}
    return data


def save_months_file(path, data):
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
