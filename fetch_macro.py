# -*- coding: utf-8 -*-
"""
宏观经济指标抓取  |  China macro indicators (CPI / PPI / PMI / GDP)

数据源 (source)：东方财富数据中心，均为官方统计口径转引。

字段含义（已逐项核对官方公布值验证）：
  CPI  RPT_ECONOMY_CPI
       NATIONAL_SAME       全国同比 (%)
       NATIONAL_SEQUENTIAL 全国环比 (%)
       CITY_SAME / RURAL_SAME  城市 / 农村同比 (%)
  PPI  RPT_ECONOMY_PPI
       BASE_SAME           同比 (%)
       BASE_ACCUMULATE     累计同比 (%)
       BASE                定基指数（上年同月=100）
  PMI  RPT_ECONOMY_PMI
       MAKE_INDEX          制造业 PMI（50 为荣枯线）
       NMAKE_INDEX         非制造业商务活动指数
  GDP  RPT_ECONOMY_GDP
       DOMESTICL_PRODUCT_BASE  累计 GDP（亿元）
       SUM_SAME            累计同比 (%)
       FIRST/SECOND/THIRD_SAME 三次产业累计同比 (%)

存储 (storage)：data/macro.json
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta

import fetch_data as fd

CST = timezone(timedelta(hours=8))

EM_API = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
          "reportName={rn}&columns=ALL&pageNumber=1&pageSize=1000"
          "&sortColumns=REPORT_DATE&sortTypes=-1")

# 指标定义：(键, 报表名, 中文名, 英文名, 数值字段映射)
INDICATORS = {
    "CPI": {
        "report": "RPT_ECONOMY_CPI",
        "name_zh": "居民消费价格指数", "name_en": "Consumer Price Index",
        "unit": "%", "freq": "月",
        "series": [
            ("yoy", "NATIONAL_SAME", "全国同比", "%"),
            ("mom", "NATIONAL_SEQUENTIAL", "全国环比", "%"),
            ("city_yoy", "CITY_SAME", "城市同比", "%"),
            ("rural_yoy", "RURAL_SAME", "农村同比", "%"),
        ],
    },
    "PPI": {
        "report": "RPT_ECONOMY_PPI",
        "name_zh": "工业生产者出厂价格指数", "name_en": "Producer Price Index",
        "unit": "%", "freq": "月",
        "series": [
            ("yoy", "BASE_SAME", "同比", "%"),
            # 注意：BASE_ACCUMULATE 是「指数」（上年同期=100），不是百分比。
            # 直接当 % 显示会误导（会看到 102.0% 与 3.8% 并列）。
            # 这里统一换算成百分比：指数 − 100。
            ("acc", "BASE_ACCUMULATE", "累计同比", "%", "minus100"),
        ],
    },
    "PMI": {
        "report": "RPT_ECONOMY_PMI",
        "name_zh": "采购经理指数", "name_en": "Purchasing Managers' Index",
        "unit": "", "freq": "月",
        "series": [
            ("mfg", "MAKE_INDEX", "制造业", ""),
            ("nonmfg", "NMAKE_INDEX", "非制造业", ""),
        ],
    },
    "GDP": {
        "report": "RPT_ECONOMY_GDP",
        "name_zh": "国内生产总值", "name_en": "Gross Domestic Product",
        "unit": "亿元", "freq": "季",
        "series": [
            ("yoy", "SUM_SAME", "累计同比", "%"),
            ("primary", "FIRST_SAME", "第一产业", "%"),
            ("secondary", "SECOND_SAME", "第二产业", "%"),
            ("tertiary", "THIRD_SAME", "第三产业", "%"),
        ],
    },
}


def _macro_path() -> str:
    return os.path.join(fd.DATA_DIR, "macro.json")


def _num(v):
    """转 float；空值返回 None。"""
    if v is None:
        return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _to_pct(v, mode: str | None):
    """
    数值规范化。

    mode == 'minus100'：源字段是「指数」（上年同期=100），换算成百分比（指数 − 100）。
    其他：原样返回。
    """
    n = _num(v)
    if n is None:
        return None
    if mode == "minus100":
        return round(n - 100.0, 4)
    return n


def fetch_indicator(key: str) -> dict:
    """抓取单个宏观指标的全部历史。"""
    spec = INDICATORS[key]
    raw = fd._http_get(EM_API.format(rn=spec["report"]),
                       referer="https://data.eastmoney.com/")
    payload = json.loads(raw.decode("utf-8", "replace"))
    rows = (payload.get("result") or {}).get("data") or []
    if not rows:
        raise ValueError(f"{key} 未返回数据（报表 {spec['report']}）")

    # 系列定义统一展开为 (key, field, label, unit, mode)
    defs = []
    for item in spec["series"]:
        sk, field, label, unit = item[0], item[1], item[2], item[3]
        mode = item[4] if len(item) > 4 else None
        defs.append((sk, field, label, unit, mode))

    periods: list[str] = []
    data: dict[str, list] = {d[0]: [] for d in defs}

    for r in rows:
        dt = str(r.get("REPORT_DATE") or "")[:10]     # YYYY-MM-DD
        if len(dt) != 10:
            continue
        periods.append(dt)
        for skey, field, _, _, mode in defs:
            data[skey].append(_to_pct(r.get(field), mode))

    # 接口按时间倒序返回，这里改为正序（旧 → 新）
    order = sorted(range(len(periods)), key=lambda i: periods[i])
    periods = [periods[i] for i in order]
    for k in data:
        data[k] = [data[k][i] for i in order]

    # 去重（同一期只保留一条）
    seen: dict[str, int] = {}
    for i, p in enumerate(periods):
        seen[p] = i
    keep = [seen[p] for p in sorted(seen)]
    periods = [periods[i] for i in keep]
    for k in data:
        data[k] = [data[k][i] for i in keep]

    latest = {d[0]: (data[d[0]][-1] if data[d[0]] else None) for d in defs}

    return {
        "key": key,
        "name_zh": spec["name_zh"],
        "name_en": spec["name_en"],
        "unit": spec["unit"],
        "freq": spec["freq"],
        "periods": periods,
        "series": [{"key": sk, "label": lb, "unit": un, "values": data[sk]}
                   for sk, _, lb, un, _ in defs],
        "latest": latest,
        "latest_period": periods[-1] if periods else None,
        "count": len(periods),
        "source": "东方财富（国家统计局口径）",
    }


def fetch_all_macro() -> dict:
    """抓取全部宏观指标，单个失败不影响其他。"""
    out: dict = {}
    for key in INDICATORS:
        try:
            out[key] = fetch_indicator(key)
            fd.log(f"宏观 {key}: 成功 {out[key]['count']} 期，"
                   f"最新 {out[key]['latest_period']}")
        except Exception as exc:  # noqa: BLE001
            fd.log(f"宏观 {key}: 失败 {exc}")
            out[key] = {"key": key, "error": str(exc)}
    return out


def load_macro() -> dict:
    try:
        with open(_macro_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def save_macro(payload: dict) -> None:
    path = _macro_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def update_macro() -> dict:
    """抓取并保存全部宏观指标。"""
    started = datetime.now(CST)
    fresh = fetch_all_macro()
    old = load_macro()

    # 合并：抓取失败的指标保留上一次的旧数据，避免页面上出现空洞
    merged: dict = {}
    for key in INDICATORS:
        new = fresh.get(key) or {}
        if new.get("error") and old.get(key) and not old[key].get("error"):
            fd.log(f"宏观 {key}: 本次抓取失败，保留上次缓存")
            merged[key] = old[key]
        else:
            merged[key] = new

    payload = {
        "updated_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "indicators": merged,
    }
    save_macro(payload)

    ok = sum(1 for k in INDICATORS if not merged[k].get("error"))
    fd.log(f"宏观数据更新完成：{ok}/{len(INDICATORS)} 个指标成功，"
           f"用时 {(datetime.now(CST)-started).total_seconds():.1f}s")
    return payload


if __name__ == "__main__":
    update_macro()
