@echo off
REM 컨콜 스크립트 원문 반영 - 더블클릭용 실행기.
REM 실제 로직은 scripts\update_concall_transcripts.ps1에 있다 - update_global_dashboard.bat과 같은 방식.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_concall_transcripts.ps1"
