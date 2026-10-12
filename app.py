# -*- coding: utf-8 -*-
"""
市场仪表盘网站  |  Market dashboard website

云 / 本地均可运行 (runs locally or in the cloud):

  本地 (local):
      python app.py                     # http://127.0.0.1:8801
      python app.py --port 9000

  生产 / 云 (production / PaaS):
      gunicorn -c gunicorn.conf.py app:app
      # 平台会注入 PORT 环境变量，本程序自动读取

环境变量 (environment variables):
      PORT              监听端口（PaaS 平台注入；默认 8801）
      HOST              监听地址（默认 127.0.0.1；容器/云需 0.0.0.0）
      DATA_DIR          数据目录（云上指向持久磁盘，如 /data）
      VIXVXN_SCHEDULER  设为 0 关闭进程内定时器（用外部 cron 触发时）
      AUTO_SEED         首次启动若无数据是否自动抓取（默认 1）
      ADMIN_TOKEN       设置后，手动更新接口需带 ?token=... 才能调用
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import threading
import time

from flask import Flask, jsonify, render_template, request

import fetch_data as fd
import fetch_macro as fm
import politburo as pb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_AS_ASCII"] = False

# 更新互斥锁：避免定时任务与手动按钮同时抓取
_update_lock = threading.Lock()
_update_state = {"running": False, "started_at": None, "trigger": None}

# 每天自动更新的时刻（北京时间，24 小时制）
DAILY_HOUR = int(os.environ.get("DAILY_HOUR", "6"))
DAILY_MINUTE = int(os.environ.get("DAILY_MINUTE", "10"))


def _env_flag(name: str, default: bool = True) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


# ---------------------------------------------------------------- 数据读取 / read
def build_payload(days: int | None = None, full: bool = False) -> dict:
    """组装页面/API 所需的数据。"""
    meta = fd.load_meta()
    out = {
        "meta": meta,
        "update_state": dict(_update_state),
        "specs": {s: {"kind": fd.SPECS[s]["kind"],
                      "group": fd.SPECS[s]["group"],
                      "name_zh": fd.SPECS[s]["name_zh"],
                      "name_en": fd.SPECS[s]["name_en"]} for s in fd.SYMBOLS},
        "groups": {g: fd.GROUP_NAMES[g] for g in fd.GROUPS},
        "order": list(fd.SYMBOLS),
        "series": {},
        "money": {},
    }
    for sym in fd.SYMBOLS:
        rows = fd.load_history(sym)
        total = len(rows)
        view = rows if (full or not days) else rows[-days:]
        quote = fd.load_quote(sym)

        dates = [r["date"] for r in view]
        closes = [r["close"] for r in view]

        # 当日涨跌：优先用报价里的 change，否则用最近两根收盘价计算
        change = change_pct = None
        if quote and quote.get("change") is not None:
            change = quote.get("change")
            change_pct = quote.get("change_percent")
        elif len(rows) >= 2 and rows[-1]["close"] is not None:
            prev, cur = rows[-2]["close"], rows[-1]["close"]
            if prev:
                change = round(cur - prev, 4)
                change_pct = round((cur - prev) / prev * 100, 3)

        last_row = rows[-1] if rows else {}
        spec = fd.SPECS[sym]
        out["series"][sym] = {
            "symbol": sym,
            "kind": spec["kind"],
            "group": spec["group"],
            "name_zh": spec["name_zh"],
            "name_en": spec["name_en"],
            "dates": dates,
            "closes": closes,
            "rows_total": total,
            "shown": len(view),
            "first_date": rows[0]["date"] if rows else None,
            "last_date": last_row.get("date"),
            "last_close": last_row.get("close"),
            "last_open": last_row.get("open"),
            "last_high": last_row.get("high"),
            "last_low": last_row.get("low"),
            "change": change,
            "change_percent": change_pct,
            "quote": quote,
        }

    # 货币供应 M0 / M1 / M2（月度，单位：亿元）
    # 始终返回全量：只有 224 期、体积极小，且前端有独立的区间选择器，
    # 不应受日线图的 days 参数限制。
    money_rows = fd.load_money_supply()
    m_last = money_rows[-1] if money_rows else None

    def _vals(key):
        return [(r.get(key) or 0) / 10000 for r in money_rows]   # 亿元 → 万亿元

    # 派生字段：同比变动（本月同比 − 上月同比）、存量（亿元）
    def _yoy(key):
        return [r.get(key) for r in money_rows]

    def _derive(yoy_key):
        out = []
        for i, r in enumerate(money_rows):
            cur = r.get(yoy_key)
            prev = money_rows[i - 1].get(yoy_key) if i > 0 else None
            out.append(None if (cur is None or prev is None) else round(cur - prev, 4))
        return out

    # 剪刀差 = M1同比 − M2同比
    scissors = []
    for r in money_rows:
        a, b = r.get("m1_yoy"), r.get("m2_yoy")
        scissors.append(None if (a is None or b is None) else round(a - b, 4))

    latest = None
    if m_last:
        prev = money_rows[-2] if len(money_rows) >= 2 else None
        latest = {
            "month": m_last["date"],
            "m2": {"value": (m_last.get("m2") or 0) / 10000,
                   "value_yi": m_last.get("m2"),
                   "yoy": m_last.get("m2_yoy"), "mom": m_last.get("m2_mom"),
                   "prev_yoy": prev.get("m2_yoy") if prev else None,
                   "yoy_delta": (None if not prev or m_last.get("m2_yoy") is None
                                 or prev.get("m2_yoy") is None
                                 else round(m_last["m2_yoy"] - prev["m2_yoy"], 4))},
            "m1": {"value": (m_last.get("m1") or 0) / 10000,
                   "value_yi": m_last.get("m1"),
                   "yoy": m_last.get("m1_yoy"), "mom": m_last.get("m1_mom"),
                   "prev_yoy": prev.get("m1_yoy") if prev else None,
                   "yoy_delta": (None if not prev or m_last.get("m1_yoy") is None
                                 or prev.get("m1_yoy") is None
                                 else round(m_last["m1_yoy"] - prev["m1_yoy"], 4))},
            "m0": {"value": (m_last.get("m0") or 0) / 10000,
                   "value_yi": m_last.get("m0"),
                   "yoy": m_last.get("m0_yoy"), "mom": m_last.get("m0_mom"),
                   "prev_yoy": prev.get("m0_yoy") if prev else None,
                   "yoy_delta": (None if not prev or m_last.get("m0_yoy") is None
                                 or prev.get("m0_yoy") is None
                                 else round(m_last["m0_yoy"] - prev["m0_yoy"], 4))},
        }

    out["money"] = {
        "months": [r["date"] for r in money_rows],
        # 存量（万亿元）
        "m0": {"value": _vals("m0"), "yoy": _yoy("m0_yoy")},
        "m1": {"value": _vals("m1"), "yoy": _yoy("m1_yoy")},
        "m2": {"value": _vals("m2"), "yoy": _yoy("m2_yoy")},
        # 同比的月度变化（pct）
        "m0_delta": _derive("m0_yoy"),
        "m1_delta": _derive("m1_yoy"),
        "m2_delta": _derive("m2_yoy"),
        "scissors": scissors,
        "latest": latest,
        "source": "东方财富",
        "unit": "万亿元",
        "rows_total": len(money_rows),
        "first_date": money_rows[0]["date"] if money_rows else None,
        "last_date": m_last["date"] if m_last else None,
    }

    # 中国指数的「月线」序列，专门用于叠加到货币供应图。
    # 不能复用 series 里的日线：日线受 days 参数裁剪（例如只取 1 年），
    # 而货币面板有独立的区间选择器（最长 224 个月），需要完整历史。
    cn_monthly = {}
    for sym in fd.SYMBOLS:
        if fd.SPECS[sym]["group"] != "cn":
            continue
        rows = fd.load_history(sym)
        by_month: dict[str, float] = {}
        for r in rows:
            c = r.get("close")
            if c is not None:
                by_month[r["date"][:7]] = c      # 月末值会覆盖月初值
        cn_monthly[sym] = {
            "name_zh": fd.SPECS[sym]["name_zh"],
            "months": sorted(by_month),
            "closes": [by_month[m] for m in sorted(by_month)],
        }
    out["cn_monthly"] = cn_monthly

    # 世界银行年度 M2（1977 起）——用于把 M2 曲线向前延伸。
    # 与央行月度序列口径不同（年度值 vs 月末余额），前端单独标识。
    wb_rows = [r for r in fd.load_wb_m2() if r["year"] < "2008"]
    if wb_rows:
        out["money"]["m2_annual"] = {
            "years": [r["year"] for r in wb_rows],
            "value": [round(r["m2"] / 10000, 4) for r in wb_rows],   # 亿元 → 万亿元
            "source": "世界银行",
            "note": "年度值，与央行月末口径相差约 1.7~2%（2017 年起）",
        }

    # 宏观经济指标 CPI / PPI / PMI / GDP（月度 / 季度，东方财富官方口径）
    out["macro"] = fm.load_macro()

    # 政治局会议纪要（人工维护 + 自动关联新闻）
    out["politburo"] = pb.load_politburo()

    return out


# ---------------------------------------------------------------- 路由 / routes
def _days_arg() -> int:
    """解析 days 参数；days=0 表示取全部历史，缺省为 365 天。"""
    raw = request.args.get("days")
    if raw is None or raw == "":
        return 365
    try:
        return max(0, int(raw))
    except ValueError:
        return 365


@app.route("/")
def index():
    days = _days_arg()
    full = request.args.get("full") == "1"
    data = build_payload(days=days, full=full)
    return render_template("index.html", data=data, initial=json.dumps(data, ensure_ascii=False))


@app.route("/api/data")
def api_data():
    days = _days_arg()
    full = request.args.get("full") == "1"
    return jsonify(build_payload(days=days, full=full))


def _run_update(trigger: str, full: bool = False):
    """在后台线程里执行更新。"""
    with _update_lock:
        _update_state.update({"running": True,
                              "started_at": dt.datetime.now(fd.CST).strftime("%Y-%m-%d %H:%M:%S"),
                              "trigger": trigger})
        try:
            fd.update_all(full=full)
        except Exception as exc:  # noqa: BLE001
            fd.log(f"更新失败: {exc}")
        finally:
            _update_state.update({"running": False, "trigger": None})
            _update_state["last_finished"] = dt.datetime.now(fd.CST).strftime("%Y-%m-%d %H:%M:%S")


@app.route("/api/update", methods=["POST"])
def api_update():
    """
    手动触发一次更新。

    安全：设置 ADMIN_TOKEN 环境变量后，必须带 ?token=xxx 才能调用，
    防止公开站点被陌生人反复触发抓取、消耗上游额度。
    """
    required = os.environ.get("ADMIN_TOKEN")
    if required and request.args.get("token") != required:
        return jsonify({"ok": False, "message": "需要有效的 token / token required"}), 403
    if _update_state["running"]:
        return jsonify({"ok": False, "message": "更新正在进行中，请稍候 / update already running"}), 409
    full = request.args.get("full") == "1"
    threading.Thread(target=_run_update, args=("manual", full), daemon=True).start()
    return jsonify({"ok": True, "message": "已开始更新 / update started"})


# ---------------------------------------------------------------- 定时器 / scheduler
def _scheduler_loop():
    """进程内每日定时更新。"""
    fd.log(f"定时任务已启动：每天 {DAILY_HOUR:02d}:{DAILY_MINUTE:02d} (北京时间) 自动更新")
    while True:
        now = dt.datetime.now(fd.CST)
        target = now.replace(hour=DAILY_HOUR, minute=DAILY_MINUTE, second=0, microsecond=0)
        if target <= now:
            target += dt.timedelta(days=1)
        wait = (target - now).total_seconds()
        fd.log(f"下次自动更新: {target.strftime('%Y-%m-%d %H:%M:%S')} (还有 {wait/3600:.1f} 小时)")
        # 分段睡眠，便于进程退出
        while wait > 0:
            time.sleep(min(wait, 300))
            wait -= 300
            if wait <= 0:
                break
        if _update_state["running"]:
            fd.log("定时更新跳过：已有更新在进行中")
            continue
        _run_update("scheduler")


def start_scheduler():
    threading.Thread(target=_scheduler_loop, daemon=True).start()


def bootstrap(seed: bool | None = None, blocking: bool = False) -> None:
    """
    首次启动准备：若数据目录为空则抓一次全量。

    **必须异步执行（默认 blocking=False）**：
    首次抓取要 60–120 秒，而云平台（Render / Railway 等）会在容器启动后立刻
    扫描监听端口；若此时阻塞在抓取上，端口长时间不开放，平台会判定启动失败
    并返回 502。因此这里默认把抓取丢到后台线程，让 Web 服务立即监听。

    页面在数据就绪前会显示空图表（前端会自动重试），抓完即正常。
    """
    if seed is None:
        seed = _env_flag("AUTO_SEED", True)
    if not seed:
        return
    if fd.load_meta():
        return

    def _seed_job():
        fd.log(f"数据目录 {fd.DATA_DIR} 为空，执行首次抓取（约 60–120 秒）…")
        try:
            _run_update("startup")
        except Exception as exc:  # noqa: BLE001
            fd.log(f"首次抓取失败（页面将显示空数据）: {exc}")

    if blocking:
        _seed_job()
        return

    # 已在抓取中（多 worker 同时加载）则不重复启动
    if _update_state.get("running"):
        fd.log("已有抓取在进行中，跳过本次启动抓取")
        return
    fd.log("首次抓取将在后台进行，Web 服务立即开始监听")
    threading.Thread(target=_seed_job, daemon=True, name="seed").start()


# ---------------------------------------------------------------- 健康检查 / health
@app.route("/healthz")
def healthz():
    """
    平台健康检查；同时暴露数据新鲜度，便于外部 cron 判断。

    始终返回 200（只要 Web 进程活着）：云平台用它判断「容器是否该重启」，
    若在首次抓取的 90 秒内返回非 200，平台会误判为启动失败而反复重启。
    数据是否就绪由 has_data 字段表达，调用方自行决定。
    """
    meta = fd.load_meta()
    return jsonify({
        "ok": True,
        "data_dir": fd.DATA_DIR,
        "has_data": bool(meta),
        "last_update": meta.get("last_update_display"),
        "updating": _update_state.get("running", False),
        "symbols": len(fd.SYMBOLS),
    }), 200


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="市场仪表盘网站")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT", "8801")))
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-scheduler", action="store_true",
                        help="不启用进程内定时更新（由外部 cron 负责时使用）")
    args = parser.parse_args()

    bootstrap()

    if not args.no_scheduler and _env_flag("VIXVXN_SCHEDULER", True):
        start_scheduler()
    else:
        fd.log("进程内定时器已关闭（由外部 cron / 平台计划任务负责更新）")

    fd.log(f"网站已启动: http://{args.host}:{args.port}  (DATA_DIR={fd.DATA_DIR})")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True,
            use_reloader=False)
else:
    # 以 WSGI 方式被 gunicorn 等加载时（app:app），也要完成初始化。
    # 注意：多 worker 下每个 worker 都会执行，靠 _update_lock 与
    # meta.json 的存在性避免重复抓取。
    bootstrap()
    if _env_flag("VIXVXN_SCHEDULER", True):
        start_scheduler()
