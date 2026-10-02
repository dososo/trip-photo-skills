# SPDX-License-Identifier: Apache-2.0
"""把选中的照片准备成工作副本：转 sRGB、去掉全部 EXIF / GPS / 机型、长边缩到 2400 像素，并做一次「画面里有没有人」的提醒。

由 episode.py prepare 调用。输出：<文件夹>/<原文件名去扩展名>.jpg；以及 people.json
（每张照片里「人」的像素占比，用 UperNet 分割估计，仅作提醒，远处很小的人会漏）。原照片不会被修改。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass


def _open(p: str) -> Image.Image:
    """打开照片；没装 pillow-heif 时，macOS 上用系统自带的 sips 把 HEIC 转成临时 JPEG 再读。"""
    try:
        im = Image.open(p)
        im.load()
        return im
    except Exception:  # noqa: BLE001
        if not shutil.which("sips"):
            raise
    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        subprocess.run(["sips", "-s", "format", "jpeg", p, "--out", tmp], check=True, capture_output=True, timeout=60)
        im = Image.open(tmp)
        im.load()
        return im
    finally:
        os.remove(tmp)


def prepare(photos: list[str], out: str, log=print) -> dict:
    """返回 {工作副本文件名: 人物像素占比}，同时写 <out>/people.json。"""
    os.makedirs(out, exist_ok=True)
    import torch
    from transformers import UperNetForSemanticSegmentation
    model = UperNetForSemanticSegmentation.from_pretrained("openmmlab/upernet-convnext-tiny").eval()
    mean, std = np.array([0.485, 0.456, 0.406]), np.array([0.229, 0.224, 0.225])
    people = {}
    for p in photos:
        im = ImageOps.exif_transpose(_open(p))
        if "icc_profile" in im.info:
            try:
                from PIL import ImageCms
                import io
                src = ImageCms.ImageCmsProfile(io.BytesIO(im.info["icc_profile"]))
                im = ImageCms.profileToProfile(im, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
            except Exception:  # noqa: BLE001
                im = im.convert("RGB")
        im = im.convert("RGB")
        im.thumbnail((2400, 2400), Image.LANCZOS)
        name = os.path.splitext(os.path.basename(p))[0] + ".jpg"
        dst = os.path.join(out, name)
        clean = Image.frombytes("RGB", im.size, im.tobytes())   # 只拷像素：不带任何 EXIF / XMP / ICC
        clean.save(dst, quality=93)
        x = (np.asarray(clean.resize((512, 512), Image.BILINEAR), np.float32) / 255.0 - mean) / std
        with torch.no_grad():
            lab = model(pixel_values=torch.from_numpy(x.transpose(2, 0, 1)).float()[None]).logits.argmax(1)[0].numpy()
        ratio = float((lab == 12).mean())                    # ADE20K 第 12 类 = person
        people[name] = round(ratio, 5)
        warn = ratio > 0.002
        log(("⚠ " if warn else "✓ ") + dst + (f"  画面里约 {ratio * 100:.1f}% 是人：请换片，或确认生图时会去掉" if warn else ""))
    json.dump(people, open(os.path.join(out, "people.json"), "w"), ensure_ascii=False, indent=1)
    return people
