#!/usr/bin/env python3
"""
국내 팔로업 기업(tickers.json market=="KR", 38개사)의 DART 공시 중 캘린더에 표시할
가치가 있는 주요 일정을 뽑아 calendar_events.json의 "domestic" 항목으로 저장한다.

- DART Open API(opendart.fss.or.kr, 무료 발급 키 필요, DART_API_KEY 환경변수) 사용.
  키가 없으면 아무 것도 하지 않고 조용히 종료한다(다른 항목을 덮어쓰지 않음) — DART_API_KEY를
  GitHub Secrets에 등록하기 전까지는 이 스크립트가 domestic 항목을 비워둔 채로 둔다.
- DART list.json은 "공시 목록"(어떤 보고서가 언제 접수됐는지)만 알려주고, 그 보고서 안에
  적힌 실제 행사일(주총일, 배당기준일 등)은 문서 본문을 열어야 확인 가능하다. 1차 버전은
  보고서 제출일(rcept_dt) 자체를 이벤트 날짜로 쓰는 근사치다 — "제출기한"이 아니라 "공시가
  실제로 뜬 날"을 표시한다. 문서 본문 파싱(정확한 주총일/배당기준일 추출)은 후속 개선 과제.
- report_nm(보고서명) 키워드로 유형을 분류한다. 아래 목록에 안 걸리는 공시는 캘린더 노이즈를
  줄이기 위해 버린다(화이트리스트 방식).

사용법:
    python scrape_calendar_domestic.py --out calendar_events.json
    python scrape_calendar_domestic.py --insecure   # 사내망 SSL 프록시 환경 전용
"""
import argparse
import datetime
import io
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

import requests

TICKERS_FILE = "scripts/tickers.json"
DART_BASE = "https://opendart.fss.or.kr/api"

DAYS_BACK = 120
DAYS_FORWARD = 120  # DART는 "이미 접수된" 공시만 조회되므로 미래 구간은 의미가 적지만
                     # 스크립트를 매일 돌리는 동안 자연스럽게 다가오는 일정이 채워진다.

# report_nm 부분일치 키워드 -> (type, 이벤트 라벨)
CATEGORY_RULES = [
    (re.compile(r"기업설명회|IR\s*개최"), "ir", "IR·기업설명회"),
    (re.compile(r"주주총회소집|정기주주총회|임시주주총회"), "agm", "주주총회"),
    (re.compile(r"현금\W*배당결정|주식배당결정|액면분할"), "exright", "배당/액면분할 결정(권리락 관련)"),
    (re.compile(r"무상증자결정|유상증자결정|신주인수권부사채권발행결정|전환사채권발행결정|신주의\s*상장"), "listing", "증자/신주 발행 결정"),
    (re.compile(r"사업보고서|반기보고서|분기보고서"), "earn", "정기보고서 제출(실적)"),
    (re.compile(r"매출액또는손익구조"), "earn", "잠정실적 공시"),
]


KR_CODE_RE = re.compile(r"^[0-9A-Za-z]{6}$")


def load_kr_tickers(path):
    """tickers.json의 ticker는 "123456.KS"/"123456.KQ" 형식이 대부분이지만, 최근
    상장한 일부 종목(예: 메쥬 0088M0)은 KRX가 문자가 섞인 6자리 코드를 쓴다. 예전
    코드는 숫자만 남기고 6자리인지 검사해서 이런 종목을 통째로 걸러냈던 버그가
    있었음(2026-08-28 수정) — 이제 "."을 기준으로 앞부분을 그대로 코드로 쓰고,
    영숫자 6자리인지만 검사한다.
    """
    with open(path, encoding="utf-8") as f:
        tickers = json.load(f)
    out = []
    for t in tickers:
        if t.get("market") != "KR":
            continue
        code = t["ticker"].split(".")[0]
        if KR_CODE_RE.match(code):
            out.append({"stock_code": code, "name": t["name"]})
        else:
            print(f"[WARN] {t['name']}: 종목코드 형식을 인식하지 못함 ({t['ticker']})", file=sys.stderr)
    return out


def fetch_corp_code_map(api_key, session, retries=3):
    """DART corpCode.xml(zip)을 받아 {stock_code: corp_code} 매핑을 만든다.
    opendart.fss.or.kr가 GitHub Actions runner에서 가끔 커넥션 타임아웃을 내는 걸
    2026-08-31 자동 실행에서 확인했다 — 재시도로 일시적 장애를 흡수한다."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": api_key}, timeout=30)
            r.raise_for_status()
            break
        except requests.exceptions.RequestException as e:
            last_err = e
            print(f"[WARN] corpCode.xml 요청 실패({attempt}/{retries}): {e}", file=sys.stderr)
            if attempt < retries:
                time.sleep(5 * attempt)
    else:
        raise last_err
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    xml_bytes = zf.read("CORPCODE.xml")
    root = ET.fromstring(xml_bytes)
    mapping = {}
    for node in root.iter("list"):
        stock_code = (node.findtext("stock_code") or "").strip()
        corp_code = (node.findtext("corp_code") or "").strip()
        if stock_code:
            mapping[stock_code] = corp_code
    return mapping


def classify(report_nm):
    for pattern, ev_type, label in CATEGORY_RULES:
        if pattern.search(report_nm):
            return ev_type, label
    return None, None


def fetch_disclosures(api_key, corp_code, bgn_de, end_de, session):
    events = []
    page = 1
    while True:
        params = {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_no": page,
            "page_count": 100,
        }
        r = session.get(f"{DART_BASE}/list.json", params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "000":
            break  # 013(조회된 데이터 없음) 등은 정상 종료
        for row in data.get("list", []):
            yield row
        if page >= int(data.get("total_page", 1)):
            break
        page += 1
        time.sleep(0.2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=TICKERS_FILE)
    ap.add_argument("--out", default="calendar_events.json")
    ap.add_argument("--insecure", action="store_true",
                     help="TLS 인증서 검증을 건너뛴다 (사내망 SSL 프록시 환경 전용, 기본 꺼짐)")
    args = ap.parse_args()

    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        print("[INFO] DART_API_KEY가 설정되지 않아 국내 공시 캘린더 수집을 건너뜁니다.",
              file=sys.stderr)
        return

    session = requests.Session()
    if args.insecure:
        session.verify = False
        print("[WARN] --insecure: TLS 인증서 검증을 건너뜁니다 (로컬 전용).", file=sys.stderr)

    kr_tickers = load_kr_tickers(args.tickers)
    try:
        corp_map = fetch_corp_code_map(api_key, session)
    except requests.exceptions.RequestException as e:
        print(f"[WARN] DART corpCode.xml 조회가 재시도 후에도 실패해 이번 실행은 건너뜁니다: {e}",
              file=sys.stderr)
        return

    today = datetime.date.today()
    bgn_de = (today - datetime.timedelta(days=DAYS_BACK)).strftime("%Y%m%d")
    end_de = (today + datetime.timedelta(days=DAYS_FORWARD)).strftime("%Y%m%d")

    all_events = []
    for t in kr_tickers:
        corp_code = corp_map.get(t["stock_code"])
        if not corp_code:
            print(f"[WARN] {t['name']}({t['stock_code']}) corp_code 매칭 실패", file=sys.stderr)
            continue
        try:
            count = 0
            for row in fetch_disclosures(api_key, corp_code, bgn_de, end_de, session):
                ev_type, label = classify(row.get("report_nm", ""))
                if not ev_type:
                    continue
                rcept_dt = row.get("rcept_dt", "")  # YYYYMMDD
                if len(rcept_dt) != 8:
                    continue
                date_iso = f"{rcept_dt[0:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}"
                rcept_no = row.get("rcept_no", "")
                all_events.append({
                    "date": date_iso,
                    "region": "domestic",
                    "type": ev_type,
                    "ticker": t["stock_code"],
                    "company": row.get("corp_name", t["name"]),
                    "title": f"{label} — {row.get('report_nm','')}",
                    "source_name": "DART 전자공시시스템",
                    "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
                    "estimated": False,
                })
                count += 1
            print(f"[INFO] {t['name']}: {count}건")
        except Exception as e:
            print(f"[WARN] {t['name']} 공시 조회 실패: {e}", file=sys.stderr)
        time.sleep(0.2)

    all_events.sort(key=lambda e: e["date"])

    try:
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}

    existing["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    existing["global"] = existing.get("global", [])
    existing["domestic"] = all_events

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"[DONE] domestic 이벤트 {len(all_events)}건 -> {args.out}")


if __name__ == "__main__":
    main()
