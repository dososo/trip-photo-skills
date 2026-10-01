#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""trip-photo-lantern：把一个文件夹的旅行照片，做成一盏会转的走马灯（单文件网页）。

  python3 lantern.py check                                   检查依赖
  python3 lantern.py all   --photos <照片文件夹> --out <输出文件夹>   一条龙：1→5
  python3 lantern.py scan  --photos <dir> --out <dir>        1 读照片：转 sRGB、去掉全部元数据，生成工作副本和清单
  python3 lantern.py mask  --out <dir> [--limit 240]         2 本机语义分割：天空概率图 + 人物/建筑/植被占比
  python3 lantern.py pick  --out <dir> [--ids 0003,0017,…]   3 挑 8 张天际线清楚、彼此不重复的（也可手动指定）
  python3 lantern.py strip --out <dir>                       4 剪成剪纸，拼成灯筒的展开长条
  python3 lantern.py html  --out <dir> [--title …]           5 打包成单文件网页 lantern.html，双击即可打开

每一步的结果都写在 <out>/work/，可以单独重跑。地名写在 <out>/work/names.json（键是照片编号），改完重跑 strip 和 html。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import io
import json
import math
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parent / "assets" / "runtime"
sys.path.insert(0, str(HERE))

EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff"}
N = 8                       # 八角灯，八格
W, H = 8192, 2048           # 灯筒展开长条（与着色器约定：第 0 行是灯筒顶部）
BAND0, BAND1 = 384, 1408    # 天际线带
PW = W // N
GAP = 14                    # 格与格之间的纸条
LOWER = 200                 # 下部实纸的透光度（255 = 完全不透光）


def log(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 2) -> None:
    print(f"✗ {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def need_base() -> None:
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
    except ImportError as err:
        die(f"缺少依赖 {err.name}；请运行：python3 -m pip install pillow numpy")


def paths(out: str) -> tuple[Path, Path]:
    o = Path(out).expanduser().resolve()
    return o, o / "work"


def load_json(p: Path, what: str):
    if not p.exists():
        die(f"找不到 {p}；请先运行「{what}」这一步")
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 1 scan

def _register_heif() -> None:
    try:
        import pillow_heif  # type: ignore

        pillow_heif.register_heif_opener()
    except ImportError:
        pass


def _open_any(p: Path):
    from PIL import Image

    try:
        im = Image.open(p)
        im.load()
        return im
    except Exception:
        if p.suffix.lower() in (".heic", ".heif") and platform.system() == "Darwin" and shutil.which("sips"):
            tmp = Path(tempfile.mkdtemp()) / (p.stem + ".jpg")
            subprocess.run(["sips", "-s", "format", "jpeg", str(p), "--out", str(tmp)], check=True, capture_output=True)
            im = Image.open(tmp)
            im.load()
            return im
        raise


def _exif(im) -> dict:
    out: dict = {}
    try:
        ex = im.getexif()
    except Exception:  # noqa: BLE001
        return out
    if not ex:
        return out
    sub = ex.get_ifd(0x8769)
    raw = sub.get(36867) or ex.get(306)
    if raw:
        try:
            out["date"] = dt.datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S").isoformat()
        except ValueError:
            pass
    gps = ex.get_ifd(0x8825)
    try:
        if 2 in gps and 4 in gps:
            dms = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600  # noqa: E731
            lat = dms(gps[2]) * (-1 if str(gps.get(1, "N")).upper().startswith("S") else 1)
            lon = dms(gps[4]) * (-1 if str(gps.get(3, "E")).upper().startswith("W") else 1)
            out["gps"] = [round(lat, 5), round(lon, 5)]
    except Exception:  # noqa: BLE001
        pass
    return out


def _to_srgb(im, icc):
    from PIL import ImageCms

    im = im.convert("RGB")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            im = ImageCms.profileToProfile(im, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except Exception:  # noqa: BLE001
            pass
    return im


def _prefilter(im) -> float:
    """便宜的预筛分：顶部亮而平滑（像天空）、整体不太暗的照片得分高。"""
    import numpy as np

    t = im.copy()
    t.thumbnail((256, 256))
    a = np.asarray(t.convert("L"), dtype=np.float32) / 255.0
    top = a[: max(4, a.shape[0] // 4)]
    rough = float(np.abs(np.diff(top, axis=0)).mean() + np.abs(np.diff(top, axis=1)).mean())
    return round(float(top.mean()) * math.exp(-rough * 25) * (1.0 if a.mean() > 0.15 else 0.2), 4)


def cmd_scan(a) -> None:
    need_base()
    from PIL import Image, ImageOps

    _register_heif()
    src_dir = Path(a.photos).expanduser().resolve()
    if not src_dir.is_dir():
        die(f"照片文件夹不存在：{src_dir}")
    out, work = paths(a.out)
    (work / "src").mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in src_dir.rglob("*") if p.suffix.lower() in EXTS and not p.name.startswith("."))
    if not files:
        die(f"在 {src_dir} 里没找到照片（支持 {', '.join(sorted(EXTS))}）")
    items = []
    for i, p in enumerate(files, 1):
        try:
            im = _open_any(p)
            meta = _exif(im)
            icc = im.info.get("icc_profile")
            im = _to_srgb(ImageOps.exif_transpose(im), icc)
        except Exception as err:  # noqa: BLE001
            log(f"  跳过 {p.name}：{type(err).__name__}")
            continue
        w, h = im.size
        if min(w, h) < 600:
            log(f"  跳过 {p.name}：分辨率太低（{w}×{h}）")
            continue
        im.thumbnail((2400, 2400), Image.LANCZOS)
        im.info = {}
        pid = f"{len(items) + 1:04d}"
        im.save(work / "src" / f"{pid}.jpg", quality=92)   # 不写 exif / icc：工作副本里没有任何元数据
        items.append({"id": pid, "source": str(p), "size": [w, h], "pre": _prefilter(im), **meta})
        if i % 50 == 0:
            log(f"  已读 {i}/{len(files)}")
    if not items:
        die("没有可用的照片")
    (work / "manifest.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"✓ 1 scan：{len(items)} 张 → {work / 'src'}（已转 sRGB，不含 EXIF / GPS / 机型）")


# ---------------------------------------------------------------- 2 mask

def cmd_mask(a) -> None:
    need_base()
    from PIL import Image

    import segment

    ok, why = segment.check()
    if not ok:
        die(why)
    out, work = paths(a.out)
    man = load_json(work / "manifest.json", "scan")
    (work / "masks").mkdir(exist_ok=True)
    cand = sorted(man, key=lambda x: -x["pre"])
    if a.limit and len(cand) > a.limit:
        log(f"  共 {len(cand)} 张，按预筛分只分割前 {a.limit} 张（用 --limit 调整）")
        cand = cand[: a.limit]
    todo = [it for it in cand if a.force or not (work / "masks" / f"{it['id']}.json").exists()]
    if not todo:
        log("✓ 2 mask：全部已分割过（--force 可重跑）")
        return
    log(f"  加载模型 {segment.MODEL_ID}（{segment.MODEL_LICENSE}）；首次运行会下载约 240 MB 权重，照片不上传")
    seg = segment.Segmenter(device=a.device)
    log(f"  设备：{seg.device}")
    for i, it in enumerate(todo, 1):
        im = Image.open(work / "src" / f"{it['id']}.jpg").convert("RGB")
        sky, stats = seg.run(im)
        sky.save(work / "masks" / f"{it['id']}_sky.png")
        (work / "masks" / f"{it['id']}.json").write_text(json.dumps(stats), encoding="utf-8")
        if i % 20 == 0 or i == len(todo):
            log(f"  已分割 {i}/{len(todo)}")
    log(f"✓ 2 mask：{len(todo)} 张 → {work / 'masks'}")


# ---------------------------------------------------------------- 3 pick

def _bell(x: float, lo: float, hi: float, soft: float) -> float:
    if x < lo:
        return math.exp(-(((lo - x) / soft) ** 2))
    if x > hi:
        return math.exp(-(((x - hi) / soft) ** 2))
    return 1.0


def _skyline(src_jpg: Path, sky_png: Path) -> dict:
    import numpy as np
    from PIL import Image

    im = Image.open(src_jpg).convert("L")
    im.thumbnail((256, 256))
    lum = np.asarray(im, dtype=np.float32) / 255.0
    sky = np.asarray(Image.open(sky_png).convert("L").resize(im.size, Image.BILINEAR), dtype=np.float32) / 255.0 > 0.5
    h = sky.shape[0]
    ground = ~sky
    first = np.where(ground.any(axis=0), ground.argmax(axis=0), h) / h   # 每一列天际线的高度（0 = 顶）
    sky_lum = float(np.median(lum[sky])) if sky.any() else 0.0
    holes = float(((lum > sky_lum * 0.86) & ground).sum() / max(1, ground.sum()))
    return {
        "top": float(sky[0].mean()),                   # 顶边是天空的列占比
        "frac": float(sky.mean()),
        "struct": float(first.std()),                  # 天际线起伏
        "tv": float(np.abs(np.diff(first)).mean()),    # 锯齿程度（树多就高）
        "sky_lum": sky_lum,                            # 天空亮度：夜景天空暗，剪出来全是碎洞
        "holes": holes,                                # 地面里比天空还亮的部分（水面反光、灯火）会刻成镂空
        "prof": [round(float(v), 3) for v in first[np.linspace(0, len(first) - 1, 32).astype(int)]],
    }


def _dhash(p: Path) -> int:
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(p).convert("L").resize((9, 8), Image.BILINEAR), dtype=np.int16)
    v = 0
    for b in (a[:, 1:] > a[:, :-1]).flatten():
        v = (v << 1) | int(b)
    return v


def _km(p, q) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (p[0], p[1], q[0], q[1]))
    x = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(x))


def _far(c: dict, d: dict, gap_s: float, gap_km: float, ham: int) -> bool:
    bits = bin(c["hash"] ^ d["hash"]).count("1")
    shape = sum(abs(x - y) for x, y in zip(c["prof"], d["prof"])) / len(c["prof"])
    if bits < ham or (bits < ham + 12 and shape < 0.06):   # 画面像、天际线也像：同一处的连拍
        return False
    if c.get("date") and d.get("date"):
        t = abs((dt.datetime.fromisoformat(c["date"]) - dt.datetime.fromisoformat(d["date"])).total_seconds())
        if t < gap_s:
            return False
    if c.get("gps") and d.get("gps") and _km(c["gps"], d["gps"]) < gap_km:
        return False
    return True


def _sheet(work: Path, ids: list[str]) -> Path:
    from PIL import Image, ImageDraw, ImageOps

    cell = 300
    sheet = Image.new("RGB", (cell * 4 + 50, cell * 2 + 30), (20, 16, 13))
    d = ImageDraw.Draw(sheet)
    for k, pid in enumerate(ids):
        im = ImageOps.fit(Image.open(work / "src" / f"{pid}.jpg").convert("RGB"), (cell, cell))
        x, y = 10 + (k % 4) * (cell + 10), 10 + (k // 4) * (cell + 10)
        sheet.paste(im, (x, y))
        d.rectangle([x, y, x + 64, y + 26], fill=(20, 16, 13))
        d.text((x + 6, y + 6), pid, fill=(233, 166, 72))
    p = work / "picks_sheet.jpg"
    sheet.save(p, quality=88)
    return p


def cmd_pick(a) -> None:
    need_base()
    out, work = paths(a.out)
    man = {it["id"]: it for it in load_json(work / "manifest.json", "scan")}
    if a.ids:
        ids = [s.strip().zfill(4) for s in a.ids.split(",") if s.strip()]
        bad = [i for i in ids if i not in man]
        if bad:
            die(f"这些编号不在清单里：{', '.join(bad)}")
        if len(ids) != N:
            die(f"需要正好 {N} 张，现在是 {len(ids)} 张")
        missing = [i for i in ids if not (work / "masks" / f"{i}_sky.png").exists()]
        if missing:
            die(f"这些照片还没分割：{', '.join(missing)}；先运行 mask --force 或把 --limit 调大")
        chosen = ids
        log("  使用手动指定的 8 张")
    else:
        cands = []
        for pid, it in man.items():
            js = work / "masks" / f"{pid}.json"
            if not js.exists():
                continue
            st = json.loads(js.read_text(encoding="utf-8"))
            if st["person"] > a.max_person:
                continue
            sl = _skyline(work / "src" / f"{pid}.jpg", work / "masks" / f"{pid}_sky.png")
            score = (sl["top"] ** 1.5) * _bell(sl["frac"], 0.15, 0.6, 0.1) * _bell(sl["struct"], 0.04, 0.2, 0.05) \
                * _bell(sl["tv"], 0.0, 0.035, 0.03) * _bell(sl["sky_lum"], 0.45, 1.0, 0.15) * _bell(sl["holes"], 0.0, 0.05, 0.05)
            cands.append({**it, **sl, "person": st["person"], "score": round(score, 4), "hash": _dhash(work / "src" / f"{pid}.jpg")})
        cands.sort(key=lambda c: -c["score"])
        if len(cands) < N:
            die(f"天际线可用的照片只有 {len(cands)} 张，不够 {N} 张；可以加照片、调大 --limit，或用 --ids 手动指定")
        chosen_c: list[dict] = []
        for gap_s, gap_km, ham in ((1800, 0.5, 14), (600, 0.2, 12), (120, 0.0, 10), (0, 0.0, 4)):
            chosen_c = []
            for c in cands:
                if all(_far(c, d, gap_s, gap_km, ham) for d in chosen_c):
                    chosen_c.append(c)
                    if len(chosen_c) == N:
                        break
            if len(chosen_c) == N:
                break
        chosen = [c["id"] for c in chosen_c]
        low = [c["id"] for c in chosen_c if c["score"] < 0.25]
        if low:
            log(f"  提醒：{', '.join(low)} 的天际线分数偏低，剪影可能不清楚，可用 --ids 换掉")
    if all(man[i].get("date") for i in chosen):
        chosen.sort(key=lambda i: man[i]["date"])          # 按时间排一圈，转起来就是一趟旅程
    (work / "picks.json").write_text(json.dumps({"ids": chosen}, indent=1), encoding="utf-8")
    sheet = _sheet(work, chosen)
    names = work / "names.json"
    if not names.exists():
        names.write_text(json.dumps({i: "" for i in chosen}, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        cur = json.loads(names.read_text(encoding="utf-8"))
        names.write_text(json.dumps({i: cur.get(i, "") for i in chosen}, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"✓ 3 pick：{', '.join(chosen)}；预览 {sheet}；地名填在 {names}")


# ---------------------------------------------------------------- 4 strip

def _crop_box(alpha, frac: float = 0.46):
    """取正方形：让天际线落在格子 46% 高处。"""
    import numpy as np

    m = np.asarray(alpha) > 127
    h, w = m.shape
    s = min(w, h)
    tops = [int(np.argmax(m[:, x])) for x in range(0, w, max(1, w // 200)) if m[:, x].any()]
    sky = int(np.median(tops)) if tops else h // 2
    top = int(np.clip(sky - frac * s, 0, h - s))
    left = (w - s) // 2
    return (left, top, left + s, top + s)


def _huiwen(draw, y0: int, y1: int, step: int = 64) -> None:
    t = 6
    for x0 in range(0, W, step):
        u = step - 12
        x, y = x0 + 6, y0 + (y1 - y0 - u) // 2
        pts = [(x, y + u), (x, y), (x + u, y), (x + u, y + u - 4 * t), (x + 4 * t, y + u - 4 * t), (x + 4 * t, y + 4 * t), (x + u - 4 * t, y + 4 * t)]
        draw.line(pts, fill=0, width=t, joint="curve")


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    d = dt.datetime.fromisoformat(iso)
    return f"{d.year}.{d.month}.{d.day}"


def cmd_strip(a) -> None:
    need_base()
    from PIL import Image, ImageDraw

    from silhouette import make

    out, work = paths(a.out)
    man = {it["id"]: it for it in load_json(work / "manifest.json", "scan")}
    ids = load_json(work / "picks.json", "pick")["ids"]
    names = json.loads((work / "names.json").read_text(encoding="utf-8")) if (work / "names.json").exists() else {}
    paper = Image.new("L", (W, H), 0)
    photo = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(paper)
    d.rectangle([0, 0, W, 23], fill=255)                 # 顶边纸条
    d.rectangle([0, BAND1, W, H], fill=LOWER)            # 下部实纸（微微透光）
    _huiwen(d, 1560, 1640)                               # 一圈回纹镂空
    d.rectangle([0, 1680, W, 1688], fill=0)              # 一道细缝
    panels = []
    for i, pid in enumerate(ids):
        im, alpha, _ = make(work / "src" / f"{pid}.jpg", work / "masks" / f"{pid}_sky.png", long_edge=2400, hole_gain=a.hole_gain)
        box = _crop_box(alpha)
        size = (PW - GAP, BAND1 - BAND0)
        cut = alpha.crop(box).resize(size, Image.LANCZOS).point(lambda v: 255 if v > 127 else 0)
        x = i * PW + GAP // 2
        paper.paste(cut, (x, BAND0))
        photo.paste(im.crop(box).resize(size, Image.LANCZOS), (x, BAND0))
        d.rectangle([i * PW - GAP // 2, BAND0, i * PW + GAP // 2, BAND1], fill=255)
        panels.append({"id": pid, "name": names.get(pid, ""), "date": _fmt_date(man[pid].get("date")), "u0": i / N, "u1": (i + 1) / N})
    d.rectangle([W - GAP // 2, BAND0, W, BAND1], fill=255)
    paper.save(out / "strip_paper.png", optimize=True)
    photo.save(out / "strip_photo.jpg", quality=90)
    (out / "panels.json").write_text(json.dumps({"width": W, "height": H, "band": [BAND0 / H, BAND1 / H], "panels": panels}, ensure_ascii=False, indent=1), encoding="utf-8")
    paper.resize((2048, 512)).save(work / "strip_preview.png")
    log(f"✓ 4 strip：{out / 'strip_paper.png'}、{out / 'strip_photo.jpg'}、{out / 'panels.json'}")


# ---------------------------------------------------------------- 5 html

def _data_url(img, fmt: str, **kw) -> str:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def cmd_html(a) -> None:
    need_base()
    from PIL import Image

    out, work = paths(a.out)
    meta = load_json(out / "panels.json", "strip")
    bundle = RUNTIME / "lantern.bundle.js"
    if not bundle.exists():
        die(f"缺少 {bundle}；请运行 scripts/build_runtime.sh 重新打包")
    paper = Image.open(out / "strip_paper.png").convert("L")
    photo = Image.open(out / "strip_photo.jpg").convert("RGB").resize((W // 2, H // 2), Image.LANCZOS)
    dates = [p["date"] for p in meta["panels"] if p["date"]]
    subtitle = a.subtitle if a.subtitle is not None else (f"{dates[0]} – {dates[-1]}" if len(dates) > 1 else (dates[0] if dates else ""))
    data = {
        "paper": _data_url(paper, "PNG", optimize=True),
        "photo": _data_url(photo, "JPEG", quality=86),
        "panels": [{"name": p["name"], "date": p["date"]} for p in meta["panels"]],
    }
    page = (RUNTIME / "template.html").read_text(encoding="utf-8")
    page = page.replace("{{TITLE}}", html.escape(a.title)).replace("{{SUBTITLE}}", html.escape(subtitle))
    page = page.replace("{{DATA}}", json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"))
    page = page.replace("{{BUNDLE}}", bundle.read_text(encoding="utf-8").replace("</script", "<\\/script"))
    dst = out / a.name
    dst.write_text(page, encoding="utf-8")
    log(f"✓ 5 html：{dst}（{dst.stat().st_size / 1e6:.1f} MB，单文件，双击用浏览器打开）")


# ---------------------------------------------------------------- check / all

def cmd_check(a) -> None:
    ok = True
    log(f"Python {platform.python_version()}（{platform.system()} {platform.machine()}）")
    for mod, hint in (("PIL", "pillow"), ("numpy", "numpy"), ("torch", "torch"), ("transformers", "transformers")):
        try:
            m = __import__(mod)
            log(f"  ✓ {hint} {getattr(m, '__version__', '')}")
        except ImportError:
            ok = False
            log(f"  ✗ {hint} 未安装")
    try:
        __import__("pillow_heif")
        log("  ✓ pillow-heif（可读 HEIC）")
    except ImportError:
        log("  · pillow-heif 未安装：HEIC 照片" + ("会用系统自带的 sips 转换" if platform.system() == "Darwin" else "会被跳过；需要时 pip install pillow-heif"))
    log(f"  {'✓' if (RUNTIME / 'lantern.bundle.js').exists() else '✗'} 运行时 {RUNTIME / 'lantern.bundle.js'}")
    if not ok:
        die("缺依赖；请运行：python3 -m pip install pillow numpy torch transformers")
    log("✓ 依赖齐全")


def cmd_all(a) -> None:
    cmd_scan(a)
    cmd_mask(a)
    cmd_pick(a)
    cmd_strip(a)
    cmd_html(a)


def main() -> None:
    ap = argparse.ArgumentParser(prog="lantern.py", description="把旅行照片做成一盏走马灯（单文件网页）。所有处理都在本机。")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, photos=False):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if name != "check":
            p.add_argument("--out", required=True, help="输出文件夹")
        if photos:
            p.add_argument("--photos", required=True, help="照片文件夹（会递归读取子文件夹）")
        return p

    add("check", cmd_check)
    add("scan", cmd_scan, photos=True)
    for name, fn, photos in (("mask", cmd_mask, False), ("all", cmd_all, True)):
        p = add(name, fn, photos)
        p.add_argument("--limit", type=int, default=240, help="最多分割多少张（按预筛分从高到低）")
        p.add_argument("--force", action="store_true", help="已分割过的也重跑")
        p.add_argument("--device", choices=["cpu", "mps"], default=None)
    for name in ("pick", "all"):
        p = sub.choices[name] if name in sub.choices and name == "all" else add(name, cmd_pick)
        p.add_argument("--ids", default=None, help="手动指定 8 张，逗号分隔的照片编号")
        p.add_argument("--max-person", type=float, default=0.002, help="人物面积超过这个比例的照片不选（默认 0.2%%）")
    for name in ("strip", "all"):
        p = sub.choices[name] if name == "all" else add(name, cmd_strip)
        p.add_argument("--hole-gain", type=float, default=0.86, help="地面亮斑刻成镂空的阈值，越小镂空越多")
    for name in ("html", "all"):
        p = sub.choices[name] if name == "all" else add(name, cmd_html)
        p.add_argument("--title", default="走马灯")
        p.add_argument("--subtitle", default=None, help="默认用照片日期范围")
        p.add_argument("--name", default="lantern.html", help="输出文件名")

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
