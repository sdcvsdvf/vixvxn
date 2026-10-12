@echo off
chcp 65001 >nul
title VIX / VXN 每日更新网站
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" (
    set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
  )
)
if not defined PY (
  echo [错误] 未找到 Python，请先安装 Python 3.10+ 或修改本脚本。
  pause
  exit /b 1
)

echo ============================================
echo   VIX / VXN 每日自动更新网站
echo   http://127.0.0.1:8801
echo   关闭本窗口即停止网站服务
echo ============================================
echo.

start "" http://127.0.0.1:8801
"%PY%" app.py --port 8801
pause
