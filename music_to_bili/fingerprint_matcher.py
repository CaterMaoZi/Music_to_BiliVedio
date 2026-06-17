# -*- coding: utf-8 -*-
"""
地标式音频指纹匹配器（Shazam 思路）

作用：判断一段“本地音频片段”出现在某个“较长的视频音轨”中的哪个位置，
并给出置信度。对 MP3 压缩、重新编码、轻微噪声都比较鲁棒。

单次匹配的流程：
  1. 在频谱图上挑出能量峰值“地标”，把相邻峰配对成 (f1, f2, dt) 哈希；
     本地片段和视频音轨各自生成一组哈希。
  2. 用“偏移直方图投票”：相同哈希在两边出现的时间差会大量集中到同一个
     偏移值上，票数最高的偏移即片段在视频中的粗略起点。
  3. 在该偏移附近做对齐校验：只在对齐后的那一小段上计算
     onset/能量包络的归一化相关，以及 MFCC、色度的逐帧余弦相似度。
  4. 把指纹证据和信号校验融合成最终置信度。

全程纯本地信号处理，不依赖任何大模型来定位时间轴。
"""
import numpy as np
from collections import defaultdict

# ---- 基本参数（采样率、帧长等）----
SR = 16000           # 统一重采样到 16kHz 单声道
N_FFT = 1024         # STFT 窗长
HOP = 512            # 帧移 512 个采样点 ≈ 32ms，对应帧率 31.25 fps
PEAK_NF_T = 15       # 峰值挑选时，时间方向的邻域大小（帧）
PEAK_NF_F = 19       # 峰值挑选时，频率方向的邻域大小（频点）
FAN_OUT = 12         # 每个锚点峰最多与后面多少个峰配对
TARGET_T_MIN = 1     # 配对峰之间允许的最小时间间隔（帧）
TARGET_T_MAX = 60    # 配对峰之间允许的最大时间间隔（帧）≈ 1.9 秒
FRAME_SEC = HOP / float(SR)   # 一帧对应多少秒


def _spectrogram(y):
    """计算对数幅度谱（STFT 取模再做 log1p 压缩动态范围）。"""
    from scipy.signal import stft
    f, t, Z = stft(y, fs=SR, nperseg=N_FFT, noverlap=N_FFT - HOP, padded=False)
    S = np.abs(Z)
    return np.log1p(S * 1000.0)


def _find_peaks(S):
    """找出频谱上的局部极大值（且高于自适应阈值），返回 (频点下标, 帧下标)。"""
    from scipy.ndimage import maximum_filter
    # 在 (频率 x 时间) 邻域内做最大值滤波，等于该点本身的即为局部峰
    local_max = maximum_filter(S, size=(PEAK_NF_F, PEAK_NF_T)) == S
    # 用中位数 + 0.8 倍标准差作底噪门限，滤掉弱峰
    floor = np.median(S) + 0.8 * np.std(S)
    mask = local_max & (S > floor)
    fi, ti = np.nonzero(mask)
    return fi, ti


def hash_landmarks(y):
    """把一段音频转成指纹列表：每个元素是 (哈希值, 锚点所在帧)。

    做法：把每个峰当锚点，与其后方一定时间窗内的若干个峰配对，
    用 (锚点频率, 目标频率, 时间差) 编码成一个整数哈希。
    """
    S = _spectrogram(y)
    fi, ti = _find_peaks(S)
    order = np.argsort(ti)          # 按时间排序
    fi, ti = fi[order], ti[order]
    n = len(ti)
    hashes = []
    for i in range(n):
        t1, f1 = int(ti[i]), int(fi[i])
        paired = 0
        j = i + 1
        while j < n and paired < FAN_OUT:
            dt = int(ti[j]) - t1
            if dt > TARGET_T_MAX:    # 超过时间窗就不再往后找
                break
            if dt >= TARGET_T_MIN:
                f2 = int(fi[j])
                # 把三个量拼进一个整数：高位放 f1，中间放 f2，低位放 dt
                h = (f1 << 18) | (f2 << 7) | dt
                hashes.append((h, t1))
                paired += 1
            j += 1
    return hashes


def match_offset(local_hashes, video_hashes):
    """偏移直方图投票：找出本地片段相对视频的最佳时间偏移及证据强度。

    返回字典：offset_sec(偏移秒数)、votes(峰票数)、second_votes(次高票)、
    n_local_hashes、fp_density(命中密度)、uniqueness(峰票/次峰票，越大越不含糊)。
    票数越高、uniqueness 越大，说明匹配越可信。
    """
    if not local_hashes or not video_hashes:
        return None
    # 把视频侧的哈希建索引：同一个哈希可能在多个时间出现
    table = defaultdict(list)
    for h, t in video_hashes:
        table[h].append(t)
    # 对本地每个哈希，找视频里相同哈希，累计“时间差”的票数
    hist = defaultdict(int)
    for h, tl in local_hashes:
        for tv in table.get(h, ()):
            hist[tv - tl] += 1
    if not hist:
        return {"offset_sec": 0.0, "votes": 0, "second_votes": 0,
                "n_local_hashes": len(local_hashes), "fp_density": 0.0,
                "uniqueness": 1.0}
    # 把相邻 ±1 帧的票并到一起，避免抖动把同一峰拆成两份
    merged = {}
    for off, v in hist.items():
        merged[off] = v + hist.get(off - 1, 0) + hist.get(off + 1, 0)
    best_off = max(merged, key=merged.get)
    votes = merged[best_off]
    # 找离最佳偏移足够远的次高峰，用来衡量这次匹配有多“唯一”
    second = 0
    for off, v in merged.items():
        if abs(off - best_off) > 8 and v > second:
            second = v
    n_uniq_anchor = len(set(t for _, t in local_hashes))
    fp_density = votes / float(max(1, n_uniq_anchor))
    uniqueness = votes / float(max(1, second))
    return {"offset_sec": best_off * FRAME_SEC, "votes": int(votes),
            "second_votes": int(second),
            "n_local_hashes": len(local_hashes),
            "fp_density": float(min(1.0, fp_density)),
            "uniqueness": float(uniqueness)}


def _energy_env(y):
    """逐帧能量包络（每帧的 RMS 均方根能量）。"""
    n = max(1, (len(y) - N_FFT) // HOP + 1)
    idx = np.arange(n) * HOP
    env = np.empty(n)
    for i, s in enumerate(idx):
        seg = y[s:s + N_FFT]
        env[i] = np.sqrt(np.mean(seg * seg)) if len(seg) else 0.0
    return env


def extract_features(y):
    """提取用于“对齐校验”的逐帧特征（hop 与指纹一致，便于缓存复用）。"""
    import librosa
    onset = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    energy = _energy_env(y)
    # 丢掉第 0 维 MFCC（主要是整体响度），保留更能表征音色的高阶系数
    mfcc = librosa.feature.mfcc(y=y, sr=SR, n_mfcc=20, hop_length=HOP)[1:]
    chroma = librosa.feature.chroma_stft(y=y, sr=SR, hop_length=HOP)
    return {"onset": onset.astype(np.float32),
            "energy": energy.astype(np.float32),
            "mfcc": mfcc.astype(np.float32),
            "chroma": chroma.astype(np.float32)}


def build_pack(y):
    """把一段音频打包成“指纹 + 特征 + 时长”，这是可缓存的最小单元。"""
    return {"hashes": hash_landmarks(y),
            "feats": extract_features(y),
            "dur": len(y) / float(SR)}


def save_pack(path, pack):
    """把 pack 压缩存盘（.npz），下次直接加载省去重复计算。"""
    h = np.array(pack["hashes"], dtype=np.int64).reshape(-1, 2)
    f = pack["feats"]
    np.savez_compressed(path, hashes=h, onset=f["onset"], energy=f["energy"],
                        mfcc=f["mfcc"], chroma=f["chroma"],
                        dur=np.float64(pack["dur"]))


def load_pack(path):
    """从 .npz 还原出 pack。"""
    z = np.load(path)
    return {"hashes": [(int(a), int(b)) for a, b in z["hashes"]],
            "feats": {"onset": z["onset"], "energy": z["energy"],
                      "mfcc": z["mfcc"], "chroma": z["chroma"]},
            "dur": float(z["dur"])}


def _norm_corr(a, b):
    """两段等长一维序列的归一化相关系数（先各自去均值再做内积）。"""
    n = min(len(a), len(b))
    if n < 8:
        return 0.0
    a = a[:n] - np.mean(a[:n])
    b = b[:n] - np.mean(b[:n])
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _refine_offset(local_env, video_env, coarse_frame, search=8):
    """在粗略偏移附近 ±search 帧内细扫，用包络相关找到最贴合的偏移。"""
    nl = len(local_env)
    best_c, best_off = -1.0, coarse_frame
    lo = max(0, coarse_frame - search)
    hi = min(len(video_env) - nl, coarse_frame + search)
    if hi < lo:
        return coarse_frame, _norm_corr(local_env, video_env[max(0, coarse_frame):])
    for off in range(lo, hi + 1):
        c = _norm_corr(local_env, video_env[off:off + nl])
        if c > best_c:
            best_c, best_off = c, off
    return best_off, best_c


def verify_aligned_feats(lf, vf, coarse_frame):
    """只在“对齐后的那一小段”上做信号级校验（基于缓存特征，不需要原始波形）。

    lf/vf 是 extract_features 得到的特征字典。
    返回 (精修后的偏移帧, onset相关, 能量相关, MFCC相关, 色度相关)。
    """
    off, onset_corr = _refine_offset(lf["onset"], vf["onset"], coarse_frame, search=8)
    nl = len(lf["energy"])
    energy_corr = _norm_corr(lf["energy"], vf["energy"][off:off + nl])

    # MFCC：逐帧做标准化后求余弦，消除整体响度差异
    lm = lf["mfcc"]
    vm = vf["mfcc"][:, off:off + lm.shape[1]]
    n = min(lm.shape[1], vm.shape[1])
    if n < 8:
        return off, max(0.0, onset_corr), max(0.0, energy_corr), 0.0, 0.0
    lm, vm = lm[:, :n], vm[:, :n]
    both = np.concatenate([lm, vm], axis=1)
    mu = np.mean(both, axis=1, keepdims=True)
    sd = np.std(both, axis=1, keepdims=True) + 1e-9
    lmz, vmz = (lm - mu) / sd, (vm - mu) / sd
    num = np.sum(lmz * vmz, axis=0)
    den = np.linalg.norm(lmz, axis=0) * np.linalg.norm(vmz, axis=0) + 1e-9
    mfcc_corr = float(np.mean(num / den))

    # 色度：直接逐帧余弦，反映和声/调性是否一致
    lc = lf["chroma"]
    vc = vf["chroma"][:, off:off + lc.shape[1]]
    n = min(lc.shape[1], vc.shape[1])
    num = np.sum(lc[:, :n] * vc[:, :n], axis=0)
    den = (np.linalg.norm(lc[:, :n], axis=0) *
           np.linalg.norm(vc[:, :n], axis=0) + 1e-9)
    chroma_corr = float(np.mean(num / den))
    return (off, max(0.0, onset_corr), max(0.0, energy_corr),
            max(0.0, mfcc_corr), max(0.0, chroma_corr))


def fp_confidence(fp, local_dur_sec):
    """把指纹证据映射到 [0,1] 置信度，核心指标是“每秒票数”。

    经验观察：真匹配通常每秒 >=10 票（常见 40+）；随机碰撞每秒 <=0.5 票。
    uniqueness 太低（匹配含糊）时再打折。
    """
    if fp is None or fp["votes"] <= 0 or local_dur_sec <= 0:
        return 0.0
    rate = fp["votes"] / float(local_dur_sec)
    conf = 1.0 - np.exp(-rate / 3.0)        # 票率越高越接近 1
    u = fp["uniqueness"]
    if u < 1.8:
        conf *= 0.4
    elif u < 3.0:
        conf *= 0.75
    return float(conf)


def fuse_confidence(fp, local_dur_sec, onset_c, energy_c, mfcc_c, chroma_c):
    """融合置信度：以指纹证据为主，信号校验作为确认系数。

    信号项里 MFCC/色度权重最高（最能区分是否同一段音乐），
    能量包络对母带/响度差异敏感，权重最低。
    """
    fpc = fp_confidence(fp, local_dur_sec)
    sig = (0.40 * mfcc_c + 0.35 * chroma_c +
           0.15 * onset_c + 0.10 * energy_c)
    return float(fpc * (0.55 + 0.45 * min(1.0, sig / 0.75)))


def load_audio(path):
    """读取任意音频文件，重采样到 16kHz 单声道返回 numpy 数组。"""
    import librosa
    y, _ = librosa.load(path, sr=SR, mono=True)
    return y


def match_packs(local_pack, video_pack):
    """基于缓存 pack 的完整匹配：投票求偏移 -> 对齐校验 -> 融合置信度。

    返回字典：match_start/match_end（片段在视频中的起止秒）、votes、
    uniqueness、各项相关分、confidence；无法匹配时返回 None。
    """
    fp = match_offset(local_pack["hashes"], video_pack["hashes"])
    if fp is None:
        return None
    coarse = int(round(max(0.0, fp["offset_sec"]) / FRAME_SEC))
    off, onset_c, energy_c, mfcc_c, chroma_c = verify_aligned_feats(
        local_pack["feats"], video_pack["feats"], coarse)
    dur = local_pack["dur"]
    conf = fuse_confidence(fp, dur, onset_c, energy_c, mfcc_c, chroma_c)
    start = off * FRAME_SEC
    return {
        "match_start": float(max(0.0, start)),
        "match_end": float(max(0.0, start) + dur),
        "votes": fp["votes"],
        "uniqueness": fp["uniqueness"],
        "fp_density": fp["fp_density"],
        "fp_conf": fp_confidence(fp, dur),
        "cross_corr": float(onset_c),
        "energy_corr": float(energy_c),
        "mfcc_frame": float(mfcc_c),
        "chroma_corr": float(chroma_c),
        "confidence": float(conf),
    }


def match_pair(local_y, video_y, local_hashes=None, video_hashes=None):
    """对两段已加载的波形做完整匹配（不走缓存）。返回同 match_packs 的字典。"""
    if local_hashes is None:
        local_hashes = hash_landmarks(local_y)
    if video_hashes is None:
        video_hashes = hash_landmarks(video_y)
    lf = extract_features(local_y)
    vf = extract_features(video_y)
    return match_packs(
        {"hashes": local_hashes, "feats": lf, "dur": len(local_y) / float(SR)},
        {"hashes": video_hashes, "feats": vf, "dur": len(video_y) / float(SR)})
