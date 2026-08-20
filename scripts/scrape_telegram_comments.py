#!/usr/bin/env python3
"""
텔레그램 채널(t.me/globalmedtech, 공개 채널)에서 리포트 발간 직후 올리는 짧은 코멘트를
가져와 reports.json의 각 리포트와 자동 매칭한다.

- 로그인/봇 토큰 없이 접근 가능한 공개 프리뷰 페이지(https://t.me/s/{channel})를 사용한다.
  Telegram 공식 API가 아니라 웹에 공개된 HTML을 파싱하는 방식이라, Telegram이 마크업을
  바꾸면 깨질 수 있다 — 실패해도 reports.json 자체에는 영향 없도록 항상 별도 스텝/파일로
  분리하고, 실패 시 조용히 종료한다(워크플로 전체를 실패시키지 않음).
- 매칭 방식: 채널 관찰 결과, 리포트를 올릴 때 항상 메시지 첫 줄이
  "{종목명}: {리포트 제목}" 형식이었다(예: "인바디: 10년만에 찾아온 랠리엔 이유가 있다",
  "씨어스: Large Cap 승급 테스트 시작"). 이 종목명이 reports.json에 있는 co와 정확히
  일치하는 메시지만 "리포트 코멘트"로 채택한다. 그 외 잡담/공시 전달/농담성 메시지는
  제외된다.
- 페이지네이션: 첫 페이지 이후 과거 메시지는 ?before={가장 오래된 message id}로 이어서
  요청한다(공개 프리뷰 페이지의 표준 방식). cutoff 이전 메시지가 나오면 중단.

사용법:
    python scrape_telegram_comments.py --reports reports.json --out telegram_comments.json
    python scrape_telegram_comments.py --channel globalmedtech --days 60
"""
import argparse
import datetime
import json
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

REQUEST_TIMEOUT = 20
REQUEST_DELAY_SEC = 0.6
MAX_PAGES = 30  # 안전장치 — 무한 루프 방지 (페이지당 대략 20개 메시지)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# 메시지 첫 줄이 "{종목명}: {제목}" 형식인지 판별. 종목명에 한글/영문/숫자만 허용
# (이모지·문장부호로 시작하는 잡담은 자동 제외됨).
FIRST_LINE_RE = re.compile(r"^([가-힣A-Za-z0-9&\s]{1,20}?)\s*[:：]\s*(.+)$")

# ---- 코멘트 본문 정제(2026-08-21 추가) ----
# 화면에는 "종목명: 제목"(이미 리포트 행 제목으로 따로 표시됨)·링크·컴플라이언스
# 문구를 빼고 실제 코멘트 본문만 작은 글씨로 헤드라인 밑에 보여주고 싶다는 요청에 따라
# 원문(comment)과 별도로 정제된 버전(comment_clean)을 만들어 둔다. 채널을 실제로
# 확인해보니(2026-08-21, t.me/s/globalmedtech) 컴플라이언스 문구가 발간처마다 문구가
# 조금씩 다르다(예: "*당사 Compliance 승인을 거친 보고서 요약 자료입니다..." vs
# "* 컴플라이언스 검필" + "* 본 정보는 키움증권 리서치센터에서...") — 완벽히 하나의
# 정규식으로 잡을 순 없어 키워드 기반으로 걸러낸다. 정제 후 내용이 하나도 안 남으면
# (제목+링크+디스클레이머만 있던 메시지) 프론트엔드에서 "코멘트 본문 없음"으로 표시한다.
_URL_RE = re.compile(r"<https?://\S+>|https?://\S+")
_DIVIDER_RE = re.compile(r"^[━\-=_]{3,}$")
_LABEL_LINK_RE = re.compile(r"^[\*\-•]?\s*(링크|보고서|report|link)\s*[:：]\s*$", re.IGNORECASE)
_DISCLAIMER_KEYWORDS = (
    "컴플라이언스", "compliance", "법적 책임", "법적책임", "증빙자료", "투자참고",
    "투자권유", "투자판단", "손실은 거래당사자", "검필", "리서치센터에서", "투자결과",
)


def clean_telegram_comment(text: str) -> str:
    """원문 메시지에서 첫 줄(종목명: 제목)·링크·컴플라이언스 문구를 제거하고
    실제 코멘트 본문만 남긴다."""
    lines = text.split("\n")
    lines = lines[1:] if lines else []  # 첫 줄(종목명: 제목) 제거
    out = []
    for raw_line in lines:
        line = _URL_RE.sub("", raw_line).strip()
        if not line:
            continue
        if _DIVIDER_RE.match(line):
            continue
        if _LABEL_LINK_RE.match(line):
            continue
        if any(kw.lower() in line.lower() for kw in _DISCLAIMER_KEYWORDS):
            continue
        out.append(line)
    return "\n".join(out).strip()


def fetch_page(channel: str, before: int | None = None) -> str:
    url = f"https://t.me/s/{channel}"
    if before:
        url += f"?before={before}"
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def parse_messages(html: str):
    """메시지 wrap 각각에서 id/date(ISO)/text/links를 뽑는다.
    구조가 바뀌어 특정 필드를 못 찾으면 그 메시지만 건너뛴다(전체 실패시키지 않음)."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for wrap in soup.select("div.tgme_widget_message_wrap"):
        msg = wrap.select_one("div.tgme_widget_message[data-post]")
        if not msg:
            continue
        data_post = msg.get("data-post", "")
        msg_id = data_post.split("/")[-1] if "/" in data_post else None
        if not msg_id or not msg_id.isdigit():
            continue
        msg_id = int(msg_id)

        date_a = msg.select_one("a.tgme_widget_message_date")
        time_tag = date_a.select_one("time") if date_a else None
        iso_dt = time_tag.get("datetime") if time_tag else None
        permalink = date_a.get("href") if date_a else f"https://t.me/{data_post}"

        text_div = msg.select_one("div.tgme_widget_message_text")
        if not text_div:
            continue  # 텍스트 없는 메시지(사진/영상만)는 코멘트 매칭 대상이 아님
        # <br>을 개행으로 바꾼 뒤 텍스트 추출
        for br in text_div.find_all("br"):
            br.replace_with("\n")
        text = text_div.get_text().strip()
        links = [a.get("href") for a in text_div.find_all("a") if a.get("href")]

        out.append({
            "id": msg_id,
            "iso_dt": iso_dt,
            "text": text,
            "links": links,
            "url": permalink,
        })
    return out


def scrape_channel(channel: str, cutoff: datetime.datetime):
    all_msgs = {}
    before = None
    for page in range(MAX_PAGES):
        try:
            html = fetch_page(channel, before)
        except requests.RequestException as e:
            print(f"[WARN] 페이지 요청 실패(page={page}, before={before}): {e}", file=sys.stderr)
            break
        msgs = parse_messages(html)
        if not msgs:
            print(f"[INFO] page {page}: 메시지 0건, 중단", file=sys.stderr)
            break

        new_count = 0
        for m in msgs:
            if m["id"] not in all_msgs:
                all_msgs[m["id"]] = m
                new_count += 1
        print(f"[INFO] page {page}: {len(msgs)}건 파싱, 신규 {new_count}건 (누적 {len(all_msgs)})", file=sys.stderr)

        oldest_on_page = min(msgs, key=lambda m: m["id"])
        oldest_dt = None
        if oldest_on_page["iso_dt"]:
            try:
                oldest_dt = datetime.datetime.fromisoformat(oldest_on_page["iso_dt"])
            except ValueError:
                pass
        if oldest_dt and oldest_dt < cutoff:
            print(f"[INFO] cutoff({cutoff.date()}) 이전 메시지 도달, 중단", file=sys.stderr)
            break
        if new_count == 0:
            print("[INFO] 신규 메시지 없음(페이지네이션 끝), 중단", file=sys.stderr)
            break

        before = oldest_on_page["id"]
        time.sleep(REQUEST_DELAY_SEC)

    return list(all_msgs.values())


def extract_report_comments(messages, known_companies: set):
    out = []
    for m in messages:
        text = m["text"]
        if not text:
            continue
        first_line = text.split("\n", 1)[0].strip()
        mobj = FIRST_LINE_RE.match(first_line)
        if not mobj:
            continue
        co_candidate = mobj.group(1).strip()
        title = mobj.group(2).strip()
        if co_candidate not in known_companies:
            continue  # reports.json에 없는 종목명이면 리포트 코멘트가 아닌 잡담으로 간주

        date_str = None
        if m["iso_dt"]:
            try:
                dt_utc = datetime.datetime.fromisoformat(m["iso_dt"])
                dt_kst = dt_utc.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
                date_str = dt_kst.strftime("%Y-%m-%d")
            except ValueError:
                pass

        out.append({
            "date": date_str,
            "co": co_candidate,
            "title": title,
            "comment": text,  # 원문 전체(디버깅/백업용, 화면에는 안 씀)
            "comment_clean": clean_telegram_comment(text),  # 화면 표시용(제목·링크·컴플라이언스 제거)
            "urls": m["links"],
            "telegram_url": m["url"],
            "message_id": m["id"],
        })
    out.sort(key=lambda r: r["date"] or "", reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="globalmedtech", help="텔레그램 공개 채널명(@ 없이)")
    ap.add_argument("--reports", default="reports.json", help="종목명 매칭 기준이 되는 reports.json")
    ap.add_argument("--out", default="telegram_comments.json")
    ap.add_argument("--days", type=int, default=60, help="최근 며칠치 메시지까지 수집할지")
    args = ap.parse_args()

    try:
        with open(args.reports, encoding="utf-8") as f:
            reports_data = json.load(f)
        known_companies = {r.get("co", "").strip() for r in reports_data.get("reports", []) if r.get("co")}
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[ERROR] {args.reports}을(를) 읽지 못해 종목명 매칭 기준이 없습니다: {e}", file=sys.stderr)
        sys.exit(0)  # reports.json 문제는 scrape_reports.py가 이미 처리 — 여기선 조용히 종료

    if not known_companies:
        print("[WARN] reports.json에 종목명이 없어 매칭할 수 없습니다.", file=sys.stderr)
        sys.exit(0)

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=args.days)

    try:
        messages = scrape_channel(args.channel, cutoff)
    except Exception as e:
        # 이 스크립트 전체가 실험적인 비공식 HTML 파싱이라, 어떤 예외가 나든
        # reports.json 자동 갱신 자체를 막지 않도록 조용히 종료한다.
        print(f"[ERROR] 텔레그램 채널 스크래핑 실패: {e}", file=sys.stderr)
        sys.exit(0)

    comments = extract_report_comments(messages, known_companies)
    if not comments:
        print("[WARN] 매칭된 리포트 코멘트가 0건입니다 (채널 구조 변경 또는 종목명 불일치 가능성).", file=sys.stderr)

    payload = {
        "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": f"t.me/{args.channel} 공개 채널 프리뷰 · 리포트 발간 코멘트 자동 매칭(비공식 HTML 파싱)",
        "count": len(comments),
        "comments": comments,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {args.out} ({len(comments)}건 매칭 / 전체 수집 메시지 {len(messages)}건)")


if __name__ == "__main__":
    main()
