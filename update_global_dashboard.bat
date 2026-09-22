@echo off
REM 글로벌 대시보드 갱신 - 더블클릭용 실행기.
REM 실제 로직은 scripts\update_global_dashboard.ps1에 있다.
REM 배치 파일은 실행 정책 제약 없이 항상 더블클릭으로 실행되지만, PowerShell 스크립트는
REM 기본 실행 정책상 더블클릭만으로는 안 돌아가는 경우가 있어 -ExecutionPolicy Bypass로 감싼다.
REM 2026-09-23: 위 REM 줄에 걸쳐있던 괄호 하나가 cmd.exe에서 "여러 줄 명령 그룹"으로
REM 오인돼 매 실행마다 "명령을 찾을 수 없음" 에러 2줄이 뜨는 버그가 있었다(동작엔 지장
REM 없었지만 출력이 지저분했음) - 괄호를 한 줄 안에서 끝내거나 없애 해결.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_global_dashboard.ps1"
