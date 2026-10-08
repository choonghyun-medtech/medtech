#!/usr/bin/env python3
"""
TIKR에서 복사해 한 워드 파일(.docx)에 모아둔 컨콜 스크립트 원문을 기업·분기별 JSON으로
나눠 concall_transcripts/에 저장한다. "기업 분석 > 해외기업 컨콜" 카드의 "컨콜 스크립트
보기" 버튼이 이 파일들을 읽는다(2026-10-08 요청).

- 원본 구조(실측): 기업마다 Heading4(날짜) + Heading3("DexCom, Inc., Q2 2026 Earnings Call,
  Jul 30, 2026") 제목으로 시작하고, 본문은 "굵은 글씨 화자 이름" 문단 → 발언 문단들 순서다.
  화자 문단은 굵은 글씨로만 구분된다(이름 길이·문장부호로 추측하지 않음).
- Q&A 시작점: 발표가 끝난 뒤 Operator가 첫 질문을 소개하는 지점을 qa_start(세그먼트 인덱스)로
  표시한다(팝업의 "Q&A로 이동" 버튼용).
- 공개 여부: 처음엔 TIKR 원문 재배포 우려로 concall_transcripts/를 .gitignore로 로컬 전용
  처리했으나, 공개 사이트에도 노출하기로 결정돼(2026-10-08 사용자 요청) 커밋 대상으로 바꿨다.
- 누적 방식: 실행할 때마다 docx에 든 컨콜만 추가/덮어쓰고(같은 티커+분기면 교체), 이번 docx에
  없는 기존 파일과 index.json 항목은 그대로 둔다. 그래서 docx에는 새로 나온 컨콜 몇 개만
  넣어도 되고, 이미 넣은 걸 다시 돌려도 결과가 같다(중복 생성 없음).
- 분기 표기는 earnings_ir.json과 같은 "2026년 2분기(Q2)"로 맞춘다(카드와 티커+분기로 매칭).
  메드트로닉처럼 회계연도가 다른 기업도 제목의 "Q1 2027"을 그대로 쓰면 earnings_ir.json의
  "2027년 1분기(Q1)"와 일치한다.

사용법(보통은 update_concall_transcripts.bat 더블클릭):
    python scripts/extract_concall_transcripts.py [--docx "<경로>.docx"] [--only DXCM]
"""
import argparse
import html
import json
import os
import re
import sys
import zipfile

OUT_DIR = "concall_transcripts"
DEFAULT_DOCX = r"D:\★사용자 폴더\Desktop\dashboard files\TIKR 컨콜 원문!!.docx"
EARNINGS_IR_PATH = "earnings_ir.json"

# 제목의 회사명(앞부분) -> earnings_ir.json 티커
NAME_TO_TICKER = [
    ("Abbott", "ABT"), ("Align Technology", "ALGN"), ("Boston Scientific", "BSX"),
    ("DexCom", "DXCM"), ("Edwards Lifesciences", "EW"), ("Guardant Health", "GH"),
    ("Hims & Hers", "HIMS"), ("Intuitive Surgical", "ISRG"), ("iRhythm", "IRTC"),
    ("Medtronic", "MDT"), ("Natera", "NTRA"), ("RadNet", "RDNT"), ("Stryker", "SYK"),
    ("Teladoc", "TDOC"), ("Tempus AI", "TEM"), ("Thermo Fisher", "TMO"),
    ("UnitedHealth", "UNH"), ("InMode", "INMD"),
]
TITLE_RE = re.compile(r"^(?P<company>.+?), Q(?P<q>[1-4]) (?P<y>\d{4}) Earnings Call, (?P<date>.+)$")
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def read_paragraphs(path):
    """(style, bold, text) 목록. python-docx 없이 document.xml을 직접 읽는다."""
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    out = []
    for p in re.findall(r"<w:p(?: [^>]*)?>.*?</w:p>", xml, re.S):
        st = re.search(r'<w:pStyle w:val="([^"]+)"', p)
        text = html.unescape("".join(re.findall(r"<w:t(?: [^>]*)?>(.*?)</w:t>", p, re.S))).strip()
        bold = bool(re.search(r'<w:b(?: w:val="(?:1|true)")?/>', p))
        out.append((st.group(1) if st else "", bold, text))
    return out


def parse_call_date(s):
    m = re.match(r"(\w{3}) (\d{1,2}), (\d{4})", s.strip())
    if not m or m.group(1) not in MONTHS:
        return None
    return f"{m.group(3)}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"


def ticker_for(company):
    for key, tk in NAME_TO_TICKER:
        if company.startswith(key):
            return tk
    return None


def split_calls(paras):
    calls, cur = [], None
    for style, bold, text in paras:
        if style == "Heading3":
            m = TITLE_RE.match(text)
            if not m:
                print(f"[WARN] 제목 형식 인식 실패: {text}", file=sys.stderr)
                cur = None
                continue
            cur = {"title": text, "company": m.group("company"), "q": int(m.group("q")),
                   "y": int(m.group("y")), "call_date": parse_call_date(m.group("date")), "segments": []}
            calls.append(cur)
            continue
        if style == "Heading4" or cur is None or not text:
            continue
        segs = cur["segments"]
        if bold:
            segs.append({"speaker": text, "paras": []})
        elif segs:
            segs[-1]["paras"].append(text)
        else:
            segs.append({"speaker": "", "paras": [text]})
    return calls


FIRST_Q_RE = re.compile(r"(first|1st) question|question (?:today )?comes from|question is from", re.I)


def find_qa_start(segments):
    """발표 후 첫 질문자를 소개하는 세그먼트. 보통 Operator지만 메드트로닉처럼 IR 담당자가
    직접 질문자를 부르는 기업도 있어 화자가 아니라 문구("first question …")로 찾는다.
    경영진 발표 속 긴 문단에 같은 표현이 섞일 수 있어(힘스앤허스 CEO 발언 실측), 질문자 소개처럼
    짧은 문단(300자 미만)에서만 인정한다."""
    for i, s in enumerate(segments):
        if i > 1 and any(len(p) < 300 and FIRST_Q_RE.search(p) for p in s["paras"]):
            return i
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docx", default=DEFAULT_DOCX)
    ap.add_argument("--only", nargs="*", help="이 티커만 저장(시험용). 생략하면 전부")
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    if not os.path.exists(args.docx):
        print(f"[오류] 워드 파일이 없습니다: {args.docx}", file=sys.stderr)
        sys.exit(1)
    calls = split_calls(read_paragraphs(args.docx))
    if not calls:
        print("[오류] 컨콜을 하나도 인식하지 못했습니다 — 제목이 'Heading3' 스타일의 "
              "'회사명, Q2 2026 Earnings Call, Jul 30, 2026' 형식인지 확인해주세요.", file=sys.stderr)
        sys.exit(1)
    # 카드(earnings_ir.json)와 연결되는지 확인용 — 없으면 버튼이 안 보이므로 경고
    try:
        with open(EARNINGS_IR_PATH, encoding="utf-8") as f:
            ir_keys = {(c.get("ticker"), c.get("quarter")) for c in json.load(f).get("companies", [])}
    except (FileNotFoundError, json.JSONDecodeError):
        ir_keys = None
    warnings = []
    os.makedirs(args.out, exist_ok=True)
    index_path = os.path.join(args.out, "index.json")
    try:
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        index = {"transcripts": []}
    by_key = {(t["ticker"], t["quarter"]): t for t in index["transcripts"]}

    for c in calls:
        tk = ticker_for(c["company"])
        quarter = f"{c['y']}년 {c['q']}분기(Q{c['q']})"
        words = sum(len(p.split()) for s in c["segments"] for p in s["paras"])
        status = "건너뜀"
        if not tk:
            warnings.append(f"티커를 모르는 기업: '{c['company']}' — scripts/extract_concall_transcripts.py의 "
                            f"NAME_TO_TICKER에 추가 필요")
        elif not args.only or tk in args.only:
            fname = f"{tk}_{c['y']}Q{c['q']}.json"
            qa = find_qa_start(c["segments"])
            payload = {"ticker": tk, "company": c["company"], "quarter": quarter, "call_date": c["call_date"],
                       "title": c["title"], "source": "TIKR 컨콜 스크립트(수동 복사)",
                       "qa_start": qa, "segments": c["segments"]}
            path = os.path.join(args.out, fname)
            text = json.dumps(payload, ensure_ascii=False, indent=1)
            try:
                with open(path, encoding="utf-8") as f:
                    old = f.read()
            except FileNotFoundError:
                old = None
            status = "신규" if old is None else ("변경없음" if old == text else "갱신")
            if status != "변경없음":
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            by_key[(tk, quarter)] = {"ticker": tk, "quarter": quarter, "call_date": c["call_date"],
                                     "title": c["title"], "file": fname}
            if qa is None:
                warnings.append(f"{tk} {quarter}: Q&A 시작점을 못 찾음(팝업의 'Q&A로 이동' 버튼만 숨겨짐)")
            if ir_keys is not None and (tk, quarter) not in ir_keys:
                warnings.append(f"{tk} {quarter}: 해외기업 컨콜 카드(IR 요약)가 아직 없어 버튼이 안 보임 — "
                                f"IR 요약이 자동 수집되면 그때부터 자동 연결됨")
        print(f"{status:4} {tk or '??':5} {quarter:14} {c['call_date']}  단어 {words:6,}  {c['company']}")

    index["transcripts"] = sorted(by_key.values(), key=lambda t: (t["ticker"], t["quarter"]))
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print(f"\n이번 파일에서 {len(calls)}개 컨콜 인식 · 누적 보관 {len(index['transcripts'])}개 → {args.out}/")
    for w in warnings:
        print(f"[확인] {w}")


if __name__ == "__main__":
    main()
