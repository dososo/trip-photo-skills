# SPDX-License-Identifier: Apache-2.0
"""照片 + 天空蒙版 → 剪纸剪影（不透明 = 纸，透明 = 天空 / 镂空亮处）。

只用 numpy + Pillow，在本机运行，不上传。
步骤：天空概率图 → 以原图亮度为引导的导向滤波（细化边缘）→ 阈值
     → 地面内部的亮斑（河面反光、灯火）刻成镂空 → 轻微平滑出剪纸边。
"""
import numpy as np
from PIL import Image, ImageFilter


def box(a, r):
    # 积分图实现的盒式滤波，半径 r
    c = np.cumsum(np.cumsum(np.pad(a, ((r + 1, r), (r + 1, r)), mode="edge"), 0), 1)
    return (c[2 * r + 1:, 2 * r + 1:] - c[:-2 * r - 1, 2 * r + 1:] - c[2 * r + 1:, :-2 * r - 1] + c[:-2 * r - 1, :-2 * r - 1]) / (2 * r + 1) ** 2


def guided(I, p, r=8, eps=1e-3):
    mI, mp = box(I, r), box(p, r)
    cov = box(I * p, r) - mI * mp
    var = box(I * I, r) - mI * mI
    a = cov / (var + eps)
    b = mp - a * mI
    return box(a, r) * I + box(b, r)


def make(photo, sky, long_edge=1600, hole_gain=0.86):
    """photo / sky：文件路径或 PIL 图。返回 (彩色图, 剪纸 alpha（255=纸）, 天空占比)。"""
    im = (photo if isinstance(photo, Image.Image) else Image.open(photo)).convert("RGB")
    im.thumbnail((long_edge, long_edge), Image.LANCZOS)
    sky = (sky if isinstance(sky, Image.Image) else Image.open(sky)).convert("L").resize(im.size, Image.BILINEAR)
    rgb = np.asarray(im).astype(np.float32) / 255.0
    lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    p = np.asarray(sky).astype(np.float32) / 255.0
    q = np.clip(guided(lum, p, r=6, eps=2e-3), 0, 1)
    sky_m = q > 0.5
    # 天空亮度中位数作参照；地面里明显更亮的连通小块刻成镂空
    ref = np.median(lum[sky_m]) if sky_m.any() else 0.8
    bright = (~sky_m) & (lum > ref * hole_gain)
    # 先腐蚀再膨胀（开运算）：去掉水面反光那种零碎小亮点，只留成片的亮处（灯火、窗、瀑布）
    k = 5 if max(im.size) >= 1800 else 3
    holes = Image.fromarray((bright * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))
    hole_m = np.asarray(holes) > 127
    paper = (~sky_m) & (~hole_m)
    # 剪纸边：轻微模糊后再阈值，去掉锯齿和孤立噪点
    a = Image.fromarray((paper * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
    alpha = (np.asarray(a) > 127).astype(np.uint8) * 255
    return im, Image.fromarray(alpha, "L"), float(sky_m.mean())
