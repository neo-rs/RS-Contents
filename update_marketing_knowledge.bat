@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo === Deploy Marketing Knowledge Base to Oracle ===
echo.
echo Git-based deploy (after push_rsbots_py_only.bat):
echo   py -3 scripts\run_oracle_deploy_marketing_knowledge.py
echo.
echo Local workspace upload (no git push required):
echo   py -3 scripts\run_oracle_deploy_marketing_knowledge.py --from-local
echo.

if /I "%~1"=="--from-local" (
  py -3 scripts\run_oracle_deploy_marketing_knowledge.py --from-local %2 %3 %4 %5 %6 %7 %8 %9
) else if /I "%~1"=="local" (
  py -3 scripts\run_oracle_deploy_marketing_knowledge.py --from-local %2 %3 %4 %5 %6 %7 %8 %9
) else (
  py -3 scripts\run_oracle_deploy_marketing_knowledge.py %*
)
set "EC=%ERRORLEVEL%"

echo.
pause
exit /b %EC%
