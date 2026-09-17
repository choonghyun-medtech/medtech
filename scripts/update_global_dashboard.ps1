# 글로벌 대시보드 Peer Table 갱신 — 로컬 실행용 원클릭 스크립트.
#
# 사용법: 블룸버그 터미널 로그인된 PC에서 "D:\★사용자 폴더\Desktop\dashboard files\
# 글로벌 대시보드!!.xlsx"를 열어 raw 탭 수식을 최신화하고, "값복사" 탭에 값으로
# 붙여넣기한 뒤 저장 — 그 다음 이 스크립트(또는 update_global_dashboard.bat)를
# 더블클릭하면 (1) 엑셀 -> global_dashboard.json 변환, (2) 변경 내용 요약 표시,
# (3) 검토 후 Y 입력 시에만 커밋 + 푸시까지 진행한다.
#
# [2026-09-17] 블룸버그 함수는 로컬 터미널 세션에서만 계산되므로 GitHub Actions
# 같은 클라우드에서는 완전 자동화가 불가능하다(scripts/convert_global_dashboard.py
# 상단 docstring 참고) — 이 스크립트는 "엑셀 저장 -> 더블클릭 한 번"까지만
# 사람 손을 줄이고, 실제 반영(push) 전에는 항상 사람이 한 번 확인하도록
# 일부러 완전 무인화하지 않았다(사용자 요청).

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "=== 글로벌 대시보드 갱신 ===" -ForegroundColor Cyan

# python/py 중 있는 걸 찾는다(환경마다 다름 — 이 PC는 'py' 런처만 있음을 확인).
$pythonCmd = $null
foreach ($cand in @('python', 'py')) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $pythonCmd = $cand; break }
}
if (-not $pythonCmd) {
    Write-Host "[오류] python 또는 py 명령을 찾을 수 없습니다. Python이 설치돼 있는지 확인해주세요." -ForegroundColor Red
    Read-Host "종료하려면 Enter"
    exit 1
}

# openpyxl 설치 확인(없으면 변환 스크립트가 ImportError로 죽으므로 먼저 안내).
& $pythonCmd -c "import openpyxl" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[오류] openpyxl 패키지가 없습니다. 아래 명령으로 먼저 설치해주세요:" -ForegroundColor Red
    Write-Host "    $pythonCmd -m pip install openpyxl" -ForegroundColor Yellow
    Read-Host "종료하려면 Enter"
    exit 1
}

Write-Host ""
Write-Host "-- 1) 엑셀 -> JSON 변환 --" -ForegroundColor Cyan
& $pythonCmd scripts\convert_global_dashboard.py --out global_dashboard.json
if ($LASTEXITCODE -ne 0) {
    Write-Host "[오류] 변환이 실패했습니다(위 로그 참고). 커밋 없이 종료합니다." -ForegroundColor Red
    Read-Host "종료하려면 Enter"
    exit 1
}

Write-Host ""
Write-Host "-- 2) 변경 내용 요약 --" -ForegroundColor Cyan
$diffStat = git diff --stat -- global_dashboard.json
if (-not $diffStat) {
    Write-Host "global_dashboard.json에 변경 사항이 없습니다(이전 커밋과 내용이 동일). 종료합니다." -ForegroundColor Yellow
    Read-Host "종료하려면 Enter"
    exit 0
}
Write-Host $diffStat

Write-Host ""
Write-Host "필요하면 아래 명령으로 상세 diff를 직접 확인할 수 있습니다:" -ForegroundColor DarkGray
Write-Host "    git diff -- global_dashboard.json" -ForegroundColor DarkGray
Write-Host ""

# -- 3) 검토 후 확인 --
$answer = Read-Host "위 변경사항을 커밋하고 GitHub에 푸시할까요? (Y/N)"
if ($answer -notmatch '^[Yy]') {
    Write-Host "커밋하지 않고 종료합니다 — global_dashboard.json은 수정된 채로 남아있습니다." -ForegroundColor Yellow
    Write-Host "되돌리려면: git checkout -- global_dashboard.json" -ForegroundColor DarkGray
    Read-Host "종료하려면 Enter"
    exit 0
}

Write-Host ""
Write-Host "-- 4) 커밋 + 푸시 --" -ForegroundColor Cyan
$dateStr = Get-Date -Format 'yyyy-MM-dd'
git add global_dashboard.json
git commit -m "chore: global_dashboard.json 갱신 (블룸버그 피어 테이블, $dateStr)"
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
