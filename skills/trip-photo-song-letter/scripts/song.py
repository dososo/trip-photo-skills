# SPDX-License-Identifier: Apache-2.0
"""一首歌：把一条山脊（或屋檐）的起伏写成一首有前奏、主歌、副歌、间奏、高潮、尾声的歌，并用小乐队编曲渲染成 WAV。

乐器：八音盒（代码合成）；钢琴、小提琴组、中提琴组、大提琴组、竖琴、长笛、钢片琴、军鼓、铃鼓、手鼓
（VSCO 2 Community Edition 采样，CC0，用 episode.py fetch 下载到 ~/.cache/trip-photo-song-letter/vsco2ce）；
木吉他（Karplus-Strong 合成 + 琴箱共鸣）、贝斯、底鼓、沙锤、镲（代码合成）。

曲式（4/4，16 小节）：0–1 前奏（八音盒独奏读山脊）· 2–5 主歌 · 6–9 副歌（王道进行 IV–V–iii–vi，倒着读山脊）·
10–11 间奏 · 12–13 高潮（读山脊最高的一段）· 14–15 尾声（渐慢，停在 I add9）。

由 episode.py song 调用。输出：WAV（48 kHz 立体声）和 JSON（每个旋律音的时间、山脊 x 坐标、段落时间）。
同一张照片、同样参数，每次结果完全一样（随机数种子来自照片文件的哈希）。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sys
from fractions import Fraction

import numpy as np
import soundfile as sf
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, fftconvolve, lfilter, resample_poly, sosfilt

SR = 48000
CACHE = os.environ.get("SONGLETTER_CACHE", os.path.expanduser("~/.cache/trip-photo-song-letter")) + "/vsco2ce"
NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
MAJOR = [0, 2, 4, 5, 7, 9, 11]
KEY = 2  # 调性（D 大调 = 2），由 make() 的 key 改写

PIANO_MAP = {}
_mc = os.path.join(CACHE, "Keys/Upright Piano/MappingChart.txt")
if os.path.exists(_mc):
    for _l in open(_mc):
        if "=" in _l and _l[:1].isdigit():
            _a, _b = _l.strip().split("=")
            PIANO_MAP[_a] = int(_b)

# (通配, 取音高的方式, 文件名比实际低几个半音)
BANKS = {
    "piano": ("Keys/Upright Piano/Player_dyn2_rr1_*.wav", "piano", 0),
    "harp": ("Strings/Harp/KSHarp_*_mf.wav", r"KSHarp_([A-G]#?\d)_", 0),
    "glock": ("Percussion/Glock/glock_medium_*.wav", r"glock_medium_([A-G]#?\d)", 0),
    "violins": ("Strings/Violin Section/susVib/VlnEns_susVib_*_v1.wav", r"susVib_([A-G]#?\d)_", 12),
    "violas": ("Strings/Viola Section/susvib/ViolaEns_susvib_*_v1_1.wav", r"susvib_([A-G]#?\d)_", 12),
    "cellos": ("Strings/Cello Section/susvib/susvib_*_v1_1.wav", r"susvib_([A-G]#?\d)_", 12),
    "flute": ("Woodwinds/Flute/susvib/LDFlute_susvib_*_v1_1.wav", r"susvib_([A-G]#?\d)_", 12),
}


def name2midi(n: str) -> int:
    m = re.match(r"([A-G])(#?)(-?\d)", n)
    return 12 * (int(m.group(3)) + 1) + NOTE[m.group(1)] + (1 if m.group(2) else 0)


def hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def load_wav(path: str) -> np.ndarray:
    x, sr = sf.read(path, always_2d=True, dtype="float32")
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    if sr != SR:
        fr = Fraction(SR, sr).limit_denominator(1000)
        x = resample_poly(x, fr.numerator, fr.denominator, axis=0).astype(np.float32)
    return x


class Sampler:
    def __init__(self):
        self.banks: dict[str, list[tuple[int, np.ndarray]]] = {}
        for name, (pat, rx, off) in BANKS.items():
            items = []
            for f in sorted(glob.glob(os.path.join(CACHE, pat))):
                if rx == "piano":
                    num = re.search(r"_(\d{3})\.wav$", f).group(1)
                    midi = PIANO_MAP.get(num)
                else:
                    mm = re.search(rx, os.path.basename(f))
                    midi = name2midi(mm.group(1)) + off if mm else None
                if midi is None:
                    continue
                x = load_wav(f)
                x = x / (np.abs(x).max() + 1e-9) * 0.8          # VSCO 录音电平低，统一峰值
                items.append((midi, x))
            if not items:
                raise SystemExit(f"缺少采样 {pat}，请先下载 VSCO-2-CE 到 {CACHE}")
            self.banks[name] = sorted(items, key=lambda t: t[0])
        self.fx = {}
        for key, rel in (("gong", "Miscellania Raw/Misc 1/cymbgong_pp.wav"),
                         ("bd", "Percussion/BDrumNewhit_v3_rr1_Sum.wav")):
            p = os.path.join(CACHE, rel)
            if os.path.exists(p):
                x = load_wav(p)
                self.fx[key] = x / (np.abs(x).max() + 1e-9) * 0.8

    def play(self, bank, midi, dur, vel, release=0.4, attack=0.0):
        items = self.banks[bank]
        sm, x = min(items, key=lambda t: abs(t[0] - midi))
        ratio = 2 ** ((midi - sm) / 12)
        fr = Fraction(1 / ratio).limit_denominator(400)
        y = resample_poly(x, fr.numerator, fr.denominator, axis=0) if fr != 1 else x.copy()
        n = min(len(y), int((dur + release) * SR))
        y = y[:n].copy()
        r0 = int(dur * SR)
        if release > 0 and n > r0:
            y[r0:] *= np.linspace(1, 0, n - r0)[:, None] ** 2
        if attack > 0:
            a = min(n, int(attack * SR))
            y[:a] *= np.linspace(0, 1, a)[:, None] ** 1.5
        return (y * vel).astype(np.float32)


def music_box(midi: float, vel: float, seed: int) -> np.ndarray:
    f = hz(midi)
    n = int(3.2 * SR)
    t = np.arange(n) / SR
    tau = 2.2 * (440 / f) ** 0.5
    y = np.sin(2 * np.pi * f * t) * np.exp(-t / tau)
    y += 0.18 * np.sin(2 * np.pi * 2.005 * f * t + 0.6) * np.exp(-t / (tau * 0.3))
    y += 0.07 * np.sin(2 * np.pi * 5.98 * f * t + 1.1) * np.exp(-t / 0.035)
    rng = np.random.default_rng(seed)
    click = rng.standard_normal(n) * np.exp(-t / 0.003)
    click = sosfilt(butter(2, [2500, 7500], btype="band", fs=SR, output="sos"), click) * 0.035
    y = (y * np.minimum(1, t / 0.0015) + click) * vel
    d = int(0.0007 * SR)
    return np.stack([y, np.concatenate([np.zeros(d), y[:-d]])], axis=1).astype(np.float32)



def chord_pcs(degree: int) -> list[int]:
    i = degree - 1
    return [(KEY + MAJOR[(i + k) % 7]) % 12 for k in (0, 2, 4)]


def scale_notes(lo, hi):
    return [m for m in range(lo, hi + 1) if (m - KEY) % 12 in MAJOR]



def voicing_piano(degree):
    pcs = chord_pcs(degree)
    root = 38 + ((pcs[0] - 38) % 12)            # 左手根音 D2–C#3
    r = 50 + ((pcs[0] - 50) % 12)               # 右手分解从 D3 起
    third = r + ((pcs[1] - pcs[0]) % 12)
    fifth = r + ((pcs[2] - pcs[0]) % 12)
    ninth = r + 14                               # 加九度，更「影视」
    return root, [r, fifth, r + 12, third + 12, ninth if degree in (1, 4) else fifth + 12, third + 12, r + 12, fifth]


def reverb_ir(seconds=3.4, seed=5):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    ir = rng.standard_normal((n, 2)) * np.exp(-t / 0.85)[:, None]
    ir = sosfilt(butter(2, 6000, btype="low", fs=SR, output="sos"), ir, axis=0)
    pre = int(0.025 * SR)
    ir = np.concatenate([np.zeros((pre, 2)), ir])[:n]
    return (ir / np.sqrt((ir ** 2).sum(axis=0, keepdims=True))).astype(np.float32)



BPM = 80.0
SPB = 60.0 / BPM

# 每小节的和弦（一个元素 = 一个和弦占整小节；二元组 = 前后半小节各一个）
PROG = {
    0: 1, 1: 5,                                  # 前奏 I – V
    2: 6, 3: 3, 4: 4, 5: 5,                      # 主歌 vi – iii – IV – V
    6: 4, 7: 5, 8: 3, 9: 6,                      # 副歌 王道进行
    10: 6, 11: 2,                                # 间奏 vi – ii
    12: (4, 5), 13: (3, 6),                      # 高潮 一小节两个和弦
    14: (2, 5), 15: 1,                           # 尾声 ii – V → I
}
STRETCH = {14: 1.08, 15: 1.40}                    # 尾声渐慢
N_BARS = 16


def chord_at(bar: int, beat: float) -> int:
    c = PROG[bar]
    if isinstance(c, tuple):
        return c[0] if beat < 2 else c[1]
    return c



# ---------------------------------------------------------------- 合成乐器
def ks_pluck(f: float, dur: float, vel: float, bright: float = 0.55, decay: float = 0.996, seed: int = 0) -> np.ndarray:
    """Karplus-Strong 拨弦，带小数延迟微调音高。"""
    n = int(dur * SR)
    period = SR / f
    N = int(period)
    a = period - N
    rng = np.random.default_rng(seed)
    exc = rng.uniform(-1, 1, N + 2)
    exc = lfilter([1 - bright], [1, -bright], exc)          # 拨弦力度 / 指甲的亮度
    x = np.zeros(n)
    x[: len(exc)] = exc
    den = np.zeros(N + 2)
    den[0] = 1.0
    den[N] = -decay * (1 - a)
    den[N + 1] = -decay * a
    y = lfilter([1.0], den, x)
    y /= max(1e-9, np.abs(y[: min(n, 6 * N)]).max())
    env = np.ones(n)
    att = int(0.002 * SR)
    env[:att] = np.linspace(0, 1, att)
    rel = int(0.06 * SR)
    env[-rel:] = np.linspace(1, 0, rel)
    return y * env * vel


_BODY = None


def guitar_body(y: np.ndarray) -> np.ndarray:
    """木吉他琴箱：几处共鸣峰 + 轻微高频收敛。"""
    out = y * 0.7
    for fc, q, g in ((105, 4, 0.35), (210, 5, 0.25), (420, 6, 0.15), (2400, 1.5, 0.12)):
        bw = fc / q
        sos = butter(2, [max(20, fc - bw / 2), fc + bw / 2], btype="band", fs=SR, output="sos")
        out = out + g * sosfilt(sos, y)
    return sosfilt(butter(2, 7000, btype="low", fs=SR, output="sos"), out)


def guitar_note(midi: float, dur: float, vel: float, seed: int, bright=0.5) -> np.ndarray:
    f = hz(midi)
    decay = 0.9965 if midi < 52 else (0.995 if midi < 64 else 0.993)
    y = guitar_body(ks_pluck(f, dur + 0.6, vel, bright=bright, decay=decay, seed=seed))
    return np.stack([y, y], axis=1).astype(np.float32)


def bass_note(midi: float, dur: float, vel: float, seed: int) -> np.ndarray:
    f = hz(midi)
    n = int((dur + 0.25) * SR)
    t = np.arange(n) / SR
    pl = ks_pluck(f, dur + 0.25, 1.0, bright=0.25, decay=0.998, seed=seed)
    sine = np.sin(2 * np.pi * f * t) * np.exp(-t / 1.4)
    y = 0.6 * pl + 0.55 * sine
    y = np.tanh(1.4 * y) / np.tanh(1.4)
    y = sosfilt(butter(2, 900, btype="low", fs=SR, output="sos"), y)
    env = np.ones(n)
    r0 = int(dur * SR)
    env[r0:] = np.linspace(1, 0, n - r0)
    y = y * env * vel
    return np.stack([y, y], axis=1).astype(np.float32)


def kick(vel: float) -> np.ndarray:
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    f = 48 + 70 * np.exp(-t / 0.035)
    ph = 2 * np.pi * np.cumsum(f) / SR
    y = np.sin(ph) * np.exp(-t / 0.18)
    y += 0.25 * np.random.default_rng(1).standard_normal(n) * np.exp(-t / 0.004)
    y = sosfilt(butter(2, 6000, btype="low", fs=SR, output="sos"), y) * vel
    return np.stack([y, y], axis=1).astype(np.float32)


def shaker(vel: float, seed: int) -> np.ndarray:
    n = int(0.12 * SR)
    t = np.arange(n) / SR
    y = np.random.default_rng(seed).standard_normal(n)
    y = sosfilt(butter(2, [4500, 11000], btype="band", fs=SR, output="sos"), y)
    env = np.minimum(1, t / 0.012) * np.exp(-t / 0.045)
    y = y * env * vel
    return np.stack([y * 0.9, y], axis=1).astype(np.float32)


def crash(vel: float, seed: int = 3) -> np.ndarray:
    n = int(3.0 * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    y = rng.standard_normal(n)
    y = sosfilt(butter(2, 3500, btype="high", fs=SR, output="sos"), y)
    metal = sum(np.sin(2 * np.pi * f * t + rng.uniform(0, 6)) for f in (3120, 4385, 5210, 6740, 7930)) * 0.08
    y = (y * 0.6 + metal) * np.exp(-t / 1.1) * np.minimum(1, t / 0.003) * vel
    return np.stack([y, np.roll(y, 37)], axis=1).astype(np.float32)


# ---------------------------------------------------------------- 作曲
def ridge_heights(ridge: np.ndarray, x0: int, x1: int):
    y = uniform_filter1d(ridge[x0:x1].astype(float), size=5)
    h = (y.max() - y) / (y.max() - y.min() + 1e-6)
    act = np.abs(np.diff(y, prepend=y[0]))
    return h, act


CELLS = {
    "calm": [[2.0, 2.0], [3.0, 1.0], [2.0, 1.0, 1.0]],
    "medium": [[1.0, 1.0, 2.0], [2.0, 1.0, 1.0], [1.5, 0.5, 1.0, 1.0]],
    "busy": [[1.0, 0.5, 0.5, 1.0, 1.0], [0.5, 0.5, 1.0, 1.0, 1.0], [1.0, 1.0, 0.5, 0.5, 1.0]],
}


def compose(ridge: np.ndarray, x0: int, x1: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    h, act = ridge_heights(ridge, x0, x1)
    n = len(h)

    def level(fa, fb):
        a, b = int(fa * (n - 1)), max(int(fa * (n - 1)) + 2, int(fb * (n - 1)))
        return float(act[a:b].mean())

    def pick_cells(bars, fa, fb, fixed=None):
        edges = np.linspace(fa, fb, len(bars) + 1)
        lv = np.array([level(edges[i], edges[i + 1]) for i in range(len(bars))])
        q = np.quantile(lv, [0.4, 0.75]) if len(lv) > 2 else [lv.mean(), lv.mean()]
        out = {}
        for i, b in enumerate(bars):
            if fixed and b in fixed:
                out[b] = fixed[b]
                continue
            key = "calm" if lv[i] <= q[0] else ("medium" if lv[i] <= q[1] else "busy")
            opts = CELLS[key]
            out[b] = opts[int(rng.integers(len(opts)))]
        return out

    def line(bars, cells, fa, fb, lo, hi, reverse=False, end_pc=None, pass_id=0):
        pool = scale_notes(lo, hi)
        out, prev = [], None
        nb = len(bars)
        for i, b in enumerate(bars):
            beat = 0.0
            for k, d in enumerate(cells[b]):
                f = (i + beat / 4.0) / nb
                f = fa + (fb - fa) * (1 - f if reverse else f)
                xi = int(round(f * (n - 1)))
                target = lo + h[xi] * (hi - lo)
                pcs = chord_pcs(chord_at(b, beat))
                strong = beat in (0.0, 2.0)
                last = (i == nb - 1 and k == len(cells[b]) - 1)
                cands = [m for m in pool if m % 12 in pcs] if (strong or last) else pool
                if last and end_pc is not None:
                    cands = [m for m in pool if m % 12 == end_pc] or cands

                def cost(m):
                    c = 0.8 * abs(m - target)
                    if prev is not None:
                        j = abs(m - prev)
                        c += 1.0 * max(0, j - 4) + (2.5 if j >= 12 else 0) + (1.8 if m == prev else 0)
                        c -= 0.5 if 0 < j <= 2 else 0
                    return c
                m = min(cands, key=cost)
                out.append(dict(bar=b, beat=beat, dur=d, midi=int(m), x=int(x0 + xi), h=float(h[xi]), pass_=pass_id))
                prev = m
                beat += d
        return out

    # 主旋律：前奏 + 主歌 6 小节从左到右读完整条山脊
    verse_bars = [0, 1, 2, 3, 4, 5]
    vc = pick_cells(verse_bars, 0.0, 1.0, fixed={0: [1.0, 1.0, 1.0, 1.0], 1: [1.0, 1.0, 1.0, 1.0], 5: [3.0, 1.0]})
    mel_verse = line(verse_bars, vc, 0.0, 1.0, 74, 93, end_pc=(KEY + 7) % 12 if False else (KEY + 2) % 12, pass_id=0)   # 停在 V（A）的五音 E，悬着
    # 副歌：4 小节从右往左倒着读，钢琴音区
    chorus_bars = [6, 7, 8, 9]
    cc = pick_cells(chorus_bars, 0.0, 1.0, fixed={9: [2.0, 2.0]})
    mel_chorus = line(chorus_bars, cc, 0.0, 1.0, 74, 88, reverse=True, end_pc=(KEY + 9) % 12, pass_id=1)
    # 间奏：八音盒读山脊中段，慢
    bridge_bars = [10, 11]
    bc = {10: [2.0, 1.0, 1.0], 11: [2.0, 2.0]}
    mel_bridge = line(bridge_bars, bc, 0.35, 0.65, 74, 90, end_pc=(KEY + 7) % 12, pass_id=2)
    # 高潮：读山脊最高的那一段，八分音符为主
    peak = int(np.argmax(uniform_filter1d(h, size=max(9, n // 20))))
    pa, pb = max(0.0, peak / n - 0.18), min(1.0, peak / n + 0.18)
    climax_bars = [12, 13]
    kc = {12: [1.0, 0.5, 0.5, 1.0, 1.0], 13: [1.0, 1.0, 1.0, 1.0]}
    mel_climax = line(climax_bars, kc, pa, pb, 78, 93, end_pc=(KEY + 9) % 12, pass_id=3)
    return dict(mel_verse=mel_verse, mel_chorus=mel_chorus, mel_bridge=mel_bridge, mel_climax=mel_climax,
                peak_range=(pa, pb), n=n, x0=x0, x1=x1)


# ---------------------------------------------------------------- 编曲与混音
def guitar_voicing(deg: int):
    pcs = chord_pcs(deg)
    root = 40 + ((pcs[0] - 40) % 12)            # E2–D#3
    fifth = root + ((pcs[2] - pcs[0]) % 12)
    up = []
    for p in pcs:                                 # 高音区 G3–F#4
        up.append(55 + ((p - 55) % 12))
    up = sorted(up)
    return root, fifth if fifth - root <= 9 else fifth - 12, up


def render(sc: dict, smp: Sampler, out_wav: str) -> tuple[list, list]:
    bar_len = [4 * SPB * STRETCH.get(b, 1.0) for b in range(N_BARS)]
    starts = np.concatenate([[0], np.cumsum(bar_len)])
    total = starts[-1] + 5.0
    names = ("box", "piano", "harp", "vln", "vla", "vc", "fl", "glock", "gtr", "bass", "kick", "snare", "hat", "tamb", "cym")
    bus = {k: np.zeros((int(total * SR) + SR, 2), np.float32) for k in names}

    def put(b, t, y, pan=0.0):
        s = int(t * SR)
        e = min(len(bus[b]), s + len(y))
        if e <= s:
            return
        gl, gr = np.sqrt(0.5 - pan / 2) * 1.414, np.sqrt(0.5 + pan / 2) * 1.414
        bus[b][s:e, 0] += y[: e - s, 0] * gl
        bus[b][s:e, 1] += y[: e - s, 1] * gr

    def T(bar, beat):
        return starts[bar] + beat * bar_len[bar] / 4

    fx = {}
    for key, rel in (("snare", ["Snare2-HitSN_v3_rr1_Sum.wav", "Snare2-HitSN_v3_rr2_Sum.wav", "Snare2-HitSN_v5_rr1_Sum.wav"]),
                     ("roll", ["Snare2-rollSN_v3_rr1_Sum.wav"]),
                     ("taps", ["Snare2-taps_v1_rr1_Sum.wav", "Snare2-taps_v1_rr2_Sum.wav"]),
                     ("tamb", ["Tamb1-Hit_v1_rr1_Sum.wav", "Tamb1-Hit_v1_rr2_Sum.wav", "Tamb1-Hit_v2_rr1_Sum.wav"]),
                     ("conga", ["Conga-HitN_v1_rr1_Sum.wav", "Conga-HitN_v2_rr1_Sum.wav", "Conga-Tap1_v1_rr1_Sum.wav"])):
        fx[key] = []
        for r in rel:
            p = os.path.join(CACHE, "Percussion", r)
            if os.path.exists(p):
                x = load_wav(p)
                fx[key].append(x / (np.abs(x).max() + 1e-9) * 0.8)
    gong = smp.fx.get("gong")

    # ---------- 前奏 + 主歌：八音盒；吉他、贝斯、沙锤、弦乐依次进
    for nt in sc["mel_verse"]:
        put("box", T(nt["bar"], nt["beat"]), music_box(nt["midi"], 0.58 + 0.22 * nt["h"], 7 * nt["bar"] + int(nt["beat"] * 4)), 0.1)
    for b in range(0, 6):
        deg = chord_at(b, 0)
        root, fifth, up = guitar_voicing(deg)
        if b >= 1:                                   # 吉他：第 1 小节轻轻进（只弹根音和泛音感的高音），之后分解
            pattern = [root, up[0], up[1], up[2], fifth, up[0], up[1], up[2]] if b >= 2 else [root, None, None, up[2], None, None, up[1], None]
            for k, m in enumerate(pattern):
                if m is None:
                    continue
                v = 0.30 if b == 1 else (0.42 if k in (0, 4) else 0.32)
                put("gtr", T(b, k * 0.5), guitar_note(m, 1.4, v, 100 * b + k), -0.35)
        if b >= 3:                                   # 贝斯
            pcs = chord_pcs(deg)
            r = 38 + ((pcs[0] - 38) % 12)
            put("bass", T(b, 0), bass_note(r, 1.6, 0.55, 300 + b), 0.0)
            put("bass", T(b, 2.5), bass_note(r + 7 if r + 7 < 50 else r - 5, 0.9, 0.42, 400 + b), 0.0)
        if b >= 4:                                   # 沙锤
            for k in range(8):
                put("hat", T(b, k * 0.5), shaker(0.22 if k % 2 else 0.30, 500 + 8 * b + k), 0.3)
        if b >= 4:                                   # 弦乐铺底，渐强
            pcs = chord_pcs(deg)
            lvl = 0.6 if b == 4 else 0.85
            for p in pcs[1:]:
                put("vla", T(b, 0), smp.play("violas", 57 + ((p - 57) % 12), bar_len[b] + 0.3, 0.38 * lvl, 0.9, attack=0.5), -0.1)
            put("vc", T(b, 0), smp.play("cellos", 50 + ((pcs[0] - 50) % 12), bar_len[b] + 0.3, 0.45 * lvl, 0.9, attack=0.4), 0.3)
    # 过门：第 5 小节后半军鼓滚奏渐强 + 手鼓 + 反向镲
    if fx.get("roll"):
        r = fx["roll"][0][: int(1.5 * SR)].copy()
        r *= np.linspace(0.15, 1.0, len(r))[:, None] ** 1.5
        put("snare", T(5, 2.5), r * 0.55, 0.05)
    for k, beat in enumerate((3.0, 3.5)):
        if fx.get("conga"):
            put("snare", T(5, beat), fx["conga"][k % len(fx["conga"])][: int(0.6 * SR)] * 0.45, -0.2)
    if gong is not None:
        g = gong[: int(2.6 * SR)][::-1].copy()
        g *= np.linspace(0, 1, len(g))[:, None] ** 2
        put("cym", starts[6] - len(g) / SR, g * 0.45, 0.0)
    # 竖琴上行刮奏进副歌
    v = voicing_piano(4)[1]
    gl = sorted(set(m + 12 * o for o in (0, 1, 2) for m in v[:4]))[:14]
    for k, m in enumerate(gl):
        put("harp", T(5, 3) + k * (bar_len[5] / 4) / len(gl), smp.play("harp", m, 1.8, 0.4, 1.2), -0.25)

    # ---------- 副歌（6–9）与高潮（12–13）：全乐队
    def full_band(b, intensity):
        put("cym", T(b, 0), crash(0.32 * intensity), 0.15) if b in (6, 12) else None
        for half in ((0.0,) if not isinstance(PROG[b], tuple) else (0.0, 2.0)):
            deg = chord_at(b, half)
            span = 4.0 if not isinstance(PROG[b], tuple) else 2.0
            root, fifth, up = guitar_voicing(deg)
            pcs = chord_pcs(deg)
            # 扫弦：下 下 上 上 下 上
            strum = [(0.0, 1, 0.85), (1.0, 1, 0.6), (1.5, -1, 0.45), (2.5, -1, 0.45), (3.0, 1, 0.7), (3.5, -1, 0.42)]
            for beat, direction, v in strum:
                if beat >= span:
                    continue
                notes = [root, fifth] + up + [up[0] + 12]
                if direction < 0:
                    notes = notes[::-1][:4]
                for i, m in enumerate(notes):
                    put("gtr", T(b, half + beat) + i * 0.011, guitar_note(m, 0.9, 0.30 * v * intensity, 900 + 13 * b + i + int(beat * 7), bright=0.62), -0.35)
            # 钢琴：左手八度根音 + 右手柱式和弦
            r = 38 + ((pcs[0] - 38) % 12)
            put("piano", T(b, half), smp.play("piano", r, span * SPB, 0.5 * intensity, 1.0), 0.0)
            put("piano", T(b, half), smp.play("piano", r + 12, span * SPB, 0.38 * intensity, 1.0), 0.0)
            chord = sorted(62 + ((p - 62) % 12) for p in pcs)
            for k, m in enumerate(chord):
                put("piano", T(b, half) + 0.01 * k, smp.play("piano", m, span * SPB * 0.9, 0.30 * intensity, 1.0), 0.05)
                if span == 4.0:
                    put("piano", T(b, half + 2), smp.play("piano", m, 2 * SPB * 0.9, 0.24 * intensity, 1.0), 0.05)
            # 贝斯：根音 + 经过音
            put("bass", T(b, half), bass_note(r, span * SPB * 0.55, 0.62 * intensity, 600 + b), 0.0)
            put("bass", T(b, half + span * 0.75), bass_note(r + 7 if r + 7 < 50 else r - 5, span * SPB * 0.2, 0.45 * intensity, 700 + b), 0.0)
            # 弦乐和声
            for p in pcs[1:]:
                put("vla", T(b, half), smp.play("violas", 57 + ((p - 57) % 12), span * SPB + 0.2, 0.42 * intensity, 0.8, attack=0.25), -0.1)
            put("glock", T(b, half), smp.play("glock", 86 + ((pcs[2] - 86) % 12), 1.6, 0.18, 1.0), 0.3)
        # 鼓：底鼓 1、2&、3；军鼓 2、4；沙锤八分；铃鼓 2、4
        for beat in (0.0, 1.5, 2.0):
            put("kick", T(b, beat), kick(0.55 * intensity), 0.0)
        for beat in (1.0, 3.0):
            if fx.get("snare"):
                put("snare", T(b, beat), fx["snare"][(b + int(beat)) % len(fx["snare"])][: int(0.8 * SR)] * 0.42 * intensity, 0.05)
            if fx.get("tamb"):
                put("tamb", T(b, beat), fx["tamb"][(b + int(beat)) % len(fx["tamb"])][: int(0.7 * SR)] * 0.30 * intensity, 0.35)
        for k in range(8):
            put("hat", T(b, k * 0.5), shaker(0.28 if k % 2 else 0.36, 2000 + 8 * b + k), 0.3)

    for b in (6, 7, 8, 9):
        full_band(b, 1.0)
        # 大提琴对位：每小节一个向下的级进长音
        pcs = chord_pcs(chord_at(b, 0))
        put("vc", T(b, 0), smp.play("cellos", 50 + ((pcs[1] - 50) % 12), bar_len[b] * 0.95, 0.5, 0.8, attack=0.2), 0.3)
        # 长笛：高音区长音，和弦三音
        put("fl", T(b, 0), smp.play("flute", 81 + ((pcs[1] - 81) % 12), bar_len[b] * 0.9, 0.26, 0.8, attack=0.3), 0.25)
    for nt in sc["mel_chorus"]:
        t, d = T(nt["bar"], nt["beat"]), nt["dur"] * SPB
        put("piano", t, smp.play("piano", nt["midi"], d * 1.05, 0.60 + 0.2 * nt["h"], 1.0), 0.05)
        put("piano", t, smp.play("piano", nt["midi"] - 12, d * 1.05, 0.38, 1.0), 0.05)
        put("vln", t, smp.play("violins", nt["midi"] - 12, d + 0.15, 0.48, 0.6, attack=0.1), -0.35)

    # ---------- 间奏：鼓退出，钢琴分解 + 弦乐 + 八音盒
    for b in (10, 11):
        deg = chord_at(b, 0)
        root, arp = voicing_piano(deg)
        put("piano", T(b, 0), smp.play("piano", root, bar_len[b] * 0.95, 0.40, 1.2), 0.0)
        for k, m in enumerate(arp):
            put("piano", T(b, k * 0.5), smp.play("piano", m, 1.6, 0.32 * (0.85 if k % 2 else 1.0), 0.9), -0.1 + 0.03 * k)
        pcs = chord_pcs(deg)
        for p in pcs[1:]:
            put("vla", T(b, 0), smp.play("violas", 57 + ((p - 57) % 12), bar_len[b] + 0.3, 0.34, 1.0, attack=0.5), -0.1)
        put("vc", T(b, 0), smp.play("cellos", 50 + ((pcs[0] - 50) % 12), bar_len[b] + 0.3, 0.42, 1.0, attack=0.5), 0.3)
        put("kick", T(b, 0), kick(0.30), 0.0)
    for nt in sc["mel_bridge"]:
        put("box", T(nt["bar"], nt["beat"]), music_box(nt["midi"], 0.55, 3000 + int(nt["beat"] * 4) + nt["bar"]), 0.1)
    # 进高潮前：军鼓轻点 + 反向镲
    if fx.get("taps"):
        for k, beat in enumerate((3.0, 3.25, 3.5, 3.75)):
            put("snare", T(11, beat), fx["taps"][k % len(fx["taps"])][: int(0.4 * SR)] * (0.22 + 0.08 * k), 0.05)
    if gong is not None:
        g = gong[: int(2.0 * SR)][::-1].copy()
        g *= np.linspace(0, 1, len(g))[:, None] ** 2
        put("cym", starts[12] - len(g) / SR, g * 0.5, 0.0)

    # ---------- 高潮：一小节两个和弦，旋律读山脊最高处
    for b in (12, 13):
        full_band(b, 1.12)
        pcs = chord_pcs(chord_at(b, 0))
        put("fl", T(b, 0), smp.play("flute", 81 + ((pcs[1] - 81) % 12), bar_len[b] * 0.95, 0.30, 0.8, attack=0.25), 0.25)
        put("vc", T(b, 0), smp.play("cellos", 50 + ((pcs[0] - 50) % 12), bar_len[b] * 0.95, 0.55, 0.8, attack=0.2), 0.3)
    for nt in sc["mel_climax"]:
        t, d = T(nt["bar"], nt["beat"]), nt["dur"] * SPB
        put("piano", t, smp.play("piano", nt["midi"], d * 1.05, 0.66 + 0.2 * nt["h"], 1.0), 0.05)
        put("piano", t, smp.play("piano", nt["midi"] - 12, d * 1.05, 0.42, 1.0), 0.05)
        put("vln", t, smp.play("violins", nt["midi"] - 12, d + 0.15, 0.55, 0.6, attack=0.08), -0.35)
        put("glock", t, smp.play("glock", nt["midi"] + 12, 1.2, 0.12, 0.8), 0.3)

    # ---------- 尾声：ii–V → I add9，渐慢；八音盒 3–2–1，最后一个高音
    for half in (0.0, 2.0):
        deg = chord_at(14, half)
        pcs = chord_pcs(deg)
        r = 38 + ((pcs[0] - 38) % 12)
        put("piano", T(14, half), smp.play("piano", r, 2 * SPB * 1.1, 0.4, 1.4), 0.0)
        for k, m in enumerate(sorted(62 + ((p - 62) % 12) for p in pcs)):
            put("piano", T(14, half) + 0.03 * k, smp.play("piano", m, 2 * SPB, 0.28, 1.4), 0.05)
        for p in pcs[1:]:
            put("vla", T(14, half), smp.play("violas", 57 + ((p - 57) % 12), 2 * SPB + 0.4, 0.30, 1.2, attack=0.4), -0.1)
        root, fifth, up = guitar_voicing(deg)
        for i, m in enumerate([root, fifth] + up):
            put("gtr", T(14, half) + i * 0.03, guitar_note(m, 2.0, 0.22, 7000 + i + int(half)), -0.35)
    for k, (m, beat) in enumerate([(78, 0.0), (76, 1.0), (74, 2.0), (76, 3.0)]):
        put("box", T(14, beat), music_box(m, 0.55, 8000 + k), 0.1)
    root, arp = voicing_piano(1)
    put("piano", T(15, 0), smp.play("piano", root, 5.0, 0.42, 2.5), 0.0)
    for k, m in enumerate([50, 57, 62, 64, 66, 69, 74]):
        put("piano", T(15, 0) + k * 0.12, smp.play("piano", m, 5.0, 0.30, 2.5), -0.1 + 0.03 * k)
    for i, m in enumerate([50, 57, 62, 66, 69, 76]):
        put("gtr", T(15, 0) + 0.2 + i * 0.05, guitar_note(m, 3.5, 0.20, 9000 + i), -0.35)
    put("harp", T(15, 0), smp.play("harp", 74, 4.5, 0.3, 2.0), -0.2)
    put("glock", T(15, 0) + 0.15, smp.play("glock", 98, 3.0, 0.2, 2.0), 0.3)
    put("box", T(15, 0) + 0.35, music_box(74, 0.55, 9999), 0.1)
    put("box", T(15, 2), music_box(86, 0.45, 9998), 0.1)
    for p in (62, 66, 69):
        put("vla", T(15, 0), smp.play("violas", 57 + ((p - 57) % 12), 6.0, 0.30, 2.0, attack=0.6), -0.1)
    put("vc", T(15, 0), smp.play("cellos", 50, 6.0, 0.42, 2.0, attack=0.5), 0.3)

    gains = {"box": 0.50, "piano": 0.72, "harp": 1.1, "vln": 1.9, "vla": 0.95, "vc": 0.75, "fl": 1.0, "glock": 2.8,
             "gtr": 1.25, "bass": 0.32, "kick": 0.70, "snare": 0.9, "hat": 0.55, "tamb": 0.8, "cym": 0.7}
    sends = {"box": 0.35, "piano": 0.28, "harp": 0.38, "vln": 0.48, "vla": 0.5, "vc": 0.4, "fl": 0.45, "glock": 0.55,
             "gtr": 0.25, "bass": 0.06, "kick": 0.06, "snare": 0.22, "hat": 0.15, "tamb": 0.25, "cym": 0.35}
    if os.environ.get("BUS_RMS"):
        for k in names:
            print("bus", k, "rms %.4f" % float(np.sqrt(((bus[k] * gains[k]) ** 2).mean())), file=sys.stderr)
    ir = reverb_ir()
    dry = sum(bus[k] * gains[k] for k in names)
    send = sum(bus[k] * gains[k] * sends[k] for k in names)
    wet = np.stack([fftconvolve(send[:, c], ir[:, c])[: len(send)] for c in (0, 1)], axis=1)
    mix = dry + wet * 0.9
    mix = sosfilt(butter(2, 32, btype="high", fs=SR, output="sos"), mix, axis=0)
    mix = mix + 0.3 * sosfilt(butter(2, 3500, btype="high", fs=SR, output="sos"), mix, axis=0)
    # 轻压缩：让乐队更「黏」
    env = uniform_filter1d(np.abs(mix).max(axis=1), size=int(0.03 * SR))
    thr = np.percentile(env, 92)
    gain = np.where(env > thr, (thr / (env + 1e-9)) ** 0.35, 1.0)
    mix = mix * gain[:, None]
    mix = mix[: int(total * SR)]
    fade = int(2.5 * SR)
    mix[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 1.5
    mix = mix / (np.abs(mix).max() + 1e-9) * 10 ** (-1.0 / 20)
    sf.write(out_wav, mix, SR, subtype="PCM_16")
    events = []
    for nt in sc["mel_verse"] + sc["mel_chorus"] + sc["mel_bridge"] + sc["mel_climax"]:
        events.append(dict(t=float(T(nt["bar"], nt["beat"])), d=float(nt["dur"] * SPB), midi=nt["midi"], x=nt["x"],
                           h=nt["h"], pass_=nt["pass_"], bar=nt["bar"]))
    sections = dict(verse=float(starts[0]), morph=float(starts[2]), chorus=float(starts[6]), bridge=float(starts[10]),
                    climax=float(starts[12]), outro=float(starts[14]), final=float(starts[15]), end=float(starts[16]))
    return events, sections



KEYS = {"C": 0, "C#": 1, "Db": 1, "D": 2, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "Ab": 8, "A": 9, "Bb": 10, "B": 11}


def make(ridge_path: str, photo_path: str, wav_path: str, json_path: str, x0f: float, x1f: float,
         key: str = "D", bpm: float = 80.0) -> dict:
    """读山脊 x0f–x1f 这一段（0–1 的比例）写歌，存 WAV 和 JSON，返回 JSON 内容。"""
    global KEY, BPM, SPB
    if not (0.0 <= x0f < x1f <= 1.0):
        raise ValueError("起点比例必须小于终点比例，且都在 0–1 之间")
    if not (60 <= bpm <= 110):
        raise ValueError("速度请在 60–110 之间")
    if key not in KEYS:
        raise ValueError("调性只能是 " + " ".join(sorted(KEYS)))
    KEY, BPM = KEYS[key], float(bpm)
    SPB = 60.0 / BPM
    ridge = np.load(ridge_path)
    w = len(ridge)
    x0, x1 = int(x0f * w), int(x1f * w)
    seed = int(hashlib.sha256(open(photo_path, "rb").read()).hexdigest()[:8], 16)
    sc = compose(ridge, x0, x1, seed)
    smp = Sampler()
    for p in (wav_path, json_path):
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    events, sections = render(sc, smp, wav_path)
    out = dict(schema_version="1", key=key, bpm=BPM, events=events, sections=sections, width=w, x0=x0, x1=x1,
               peak_range=sc["peak_range"])
    json.dump(out, open(json_path, "w"), ensure_ascii=False, indent=1)
    return out
