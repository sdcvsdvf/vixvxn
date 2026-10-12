# -*- coding: utf-8 -*-
"""
政治局会议监测  |  Politburo meeting monitor

数据源（全自动）：
    人民网搜索接口 http://search.people.cn/search-platform/front/search
    —— 可检索到新华社通稿原文与官方链接。

筛选策略（重要）：
    搜索结果会混入大量「地方传达学习」「媒体解读」类文章，必须过滤：
      保留：标题符合通稿形态（如「中共中央政治局召开会议 …」）
      排除：传达学习 / 学习贯彻 / 自治区·省市县委 / 解读 / 观察 / 述评 …
    这样得到的是会议通稿本身，而非二手解读。

议题标签：
    按参考站点的做法——**只做原文关键词匹配**，不做任何主观解读，
    页面上明确标注「依据通稿原文出现该词，非解读」。

存储：data/politburo.json
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime, timezone, timedelta

import fetch_data as fd

CST = timezone(timedelta(hours=8))
PEOPLE_SEARCH = "http://search.people.cn/search-platform/front/search"

# 保留：通稿标题形态
KEEP_PAT = [
    r"中共中央政治局召开会议",
    r"中共中央政治局.*召开会议",
    r"中央政治局.*召开会议",
]
# 排除：地方传达、媒体解读、评论
DROP_PAT = [
    r"传达学习", r"传达贯彻", r"学习贯彻", r"认真学习贯彻", r"深入学习贯彻",
    r"自治区", r"省委", r"市委", r"县委", r"党委常委会", r"党组",
    r"新闻1\+1", r"解读", r"观察", r"述评", r"评论", r"专访", r"连线",
    r"信号", r"如何看", r"怎么干", r"看下半年", r"前瞻", r"要点", r"着力点",
    r"新走向", r"钱往", r"重磅", r"梳理",
]

# 议题关键词（仅用于原文匹配，不做解读）
TOPIC_RULES = [
    ("经济议题", [r"经济形势", r"经济工作", r"经济运行", r"经济增长"]),
    ("货币政策", [r"货币政策", r"流动性", r"降准", r"降息"]),
    ("财政政策", [r"财政政策", r"专项债", r"国债", r"减税降费", r"积极财政"]),
    ("房地产",   [r"房地产", r"住房", r"楼市", r"城中村", r"保障性住房"]),
    ("资本市场", [r"资本市场", r"股市", r"上市公司", r"投资者"]),
    ("就业民生", [r"就业", r"民生", r"社会保障", r"养老", r"医疗"]),
    ("内需消费", [r"内需", r"消费", r"以旧换新", r"扩大国内需求"]),
    ("科技产业", [r"科技", r"创新", r"新质生产力", r"人工智能", r"产业链", r"制造业"]),
    ("风险化解", [r"风险", r"地方债务", r"债务", r"金融风险", r"化债", r"抢险", r"灾害", r"安全生产"]),
    ("改革开放", [r"改革", r"开放", r"营商环境", r"民营经济", r"外资", r"一带一路"]),
    ("从严治党", [r"从严治党", r"党风廉政", r"巡视", r"反腐败", r"党纪"]),
    ("生态环保", [r"生态", r"环保", r"污染", r"绿色", r"碳排放"]),
    ("农业农村", [r"农业", r"农村", r"粮食", r"乡村振兴"]),
    ("区域发展", [r"区域", r"长江", r"京津冀", r"粤港澳", r"长三角", r"西部"]),
    ("外事工作", [r"外事", r"外交", r"对外开放", r"国际合作"]),
]


def _path() -> str:
    return os.path.join(fd.DATA_DIR, "politburo.json")


def _clean(s) -> str:
    """去掉高亮标签与 HTML，合并空白。"""
    s = re.sub(r"</?em>", "", str(s or ""))
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _post_json(url: str, body: dict, referer: str, timeout: int = 40) -> dict:
    h = {"User-Agent": fd.UA, "Content-Type": "application/json",
         "Accept": "application/json, text/plain, */*",
         "Accept-Language": "zh-CN,zh;q=0.9", "Referer": referer}
    req = urllib.request.Request(url, headers=h, data=json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def topics_of(text: str) -> list[str]:
    """按原文关键词匹配议题标签（不做解读）。"""
    hits = []
    for name, pats in TOPIC_RULES:
        if any(re.search(p, text) for p in pats):
            hits.append(name)
    return hits


def _parse_date(rec: dict) -> str:
    """从多种字段解析日期。displayTime 是秒级时间戳。"""
    for key in ("displayTime", "inputTime"):
        v = rec.get(key)
        if v is None:
            continue
        try:
            ts = int(v)
            # 兼容毫秒时间戳
            if ts > 1e11:
                ts //= 1000
            return datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            pass
    # 从 URL 兜底：/n1/2026/0130/
    m = re.search(r"/(\d{4})/(\d{2})(\d{2})/", str(rec.get("url") or ""))
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def fetch_meetings(max_pages: int = 10, limit: int = 20) -> list[dict]:
    """抓取并筛选政治局会议通稿。"""
    recs: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            d = _post_json(PEOPLE_SEARCH,
                           {"key": "中共中央政治局召开会议", "page": page,
                            "limit": limit, "hasTitle": True,
                            "hasContent": True, "sortType": 2},
                           "http://search.people.cn/")
            rows = (d.get("data") or {}).get("records") or []
        except Exception as exc:  # noqa: BLE001
            fd.log(f"  政治局搜索第 {page} 页失败: {exc}")
            break
        if not rows:
            break
        recs.extend(rows)

    out: list[dict] = []
    seen: set[str] = set()
    for r in recs:
        title = _clean(r.get("title"))
        if not title or title in seen:
            continue
        if any(re.search(p, title) for p in DROP_PAT):
            continue
        if not any(re.search(p, title) for p in KEEP_PAT):
            continue
        seen.add(title)

        content = _clean(r.get("content"))
        blob = title + " " + content
        topics = topics_of(blob)
        out.append({
            "date": _parse_date(r),
            "title": title,
            "url": r.get("url") or "",
            "source": _clean(r.get("source") or r.get("originName") or "人民网"),
            "summary": content[:300],
            "topics": topics,
            "is_economic": "经济议题" in topics,
        })

    out.sort(key=lambda x: x["date"], reverse=True)

    # 去重：同一次会议常有多条报道（视频版、不同栏目转载、日期差 1–3 天）。
    # 步骤：先用「标题主干」精确聚合，再把日期相邻(≤4天)且主干高度重合的并为一组。
    def stem(t: str) -> str:
        """标题主干：去掉「…习近平主持会议」等后缀与空白。"""
        t = re.sub(r"中共中央总书记习近平主持.{0,4}会议?", "", t)
        t = re.sub(r"习近平主持.{0,4}会议?", "", t)
        return re.sub(r"[\s　]+", "", t)[:30]

    def merge(dst: dict, src: dict) -> None:
        dst["topics"] = sorted(set(dst.get("topics") or []) | set(src.get("topics") or []))
        dst["is_economic"] = "经济议题" in dst["topics"]
        if len(src.get("summary") or "") > len(dst.get("summary") or ""):
            dst["summary"] = src["summary"]
        if not dst.get("date") and src.get("date"):
            dst["date"] = src["date"]

    groups: list[dict] = []
    for m in out:
        m = dict(m)
        m["_s"] = stem(m["title"])
        hit = None
        for g in groups:
            if g["_s"] == m["_s"]:
                hit = g
                break
            if not (g["date"] and m["date"]):
                continue
            try:
                gap = abs((datetime.strptime(g["date"], "%Y-%m-%d")
                           - datetime.strptime(m["date"], "%Y-%m-%d")).days)
            except Exception:  # noqa: BLE001
                continue
            if gap > 4:
                continue
            a, b = g["_s"], m["_s"]
            short = a if len(a) <= len(b) else b
            long_ = b if len(a) <= len(b) else a
            if len(short) >= 12 and short[:12] in long_:
                hit = g
                break
        if hit is None:
            groups.append(m)
        else:
            merge(hit, m)

    for g in groups:
        g.pop("_s", None)
        g.setdefault("topics", [])
        g.setdefault("is_economic", False)
    return sorted(groups, key=lambda x: x["date"], reverse=True)


def update() -> dict:
    """抓取并保存。"""
    started = datetime.now(CST)
    try:
        meetings = fetch_meetings()
    except Exception as exc:  # noqa: BLE001
        fd.log(f"政治局会议抓取失败: {exc}")
        return load()

    if not meetings:
        fd.log("政治局会议：本次未获取到通稿，保留上次缓存")
        old = load()
        return old if old.get("meetings") else {"meetings": []}

    econ = [m for m in meetings if m["is_economic"]]
    payload = {
        "updated_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "人民网（新华社通稿）",
        "note": "议题标签依据通稿原文关键词匹配，仅表示官方文本中出现该词，非解读。",
        "meetings": meetings,
        "meeting_count": len(meetings),
        "econ_count": len(econ),
        "latest": meetings[0] if meetings else None,
        "latest_econ": econ[0] if econ else None,
        "last_check": started.strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, _path())

    fd.log(f"政治局会议: 成功 {len(meetings)} 次（其中经济议题 {len(econ)} 次）")
    return payload


def load() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {"meetings": [], "meeting_count": 0, "econ_count": 0}


if __name__ == "__main__":
    update()
