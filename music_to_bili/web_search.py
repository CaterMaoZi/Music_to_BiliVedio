# -*- coding: utf-8 -*-
"""
网页搜索兜底：当 B 站站内搜索找不到理想候选时，用通用搜索引擎（Bing 等）
按“歌名 + bilibili”去搜，从结果页里抽取 BV 号，作为补充候选。

为什么需要它：站内搜索对冷门/外文歌名、或被标题党稀释的内容命中率不稳；
而搜索引擎往往能直接把“某歌的官方/原版 B 站视频”排到前面。本模块只做
“查询 + 从 HTML 里正则抽 BV 号”，不依赖任何账号或密钥。

注意：搜索引擎可能返回验证码页或随时改版式
"""
import re
import time
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# B 站 BV 号的固定格式：BV 开头 + 10 位字母数字
_BV_RE = re.compile(r"BV[a-zA-Z0-9]{10}")

# 可用的搜索引擎及其搜索接口；按顺序尝试，先成功的先用
SEARCH_ENGINES = [
    ("bing", "https://cn.bing.com/search"),
    ("bing_intl", "https://www.bing.com/search"),
]


def _looks_blocked(html):
    """粗略判断结果页是不是被验证码/人机校验拦了。"""
    low = html.lower()
    return ("captcha" in low or "verify" in low or "unusual traffic" in low)


def _extract_bvids(html, limit):
    """从 HTML 文本里按出现顺序抽取去重后的 BV 号。"""
    seen, out = set(), []
    for bv in _BV_RE.findall(html):
        if bv not in seen:
            seen.add(bv)
            out.append(bv)
            if len(out) >= limit:
                break
    return out


def search_bvids(query, limit=5, timeout=10):
    """用搜索引擎搜 query，返回最多 limit 个候选 BV 号；全失败则返回 []。

    内部会自动在标准查询词后补上 “bilibili”，引导结果指向 B 站视频。
    """
    full_query = query if "bilibili" in query.lower() else (query + " bilibili")
    headers = {"User-Agent": UA}
    for _name, url in SEARCH_ENGINES:
        try:
            r = requests.get(url, params={"q": full_query}, headers=headers,
                             timeout=timeout)
            if r.status_code != 200 or _looks_blocked(r.text):
                continue
            bvids = _extract_bvids(r.text, limit)
            if bvids:
                return bvids
        except Exception:
            continue
    return []


def search_song(song_name, version_hint="", limit=5):
    """针对“一首歌”做几组面向官方/原版的查询，合并去重后返回候选 BV。

    version_hint 可传入版本线索（如 'MV'、'原版'、某游戏/企划名），
    用来拼出更精确的查询词，提高命中正确来源的概率。
    """
    queries = [song_name + " 官方MV", song_name + " MV", song_name]
    if version_hint:
        queries.insert(0, "%s %s" % (song_name, version_hint))
    seen, merged = set(), []
    for q in queries:
        for bv in search_bvids(q, limit=limit):
            if bv not in seen:
                seen.add(bv)
                merged.append(bv)
        time.sleep(0.5)   # 限速，降低被搜索引擎拦截的概率
        if len(merged) >= limit:
            break
    return merged[:limit]
