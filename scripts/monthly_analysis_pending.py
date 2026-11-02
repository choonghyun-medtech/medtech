"""수집 워크플로(update-export-data / update-medical-tour / update-visitor-stats)가 수치를
받은 직후 "AI 월간 분석을 이어서 돌려야 하는지" 판단한다(2026-11-02 사용자 요청 — 예전엔
수집 창이 끝난 다음날 고정 날짜에 분석했는데, 실제 수치가 들어온 날 바로 이어서 돌리도록).

판단 기준: 데이터 파일의 최신 달에 대한 분석이 분석 파일에 아직 없으면 → 실행.
- 수치가 새로 들어온 날: 최신 달이 바뀌었고 그 달 분석이 없으니 실행된다.
- 수치가 아직 안 들어온 날: 최신 달은 지난달 그대로이고 이미 분석돼 있으니 건너뛴다.
- 수치가 들어온 날 분석이 쿼터 소진 등으로 실패했다면, 창 안의 다음 날 다시 시도된다.

각 analyze_*_monthly.py의 대상월 계산과 같은 규칙으로 최신 달을 구한다. 그 스크립트들은
AI 패키지(google-genai 등)를 import하므로, 수집 워크플로(requests만 설치)에서도 돌 수 있게
여기서는 import하지 않고 같은 규칙을 직접 적는다.

사용: python scripts/monthly_analysis_pending.py export|medtour|visitor
출력: GITHUB_OUTPUT 형식 두 줄 — "run=true|false", "month=YYYY-MM"
"""
import json
import sys

KINDS = {
    # 종류: (데이터 파일, 분석 파일, 분석 파일 안 섹션 이름)
    "export": ("export_data.json", "export_monthly_analysis.json", "export"),
    "medtour": ("medical_tour.json", "medtour_monthly_analysis.json", "medtour"),
    "visitor": ("visitor_stats.json", "medtour_monthly_analysis.json", "visitor"),
}


def latest_ym(series, value_key, positive_only=False):
    yms = [it["ym"] for it in series
           if it.get("ym") and it.get(value_key) is not None and (not positive_only or it[value_key] > 0)]
    return max(yms) if yms else None


def latest_data_month(kind, data):
    if kind == "export":
        # analyze_export_monthly.analyze_once: 카테고리별 최신 달 중 가장 늦은 달
        yms = [latest_ym(c.get("monthly", []), "expDlr") for c in data.get("categories", [])]
        yms = [y for y in yms if y]
        return max(yms) if yms else None
    if kind == "medtour":
        # analyze_medtour_monthly.analyze_once: 'all' 탭 소비액의 최신 달
        all_tab = next((t for t in data.get("tabs", []) if t.get("key") == "all"), None)
        return latest_ym(all_tab.get("monthly", []), "amt") if all_tab else None
    # analyze_visitor_monthly.analyze_once: 미발표 달이 0으로 들어오는 경우가 있어 0 이하는 제외
    return latest_ym(data.get("monthly", []), "visitors", positive_only=True)


def main():
    kind = sys.argv[1]
    data_path, out_path, section = KINDS[kind]
    try:
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("run=false\nmonth=")
        return
    month = latest_data_month(kind, data)
    try:
        with open(out_path, encoding="utf-8") as f:
            out = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        out = {}
    done = section in ((out.get("months") or {}).get(month) or {})
    if kind == "export" and not done:
        # 2026-10-13 이전 형식(최신 한 달만 최상위 target_month에 저장)도 분석된 것으로 본다
        done = out.get("target_month") == month and bool(out.get("summary"))
    print(f"run={'true' if month and not done else 'false'}\nmonth={month or ''}")


if __name__ == "__main__":
    main()
