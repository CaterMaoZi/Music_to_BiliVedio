# -*- coding: utf-8 -*-
"""
Bilibili 候选视频搜索：给定一个歌名/关键词，返回一批候选视频(BV号+标题+作者等)。


说明：
- 使用 B 站公开的网页搜索接口
- 自带一组“硬排除”关键词，过滤明显不是官方/原版的内容（混剪、鬼畜、二创等），
  这组词可按你的场景自行增删。
"""
import re
import time
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 标题命中这些词的候选直接排除：多为非官方/改编/合集类内容
DEFAULT_EXCLUDE = [
    "混剪", "AMV", "MAD", "翻跳", "鬼畜", "二创", "合集", "串烧",
    "mix", "镜面", "搬运", "reaction", "盘点",
]


def new_session():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    try:
        s.get("https://www.bilibili.com", timeout=15)
    except Exception:
        pass
    return s


def search(session, keyword, limit=8, exclude=None):
    """按关键词搜索视频，返回候选列表。

    每个候选是字典：bvid / title / author / play / duration。
    标题命中排除词的候选会被过滤掉。
    """
    exclude = DEFAULT_EXCLUDE if exclude is None else exclude
    try:
        r = session.get(
            "https://api.bilibili.com/x/web-interface/search/all/v2",
            params={"keyword": keyword, "page": 1, "pagesize": limit},
            headers={"Referer": "https://search.bilibili.com"}, timeout=15)
        d = r.json()
        if d.get("code") != 0:
            return []
        for item in d.get("data", {}).get("result", []):
            if item.get("result_type") != "video":
                continue
            out = []
            for v in item.get("data", [])[:limit]:
                title = re.sub(r"<[^>]+>", "", v.get("title", ""))
                bvid = v.get("bvid", "")
                if not bvid:
                    continue
                if any(kw.lower() in title.lower() for kw in exclude):
                    continue
                out.append({"bvid": bvid, "title": title,
                            "author": v.get("author", ""),
                            "play": v.get("play", 0),
                            "duration": v.get("duration", "")})
            return out
    except Exception:
        pass
    return []


def search_candidates(session, song_name, extra_queries=None, limit=6,
                      exclude=None, delay=0.4):
    queries = [song_name + " 官方MV", song_name + " MV", song_name]
    if extra_queries:
        queries = list(extra_queries) + queries
    seen, merged = set(), []
    for q in queries:
        for v in search(session, q, limit=limit, exclude=exclude):
            if v["bvid"] not in seen:
                seen.add(v["bvid"])
                merged.append(v)
        time.sleep(delay)   # 限速，避免触发风控
    return merged
