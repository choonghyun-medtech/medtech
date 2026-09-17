@echo off
REM 글로벌 대시보드 갱신 - 더블클릭용 실행기.
REM 실제 로직은 scripts\update_global_dashboard.ps1에 있다(배치 파일은 실행 정책
REM 제약 없이 항상 더블클릭으로 실행되지만, PowerShell 스크립트는 기본 실행 정책상
REM 더블클릭만으로는 안 돌아가는 경우가 있어 -ExecutionPolicy Bypass로 감싼다).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_global_dashboard.ps1"
