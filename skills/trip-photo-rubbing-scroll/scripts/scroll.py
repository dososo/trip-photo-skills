#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""trip-photo-rubbing-scroll：把几张旅行照片做成一卷「拓片长卷」（单文件网页）。

  python3 scroll.py check                                  检查依赖
  python3 scroll.py all   --photos <照片文件夹> --out <输出文件夹>   一条龙：build → html
  python3 scroll.py build --photos <dir> --out <dir>       读照片（转 sRGB、去元数据）、按拍摄时间排序、算拓片白纹
  python3 scroll.py html  --out <dir> [--title …]          打包成单文件网页 scroll.html，双击即可打开

每张照片是卷上的一段，按拍摄时间从右往左排（手卷的读法：卷首在最右）。
地名、印文（和缺 EXIF 时的日期）写在 <out>/work/names.json（键是段号），改完重跑 html。
只需要 Pillow 和 numpy；装了 scipy 会快一些。所有处理都在本机。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import io
import json
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
STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
SEASONS = {12: "冬", 1: "冬", 2: "冬", 3: "春", 4: "春", 5: "春", 6: "夏", 7: "夏", 8: "夏", 9: "秋", 10: "秋", 11: "秋"}


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


def seal_date(iso: str | None) -> str:
    """拍摄日期 → 干支年 + 季，例如 2024-10-01 → 甲辰秋（立春前算上一年）。"""
    if not iso:
        return ""
    d = dt.datetime.fromisoformat(iso)
    y = d.year - (1 if (d.month == 1 or (d.month == 2 and d.day < 4)) else 0)
    return STEMS[(y - 4) % 10] + BRANCHES[(y - 4) % 12] + SEASONS[d.month]


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


def _date(im) -> str | None:
    try:
        ex = im.getexif()
        raw = ex.get_ifd(0x8769).get(36867) or ex.get(306)
        return dt.datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S").isoformat() if raw else None
    except Exception:  # noqa: BLE001
        return None


def _to_srgb(im, icc):
    from PIL import ImageCms

    im = im.convert("RGB")
    if icc:
        try:
            im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(io.BytesIO(icc)), ImageCms.createProfile("sRGB"), outputMode="RGB")
        except Exception:  # noqa: BLE001
            pass
    return im


def cmd_build(a) -> None:
    need_base()
    import numpy as np
    from PIL import Image, ImageOps

    from rubbing import white_map

    _register_heif()
    src_dir = Path(a.photos).expanduser().resolve()
    if not src_dir.is_dir():
        die(f"照片文件夹不存在：{src_dir}")
    out, work = paths(a.out)
    (work / "src").mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in src_dir.rglob("*") if p.suffix.lower() in EXTS and not p.name.startswith("."))
    if not files:
        die(f"在 {src_dir} 里没找到照片")
    items = []
    for p in files:
        try:
            im = _open_any(p)
            date = _date(im)
            icc = im.info.get("icc_profile")
            im = _to_srgb(ImageOps.exif_transpose(im), icc)
        except Exception as err:  # noqa: BLE001
            log(f"  跳过 {p.name}：{type(err).__name__}")
            continue
        if min(im.size) < 600:
            log(f"  跳过 {p.name}：分辨率太低")
            continue
        items.append((date or "", p, im))
    if not items:
        die("没有可用的照片")
    items.sort(key=lambda x: (x[0] or "9999", str(x[1])))
    if len(items) > a.max:
        # 照片太多：按「纹理丰富度」挑，并尽量分散在不同时间
        scored = []
        for date, p, im in items:
            t = im.copy()
            t.thumbnail((480, 480))
            scored.append((float(np.asarray(white_map(t), dtype=np.float32).mean()), date, p, im))
        scored.sort(key=lambda x: -x[0])
        keep, used = [], set()
        for s, date, p, im in scored:
            day = date[:10]
            if day in used and len(set(d for _, d, _, _ in scored)) > a.max:
                continue
            keep.append((date, p, im))
            used.add(day)
            if len(keep) == a.max:
                break
        items = sorted(keep, key=lambda x: (x[0] or "9999", str(x[1])))
        log(f"  照片多于 {a.max} 张，按纹理丰富度和日期分散挑了 {len(items)} 张；分享前请自己看一眼有没有人像")
    names_path = work / "names.json"
    old = json.loads(names_path.read_text(encoding="utf-8")) if names_path.exists() else {}
    sections, names = [], {}
    for n, (date, p, im) in enumerate(items):
        im = im.resize((round(im.width * a.height / im.height), a.height), Image.LANCZOS)
        im.info = {}
        sid = f"{n:02d}"
        im.save(work / "src" / f"{sid}.jpg", quality=92)                 # 不写 exif / icc
        white_map(im, seed=11 + n * 7).save(out / f"white_{sid}.jpg", quality=86)
        im.save(out / f"photo_{sid}.jpg", quality=86)
        names[sid] = old.get(sid, {"name": "", "seal": ""})
        sections.append({"id": sid, "date": date or "", "w": im.width, "h": im.height})
    (work / "sources.json").write_text(json.dumps({s["id"]: str(it[1]) for s, it in zip(sections, items)}, ensure_ascii=False, indent=1), encoding="utf-8")
    names_path.write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "sections.json").write_text(json.dumps({"sections": sections}, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"✓ build：{len(sections)} 段 → {out}；地名和印文填在 {names_path}")


def _data_url(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def cmd_html(a) -> None:
    out, work = paths(a.out)
    meta_p = out / "sections.json"
    if not meta_p.exists():
        die("找不到 sections.json；请先运行 build")
    meta = json.loads(meta_p.read_text(encoding="utf-8"))["sections"]
    names = json.loads((work / "names.json").read_text(encoding="utf-8")) if (work / "names.json").exists() else {}
    bundle = RUNTIME / "scroll.bundle.js"
    if not bundle.exists():
        die(f"缺少 {bundle}；请运行 scripts/build_runtime.sh 重新打包")
    secs = []
    for s in meta:
        nm = names.get(s["id"], {}) or {}
        iso = nm.get("date") or s["date"]          # 照片没有 EXIF 时，可以在 names.json 里补 "date": "2024-10-01"
        d = dt.datetime.fromisoformat(iso) if iso else None
        secs.append({
            "name": nm.get("name", ""),
            "date": f"{d.year}.{d.month}.{d.day}" if d else "",
            "seal": nm.get("seal") or "拓",
            "sealDate": seal_date(iso or None),
            "w": s["w"],
            "h": s["h"],
            "white": _data_url(out / f"white_{s['id']}.jpg", "image/jpeg"),
            "photo": _data_url(out / f"photo_{s['id']}.jpg", "image/jpeg"),
        })
    dates = [x["date"] for x in secs if x["date"]]
    subtitle = a.subtitle if a.subtitle is not None else (f"{dates[0]} – {dates[-1]}" if len(dates) > 1 else (dates[0] if dates else ""))
    page = (RUNTIME / "template.html").read_text(encoding="utf-8")
    page = page.replace("{{TITLE}}", html.escape(a.title)).replace("{{SUBTITLE}}", html.escape(subtitle))
    page = page.replace("{{GRAIN}}", _data_url(RUNTIME / "seal_grain.png", "image/png"))
    page = page.replace("{{DATA}}", json.dumps({"sections": secs}, ensure_ascii=False).replace("<", "\\u003c"))
    page = page.replace("{{BUNDLE}}", bundle.read_text(encoding="utf-8").replace("</script", "<\\/script"))
    dst = out / a.name
    dst.write_text(page, encoding="utf-8")
    log(f"✓ html：{dst}（{dst.stat().st_size / 1e6:.1f} MB，单文件，双击用浏览器打开）")


def cmd_check(a) -> None:
    log(f"Python {platform.python_version()}（{platform.system()} {platform.machine()}）")
    ok = True
    for mod, hint in (("PIL", "pillow"), ("numpy", "numpy")):
        try:
            m = __import__(mod)
            log(f"  ✓ {hint} {getattr(m, '__version__', '')}")
        except ImportError:
            ok = False
            log(f"  ✗ {hint} 未安装")
    try:
        __import__("scipy")
        log("  ✓ scipy（可选，加速模糊）")
    except ImportError:
        log("  · scipy 未安装：用 numpy 盒式滤波代替，慢一些但结果相近")
    log(f"  {'✓' if (RUNTIME / 'scroll.bundle.js').exists() else '✗'} 运行时 {RUNTIME / 'scroll.bundle.js'}")
    if not ok:
        die("缺依赖；请运行：python3 -m pip install pillow numpy")
    log("✓ 依赖齐全")


def cmd_all(a) -> None:
    cmd_build(a)
    cmd_html(a)


def main() -> None:
    ap = argparse.ArgumentParser(prog="scroll.py", description="把旅行照片做成一卷拓片长卷（单文件网页）。所有处理都在本机。")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.set_defaults(fn=cmd_check)
    for name, fn in (("build", cmd_build), ("html", cmd_html), ("all", cmd_all)):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        p.add_argument("--out", required=True, help="输出文件夹")
        if name in ("build", "all"):
            p.add_argument("--photos", required=True, help="照片文件夹（会递归读取子文件夹）")
            p.add_argument("--max", type=int, default=6, help="最多几段（默认 6）")
            p.add_argument("--height", type=int, default=900, help="每段的像素高度（默认 900）")
        if name in ("html", "all"):
            p.add_argument("--title", default="拓片长卷")
            p.add_argument("--subtitle", default=None, help="默认用照片日期范围")
            p.add_argument("--name", default="scroll.html", help="输出文件名")
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
