# SPDX-License-Identifier: Apache-2.0
"""精确山脊线：语义分割给出大致位置，再沿亮度梯度逐列找真实边缘，用动态规划保证连续。

步骤：
1. UperNet（ADE20K，MIT）整图推理得到天空概率；每列从上往下，第一个「下面 40 像素九成不是天空」的位置作为粗边界；
2. 在粗边界上下 ±band 像素内，计算「上亮下暗」的竖直亮度梯度；
3. 动态规划逐列选点：分数 = 梯度强度 − λ·|相邻两列的高度差|；相邻两列差超过 max_step 像素时付一个固定代价（悬崖、山墙这类垂直边缘）；
4. 闭运算去掉宽度小于 despike 像素的细尖（如信号塔），保留山形。

由 episode.py ridge 调用：写歌照片先缩到 1600 宽（参数按这个宽度调好），副歌画按原尺寸提取。
输出：每列（与图片同宽）山脊的 y 坐标（像素，float）。
"""
from __future__ import annotations

import os
import shutil

import cv2
import numpy as np
import torch
from PIL import Image
from scipy.ndimage import grey_closing, median_filter
from transformers import UperNetForSemanticSegmentation

MEAN = np.array([0.485, 0.456, 0.406])
STD = np.array([0.229, 0.224, 0.225])
SKY = 2


def sky_prob(img: Image.Image) -> np.ndarray:
    """整图推理一次得到天空概率（分块推理会把天上的暗云误判成山，所以不用）。"""
    m = UperNetForSemanticSegmentation.from_pretrained("openmmlab/upernet-convnext-tiny").eval()
    dev = "cpu"
    try:
        m.to("mps")
        with torch.no_grad():
            m(pixel_values=torch.zeros(1, 3, 512, 512, device="mps"))
        dev = "mps"
    except Exception:  # noqa: BLE001
        m.to("cpu")
    w, h = img.size
    x = np.asarray(img.resize((512, 512), Image.BILINEAR), dtype=np.float32) / 255.0
    x = (x - MEAN) / STD
    t = torch.from_numpy(x.transpose(2, 0, 1)).float()[None].to(dev)
    with torch.no_grad():
        lg = m(pixel_values=t).logits
        lg = torch.nn.functional.interpolate(lg, size=(h, w), mode="bilinear", align_corners=False)
        return lg.softmax(1)[0, SKY].cpu().numpy()


def coarse_boundary(p: np.ndarray) -> np.ndarray:
    """每列从上往下，找第一个「下面 40 像素里九成都不是天空」的位置，避开孤立的云块误判。"""
    h, w = p.shape
    non = (p <= 0.5).astype(np.float32)
    k = 40
    run = np.cumsum(non[::-1], axis=0)[::-1]          # 从该行到底部的非天空计数
    out = np.zeros(w)
    for c in range(w):
        col = non[:, c]
        cs = np.concatenate([[0], np.cumsum(col)])
        ok = np.where((cs[k:] - cs[:-k]) >= 0.9 * k)[0]
        out[c] = ok[0] if len(ok) else h - 1
    return median_filter(out, size=9)


def refine(img: np.ndarray, coarse: np.ndarray, band: int = 40, lam: float = 0.35, max_step: int = 6,
           despike: int = 9) -> np.ndarray:
    h, w = img.shape[:2]
    L = cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_RGB2LAB)[..., 0].astype(np.float32)
    L = cv2.GaussianBlur(L, (0, 0), 1.0)
    g = np.zeros_like(L)
    g[2:-2] = L[:-4] - L[4:]                      # 上亮下暗为正
    g = np.clip(g, 0, None)
    K = 2 * band + 1
    off = np.arange(-band, band + 1)
    cand = np.clip(coarse[None, :].astype(int) + off[:, None], 0, h - 1)      # K × w
    score = g[cand, np.arange(w)[None, :]]
    scale = np.percentile(score, 95) + 1e-6
    score = score / scale
    score -= 0.15 * (np.abs(off)[:, None] / band)                            # 轻微偏向粗边界
    dp = np.zeros((K, w), np.float64)                # float64：避免大惩罚吞掉小分数
    bp = np.zeros((K, w), np.int32)
    dp[:, 0] = score[:, 0]
    jump_cost = 3.0                                    # 悬崖、树干这类垂直边缘：允许跳，但要付代价
    for c in range(1, w):
        prev = dp[:, c - 1]
        best = np.empty(K, np.float64)
        arg = np.zeros(K, np.int32)
        yc, yp = cand[:, c], cand[:, c - 1]
        for k in range(K):
            dy = np.abs(yp - yc[k]).astype(np.float64)
            v = np.where(dy <= max_step, prev - lam * dy, prev - jump_cost - 0.002 * dy)
            j = int(np.argmax(v))
            best[k], arg[k] = v[j], j
        dp[:, c] = best + score[:, c]
        bp[:, c] = arg
    k = int(np.argmax(dp[:, -1]))
    path = np.zeros(w)
    for c in range(w - 1, -1, -1):
        path[c] = cand[k, c]
        k = bp[k, c]
    if despike > 1:
        path = grey_closing(path, size=despike)     # 去掉比 despike 窄的向上细尖（信号塔等）
    return path.astype(np.float32)


def extract(image: str, out: str, width: int | None = None, save_image: str | None = None,
            preview: str | None = None, band: int = 40) -> np.ndarray:
    """提取山脊，存成 out（.npy）。给了 width 就先缩放到这个宽度，并把缩放后的图存到 save_image：
    之后写歌和出片都用这张，山脊数据只和它对得上。"""
    im = Image.open(image).convert("RGB")
    if width:
        if not save_image:
            raise ValueError("给了 width 就必须给 save_image")
        os.makedirs(os.path.dirname(os.path.abspath(save_image)), exist_ok=True)
        src = Image.open(image)
        if im.width == width and src.format == "JPEG" and not src.info.get("exif") and not src.info.get("icc_profile"):
            shutil.copyfile(image, save_image)       # 已经是 1600 宽的干净 JPEG：原样拷贝，同一张照片写出同一首歌
        else:
            if im.width != width:
                im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
            Image.frombytes("RGB", im.size, im.tobytes()).save(save_image, quality=95)   # 只存像素，不带元数据
    p = sky_prob(im)
    coarse = coarse_boundary(p)
    ys = refine(np.asarray(im, np.float32), coarse, band=band)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    np.save(out, ys)
    if preview:
        from PIL import ImageDraw
        pv = im.copy()
        d = ImageDraw.Draw(pv)
        d.line([(x, float(coarse[x])) for x in range(0, len(coarse), 2)], fill=(255, 60, 60), width=1)
        d.line([(x, float(ys[x])) for x in range(0, len(ys), 1)], fill=(80, 255, 120), width=1)
        os.makedirs(os.path.dirname(os.path.abspath(preview)), exist_ok=True)
        pv.save(preview, quality=92)
    return ys
