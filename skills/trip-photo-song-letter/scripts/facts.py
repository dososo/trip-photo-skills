# SPDX-License-Identifier: Apache-2.0
"""从照片文件夹里读拍摄时间，算出写信能用的事实。只读文件，不改照片，不读 GPS。

由 episode.py facts 调用。输出：总张数、每天的张数和第一张 / 最后一张的时间、连拍（20 秒内连续 ≥ 6 张）、
所标照片的拍摄时间。
读不到 EXIF 拍摄时间的照片会列在 no_time 里，不参与时间统计（不会用文件修改时间冒充）。
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from collections import Counter, defaultdict

from PIL import Image

try:
    import pillow_heif  # 可选：读 HEIC
    pillow_heif.register_heif_opener()
except ImportError:
    pass

EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff"}


def shot_time(path):
    try:
        ex = Image.open(path).getexif()
        v = ex.get_ifd(0x8769).get(36867) or ex.get(306)      # DateTimeOriginal，退而求其次 DateTime
        if v:
            return dt.datetime.strptime(str(v).strip()[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        pass
    if shutil.which("sips"):                                  # macOS：没装 pillow-heif 时，用系统自带的 sips 读 HEIC 拍摄时间
        try:
            out = subprocess.run(["sips", "-g", "creation", path], capture_output=True, text=True, timeout=20).stdout
            for line in out.splitlines():
                if "creation:" in line:
                    return dt.datetime.strptime(line.split("creation:")[1].strip()[:19], "%Y:%m:%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            return None
    return None


def collect(folder: str, marks: list[str] | None = None) -> dict:
    """读文件夹里每张照片的拍摄时间，返回 facts.json 的内容。"""
    items, no_time = [], []
    for root, _, fs in os.walk(folder):
        for f in sorted(fs):
            if os.path.splitext(f)[1].lower() not in EXTS:
                continue
            p = os.path.join(root, f)
            t = shot_time(p)
            (items.append((t, f)) if t else no_time.append(f))
    items.sort()
    days = defaultdict(list)
    for t, f in items:
        days[t.date().isoformat()].append(t)
    bursts, cur = [], items[:1]
    for prev, it in zip(items, items[1:]):
        if (it[0] - prev[0]).total_seconds() <= 20:
            cur.append(it)
        else:
            if len(cur) >= 6:
                bursts.append(dict(start=cur[0][0].strftime("%m-%d %H:%M:%S"), count=len(cur),
                                   seconds=int((cur[-1][0] - cur[0][0]).total_seconds())))
            cur = [it]
    if len(cur) >= 6:
        bursts.append(dict(start=cur[0][0].strftime("%m-%d %H:%M:%S"), count=len(cur),
                           seconds=int((cur[-1][0] - cur[0][0]).total_seconds())))
    marked = {}
    for m in marks or []:
        hit = [t for t, f in items if f == os.path.basename(m)]
        marked[os.path.basename(m)] = hit[0].strftime("%Y-%m-%d %H:%M:%S") if hit else None
    return dict(schema_version="1", total=len(items) + len(no_time), with_time=len(items),
                days={d: dict(count=len(v), first=v[0].strftime("%H:%M"), last=v[-1].strftime("%H:%M")) for d, v in sorted(days.items())},
                by_hour=dict(sorted(Counter(t.hour for t, _ in items).items())), bursts=bursts, marks=marked, no_time=no_time[:50])
