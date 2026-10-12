#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
定时更新入口  |  scheduled-update entrypoint for cloud cron jobs

云上为什么需要它：
  PaaS 免费套餐的 Web 实例会休眠，进程内定时器不可靠。
  因此把「抓取」交给平台自带的定时任务（Render Cron / Railway Cron /
  GitHub Actions / crontab），它们独立于 Web 实例运行。

三种用法 (three ways)：

  1) 直接抓取（容器内执行，写同一个 DATA_DIR）
     python cron_update.py

  2) 远程触发（Web 实例有独立文件系统时，调它的接口）
     python cron_update.py --url https://your-app.onrender.com --token SECRET

  3) 保活 + 触发（免费实例休眠时，先唤醒再触发）
     python cron_update.py --url https://your-app.onrender.com --wake

退出码 (exit codes)：0 成功；1 失败（便于 cron 平台告警）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def _get(url: str, timeout: int = 60) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "vixvxn-cron/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def _post(url: str, timeout: int = 180) -> tuple[int, str]:
    req = urllib.request.Request(url, method="POST",
                                 headers={"User-Agent": "vixvxn-cron/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def remote_update(base: str, token: str | None, wake: bool) -> int:
    base = base.rstrip("/")

    # 1) 保活：免费实例休眠时，先访问首页把它唤醒（冷启动可能 30–60 秒）
    if wake:
        print(f"[1/3] 唤醒实例 {base}/healthz …")
        for i in range(12):
            code, body = _get(base + "/healthz", timeout=30)
            if code == 200:
                print(f"      已就绪 (第 {i+1} 次尝试)")
                break
            print(f"      等待中… (第 {i+1} 次, code={code})")
            time.sleep(10)
        else:
            print("      唤醒失败：实例长时间无响应")
            return 1

    # 2) 检查当前数据新鲜度
    code, body = _get(base + "/healthz", timeout=30)
    if code == 200:
        try:
            info = json.loads(body)
            print(f"[2/3] 当前状态: has_data={info.get('has_data')} "
                  f"last_update={info.get('last_update')}")
        except Exception:  # noqa: BLE001
            pass

    # 3) 触发更新
    url = base + "/api/update" + (f"?token={token}" if token else "")
    print(f"[3/3] 触发更新 {base}/api/update")
    code, body = _post(url, timeout=180)
    print(f"      响应 {code}: {body[:200]}")
    return 0 if code in (200, 202) else 1


def local_update(full: bool) -> int:
    print(f"本地抓取中（DATA_DIR={os.environ.get('DATA_DIR') or '(默认 ./data)'}）…")
    import fetch_data as fd
    meta = fd.update_all(full=full)
    ok = bool(meta.get("success"))
    print(f"完成: success={ok} 用时 {meta.get('duration_sec')}s")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="定时更新（云 cron 入口）")
    ap.add_argument("--url", help="Web 实例地址，如 https://xxx.onrender.com")
    ap.add_argument("--token", default=os.environ.get("ADMIN_TOKEN"),
                    help="与 Web 服务一致的 ADMIN_TOKEN")
    ap.add_argument("--wake", action="store_true",
                    help="先唤醒休眠实例（免费套餐建议开启）")
    ap.add_argument("--full", action="store_true", help="本地模式下全量重抓")
    args = ap.parse_args()

    if args.url:
        return remote_update(args.url, args.token, args.wake)
    return local_update(args.full)


if __name__ == "__main__":
    sys.exit(main())
