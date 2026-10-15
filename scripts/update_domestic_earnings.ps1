# 국내 커버 기업 실적발표 일정 갱신 — 로컬 실행용 원클릭 스크립트 (2026-10-15).
#
# 사용법: IR 담당자 회신을 받으면 "D:\★사용자 폴더\Desktop\dashboard files\국내 실적발표 일정!!.xlsx"에
# 회신 문구를 그대로 적고 저장 — 그 다음 update_domestic_earnings.bat을 더블클릭하면
# (1) 엑셀 -> calendar_domestic_manual.json 변환, (2) 변경 내용 요약 표시,
# (3) 검토 후 Y 입력 시에만 커밋 + 푸시까지 진행한다(글로벌 대시보드 갱신과 같은 흐름).
# 회신이 이메일로 오기 때문에 자동 수집이 불가능해 사람이 한 번 확인하고 올리는 구조다.

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "=== 국내 실적발표 일정 갱신 ===" -ForegroundColor Cyan

$pythonCmd = $null
foreach ($cand in @('python', 'py')) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $pythonCmd = $cand; break }
}
if (-not $pythonCmd) {
    Write-Host "[오류] python 또는 py 명령을 찾을 수 없습니다. Python이 설치돼 있는지 확인해주세요." -ForegroundColor Red
    Read-Host "종료하려면 Enter"
    exit 1
}

& $pythonCmd -c "import openpyxl" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[오류] openpyxl 패키지가 없습니다. 아래 명령으로 먼저 설치해주세요:" -ForegroundColor Red
    Write-Host "    $pythonCmd -m pip install openpyxl" -ForegroundColor Yellow
    Read-Host "종료하려면 Enter"
    exit 1
}

Write-Host ""
Write-Host "-- 1) 엑셀 -> JSON 변환 --" -ForegroundColor Cyan
# 종료 코드 2 = 일부 줄을 해석하지 못함(나머지는 정상 변환됨) — 경고만 하고 계속 진행할지 묻는다.
& $pythonCmd scripts\convert_domestic_earnings.py --out calendar_domestic_manual.json
$convertCode = $LASTEXITCODE
if ($convertCode -eq 1 -or $convertCode -gt 2) {
    Write-Host "[오류] 변환이 실패했습니다(위 로그 참고). 커밋 없이 종료합니다." -ForegroundColor Red
    Read-Host "종료하려면 Enter"
    exit 1
}
if ($convertCode -eq 2) {
    Write-Host ""
    Write-Host "위 '확인 필요' 줄은 캘린더에 들어가지 않습니다. 엑셀을 고쳐 다시 실행하는 걸 권장합니다." -ForegroundColor Yellow
}

$changedFiles = @('calendar_domestic_manual.json')

Write-Host ""
Write-Host "-- 2) 변경 내용 요약 --" -ForegroundColor Cyan
git add -N -- $changedFiles 2>$null  # 처음 만들어진 파일도 diff에 보이도록
$diffStat = git diff --stat -- $changedFiles
if (-not $diffStat) {
    Write-Host "calendar_domestic_manual.json에 변경 사항이 없습니다(이전 커밋과 내용이 동일). 종료합니다." -ForegroundColor Yellow
    Read-Host "종료하려면 Enter"
    exit 0
}
Write-Host $diffStat
Write-Host ""
Write-Host "필요하면 아래 명령으로 상세 diff를 직접 확인할 수 있습니다:" -ForegroundColor DarkGray
Write-Host "    git diff -- calendar_domestic_manual.json" -ForegroundColor DarkGray
Write-Host ""

$answer = Read-Host "위 변경사항을 커밋하고 GitHub에 푸시할까요? (Y/N)"
if ($answer -notmatch '^[Yy]') {
    Write-Host "커밋하지 않고 종료합니다 — calendar_domestic_manual.json은 수정된 채로 남아있습니다." -ForegroundColor Yellow
    Write-Host "되돌리려면: git checkout -- calendar_domestic_manual.json" -ForegroundColor DarkGray
    Read-Host "종료하려면 Enter"
    exit 0
}

Write-Host ""
Write-Host "-- 3) 커밋 + 푸시 --" -ForegroundColor Cyan
$dateStr = Get-Date -Format 'yyyy-MM-dd'
git add -- $changedFiles
git commit -m "chore: 국내 실적발표 일정 갱신 (IR 회신, $dateStr)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[오류] 커밋에 실패했습니다." -ForegroundColor Red
    Read-Host "종료하려면 Enter"
    exit 1
}
git push
if ($LASTEXITCODE -ne 0) {
    Write-Host "[오류] 푸시에 실패했습니다 — 원격에 새 커밋이 있을 수 있습니다. 'git pull --rebase' 후 'git push'를 직접 실행해주세요." -ForegroundColor Red
    Read-Host "종료하려면 Enter"
    exit 1
}

Write-Host ""
Write-Host "완료 — 몇 분 안에 GitHub Pages 사이트에 반영됩니다." -ForegroundColor Green
Read-Host "종료하려면 Enter"
