# SPDX-License-Identifier: Apache-2.0
"""运行时素材：下载到本机缓存（不进仓库），以及检查缓存齐不齐。由 episode.py fetch / check 调用。
  - VSCO 2 Community Edition 的一部分乐器采样（CC0）：钢琴、弦乐、竖琴、长笛、钢片琴、军鼓、铃鼓、手鼓、锣
  - 霞鹜文楷 LXGW WenKai Regular / Medium（SIL OFL 1.1）
缓存目录：$SONGLETTER_CACHE，默认 ~/.cache/trip-photo-song-letter
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

CACHE = os.environ.get("SONGLETTER_CACHE", os.path.expanduser("~/.cache/trip-photo-song-letter"))
VSCO_TREE = "https://api.github.com/repos/sgossner/VSCO-2-CE/git/trees/master?recursive=1"
VSCO_RAW = "https://raw.githubusercontent.com/sgossner/VSCO-2-CE/master/"
WANT = [
    r"^Keys/Upright Piano/(Player_dyn2_rr1_\d{3}\.wav|MappingChart\.txt|Info\.txt)$",
    r"^Strings/Harp/KSHarp_[A-G]#?\d_mf\.wav$",
    r"^Percussion/Glock/glock_medium_[A-G]#?\d\.wav$",
    r"^Strings/Violin Section/susVib/VlnEns_susVib_[A-G]#?\d_v1\.wav$",
    r"^Strings/Viola Section/susvib/ViolaEns_susvib_[A-G]#?\d_v1_1\.wav$",
    r"^Strings/Cello Section/susvib/susvib_[A-G]#?\d_v1_1\.wav$",
    r"^Woodwinds/Flute/susvib/LDFlute_susvib_[A-G]#?\d_v1_1\.wav$",
    r"^Percussion/(Snare2-HitSN_v3_rr[12]|Snare2-HitSN_v5_rr1|Snare2-rollSN_v3_rr1|Snare2-taps_v1_rr[12]|Tamb1-Hit_v1_rr[12]|Tamb1-Hit_v2_rr1|Conga-HitN_v[12]_rr1|Conga-Tap1_v1_rr1)_Sum\.wav$",
    r"^Miscellania Raw/Misc 1/cymbgong_pp\.wav$",
    r"^LICENSE$",
]
FONTS = [
    ("https://github.com/lxgw/LxgwWenKai/releases/download/v1.522/LXGWWenKai-Regular.ttf", "fonts/LXGWWenKai-Regular.ttf"),
    ("https://github.com/lxgw/LxgwWenKai/releases/download/v1.522/LXGWWenKai-Medium.ttf", "fonts/LXGWWenKai-Medium.ttf"),
    ("https://raw.githubusercontent.com/lxgw/LxgwWenKai/main/OFL.txt", "fonts/OFL.txt"),
]


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "trip-photo-song-letter"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def missing() -> list[str]:
    """缓存里缺的关键文件（只看几个代表文件，不联网）。"""
    need = [os.path.join(CACHE, "vsco2ce", "Keys", "Upright Piano", "MappingChart.txt"),
            os.path.join(CACHE, "vsco2ce", "Strings", "Harp"),
            os.path.join(CACHE, "fonts", "LXGWWenKai-Regular.ttf"),
            os.path.join(CACHE, "fonts", "LXGWWenKai-Medium.ttf")]
    return [p for p in need if not os.path.exists(p)]


def plan() -> list[tuple[str, int]]:
    """联网读 VSCO-2-CE 的文件树，返回要下载的 (路径, 字节数)。"""
    tree = json.loads(get(VSCO_TREE))["tree"]
    pats = [re.compile(p) for p in WANT]
    return [(t["path"], t["size"]) for t in tree if t["type"] == "blob" and any(p.match(t["path"]) for p in pats)]


def _save(data: bytes, out: str) -> None:
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, out)                              # 先写临时文件再改名，中断不会留下半个文件


def download(files: list[tuple[str, int]], log=print) -> int:
    """下载采样和字体；已存在且大小一致的跳过。返回就绪的采样数。"""
    ok = 0
    for path, size in files:
        out = os.path.join(CACHE, "vsco2ce", path)
        if os.path.exists(out) and os.path.getsize(out) == size:
            ok += 1
            continue
        data = get(VSCO_RAW + urllib.parse.quote(path), timeout=120)
        if len(data) != size:
            raise RuntimeError(f"大小不符：{path}")
        _save(data, out)
        ok += 1
        log(f"  {ok}/{len(files)} {path}")
    for url, rel in FONTS:
        out = os.path.join(CACHE, rel)
        if os.path.exists(out) and os.path.getsize(out) > 1000:
            continue
        data = get(url, timeout=300)
        _save(data, out)
        log(f"  {rel} {len(data) / 1e6:.1f} MB")
    return ok
