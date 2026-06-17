# -*- coding: utf-8 -*-
"""
端到端示例：给定一段本地音频 + 它的歌名，自动在 B 站找到来源视频，
并定位这段音频在视频中的精确起止时间。

串起了库里的三个模块：
  bili_search        —— 按歌名搜出一批候选视频(BV号)
  downloader         —— 把候选视频音轨下成 16kHz WAV
  fingerprint_matcher —— 指纹匹配，给出片段在视频中的时间区间与置信度

运行：
  python examples/find_clip.py "我的片段.mp3" "歌名"
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from music_to_bili import bili_search, downloader, fingerprint_matcher as fm

# 置信度阈值：>=0.85 才认为是可信命中（自行调整）
CONFIDENCE_THRESHOLD = 0.85

# 每首歌最多试几个候选视频（按搜索得分从高到低）
MAX_CANDIDATES = 5


def find_clip(local_path, song_name):
    """返回最佳命中 (bvid, match_start, match_end, confidence)，找不到则返回 None。"""

    local_pack = fm.build_pack(fm.load_audio(local_path))

    session = bili_search.new_session()
    candidates = bili_search.search_candidates(session, song_name,
                                               limit=MAX_CANDIDATES)
    if not candidates:
        print("没有搜到任何候选视频")
        return None

    dl_session = downloader.new_session()
    best = None
    for cand in candidates[:MAX_CANDIDATES]:
        bvid = cand["bvid"]
        wav = os.path.join(tempfile.gettempdir(), bvid + ".wav")
        try:
            downloader.download_bv_audio(dl_session, bvid, wav)
            video_pack = fm.build_pack(fm.load_audio(wav))
            r = fm.match_packs(local_pack, video_pack)
        except downloader.DownloadError as e:
            print("  跳过 %s（下载失败：%s）" % (bvid, e))
            continue
        finally:
            if os.path.exists(wav):
                os.remove(wav)
        if not r:
            continue
        print("  %s 标题《%s》 -> %.1f~%.1f 秒，置信度 %.3f"
              % (bvid, cand["title"][:30], r["match_start"],
                 r["match_end"], r["confidence"]))
        if best is None or r["confidence"] > best[3]:
            best = (bvid, r["match_start"], r["match_end"], r["confidence"])
        if best[3] >= 0.98:
            break
    return best


def main():
    if len(sys.argv) < 3:
        print("用法: python examples/find_clip.py <音频文件> <歌名>")
        sys.exit(1)
    local_path, song_name = sys.argv[1], sys.argv[2]
    best = find_clip(local_path, song_name)
    if best and best[3] >= CONFIDENCE_THRESHOLD:
        bvid, st, en, conf = best
        print("\n命中：%s 的 %.1f 秒 ~ %.1f 秒（置信度 %.3f）"
              % (bvid, st, en, conf))
    else:
        print("\n未找到足够可信的来源视频片段")


if __name__ == "__main__":
    main()
