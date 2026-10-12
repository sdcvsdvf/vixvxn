# -*- coding: utf-8 -*-
"""
政治局会议纪要  |  Politburo meeting notes

为什么需要人工维护：
    政治局会议没有公开的结构化数据接口。会议通稿的「要点/信号」属于
    需要解读的内容，任何自动抓取都无法可靠生成。因此本模块采用：

      1. 人工维护的会议纪要   data/politburo.json   ← 核心内容
      2. 自动抓取的相关新闻   data/politburo_news.json ← 辅助信息

首次运行会自动生成一个带示例结构的 politburo.json，用户按同样格式追加即可。

纪要字段：
    date       会议日期 YYYY-MM-DD
    title      会议名称
    summary    一句话定调
    points     要点列表 [{topic, text, signal}]
               signal: 偏松 / 偏紧 / 中性   （用于着色）
    markets    市场影响简述（可选）
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from datetime import datetime, timezone, timedelta

import fetch_data as fd

CST = timezone(timedelta(hours=8))

EM_SEARCH = ("https://search-api-web.eastmoney.com/search/jsonp?cb=cb"
             "&param=%7B%22uid%22%3A%22%22%2C%22keyword%22%3A%22{kw}%22"
             "%2C%22type%22%3A%5B%22cmsArticleWebOld%22%5D%2C%22client%22%3A%22web%22"
             "%2C%22clientType%22%3A%22web%22%2C%22clientVersion%22%3A%22curr%22"
             "%2C%22param%22%3A%7B%22cmsArticleWebOld%22%3A%7B%22searchScope%22%3A%22default%22"
             "%2C%22sort%22%3A%22default%22%2C%22pageIndex%22%3A1%2C%22pageSize%22%3A{ps}"
             "%2C%22preTag%22%3A%22%3Cem%3E%22%2C%22postTag%22%3A%22%3C%2Fem%3E%22%7D%7D%7D")


def _notes_path() -> str:
    return os.path.join(fd.DATA_DIR, "politburo.json")


def _news_path() -> str:
    return os.path.join(fd.DATA_DIR, "politburo_news.json")


# 首次运行生成的模板（含真实历史会议结构与示例要点）
SEED_NOTES = {
    "note": "政治局会议纪要（人工维护）。按同样结构在 meetings 数组开头追加新会议即可。"
            "signal 取值：偏松 / 偏紧 / 中性，用于页面着色。",
    "meetings": [
        {
            "date": "2026-07-30",
            "title": "中共中央政治局会议（分析研究上半年经济形势）",
            "summary": "示例条目：请替换为真实通稿要点。",
            "points": [
                {"topic": "财政政策", "text": "示例：加力提效，加快专项债发行使用。", "signal": "偏松"},
                {"topic": "货币政策", "text": "示例：稳健取向，保持流动性合理充裕。", "signal": "偏松"},
                {"topic": "房地产", "text": "示例：因城施策，消化存量优化增量。", "signal": "中性"},
            ],
            "markets": "示例：通常影响 A 股风险偏好与利率预期。",
            "is_sample": True,
        }
    ],
}


def _ensure_notes() -> dict:
    """读取纪要；不存在则写入模板。"""
    path = _notes_path()
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(SEED_NOTES, fh, ensure_ascii=False, indent=2)
            fd.log(f"已生成政治局会议纪要模板: {path}（请按格式填写真实内容）")
        except Exception as exc:  # noqa: BLE001
            fd.log(f"生成纪要模板失败: {exc}")
        return SEED_NOTES
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001
        fd.log(f"读取纪纪要失败: {exc}")
        return {"meetings": []}


def _clean(t: str) -> str:
    """去掉搜索接口插入的高亮标签。"""
    return re.sub(r"</?em>", "", str(t or "")).strip()


def fetch_news(page_size: int = 100, pages: int = 5) -> list[dict]:
    """
    抓取与「政治局会议」相关的财经新闻。

    实现说明（为什么不直接用搜索接口）：
        东方财富的 search 接口会**忽略关键词**、返回通用财经要闻，
        因此改为抓取新闻列表接口的多页内容，再在本地按关键词过滤。
        实测 500 条要闻中通常只有 0–2 条命中——政治局会议并非常见新闻，
        这属于正常情况，不是抓取失败。
    """
    base = ("https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?"
            "client=web&biz=web_news_col&column=345&order=1&needInteractData=0"
            "&page_index={pg}&page_size={ps}&req_trace=1"
            "&fields=code,showTime,title,mediaName,summary,url,uniqueUrl,Np_dst&types=1,20")

    keywords = ("政治局", "中央经济工作会议", "中央政治局")
    out: list[dict] = []
    seen: set[str] = set()

    for pg in range(1, max(1, pages) + 1):
        try:
            raw = fd._http_get(base.format(pg=pg, ps=page_size),
                               referer="https://finance.eastmoney.com/")
            payload = json.loads(raw.decode("utf-8", "replace"))
            items = (payload.get("data") or {}).get("list") or []
        except Exception as exc:  # noqa: BLE001
            fd.log(f"  新闻列表第 {pg} 页失败: {exc}")
            continue

        if not items:
            break

        for it in items:
            title = _clean(it.get("title"))
            summary = _clean(it.get("summary"))
            blob = title + summary
            if not any(k in blob for k in keywords):
                continue
            url = it.get("uniqueUrl") or it.get("url") or ""
            if url in seen:
                continue
            seen.add(url)
            out.append({
                "date": str(it.get("showTime") or "")[:16],
                "title": title,
                "media": _clean(it.get("mediaName")),
                "url": url,
                "summary": summary[:160],
            })

    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def update_news() -> dict:
    """抓取并保存相关新闻。"""
    try:
        news = fetch_news()
        payload = {"updated_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
                   "items": news}
        tmp = _news_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, _news_path())
        fd.log(f"政治局会议相关新闻: 成功 {len(news)} 条")
        return payload
    except Exception as exc:  # noqa: BLE001
        fd.log(f"政治局会议相关新闻抓取失败: {exc}")
        return load_news()


def load_news() -> dict:
    try:
        with open(_news_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {"items": []}


def load_politburo() -> dict:
    """页面所需的政治局会议数据（纪要 + 相关新闻）。"""
    notes = _ensure_notes()
    meetings = notes.get("meetings") or []
    # 按日期倒序，最新的在前
    meetings = sorted(meetings, key=lambda m: str(m.get("date") or ""), reverse=True)
    news = load_news()
    return {
        "note": notes.get("note", ""),
        "meetings": meetings,
        "meeting_count": len(meetings),
        "latest": meetings[0] if meetings else None,
        "news": news.get("items", []),
        "news_updated_at": news.get("updated_at"),
        "has_real_content": any(not m.get("is_sample") for m in meetings),
    }


if __name__ == "__main__":
    update_news()
