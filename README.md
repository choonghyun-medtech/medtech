# 의료기기·디지털헬스 모니터링 대시보드

GitHub Pages로 서빙되는 단일 페이지 대시보드(`index.html`)입니다. 화면에 보이는 데이터는 모두 실데이터이며,
GitHub Actions가 매일(평일) 또는 매월 자동으로 수집해 `*.json`으로 커밋합니다.
**예외:** 글로벌 대시보드(`global_dashboard.json`)는 Bloomberg 터미널이 있는 로컬 PC에서 수동으로 갱신합니다.

## 탭 구성과 데이터

| 탭 | 내용 | 데이터 파일 | 출처 | 갱신 주기 |
|---|---|---|---|---|
| 산업·기업 뉴스 | 오늘의 클리핑(국내/해외), 아카이브, AI 브리핑 | `news.json`, `news_history.jsonl`, `news_trend.json` | 네이버 뉴스검색 API, 해외 매체 RSS | 평일 |
| 주가 데이터 › 주가 Performance | 수익률 Top/Bottom10, 외국인 지분율, 종목별 주가·지분율·수급 차트 | `stock_performance.json`, `investor_flow.json`, `fx_rate.json` | yfinance, Daum, 네이버증권, ECB | 평일 |
| 주가 데이터 › 글로벌 대시보드 | Peer Table(밸류에이션·실적 추정) | `global_dashboard.json` | Bloomberg 컨센서스 | **수동** |
| 주가 데이터 › 수급 판독기 | 국내 종목 수급 포지셔닝·전환점 | `investor_flow.json` | 네이버증권 | 평일 |
| 이벤트 캘린더 | 실적·IR·공시·주총 일정 | `calendar_events.json` | DART, 해외 기업 IR 페이지 | 평일 |
| 산업 데이터 › 수출 | HS코드별 수출 추이·월간 분석 | `export_data.json`, `export_monthly_analysis.json` | 관세청(공공데이터포털) | 매월 15~18일, 분석 19일 |
| 산업 데이터 › 의료관광 | 외국인 의료소비·방한객·침투율 | `medical_tour.json`, `visitor_stats.json`, `medtour_penetration.json`, `medtour_monthly_analysis.json` | 한국관광 데이터랩 | 매월 |
| 산업 데이터 › 진료행위 통계 | 심평원 진료행위 사용량·금액 | `hira_procedure.json` | 건강보험심사평가원 | 매월 2~5일 |
| 기업 분석 | 리서치 보고서(+텔레그램 코멘트), 해외기업 실적 요약 | `reports.json`, `reports_bio.json`, `telegram_comments.json`, `earnings_ir.json` | 미래에셋증권 게시판, SEC 8-K/6-K | 평일 |
| 기업 스냅샷 | 한 기업의 주가·실적 전망·리포트·수출·뉴스·일정 | 위 데이터 + `consensus.json` | 네이버 컨센서스(WiseReport) | 평일 |

종목 유니버스는 `scripts/tickers.json`(현재 258종목, 국내 64종목)입니다.

## 폴더 구성

```
index.html                 대시보드 페이지
*.json / *.jsonl           각 탭 데이터 (위 표 참고, 자동 커밋 대상)
scripts/                   수집·분석 스크립트 (scrape_*.py, analyze_*.py 등)
scripts/tickers.json       종목 유니버스 (ticker/name/sector/market)
.github/workflows/         자동 갱신 워크플로 (데이터별 1개씩)
update_global_dashboard.bat / scripts/update_global_dashboard.ps1
                           글로벌 대시보드 수동 갱신용 (로컬 Bloomberg PC)
```

## 처음 설정하는 방법

1. 저장소 **Settings → Pages**에서 Source를 "Deploy from a branch", Branch를 `main` / `/(root)`로 설정합니다.
2. **Settings → Actions → General**에서 "Workflow permissions"를 **Read and write permissions**로 설정합니다
   (워크플로가 데이터 파일을 커밋할 수 있어야 함).
3. **Settings → Secrets and variables → Actions**에 아래 키를 등록합니다. 키가 없는 워크플로는 해당 수집을 건너뜁니다.

| Secret | 사용처 |
|---|---|
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 국내 뉴스 수집 |
| `DART_API_KEY` | 국내 공시 캘린더 |
| `DATA_GO_KR_SERVICE_KEY` | 관세청 수출 데이터 |
| `GEMINI_API_KEY` (+ `GEMINI_API_KEY_EARNINGS`), `ANTHROPIC_API_KEY` | 뉴스 요약·브리핑, 월간 분석, 해외 실적 요약 |

4. **Actions** 탭에서 각 워크플로를 **Run workflow**로 한 번씩 수동 실행해 정상 동작을 확인합니다.

## 글로벌 대시보드 수동 갱신

Bloomberg 엑셀(값복사 탭)을 저장한 뒤 `update_global_dashboard.bat`을 실행하면
`scripts/convert_global_dashboard.py`로 `global_dashboard.json`을 만들고, 변경 내역을 보여준 뒤 커밋·푸시까지 진행합니다.
대시보드 제목 옆 배지에 업데이트일이 표시되고, 7일 이상 지나면 주황색 경고로 바뀝니다.

## 로컬에서 미리보기

`index.html`이 JSON을 `fetch`로 불러오므로 파일을 더블클릭해 열면 데이터가 뜨지 않습니다. 로컬 서버로 여세요.
```bash
py -m http.server 8000
# 브라우저에서 http://localhost:8000/
```

## 자동화가 실패했을 때 확인할 것

- Actions 탭에서 해당 워크플로 로그를 확인합니다.
- 소스 사이트(미래에셋 게시판, 네이버, Daum, 한국관광 데이터랩 등)의 구조가 바뀌면 해당 `scripts/scrape_*.py`의 파싱 로직을 조정해야 할 수 있습니다.
- 주가 스크래퍼에서 실패한 종목은 `stock_performance.json`의 `error` 필드에 사유가 남습니다.
