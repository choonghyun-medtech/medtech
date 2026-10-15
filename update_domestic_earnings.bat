@echo off
REM 국내 실적발표 일정 갱신 - 더블클릭용 실행기.
REM 실제 로직은 scripts\update_domestic_earnings.ps1에 있다. 글로벌 대시보드 갱신 bat과 같은 방식.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_domestic_earnings.ps1"
