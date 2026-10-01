# SPDX-License-Identifier: Apache-2.0
"""本机语义分割：给每张照片算出「天空概率图」和人物、建筑、植被的面积占比。

模型：openmmlab/upernet-convnext-tiny（MIT 许可，ADE20K 150 类，约 240 MB）。
首次运行会从 Hugging Face 下载模型权重；照片本身不上传，推理在本机 CPU / Apple MPS 上跑。

不用 transformers 的 AutoImageProcessor（它依赖 torchvision）：
官方 preprocessor_config 就是「缩放到 512×512（双线性）→ 除以 255 → ImageNet 均值方差归一化」，
这里用 Pillow + numpy 手写。
"""
from __future__ import annotations

import contextlib
import io
import logging
import os

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

MODEL_ID = "openmmlab/upernet-convnext-tiny"
MODEL_LICENSE = "MIT"
INPUT = 512
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)

# ADE20K 类别编号（0 起）
SKY = 2
PERSON = 12
BUILDING = (1, 25, 48)          # building, house, skyscraper
PLANT = (4, 9, 17, 66, 72)      # tree, grass, plant, flower, palm


class SegmentError(RuntimeError):
    pass


def check() -> tuple[bool, str]:
    """依赖是否齐全。不下载任何东西。"""
    try:
        import numpy  # noqa: F401
        import torch  # noqa: F401
        from PIL import Image  # noqa: F401
        from transformers import UperNetForSemanticSegmentation  # noqa: F401
    except ImportError as err:
        return False, f"缺少依赖 {err.name}；请运行：python3 -m pip install torch transformers pillow numpy"
    return True, "ok"


class Segmenter:
    def __init__(self, device: str | None = None):
        ok, why = check()
        if not ok:
            raise SegmentError(why)
        import torch
        from transformers import UperNetForSemanticSegmentation

        logging.getLogger("transformers").setLevel(logging.ERROR)
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
        self.torch = torch
        with contextlib.redirect_stderr(io.StringIO()):
            self.model = UperNetForSemanticSegmentation.from_pretrained(MODEL_ID)
        self.model.eval()
        self.device = "cpu"
        want_mps = device in (None, "mps") and torch.backends.mps.is_available()
        if want_mps:
            # UperNet 的金字塔池化在部分 PyTorch 版本的 MPS 上会报错；先空跑一次，失败就退回 CPU
            try:
                self.model.to("mps")
                with torch.no_grad():
                    self.model(pixel_values=torch.zeros(1, 3, INPUT, INPUT, device="mps"))
                self.device = "mps"
            except Exception:  # noqa: BLE001
                self.model.to("cpu")
                self.device = "cpu"
        self.model.to(self.device)

    def run(self, image):
        """image: PIL RGB。返回 (sky_prob: PIL L 原图尺寸, stats: dict)。"""
        import numpy as np
        from PIL import Image

        w, h = image.size
        x = np.asarray(image.resize((INPUT, INPUT), Image.BILINEAR), dtype=np.float32) / 255.0
        x = (x - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)
        t = self.torch.from_numpy(x).permute(2, 0, 1).unsqueeze(0).to(self.device)
        with self.torch.no_grad():
            logits = self.model(pixel_values=t).logits
            probs = self.torch.softmax(logits, dim=1)[0]
            label = probs.argmax(dim=0).cpu().numpy()
            sky = probs[SKY].cpu().numpy()
        total = float(label.size)
        stats = {
            "sky": round(float((label == SKY).sum()) / total, 5),
            "person": round(float((label == PERSON).sum()) / total, 5),
            "building": round(float(np.isin(label, BUILDING).sum()) / total, 5),
            "plant": round(float(np.isin(label, PLANT).sum()) / total, 5),
        }
        sky_img = Image.fromarray((np.clip(sky, 0, 1) * 255).astype(np.uint8), "L").resize((w, h), Image.BILINEAR)
        return sky_img, stats
