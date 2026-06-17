# Music_to_BiliVedio

> 给定一段「音频名 + 片段描述」，在 Bilibili 上找到对应视频，并精确定位该片段在视频中的起止时间。

这是一个通用的音频对齐工具库：你手上有一小段音乐（可能只是副歌、或某个时间段的截取），
想知道它出自哪个 B 站视频、以及在那个视频里的**精确时间区间**。用地标式音频指纹 +
信号级对齐校验纯本地算法完成定位

## WHAT CAN DO

- **片段对整段**：本地音频往往只是完整歌曲的一小段（副歌/某 30 秒），需要在几分钟长的视频音轨里找到它。
- **抗压缩/重编码**：来源视频经过转码、轻微噪声、响度差异，仍要能稳定匹配。
- **精确时间轴**：不仅判断「是不是同一首」，还给出片段在视频中的 `match_start ~ match_end`（秒级）。
- **可量化置信度**：每次匹配输出 0~1 的置信度，便于设阈值决定「自动采纳 / 人工复核 / 丢弃」。

## 核心原理

1. **地标指纹**：对音频频谱挑能量峰值，把相邻峰配对成 `(f1, f2, dt)` 哈希；本地片段与视频各生成一组。
2. **偏移投票**：相同哈希在两边的时间差会集中到同一偏移值，票数最高的偏移即片段在视频中的粗略起点；
   票数与「唯一性」共同构成指纹证据强度。
3. **对齐校验**：只在对齐后的那一小段上计算 onset/能量包络相关、MFCC 与色度的逐帧余弦，
   与指纹证据融合成最终置信度。

## 安装 INSTALL

```bash
pip install numpy scipy librosa requests
# 另需系统安装 ffmpeg（用于把下载到的音频转成 16kHz 单声道 WAV）
```

## 示例 EXAMPLE

```python
from music_to_bili import bili_search, downloader
from music_to_bili import fingerprint_matcher as fm

# 1) 加载本地片段，做成可缓存的指纹包
local = fm.build_pack(fm.load_audio(\"my_clip.mp3\"))

# 2) 按歌名搜候选视频
sess = bili_search.new_session()
cands = bili_search.search_candidates(sess, \"歌名\")

# 3) 逐个候选下载音轨、匹配，取置信度最高的
dl = downloader.new_session()
for c in cands:
    downloader.download_bv_audio(dl, c[\"bvid\"], \"cand.wav\")
    r = fm.match_packs(local, fm.build_pack(fm.load_audio(\"cand.wav\")))
    if r and r[\"confidence\"] >= 0.85:
        print(\"命中 %s：%.1f~%.1f 秒，置信度 %.3f\"
              % (c[\"bvid\"], r[\"match_start\"], r[\"match_end\"], r[\"confidence\"]))
        break
```

完整可运行示例见 `examples/find_clip.py`。

## 模块说明

| 模块 | 作用 |
|------|------|
| `music_to_bili/bili_search.py` | 按歌名/关键词搜索候选视频；可扩展多搜索引擎与排除词 |
| `music_to_bili/downloader.py` | 通过 Bilibili playurl 接口下载视频音轨并转成 16kHz 单声道 WAV |
| `music_to_bili/fingerprint_matcher.py` | 地标指纹 + 偏移投票 + 对齐校验 + 置信度融合；支持指纹包存盘复用 |

## 置信度

`match_packs` 返回字典里的 `confidence` 在 0~1 之间。经验阈值：

- `>= 0.98`：几乎可确定，适合自动采纳；
- `0.85 ~ 0.98`：很可能正确，建议人工复核；
- `< 0.85`：不可靠，建议丢弃或换候选。

具体阈值请结合你的数据自行标定。

## 实例用法：RandomDance_to_BiliVedio

[RandomDance_to_BiliVedio](https://github.com/CaterMaoZi/RandomDance_to_BiliVedio) 是我基于本库做的一个完整实例项目——
「随舞音频自动溯源」：把上千个随机舞蹈（宅舞）音频片段，逐一在 B 站找到来源视频与精确时间段，
再统一汇总进审核池。它展示了如何在真实、规模化的场景里使用本库：批量建立本地指纹缓存、
按歌名分层搜索候选、并行下载、早停匹配、生成可人工核对的报告。想看本库在实战中怎么接，直接参考那个项目。

## License

MIT
