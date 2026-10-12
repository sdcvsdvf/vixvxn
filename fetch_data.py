# -*- coding: utf-8 -*-
"""
多市场数据抓取与本地存储
Data fetching & local storage: volatility indices, equity indices, China money supply

数据源 (Data sources, all official):
  波动率 VIX / VXN
    历史 : https://cdn.cboe.com/api/global/us_indices/daily_prices/{SYM}_History.csv
    报价 : https://cdn.cboe.com/api/global/delayed_quotes/quotes/_{SYM}.json
  美股指数 SPX / NDX
    SPX 历史 : CBOE 官方 CSV（仅收盘价）
    NDX 历史 : https://api.nasdaq.com/api/quote/NDX/historical
    报价     : CBOE 延时报价接口
  中国指数 上证指数 / 沪深300 / 创业板指
    历史与报价 : 东方财富 K 线接口 push2his.eastmoney.com（盘中为实时价）
  货币供应 M0 / M1 / M2
    月度数据 : 东方财富数据中心 RPT_ECONOMY_CURRENCY_SUPPLY
               FREE_CASH=M0, CURRENCY=M1, BASIC_CURRENCY=M2（单位：亿元）

存储 (Storage):
  data/{SYM}_daily.csv    全量日线历史 (full daily history)
  data/{SYM}_latest.json  最新报价快照 (latest snapshot)
  data/money_supply.csv   月度货币供应 (monthly money supply)
  data/meta.json          抓取元信息 (fetch metadata)
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------- 配置 / config
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 数据目录可通过环境变量覆盖：
#   本地默认 ./data ；云上常指向挂载的持久磁盘（例如 DATA_DIR=/data）。
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE_DIR, "data")
LOG_DIR = os.environ.get("LOG_DIR") or os.path.join(BASE_DIR, "logs")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# 全部标的，按展示顺序（分组见 SPECS 的 group 字段）
SYMBOLS = ("VIX", "VXN", "SPX", "NDX", "SHCOMP", "CSI300", "CHINEXT")

GROUPS = ("vol", "us", "cn")
GROUP_NAMES = {
    "vol": {"zh": "波动率指数", "en": "Volatility Indices"},
    "us":  {"zh": "美国指数",   "en": "US Indices"},
    "cn":  {"zh": "中国指数",   "en": "China Indices"},
}

SPECS = {
    "VIX": {"kind": "volatility", "group": "vol", "history": "cboe", "quote": "cboe",
            "name_zh": "标普500波动率", "name_en": "S&P 500 Volatility"},
    "VXN": {"kind": "volatility", "group": "vol", "history": "cboe", "quote": "cboe",
            "name_zh": "纳斯达克100波动率", "name_en": "Nasdaq-100 Volatility"},
    "SPX": {"kind": "equity", "group": "us", "history": "cboe", "quote": "cboe",
            "name_zh": "标普500指数", "name_en": "S&P 500 Index"},
    "NDX": {"kind": "equity", "group": "us", "history": "nasdaq", "quote": "cboe",
            "name_zh": "纳斯达克100指数", "name_en": "Nasdaq-100 Index"},
    "SHCOMP": {"kind": "equity", "group": "cn", "history": "eastmoney", "quote": "derived",
               "secid": "1.000001", "sina": "sh000001",
               "name_zh": "上证指数", "name_en": "SSE Composite"},
    "CSI300": {"kind": "equity", "group": "cn", "history": "eastmoney", "quote": "derived",
               "secid": "1.000300", "sina": "sh000300",
               "name_zh": "沪深300", "name_en": "CSI 300"},
    "CHINEXT": {"kind": "equity", "group": "cn", "history": "eastmoney", "quote": "derived",
                "secid": "0.399006", "sina": "sz399006",
                "name_zh": "创业板指", "name_en": "ChiNext"},
}

CBOE_HISTORY = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{sym}_History.csv"
CBOE_QUOTE = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_{sym}.json"
NASDAQ_HISTORY = ("https://api.nasdaq.com/api/quote/NDX/historical"
                  "?assetclass=index&fromdate={frm}&todate={to}&limit=20000")
EM_KLINE = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?"
            "secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
            "&klt=101&fqt=1&beg=0&end=20500101&lmt=1000000")
# 备用源：新浪财经（东方财富偶发限流时使用），返回同样长度的历史
SINA_KLINE = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen=100000")
EM_MONEY = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
            "reportName=RPT_ECONOMY_CURRENCY_SUPPLY&columns=ALL"
            "&pageNumber={page}&pageSize=500&sortColumns=REPORT_DATE&sortTypes=-1")
# 世界银行年度广义货币（现价本币）——用于把 M2 历史向前延伸到 1977 年。
# 注意：世界银行口径为「年度值/年均」，与央行月末余额存在约 1.7~2% 差异（见 README）。
WORLD_BANK_M2 = ("https://api.worldbank.org/v2/country/CHN/indicator/"
                 "FM.LBL.BMNY.CN?format=json&per_page=200")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

CST = timezone(timedelta(hours=8))   # 北京时间
TIMEOUT = 30
RETRIES = 3

MONEY_FIELDS = ["date", "m0", "m1", "m2",
                "m0_yoy", "m1_yoy", "m2_yoy",
                "m0_mom", "m1_mom", "m2_mom"]


# ---------------------------------------------------------------- 工具 / helpers
def _http_get(url: str, referer: str | None = None, retries: int | None = None) -> bytes:
    """带重试的 GET 请求 (GET with retries)."""
    attempts = retries if retries is not None else RETRIES
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            headers = {"User-Agent": UA,
                       "Accept": "application/json, text/csv, */*",
                       "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
            if referer:
                headers["Referer"] = referer
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < attempts:
                import time
                time.sleep(3 * attempt)
    raise RuntimeError(f"请求失败 {url}: {last_err}") from last_err


def _now_cst() -> datetime:
    return datetime.now(CST)


def log(msg: str) -> None:
    """同时输出到控制台与日志文件 (print + append to log file)."""
    stamp = _now_cst().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        with open(os.path.join(LOG_DIR, "fetch.log"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def _csv_path(sym: str) -> str:
    return os.path.join(DATA_DIR, f"{sym}_daily.csv")


def _json_path(sym: str) -> str:
    return os.path.join(DATA_DIR, f"{sym}_latest.json")


def _money_path() -> str:
    return os.path.join(DATA_DIR, "money_supply.csv")


def _wb_path() -> str:
    return os.path.join(DATA_DIR, "worldbank_m2_annual.csv")


def _num(raw) -> float | None:
    """把 '30,883.15' / '--' / '' 之类的值转成 float 或 None。"""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace("%", "")
    if s in ("", "--", "N/A", "n/a", "null", "None"):
        return None
    try:
        return round(float(s), 4)
    except ValueError:
        return None


def _to_iso(raw: str) -> str | None:
    """兼容 MM/DD/YYYY 与 YYYY-MM-DD 两种日期格式。"""
    raw = (raw or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _atomic_write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 解析 / parsers
def parse_cboe_history(raw: bytes) -> list[dict]:
    """
    解析 CBOE 历史 CSV。两种格式都支持：
      DATE,OPEN,HIGH,LOW,CLOSE   (VIX / VXN)
      DATE,SPX                   (SPX —— 只有收盘价)
    """
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    fields = [(f or "").strip() for f in (reader.fieldnames or [])]
    upper = {f.upper(): f for f in fields}

    d_key = upper.get("DATE")
    if not d_key:
        raise ValueError("历史 CSV 缺少 DATE 列")

    o_key, h_key = upper.get("OPEN"), upper.get("HIGH")
    l_key, c_key = upper.get("LOW"), upper.get("CLOSE")
    if c_key is None:
        # 只有 DATE + 一个数值列（如 SPX），把该列当作收盘价
        others = [f for f in fields if f.upper() != "DATE"]
        if not others:
            raise ValueError("历史 CSV 没有数值列")
        c_key = others[0]

    rows: list[dict] = []
    for rec in reader:
        iso = _to_iso(rec.get(d_key) or "")
        if not iso:
            continue
        close = _num(rec.get(c_key))
        if close is None:
            continue
        rows.append({
            "date": iso,
            "open": _num(rec.get(o_key)) if o_key else None,
            "high": _num(rec.get(h_key)) if h_key else None,
            "low": _num(rec.get(l_key)) if l_key else None,
            "close": close,
        })
    return _dedup_sorted(rows)


def parse_nasdaq_history(raw: bytes) -> list[dict]:
    """
    解析纳斯达克官方 API 的历史数据（倒序返回，含千分位逗号）。
    注意：早期数据与部分日期的 open/high/low 会返回 0.0 占位，这里统一转成 None，
    避免污染 CSV；收盘价永远保留（收盘价不可能为 0）。
    """
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    table = ((payload.get("data") or {}).get("tradesTable") or {})

    def _pos(v):
        """价格类字段：0 或负数视为缺失。"""
        n = _num(v)
        return None if (n is None or n <= 0) else n

    out: list[dict] = []
    for rec in (table.get("rows") or []):
        iso = _to_iso(rec.get("date") or "")
        if not iso:
            continue
        close = _pos(rec.get("close"))
        if close is None:
            continue
        out.append({
            "date": iso,
            "open": _pos(rec.get("open")),
            "high": _pos(rec.get("high")),
            "low": _pos(rec.get("low")),
            "close": close,
        })
    if not out:
        raise ValueError("纳斯达克 API 未返回历史数据")
    return _dedup_sorted(out)


def parse_eastmoney_kline(raw: bytes) -> list[dict]:
    """
    解析东方财富 K 线。
    字段顺序：f51 日期, f52 开盘, f53 收盘, f54 最高, f55 最低, f56 成交量…
    返回后按日期升序，并校验 high/low 与 open/close 的包含关系。
    """
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    if not klines:
        raise ValueError(f"东方财富未返回 K 线数据 ({data.get('name') or '?'})")

    out: list[dict] = []
    bad = 0
    for line in klines:
        p = str(line).split(",")
        if len(p) < 5:
            continue
        iso = _to_iso(p[0])
        if not iso:
            continue
        o, c, h, l = _num(p[1]), _num(p[2]), _num(p[3]), _num(p[4])
        if c is None:
            continue
        # 自检：最高价必须 ≥ 开/收，最低价必须 ≤ 开/收
        if h is not None and o is not None and c is not None and h < max(o, c) - 1e-6:
            bad += 1
            continue
        if l is not None and o is not None and c is not None and l > min(o, c) + 1e-6:
            bad += 1
            continue
        out.append({"date": iso, "open": o, "high": h, "low": l, "close": c})

    if bad:
        log(f"  东方财富 K 线：跳过 {bad} 条高低价异常的记录")
    if not out:
        raise ValueError("东方财富 K 线解析后为空")
    return _dedup_sorted(out)


# 东方财富对连续请求较敏感：实测间隔过短会直接断开连接（Remote end closed
# connection without response）。这里串行节流 + 抖动，并允许多次重试。
_EM_MIN_INTERVAL = 5.0
_em_last_call = [0.0]


def _em_throttle() -> None:
    """东方财富请求节流：确保两次请求之间至少间隔 _EM_MIN_INTERVAL 秒（含抖动）。"""
    import random
    import time
    gap = time.time() - _em_last_call[0]
    need = _EM_MIN_INTERVAL + random.uniform(0, 1.5)
    if gap < need:
        time.sleep(need - gap)
    _em_last_call[0] = time.time()


def parse_sina_kline(raw: bytes) -> list[dict]:
    """
    解析新浪财经 K 线（备用源）。
    返回 [{day, open, high, low, close, volume}]，日期升序。
    注意：新浪对早期数据偶尔返回 open/high/low = 0，这里统一转成 None。
    """
    text = raw.decode("utf-8", errors="replace").strip()
    if not text or not text.startswith("["):
        raise ValueError("新浪返回内容不是 JSON 数组")
    arr = json.loads(text)
    if not isinstance(arr, list) or not arr:
        raise ValueError("新浪未返回 K 线数据")

    def _pos(v):
        n = _num(v)
        return None if (n is None or n <= 0) else n

    out: list[dict] = []
    for rec in arr:
        if not isinstance(rec, dict):
            continue
        iso = _to_iso(str(rec.get("day") or ""))
        if not iso:
            continue
        close = _pos(rec.get("close"))
        if close is None:
            continue
        out.append({"date": iso, "open": _pos(rec.get("open")),
                    "high": _pos(rec.get("high")), "low": _pos(rec.get("low")),
                    "close": close})
    if not out:
        raise ValueError("新浪 K 线解析后为空")
    return _dedup_sorted(out)


def _dedup_sorted(rows: list[dict]) -> list[dict]:
    """按日期升序去重。"""
    dedup: dict[str, dict] = {}
    for r in rows:
        dedup[r["date"]] = r
    return [dedup[d] for d in sorted(dedup)]


# ---------------------------------------------------------------- 抓取 / fetch
def fetch_history(sym: str) -> list[dict]:
    """按标的抓取全量历史日线。"""
    spec = SPECS[sym]
    src = spec["history"]
    if src == "cboe":
        rows = parse_cboe_history(_http_get(CBOE_HISTORY.format(sym=sym)))
    elif src == "nasdaq":
        today = _now_cst().strftime("%Y-%m-%d")
        rows = parse_nasdaq_history(_http_get(NASDAQ_HISTORY.format(frm="1990-01-01", to=today)))
    elif src == "eastmoney":
        # 注意：中国指数走 _fetch_cn_kline，其备用源在「本地已是最新」时会返回空列表，
        # 这属于正常情况（无新增数据），因此这里直接返回、不做空值校验。
        return _fetch_cn_kline(sym)
    else:
        raise ValueError(f"未知数据源: {src}")
    if not rows:
        raise RuntimeError(f"{sym} 历史数据为空")
    return rows


def _fetch_cn_kline(sym: str) -> list[dict]:
    """
    抓取中国指数日线：优先东方财富，失败则自动回退到新浪财经。

    东方财富会对同一 IP 做偶发性限流（表现为连接被直接断开，且失败的标的每次不同），
    因此单纯增加重试次数并不可靠；这里改为「重试若干次 → 换备用源」。

    备用源只用于「补新」，不会覆盖已有历史：
    实测两个数据源在近年完全一致（偏差 0.000%），但在上证指数 1990 年代早期存在差异
    （最大约 8%，属早期指数基期/修订口径不同）。因此新浪返回的数据会被裁剪为
    只保留本地尚无的日期，避免用另一口径的数值改写已校验的历史。
    """
    import time
    spec = SPECS[sym]
    last_err: Exception | None = None

    # 主源：东方财富
    for attempt in range(1, 4):
        _em_throttle()
        try:
            raw = _http_get(EM_KLINE.format(secid=spec["secid"]),
                            referer="https://quote.eastmoney.com/", retries=1)
            return parse_eastmoney_kline(raw)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < 3:
                time.sleep(6 * attempt)

    # 备用源：新浪财经（仅补齐本地缺失的日期）
    code = spec.get("sina")
    if not code:
        raise RuntimeError(f"{sym} 无备用源: {last_err}")
    log(f"  {sym} 东方财富连续失败，改用新浪备用源（仅补新）…")

    local_dates = {r["date"] for r in load_history(sym)}
    newest = max(local_dates) if local_dates else None

    for attempt in range(1, 4):
        try:
            raw = _http_get(SINA_KLINE.format(code=code),
                            referer="https://finance.sina.com.cn/", retries=1)
            rows = parse_sina_kline(raw)
            if newest:
                rows = [r for r in rows if r["date"] > newest]
            if not rows:
                # 没有新日期：说明本地已是最新，视为成功（无需改写历史）
                log(f"  {sym} 备用源无新增日期，保留本地历史")
                return []
            log(f"  {sym} 已通过新浪备用源补齐 {len(rows)} 行"
                f"（{rows[0]['date']} → {rows[-1]['date']}）")
            return rows
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < 3:
                time.sleep(5 * attempt)

    raise RuntimeError(f"{sym} 主源与备用源均失败: {last_err}")


def quote_from_history(sym: str, rows: list[dict]) -> dict:
    """用最近两根日线推导报价（用于中国指数：K 线盘中即为实时价）。"""
    if not rows:
        raise ValueError(f"{sym} 无历史数据，无法推导报价")
    last = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None
    prev_close = prev["close"] if prev else None
    change = pct = None
    if prev_close:
        change = round(last["close"] - prev_close, 4)
        pct = round(change / prev_close * 100, 3)
    return {
        "symbol": sym,
        "source_timestamp": last["date"],
        "last_trade_time": last["date"],
        "price": last["close"],
        "change": change,
        "change_percent": pct,
        "open": last.get("open"),
        "high": last.get("high"),
        "low": last.get("low"),
        "prev_close": prev_close,
        "iv30": None,
    }


def fetch_quote(sym: str, rows: list[dict] | None = None) -> dict:
    """抓取最新报价快照。"""
    spec = SPECS[sym]
    if spec["quote"] == "derived":
        return quote_from_history(sym, rows if rows is not None else load_history(sym))

    raw = _http_get(CBOE_QUOTE.format(sym=sym))
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    data = payload.get("data") or {}

    def _pos(key):
        """价格类字段：0 视为缺失（CBOE 对部分指数用 0 占位）。"""
        n = _num(data.get(key))
        return None if (n is None or n <= 0) else n

    return {
        "symbol": sym,
        "source_timestamp": payload.get("timestamp"),
        "last_trade_time": data.get("last_trade_time"),
        "price": _pos("current_price"),
        "change": _num(data.get("price_change")),
        "change_percent": _num(data.get("price_change_percent")),
        "open": _pos("open"),
        "high": _pos("high"),
        "low": _pos("low"),
        "prev_close": _pos("prev_day_close"),
        "iv30": _num(data.get("iv30")),
    }


# ---------------------------------------------------------------- 货币供应 / money
def fetch_money_supply() -> list[dict]:
    """抓取月度货币供应 M0 / M1 / M2（单位：亿元）。"""
    raw_rows: list[dict] = []
    page = 1
    while True:
        _em_throttle()
        payload = json.loads(
            _http_get(EM_MONEY.format(page=page),
                      referer="https://data.eastmoney.com/").decode("utf-8", "replace"))
        res = payload.get("result") or {}
        data = res.get("data") or []
        if not data:
            break
        raw_rows.extend(data)
        pages = res.get("pages") or 1
        if page >= pages or page >= 20:
            break
        page += 1

    out: list[dict] = []
    for r in raw_rows:
        month = str(r.get("REPORT_DATE") or "")[:7]        # YYYY-MM
        if len(month) != 7 or month[4] != "-":
            continue
        rec = {
            "date": month,
            "m0": _num(r.get("FREE_CASH")),          # 流通中现金
            "m1": _num(r.get("CURRENCY")),           # 狭义货币
            "m2": _num(r.get("BASIC_CURRENCY")),     # 广义货币
            "m0_yoy": _num(r.get("FREE_CASH_SAME")),
            "m1_yoy": _num(r.get("CURRENCY_SAME")),
            "m2_yoy": _num(r.get("BASIC_CURRENCY_SAME")),
            "m0_mom": _num(r.get("FREE_CASH_SEQUENTIAL")),
            "m1_mom": _num(r.get("CURRENCY_SEQUENTIAL")),
            "m2_mom": _num(r.get("BASIC_CURRENCY_SEQUENTIAL")),
        }
        if rec["m2"] is None and rec["m1"] is None and rec["m0"] is None:
            continue
        out.append(rec)

    if not out:
        raise ValueError("货币供应数据为空")
    out.sort(key=lambda r: r["date"])
    dedup: dict[str, dict] = {}
    for r in out:
        dedup[r["date"]] = r
    return [dedup[d] for d in sorted(dedup)]


def load_money_supply() -> list[dict]:
    path = _money_path()
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for rec in csv.DictReader(fh):
            row = {"date": rec["date"]}
            for k in MONEY_FIELDS[1:]:
                row[k] = _num(rec.get(k))
            out.append(row)
    out.sort(key=lambda r: r["date"])
    return out


def save_money_supply(rows: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=MONEY_FIELDS)
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else
                        (r[k] if k == "date" else f"{r[k]:.4f}")) for k in MONEY_FIELDS})
    _atomic_write(_money_path(), buf.getvalue())


def update_money(full: bool = False) -> dict:
    """更新货币供应数据。"""
    existing = [] if full else load_money_supply()
    merged: dict[str, dict] = {r["date"]: r for r in existing}
    ok, err, added = False, None, 0
    try:
        before = len(merged)
        for r in fetch_money_supply():
            merged[r["date"]] = r
        added = len(merged) - before
        ok = True
    except Exception as exc:  # noqa: BLE001
        err = str(exc)

    rows = [merged[d] for d in sorted(merged)]
    if ok and rows:
        save_money_supply(rows)

    last = rows[-1] if rows else None
    log(f"M0/M1/M2: {'成功' if ok else '失败'} 新增{added}期 共{len(rows)}期 "
        f"最新{last['date'] if last else '—'}")
    if err:
        log(f"  货币供应错误: {err}")

    return {
        "symbol": "MONEY",
        "history_ok": ok,
        "history_error": err,
        "rows_added": added,
        "rows_total": len(rows),
        "first_date": rows[0]["date"] if rows else None,
        "last_date": last["date"] if last else None,
        "latest": last,
    }


def fetch_wb_m2() -> list[dict]:
    """
    抓取世界银行年度广义货币 M2（现价本币），换算为亿元。

    用途：把 M2 历史从央行月度数据的 2008-01 向前延伸到 1977 年
    （更早的月度 M0/M1/M2 公开数据不存在）。

    与央行口径的差异（已实测）：2008–2016 年两者完全一致（偏差 0.00%）；
    2017 年起世界银行比央行 12 月末余额低约 1.7~2.1%，因其采用年度值。
    因此前端会把该序列单独标识，不与月度值混为一条线。
    """
    payload = json.loads(_http_get(WORLD_BANK_M2).decode("utf-8", "replace"))
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        raise ValueError("世界银行返回结构异常")
    out: list[dict] = []
    for rec in payload[1]:
        v = _num(rec.get("value"))
        y = str(rec.get("date") or "").strip()
        if v is None or len(y) != 4 or not y.isdigit():
            continue
        out.append({"year": y, "m2": round(v / 1e8, 4)})   # LCU → 亿元
    if not out:
        raise ValueError("世界银行未返回 M2 数据")
    out.sort(key=lambda r: r["year"])
    return out


def load_wb_m2() -> list[dict]:
    path = _wb_path()
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for rec in csv.DictReader(fh):
            v = _num(rec.get("m2"))
            if v is not None and rec.get("year"):
                out.append({"year": rec["year"], "m2": v})
    out.sort(key=lambda r: r["year"])
    return out


def save_wb_m2(rows: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["year", "m2"])
    w.writeheader()
    for r in rows:
        w.writerow({"year": r["year"], "m2": f"{r['m2']:.4f}"})
    _atomic_write(_wb_path(), buf.getvalue())


def update_wb_m2(full: bool = False) -> dict:
    """更新世界银行年度 M2。"""
    existing = [] if full else load_wb_m2()
    merged: dict[str, dict] = {r["year"]: r for r in existing}
    ok, err, added = False, None, 0
    try:
        before = len(merged)
        for r in fetch_wb_m2():
            merged[r["year"]] = r
        added = len(merged) - before
        ok = True
    except Exception as exc:  # noqa: BLE001
        err = str(exc)

    rows = [merged[y] for y in sorted(merged)]
    if ok and rows:
        save_wb_m2(rows)

    log(f"世界银行年度M2: {'成功' if ok else '失败'} 新增{added}年 共{len(rows)}年 "
        f"{rows[0]['year'] if rows else '—'}—{rows[-1]['year'] if rows else '—'}")
    if err:
        log(f"  世界银行错误: {err}")

    return {"symbol": "WB_M2", "history_ok": ok, "history_error": err,
            "rows_added": added, "rows_total": len(rows),
            "first_date": rows[0]["year"] if rows else None,
            "last_date": rows[-1]["year"] if rows else None,
            "latest": rows[-1] if rows else None}


# ---------------------------------------------------------------- 本地读写 / io
def load_history(sym: str) -> list[dict]:
    path = _csv_path(sym)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for rec in csv.DictReader(fh):
            row = {"date": rec["date"]}
            for k in ("open", "high", "low", "close"):
                row[k] = _num(rec.get(k))
            out.append(row)
    out.sort(key=lambda r: r["date"])
    return out


def save_history(sym: str, rows: list[dict]) -> None:
    """写回本地 CSV（原子写入，避免读到半截文件）。"""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["date", "open", "high", "low", "close"])
    w.writeheader()
    for r in rows:
        w.writerow({"date": r["date"],
                    **{k: ("" if r.get(k) is None else f"{r[k]:.4f}")
                       for k in ("open", "high", "low", "close")}})
    _atomic_write(_csv_path(sym), buf.getvalue())


def save_quote(sym: str, quote: dict) -> None:
    quote = dict(quote)
    quote["fetched_at"] = _now_cst().isoformat(timespec="seconds")
    _atomic_write(_json_path(sym), json.dumps(quote, ensure_ascii=False, indent=2))


def load_quote(sym: str) -> dict | None:
    try:
        with open(_json_path(sym), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def load_meta() -> dict:
    try:
        with open(os.path.join(DATA_DIR, "meta.json"), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def save_meta(meta: dict) -> None:
    _atomic_write(os.path.join(DATA_DIR, "meta.json"),
                  json.dumps(meta, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- 主流程 / update
def update_symbol(sym: str, full: bool = False) -> dict:
    """更新单个标的：抓历史（全量或增量合并）+ 最新报价。"""
    existing = [] if full else load_history(sym)
    merged: dict[str, dict] = {r["date"]: r for r in existing}

    history_ok, history_err, added = False, None, 0
    try:
        before = len(merged)
        for r in fetch_history(sym):
            merged[r["date"]] = r
        added = len(merged) - before
        history_ok = True
    except Exception as exc:  # noqa: BLE001
        history_err = str(exc)

    if history_ok:
        rows = [merged[d] for d in sorted(merged)]
        save_history(sym, rows)
    else:
        rows = sorted(merged.values(), key=lambda r: r["date"])

    quote, quote_err = None, None
    try:
        quote = fetch_quote(sym, rows)
        save_quote(sym, quote)
    except Exception as exc:  # noqa: BLE001
        quote_err = str(exc)

    last_row = rows[-1] if rows else None
    spec = SPECS[sym]
    return {
        "symbol": sym,
        "kind": spec["kind"],
        "group": spec["group"],
        "history_ok": history_ok,
        "history_error": history_err,
        "rows_added": added,
        "rows_total": len(rows),
        "first_date": rows[0]["date"] if rows else None,
        "last_date": last_row["date"] if last_row else None,
        "last_close": last_row["close"] if last_row else None,
        "quote_ok": quote is not None,
        "quote_error": quote_err,
        "quote": quote,
    }


def update_all(full: bool = False) -> dict:
    """更新全部标的与货币供应，写入 meta.json，返回摘要。"""
    started = _now_cst()
    log(f"===== 开始更新 ({'全量' if full else '增量'}) =====")
    results = []
    for sym in SYMBOLS:
        try:
            res = update_symbol(sym, full=full)
        except Exception as exc:  # noqa: BLE001
            log(f"{sym} 更新异常: {exc}")
            res = {"symbol": sym, "kind": SPECS[sym]["kind"], "group": SPECS[sym]["group"],
                   "history_ok": False, "history_error": str(exc),
                   "quote_ok": False, "quote_error": str(exc),
                   "rows_added": 0, "rows_total": 0,
                   "first_date": None, "last_date": None, "last_close": None}
        results.append(res)
        log(f"{sym}: 历史{'成功' if res['history_ok'] else '失败'} "
            f"新增{res['rows_added']}行 共{res['rows_total']}行 "
            f"最新{res['last_date']} 收盘{res['last_close']} | "
            f"报价{'成功' if res['quote_ok'] else '失败'}")
        if res.get("history_error"):
            log(f"  {sym} 历史错误: {res['history_error']}")
        if res.get("quote_error"):
            log(f"  {sym} 报价错误: {res['quote_error']}")

    # 货币供应
    try:
        money = update_money(full=full)
    except Exception as exc:  # noqa: BLE001
        log(f"货币供应更新异常: {exc}")
        money = {"symbol": "MONEY", "history_ok": False, "history_error": str(exc),
                 "rows_added": 0, "rows_total": 0, "first_date": None,
                 "last_date": None, "latest": None}

    # 世界银行年度 M2（把 M2 历史延伸到 1977 年）
    try:
        wb_m2 = update_wb_m2(full=full)
    except Exception as exc:  # noqa: BLE001
        log(f"世界银行 M2 更新异常: {exc}")
        wb_m2 = {"symbol": "WB_M2", "history_ok": False, "history_error": str(exc),
                 "rows_added": 0, "rows_total": 0, "first_date": None,
                 "last_date": None, "latest": None}

    finished = _now_cst()
    ok = (any(r["history_ok"] or r.get("quote_ok") for r in results)
          or money["history_ok"] or wb_m2["history_ok"])
    meta = {
        "last_update": finished.isoformat(timespec="seconds"),
        "last_update_display": finished.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_sec": round((finished - started).total_seconds(), 2),
        "mode": "full" if full else "incremental",
        "success": ok,
        "results": results,
        "money": money,
        "wb_m2": wb_m2,
    }
    save_meta(meta)
    log(f"===== 更新结束 用时{meta['duration_sec']}秒 成功={ok} =====")
    return meta


if __name__ == "__main__":
    full_flag = "--full" in sys.argv or "-f" in sys.argv
    m = update_all(full=full_flag)
    sys.exit(0 if m["success"] else 1)
