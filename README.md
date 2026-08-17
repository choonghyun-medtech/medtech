# 의료기기·디지털헬스 모니터링 대시보드

리서치 보고서 탭은 미래에셋증권 리서치 게시판에서 작성자 "김충현, CFA" 발간분을, 주가 데이터 탭의 수익률 Top10/Bottom10은
글로벌 헬스케어 145종목의 가격·시가총액을 매일 자동으로 긁어와 갱신합니다.
나머지 부분(뉴스·개별종목 수급차트·이벤트 캘린더·산업데이터)은 아직 샘플 데이터입니다.

## 폴더 구성

```
index.html                                  대시보드 페이지 (GitHub Pages가 서빙)
reports.json                                리서치 보고서 데이터 (자동 갱신 대상)
stock_performance.json                      종목별 수익률·시가총액 데이터 (자동 갱신 대상, 최초 1회 Actions 실행 전까지는 없음)
scripts/scrape_reports.py                   미래에셋 게시판 스크래퍼
scripts/scrape_stock_performance.py         yfinance/Daum 기반 주가 퍼포먼스 스크래퍼
scripts/tickers.json                        종목 유니버스 (145종목, 섹터/거래소 포함)
.github/workflows/update-reports.yml               매일 자동 실행 (리서치 보고서)
.github/workflows/update-stock-performance.yml     매일 자동 실행 (주가 퍼포먼스)
```

## 처음 설정하는 방법 (한 번만 하면 됨)

1. GitHub에서 새 저장소를 만듭니다 (Public이어야 GitHub Pages 무료로 사용 가능).
2. 이 폴더의 파일 전체를 그 저장소에 올립니다 (그대로 커밋 & 푸시).
3. 저장소 **Settings → Pages** 로 이동해서 Source를 "Deploy from a branch", Branch를 `main` / `/(root)` 로 설정합니다.
   - 몇 분 뒤 `https://<사용자아이디>.github.io/<저장소이름>/` 주소로 대시보드가 뜹니다.
4. **Settings → Actions → General** 에서 "Workflow permissions"를 **Read and write permissions**로 설정합니다.
   (자동 커밋이 저장소에 push할 수 있어야 하기 때문입니다.)
5. **Actions** 탭에서 두 워크플로("Update research reports", "Update stock performance")를 각각 **Run workflow** 버튼으로 한 번씩 수동 실행해서 정상 동작하는지 확인합니다.
   - 성공하면 `reports.json` / `stock_performance.json`이 자동으로 커밋되고, 대시보드에 반영됩니다.
   - `stock_performance.json`은 145종목을 하나씩 조회하기 때문에 첫 실행에 몇 분 걸릴 수 있습니다.

이후로는 평일(월~금) 한국시간 오전에 자동으로 재실행되어 데이터를 갱신하고 커밋합니다.
토큰이나 비밀번호를 따로 등록할 필요는 없습니다 — GitHub Actions가 기본 제공하는 권한(`GITHUB_TOKEN`)으로 같은 저장소에 커밋합니다.

## 수동으로 다시 긁어오고 싶을 때

로컬에서:
```bash
pip install requests beautifulsoup4 yfinance
python scripts/scrape_reports.py --out reports.json
python scripts/scrape_stock_performance.py --tickers scripts/tickers.json --out stock_performance.json
```

다른 작성자로 바꾸고 싶다면:
```bash
python scripts/scrape_reports.py --author "홍길동" --out reports.json
```

종목 유니버스를 바꾸고 싶다면 `scripts/tickers.json`을 직접 편집하면 됩니다 (ticker/name/sector/market 4개 필드).
거래소 접미사 규칙: 미국 없음 · 한국 `.KS`(코스피)/`.KQ`(코스닥) · 홍콩 `.HK` · 일본 `.T` · 중국 `.SZ`/`.SS` · 독일 `.DE` · 스위스 `.SW` · 영국 `.L` · 프랑스 `.PA` (yfinance 표기 기준).

## 자동화가 실패했을 때 확인할 것

- Actions 탭에서 워크플로 로그 확인 (작성자 검색어가 게시판 표기와 다르면 0건이 나올 수 있음)
- 게시판 구조가 바뀌면 `scripts/scrape_reports.py`의 파싱 로직 조정이 필요할 수 있음
- PDF 원문 링크는 `https://securities.miraeasset.com/bbs/download/{pdf}.pdf?attachmentId={pdf}` 형식이며 로그인 없이 접근 가능함을 확인했습니다.
- 주가 퍼포먼스 스크래퍼는 제 작업 환경(네트워크 제한된 샌드박스)에서는 Yahoo Finance 접속 자체가 막혀 있어 직접 실행 검증을 못 했습니다. 로직은 단위 테스트로 검증했지만, GitHub Actions에서의 첫 실행 결과는 꼭 확인해주세요. 실패하는 종목은 `stock_performance.json`의 `error` 필드에 사유가 남습니다.
- 한국 종목 시가총액은 Daum Finance API를 우선 사용하고 실패하면 yfinance로 대체합니다. Daum API 응답 구조가 바뀌면 `daum_market_cap()` 함수를 손봐야 할 수 있습니다.
