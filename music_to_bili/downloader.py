# -*- coding: utf-8 -*-
"""
Bilibili 音轨下载器（走 playurl 接口）

用 BV 号查到视频信息(cid/时长/标题等) -> 取 DASH 音频流地址 ->
下载码率最高的那条音轨 -> 用 ffmpeg 转成 16kHz 单声道 WAV。
转成 16kHz 单声道是为了和指纹匹配器的采样率保持一致，体积也更小。

"""
import os
import subprocess
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def new_session():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    try:
        s.get("https://www.bilibili.com", timeout=15)
    except Exception:
        pass
    return s


class DownloadError(Exception):
    """下载相关错误。code 用来区分原因，便于上层决定重试还是跳过：

    - 'gone'   : 视频已失效/下架/不可解析，重试也没用，应跳过
    - 'blocked': 触发风控(如 HTTP 412)，通常等一会儿换 IP/会话再试
    - 'net'    : 网络/流问题，可重试
    - 'other'  : 其它接口返回异常
    """
    def __init__(self, code, msg):
        super(DownloadError, self).__init__(msg)
        self.code = code


def get_video_info(session, bvid):
    r = session.get("https://api.bilibili.com/x/web-interface/view",
                    params={"bvid": bvid},
                    headers={"Referer": "https://www.bilibili.com/"},
                    timeout=20)
    if r.status_code == 412:
        raise DownloadError("blocked", "412 风控拦截")
    d = r.json()
    if d.get("code") in (-404, 62002, 62012, -403):
        raise DownloadError("gone", "视频已失效: %s" % d.get("code"))
    if d.get("code") != 0:
        raise DownloadError("other", "view 接口返回码 %s" % d.get("code"))
    data = d["data"]
    if not data.get("cid"):
        raise DownloadError("gone", "缺少 cid（视频不可用）")
    return {"cid": data["cid"], "duration": data["duration"],
            "title": data.get("title", ""),
            "author": data.get("owner", {}).get("name", ""),
            "desc": (data.get("desc", "") or "")[:500],
            "play": data.get("stat", {}).get("view", 0)}


def get_audio_url(session, bvid, cid):
    r = session.get("https://api.bilibili.com/x/player/playurl",
                    params={"bvid": bvid, "cid": cid, "fnval": 16},
                    headers={"Referer": "https://www.bilibili.com/video/" + bvid},
                    timeout=20)
    if r.status_code == 412:
        raise DownloadError("blocked", "412 风控拦截")
    d = r.json()
    if d.get("code") != 0:
        raise DownloadError("other", "playurl 接口返回码 %s" % d.get("code"))
    audio = (d.get("data", {}).get("dash", {}) or {}).get("audio") or []
    if not audio:
        raise DownloadError("other", "该视频没有 DASH 音频流")
    best = max(audio, key=lambda a: a.get("bandwidth", 0))
    return best["baseUrl"]


def download_stream(session, url, out_path):
    """把音频流下载到本地文件，分块写入。"""
    r = session.get(url, headers={"Referer": "https://www.bilibili.com/"},
                    stream=True, timeout=60)
    if r.status_code not in (200, 206):
        raise DownloadError("net", "音频流 HTTP %d" % r.status_code)
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(262144):
            f.write(chunk)
    if os.path.getsize(out_path) < 10000:
        raise DownloadError("net", "下载到的文件过小")


def to_wav16k(src, dst):
    p = subprocess.run(["ffmpeg", "-y", "-i", src, "-ar", "16000",
                        "-ac", "1", dst], capture_output=True, timeout=120)
    if p.returncode != 0 or not os.path.exists(dst) \
            or os.path.getsize(dst) < 5000:
        raise DownloadError("other", "ffmpeg 转码失败")


def download_bv_audio(session, bvid, out_wav, keep_raw=False):
    info = get_video_info(session, bvid)
    url = get_audio_url(session, bvid, info["cid"])
    raw = out_wav + ".m4s"
    try:
        download_stream(session, url, raw)
        to_wav16k(raw, out_wav)
    finally:
        if not keep_raw:
            try:
                os.remove(raw)
            except OSError:
                pass
    return info
