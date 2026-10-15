#!/usr/bin/env python3
"""
이벤트 캘린더 "국내 커버 기업 실적발표 일정" 변환 스크립트 (2026-10-15 신설).

- 배경: 해외 커버 기업(scripts/calendar_ir_sources.json 18개사)의 실적발표일은 IR 페이지에서
  자동 수집하지만, 국내 기업은 IR 담당자에게 이메일로 문의해 받는다. 회신도 "10월 23일"처럼
  하루로 오는 경우보다 "11월 초", "10월 말~11월 초", "13일 또는 16일"처럼 두루뭉술하게 오는
  경우가 많다. 그래서 글로벌 대시보드와 같은 방식으로, 사람이 엑셀에 회신 문구를 그대로 적고
  이 스크립트가 날짜 범위로 바꿔 calendar_domestic_manual.json을 만든다
  (update_domestic_earnings.bat 더블클릭 → 변환 → 변경 요약 확인 → Y면 커밋·푸시).
- 엑셀(1행 머리글, 2행부터 데이터): 기업명 | 종목코드 | 분기 | 회신 일정(받은 그대로) | 메모
  종목코드는 비워두면 scripts/tickers.json에서 기업명으로 찾아 채운다.
- 회신 문구 해석 규칙(캘린더 화면의 표기도 이 규칙을 따른다):
    "10/23"                → 하루 확정 → 그 날짜 칸
    "11월 초/중순/말"       → 1~10일 / 11~20일 / 21일~말일
    "11월 둘째 주", "2주차" → 그 주 월~금(첫째 주 = 그 달 첫 목요일이 들어있는 주, 월요일 시작)
    "11월"                 → 그 달 전체
    "10월 말~11월 초"       → 앞쪽의 시작일 ~ 뒤쪽의 종료일
    "11/13 또는 11/16"      → 후보일 목록("또는", "or", 쉼표로 구분)
  해석하지 못한 줄은 건너뛰고 오류로 알려준다(엑셀 문구를 위 형태로 고쳐 다시 실행).
- 날짜 표기는 "10/21" 형식으로 통일한다(2026-10-15 요청). "10월 21일"로 적어도 알아듣지만,
  화면에 보이는 문구(text)는 변환할 때 "10/21"로 다시 써서 저장한다. 그래서 "/"는 날짜 구분자이고
  후보일 구분자로는 쓰지 않는다.
- 연도: 분기(예: 3Q26)가 있으면 그 다음 분기에 발표한다고 보고(3Q26 → 2026년, 4Q26 → 2027년),
  없으면 오늘 기준으로 가장 가까운 앞으로의 연도로 본다.

사용법:
    python scripts/convert_domestic_earnings.py --out calendar_domestic_manual.json
    python scripts/convert_domestic_earnings.py --source "<엑셀 경로>" --out calendar_domestic_manual.json
"""
import argparse
import calendar
import datetime
import json
import os
import re
import sys

import openpyxl

DEFAULT_SOURCE = "D:/★사용자 폴더/Desktop/dashboard files/국내 실적발표 일정!!.xlsx"
TICKERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tickers.json")

ORDINAL = {"첫": 1, "첫째": 1, "둘": 2, "둘째": 2, "셋": 3, "셋째": 3, "넷": 4, "넷째": 4, "다섯": 5, "다섯째": 5}


class ParseError(ValueError):
    pass


def announce_year(quarter, month, today):
    """분기 문자열(3Q26, 2026 3Q, 26년 3분기 등)로 발표 연도를 정한다."""
    m = re.search(r"([1-4])\s*Q\s*'?(\d{2,4})|(\d{2,4})\s*년?\s*([1-4])\s*(?:Q|분기)", quarter or "", re.I)
    if m:
        q = int(m.group(1) or m.group(4))
        y = int(m.group(2) or m.group(3))
        y = y + 2000 if y < 100 else y
        return y + 1 if q == 4 and month <= 6 else y
    # 분기 정보가 없으면 오늘 기준: 석 달 넘게 지난 달이면 내년으로 본다
    return today.year + 1 if month < today.month - 3 else today.year


def nth_week(year, month, n):
    """n번째 주의 월~금을 그 달 안으로 잘라 돌려준다. 주는 월요일 시작이고, 그 달의 첫 목요일이
    들어있는 주가 첫째 주다(달력 관행 — 1일이 일요일인 2026년 11월도 첫째 주가 11/2~6이 된다)."""
    first = datetime.date(year, month, 1)
    first_thu = first + datetime.timedelta(days=(3 - first.weekday()) % 7)
    monday = first_thu - datetime.timedelta(days=3) + datetime.timedelta(weeks=n - 1)
    if (monday + datetime.timedelta(days=3)).month != month:
        raise ParseError(f"{month}월에는 {n}째 주가 없음")
    last = datetime.date(year, month, calendar.monthrange(year, month)[1])
    return max(monday, first), min(monday + datetime.timedelta(days=4), last)


def parse_piece(piece, ctx_month, quarter, today):
    """'11월 초', '10/23', '23일', '둘째 주' 같은 조각 하나를 (시작일, 종료일, 월, 표기)로 바꾼다.
    표기는 화면에 보일 통일된 문구 — 날짜는 "10/23", 나머지는 "11월 초"·"11월 둘째 주"·"11월"."""
    s = piece.strip()
    md = re.match(r"^(\d{1,2})\s*/\s*(\d{1,2})$", s)  # "10/23"
    if md:
        month, rest = int(md.group(1)), md.group(2)
    else:
        m = re.match(r"^(?:(\d{1,2})\s*월)?\s*(.*)$", s)
        month = int(m.group(1)) if m.group(1) else ctx_month
        rest = m.group(2).strip()
    if not month or not 1 <= month <= 12:
        raise ParseError(f"월을 알 수 없음: '{piece}'")
    year = announce_year(quarter, month, today)
    last_day = calendar.monthrange(year, month)[1]
    d = lambda day: datetime.date(year, month, day)

    if rest == "":
        return d(1), d(last_day), month, f"{month}월"
    dm = re.match(r"^(\d{1,2})\s*일?$", rest)
    if dm:
        day = int(dm.group(1))
        if not 1 <= day <= last_day:
            raise ParseError(f"{month}/{day}는 없는 날짜")
        return d(day), d(day), month, f"{month}/{day}"
    if re.match(r"^(초|초순)$", rest):
        return d(1), d(10), month, f"{month}월 초"
    if re.match(r"^(중|중순)$", rest):
        return d(11), d(20), month, f"{month}월 중순"
    if re.match(r"^(말|하순|말경)$", rest):
        return d(21), d(last_day), month, f"{month}월 말"
    wm = re.match(r"^(\d|첫째|첫|둘째|둘|셋째|셋|넷째|넷|다섯째|다섯)\s*(?:째)?\s*(?:주|주차)$", rest)
    if wm:
        tok = wm.group(1)
        n = int(tok) if tok.isdigit() else ORDINAL[tok]
        start, end = nth_week(year, month, n)
        word = ["", "첫째", "둘째", "셋째", "넷째", "다섯째"][n]
        return start, end, month, f"{month}월 {word} 주"
    raise ParseError(f"해석할 수 없는 표현: '{piece}'")


def parse_schedule(text, quarter, today=None):
    """회신 문구 → {"text"(통일된 표기), "from", "to"} (+후보일이면 "dates":[...])."""
    today = today or datetime.date.today()
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    s = re.sub(r"(예정|경|쯤|무렵|께|중\s*발표|발표)$", "", s).strip()
    if not s:
        raise ParseError("회신 일정이 비어 있음")

    # 후보일: "11/13 또는 11/16", "11월 13일 or 16일", "11/13, 11/16"
    parts = re.split(r"\s*(?:또는|혹은|\bor\b|,)\s*", s, flags=re.I)
    if len(parts) > 1:
        dates, month = [], None
        for p in parts:
            start, end, month, _ = parse_piece(p, month, quarter, today)
            if start != end:
                raise ParseError(f"후보일은 하루 단위여야 함: '{p}'")
            dates.append(start)
        dates = sorted(set(dates))
        return {"text": " 또는 ".join(f"{x.month}/{x.day}" for x in dates),
                "from": dates[0].isoformat(), "to": dates[-1].isoformat(), "dates": [x.isoformat() for x in dates]}

    # 범위: "10월 말~11월 초", "10/28~10/30", "10월 28일~30일"
    parts = re.split(r"\s*[~∼〜]\s*|\s+-\s+", s)
    if len(parts) == 2:
        start, _, month, a = parse_piece(parts[0], None, quarter, today)
        _, end, _, b = parse_piece(parts[1], month, quarter, today)
        if end < start:
            raise ParseError(f"끝 날짜가 시작보다 빠름: '{s}'")
        return {"text": f"{a}~{b}", "from": start.isoformat(), "to": end.isoformat()}
    if len(parts) > 2:
        raise ParseError(f"범위는 '~' 하나로만: '{s}'")

    start, end, _, label = parse_piece(s, None, quarter, today)
    return {"text": label, "from": start.isoformat(), "to": end.isoformat()}


def load_ticker_lookup():
    try:
        with open(TICKERS_PATH, encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    rows = rows if isinstance(rows, list) else rows.get("tickers", [])
    out = {}
    for r in rows:
        tk, name = r.get("ticker", ""), r.get("name", "")
        if name and re.match(r"^\d{6}\.K[SQ]$", tk):
            out[name] = tk.split(".")[0]
    return out


def cell_text(v):
    if v is None:
        return ""
    if isinstance(v, (datetime.date, datetime.datetime)):
        # 엑셀이 "10/23"을 날짜로 바꿔버린 경우 — 날짜 그대로 쓴다
        d = v.date() if isinstance(v, datetime.datetime) else v
        return f"{d.month}/{d.day}"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    ap.add_argument("--out", default="calendar_domestic_manual.json")
    args = ap.parse_args()

    if not os.path.exists(args.source):
        print(f"[오류] 엑셀 파일이 없습니다: {args.source}", file=sys.stderr)
        sys.exit(1)
    wb = openpyxl.load_workbook(args.source, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        print("[오류] 엑셀이 비어 있습니다.", file=sys.stderr)
        sys.exit(1)

    tickers = load_ticker_lookup()
    events, errors = [], []
    for i, row in enumerate(rows[1:], start=2):
        company, code, quarter, text, memo = [cell_text(row[j]) if j < len(row) else "" for j in range(5)]
        if not company and not text:
            continue
        if not company:
            errors.append(f"{i}행: 기업명이 비어 있음")
            continue
        code = code.zfill(6) if code.isdigit() else code
        if not code:
            code = tickers.get(company, "")
        try:
            span = parse_schedule(text, quarter)
        except ParseError as e:
            errors.append(f"{i}행 {company}: {e}")
            continue
        # text는 화면 표기용으로 통일된 문구("10월 23일" → "10/23")
        ev = {"company": company, "ticker": code, "quarter": quarter, **span}
        if memo:
            ev["memo"] = memo
        events.append(ev)
        kind = "후보일 " + ", ".join(span["dates"]) if "dates" in span else (
            "확정 " + span["from"] if span["from"] == span["to"] else f"기간 {span['from']} ~ {span['to']}")
        print(f"  {company:<12} '{text}' → 표시 '{span['text']}' · {kind}")

    events.sort(key=lambda e: (e["from"], e["company"]))
    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": "IR 담당자 이메일 회신(수동 입력)",
        "events": events,
    }
    # 내용이 같으면 updated만 바뀌어 매번 커밋이 생기지 않도록, 이벤트가 같으면 이전 시각 유지
    try:
        with open(args.out, encoding="utf-8") as f:
            prev = json.load(f)
        if prev.get("events") == events:
            out["updated"] = prev.get("updated", out["updated"])
    except (OSError, json.JSONDecodeError):
        pass
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"완료: {args.out} ({len(events)}건)")
    if errors:
        print("", file=sys.stderr)
        print(f"[확인 필요] 해석하지 못해 건너뛴 줄 {len(errors)}개 — 엑셀 문구를 고쳐 다시 실행하세요:", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
