# -*- coding: utf-8 -*-
"""
生成可上传 GitHub 的发布包  |  build a clean release archive

用法:
    python make_release.py            # 生成 dist/vixvxn-release.zip
    python make_release.py --with-data  # 把 data/ 一起打包（镜像自带数据，冷启动秒开）

排除内容：缓存、日志、临时文件、本地专用脚本。
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, "dist")

# 必须包含的文件（相对于项目根）
INCLUDE_FILES = [
    "app.py",
    "fetch_data.py",
    "cron_update.py",
    "gunicorn.conf.py",
    "setup_task.py",
    "requirements.txt",
    "Dockerfile",
    "docker-compose.yml",
    "render.yaml",
    "Procfile",
    "start.bat",
    "README.md",
    "DEPLOY.md",
    "screenshot.png",
    ".gitignore",
    ".dockerignore",
    ".github/workflows/daily-update.yml",
    "templates/index.html",
]

# 必须包含的目录
INCLUDE_DIRS = ["templates", ".github"]

# 明确排除（即使匹配目录规则也不要）
EXCLUDE_NAMES = {"__pycache__", ".git", "dist", ".venv", "venv", "env"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".tmp", ".log")


def iter_entries(with_data: bool):
    """产出 (绝对路径, 压缩包内相对路径)。"""
    root_name = "vixvxn"
    seen = set()

    for rel in INCLUDE_FILES:
        src = os.path.join(BASE_DIR, rel.replace("/", os.sep))
        if os.path.isfile(src):
            arc = f"{root_name}/{rel.replace(os.sep, '/')}"
            if arc not in seen:
                seen.add(arc)
                yield src, arc
        else:
            print(f"  [警告] 缺少文件: {rel}")

    if with_data:
        data_dir = os.path.join(BASE_DIR, "data")
        if os.path.isdir(data_dir):
            for name in sorted(os.listdir(data_dir)):
                src = os.path.join(data_dir, name)
                if os.path.isfile(src) and not name.endswith(EXCLUDE_SUFFIX):
                    arc = f"{root_name}/data/{name}"
                    if arc not in seen:
                        seen.add(arc)
                        yield src, arc


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 GitHub 发布包")
    ap.add_argument("--with-data", action="store_true",
                    help="把 data/ 一起打包（镜像自带数据，云上冷启动可秒开）")
    ap.add_argument("-o", "--out", help="输出路径（默认 dist/vixvxn-release.zip）")
    args = ap.parse_args()

    os.makedirs(DIST_DIR, exist_ok=True)
    out = args.out or os.path.join(
        DIST_DIR, "vixvxn-release-with-data.zip" if args.with_data
        else "vixvxn-release.zip")

    entries = list(iter_entries(args.with_data))
    if not entries:
        print("没有可打包的文件")
        return 1

    print(f"打包 {len(entries)} 个文件 -> {out}")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for src, arc in entries:
            z.write(src, arc)

    size = os.path.getsize(out)
    print(f"完成: {size/1024:.1f} KB")
    print()
    print("压缩包内容:")
    with zipfile.ZipFile(out) as z:
        for n in sorted(z.namelist()):
            info = z.getinfo(n)
            print(f"  {n:<52} {info.file_size/1024:8.1f} KB")
    print()
    print(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if not args.with_data:
        print("提示: 未含 data/。云端首次启动会自动抓取（约 90 秒）。")
        print("      想冷启动秒开，加 --with-data 重新生成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
