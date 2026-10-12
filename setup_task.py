# -*- coding: utf-8 -*-
"""
注册 / 卸载 Windows 每日计划任务  |  Register / remove the daily Windows scheduled task

用法 (usage):
    python setup_task.py            # 注册每天 06:10 自动更新
    python setup_task.py --time 07:30
    python setup_task.py --remove   # 删除任务
    python setup_task.py --run      # 立即运行一次以验证

任务名 (task name): VIXVXN_DailyUpdate
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

TASK_NAME = "VIXVXN_DailyUpdate"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(BASE_DIR, "fetch_data.py")


def run_ps(cmd: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def register(hhmm: str) -> int:
    python = sys.executable
    # 用 PowerShell 的 ScheduledTasks 模块注册，避免依赖 schtasks 的本地化参数
    ps = f"""
$ErrorActionPreference = 'Stop'
$action  = New-ScheduledTaskAction -Execute '{python}' -Argument '"{SCRIPT}"' -WorkingDirectory '{BASE_DIR}'
$trigger = New-ScheduledTaskTrigger -Daily -At '{hhmm}'
$set     = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
if (Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue) {{
    Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false
}}
Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger $trigger -Settings $set -Description 'VIX/VXN 波动率指数每日自动更新 (CBOE 官方数据源)' | Out-Null
$t = Get-ScheduledTask -TaskName '{TASK_NAME}'
"OK name=$($t.TaskName) state=$($t.State)"
$i = Get-ScheduledTaskInfo -TaskName '{TASK_NAME}'
"OK nextrun=$($i.NextRunTime)"
"""
    code, out = run_ps(ps)
    print(out.strip())
    return code


def remove() -> int:
    ps = f"""
$ErrorActionPreference='Stop'
if (Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue) {{
  Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false
  "OK removed"
}} else {{ "OK not-found" }}
"""
    code, out = run_ps(ps)
    print(out.strip())
    return code


def run_now() -> int:
    ps = f"""
$ErrorActionPreference='Stop'
Start-ScheduledTask -TaskName '{TASK_NAME}'
"OK started"
"""
    code, out = run_ps(ps)
    print(out.strip())
    return code


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="注册 VIX/VXN 每日自动更新计划任务")
    ap.add_argument("--time", default="06:10", help="每日运行时刻，24 小时制 HH:MM（默认 06:10）")
    ap.add_argument("--remove", action="store_true", help="删除计划任务")
    ap.add_argument("--run", action="store_true", help="立即运行一次任务")
    args = ap.parse_args()

    if args.remove:
        sys.exit(remove())
    if args.run:
        sys.exit(run_now())
    sys.exit(register(args.time))
