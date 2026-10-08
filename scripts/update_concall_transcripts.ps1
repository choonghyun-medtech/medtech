# 컨콜 스크립트 원문 반영 — 로컬 실행용 원클릭 스크립트(2026-10-08).
#
# 사용법: TIKR에서 새로 나온 컨콜 스크립트를 "D:\★사용자 폴더\Desktop\dashboard files\
# TIKR 컨콜 원문!!.docx"에 붙여넣고 저장 — 그 다음 update_concall_transcripts.bat을
# 더블클릭하면 (1) 워드 -> 기업·분기별 JSON 분리(concall_transcripts/), (2) 변경 내용 요약,
# (3) 검토 후 Y 입력 시에만 커밋 + 푸시까지 진행한다.
#
# 워드 파일에는 이번에 새로 나온 컨콜 몇 개만 넣어도 된다 — 이미 반영된 과거 분기는
# concall_transcripts/에 그대로 남고, 같은 기업·분기를 다시 넣으면 그것만 교체된다.
# (워드 파일을 비우지 않고 계속 이어 붙여도 결과는 같다.)
# 각 컨콜은 TIKR 제목 줄("DexCom, Inc., Q2 2026 Earnings Call, Jul 30, 2026")이 워드의
# "제목 3" 스타일, 화자 이름이 굵은 글씨여야 한다 — TIKR에서 그대로 복사하면 이렇게 들어온다.
# 글로벌 대시보드 갱신(update_global_dashboard.ps1)과 같은 흐름: 푸시 전엔 항상 사람이 확인.

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "=== 컨콜 스크립트 원문 반영 ===" -ForegroundColor Cyan

$pythonCmd = $null
foreach ($cand in @('python', 'py')) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $pythonCmd = $cand; break }
}
if (-not $pythonCmd) {
    Write-Host "[오류] python 또는 py 명령을 찾을 수 없습니다. Python이 설치돼 있는지 확인해주세요." -ForegroundColor Red
    Read-Host "종료하려면 Enter"
    exit 1
}

Write-Host ""
Write-Host "-- 1) 워드 -> 기업·분기별 분리 --" -ForegroundColor Cyan
$env:PYTHONIOENCODING = 'utf-8'
& $pythonCmd scripts\extract_concall_transcripts.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[오류] 분리가 실패했습니다(위 로그 참고). 커밋 없이 종료합니다." -ForegroundColor Red
    Read-Host "종료하려면 Enter"
    exit 1
}

Write-Host ""
Write-Host "-- 2) 변경 내용 요약 --" -ForegroundColor Cyan
git add -N -- concall_transcripts 2>$null  # 새 파일도 diff 요약에 보이도록(내용은 아직 스테이징 안 함)
$diffStat = git diff --stat -- concall_transcripts
if (-not $diffStat) {
    Write-Host "변경 사항이 없습니다(워드 파일 내용이 이미 모두 반영돼 있음). 종료합니다." -ForegroundColor Yellow
    Read-Host "종료하려면 Enter"
    exit 0
}
Write-Host $diffStat
Write-Host ""

$answer = Read-Host "위 변경사항을 커밋하고 GitHub에 푸시할까요? (Y/N)"
if ($answer -notmatch '^[Yy]') {
    Write-Host "커밋하지 않고 종료합니다 — concall_transcripts/는 수정된 채로 남아있습니다." -ForegroundColor Yellow
    Read-Host "종료하려면 Enter"
    exit 0
}

Write-Host ""
Write-Host "-- 3) 커밋 + 푸시 --" -ForegroundColor Cyan
$dateStr = Get-Date -Format 'yyyy-MM-dd'
git add -- concall_transcripts
git commit -m "chore: 컨콜 스크립트 원문 반영 ($dateStr)"
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
Write-Host "완료 — 사이트 반영까지 1~2분 걸릴 수 있습니다." -ForegroundColor Green
Read-Host "종료하려면 Enter"
