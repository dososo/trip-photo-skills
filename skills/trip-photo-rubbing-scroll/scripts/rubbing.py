# SPDX-License-Identifier: Apache-2.0
"""照片 → 拓片「白纹」图（0 = 墨，1 = 纸白）。只用 numpy + Pillow（有 scipy 会更快）。

拓片的道理：石面平整处上墨是黑的；刻进去的线和凹处碰不到墨，留白。
照片里对应「刻线」的是细节高通：瓦楞、窗格、山脊、树冠的亮边；天空、水面这类平滑的面就是墨黑的石面。
墨色不均和纸纤维交给着色器实时算，这里只输出白纹的强度。
"""
import numpy as np
from PIL import Image


def _box(a, r):
    c = np.cumsum(np.cumsum(np.pad(a, ((r + 1, r), (r + 1, r)), mode="edge"), 0), 1)
    return (c[2 * r + 1:, 2 * r + 1:] - c[:-2 * r - 1, 2 * r + 1:] - c[2 * r + 1:, :-2 * r - 1] + c[:-2 * r - 1, :-2 * r - 1]) / (2 * r + 1) ** 2


def gblur(a, s):
    try:
        from scipy.ndimage import gaussian_filter
        return gaussian_filter(a.astype(np.float32), s, mode="nearest")
    except ImportError:
        r = max(0, int(round((np.sqrt(4 * s * s + 1) - 1) / 2)))
        out = a.astype(np.float64)
        for _ in range(3):
            out = _box(out, r) if r > 0 else out
        return out.astype(np.float32)


def fbm(h, w, seed, scales=(4, 8, 16, 32), weights=(0.4, 0.3, 0.2, 0.1)):
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    for s, wt in zip(scales, weights):
        n = rng.random((max(2, h // s), max(2, w // s))).astype(np.float32)
        acc += wt * np.asarray(Image.fromarray(n, mode="F").resize((w, h), Image.BICUBIC), dtype=np.float32)
    acc -= acc.min()
    return acc / max(1e-6, acc.max())


def white_map(rgb, seed=1, fine=5.0, gain=0.03, thresh=0.012, broad=30.0):
    """返回 PIL L 图：白纹强度。fine 控制刻线粗细，thresh 越小纹越密。"""
    a = np.asarray(rgb.convert("RGB"), dtype=np.float32) / 255.0
    L = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    h, w = L.shape
    k = h / 900.0                                   # 参数按 900 像素高标定，其他尺寸等比
    s = L - gblur(L, fine * k)
    lines = np.clip((s - thresh) / gain, 0, 1)
    faces = np.clip((L - gblur(L, broad * k) - 0.03) / 0.12, 0, 1) * 0.5
    white = np.clip(lines + faces, 0, 1)
    # 石面剥蚀：成片吃掉一些白纹，像年代久的碑
    erode = fbm(h, w, seed, scales=tuple(int(v * k) or 1 for v in (5, 10, 20, 40)), weights=(0.35, 0.3, 0.2, 0.15))
    white *= np.clip((erode - 0.2) * 3.5, 0, 1) * 0.8 + 0.2
    return Image.fromarray((white * 255).astype(np.uint8), "L")
