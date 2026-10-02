#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""trip-photo-song-letter：把一个地方的旅行照片做成一集「一张画 · 一首歌 · 一封信」竖版短视频。
  python3 episode.py check                                                 检查依赖和本机缓存
  python3 episode.py fetch    [--yes]                                      下载乐器采样和字体到本机缓存（不加 --yes 只列清单）
  python3 episode.py prepare  --photos <照片…> --out <输出文件夹>             1 工作副本：转 sRGB、去掉全部元数据，提醒有人的照片
  python3 episode.py facts    --photos <照片文件夹> --out <dir> [--mark …]   2 读拍摄时间：信里只能用这些事实
  python3 episode.py ridge    --out <dir> --photo <写歌照片>                 3 缩到 1600 宽，提取山脊，出检查图
  python3 episode.py ridge    --out <dir> --chorus <副歌画>                  3 （可选）副歌画自己的山脊
  python3 episode.py song     --out <dir> --from 0.12 --to 0.80 [--key D --bpm 80]   4 沿山脊写歌：<out>/song.wav
  python3 episode.py validate --out <dir>                                  5 校验 work/episode.json
  python3 episode.py stills   --out <dir> [--at 3,8,20,33,45]              6 出几张静帧给用户看
  python3 episode.py render   --out <dir> [--crf 20]                       7 出片：<out>/episode.mp4
每一步的结果都写在 <out>/work/，可以单独重跑。信、字幕和画面写在 <out>/work/episode.json，改完重跑 validate、stills、render。
写信和画都要用户过目，所以没有一条龙命令。所有处理都在本机；可选的「画」用用户自己的生图服务，不在这个脚本里。
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PKGS = (("numpy", "numpy"), ("scipy", "scipy"), ("PIL", "pillow"), ("cv2", "opencv-python"), ("soundfile", "soundfile"),
        ("torch", "torch"), ("transformers", "transformers"))
KEYS = ("C", "C#", "Db", "D", "Eb", "E", "F", "F#", "Gb", "G", "Ab", "A", "Bb", "B")


def log(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 2) -> None:
    print(f"✗ {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def paths(out: str) -> tuple[Path, Path]:
    o = Path(out).expanduser().resolve()
    w = o / "work"
    w.mkdir(parents=True, exist_ok=True)
    return o, w


def write_json(p: Path, data) -> None:
    tmp = p.with_name(p.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def cmd_check(a) -> None:
    import assets
    log(f"Python {platform.python_version()}（{platform.system()} {platform.machine()}）")
    bad = []
    for mod, hint in PKGS:
        try:
            m = importlib.import_module(mod)
            log(f"  ✓ {hint} {getattr(m, '__version__', '')}")
        except ImportError:
            bad.append(hint)
            log(f"  ✗ {hint} 未安装")
    try:
        importlib.import_module("pillow_heif")
        log("  ✓ pillow-heif（可读 HEIC）")
    except ImportError:
        log("  · pillow-heif 未安装：HEIC 照片" + ("会用系统自带的 sips 转换" if platform.system() == "Darwin" else "读不了；需要时 pip install pillow-heif"))
    ff = shutil.which("ffmpeg")
    log(f"  {'✓' if ff else '✗'} ffmpeg" + ("" if ff else " 未安装（macOS：brew install ffmpeg）"))
    miss = assets.missing()
    log(f"  {'✓' if not miss else '✗'} 乐器采样和字体：{assets.CACHE}")
    if bad:
        die("缺依赖；请运行：python3 -m pip install " + " ".join(bad))
    if not ff:
        die("缺 ffmpeg")
    if miss:
        die("缺乐器采样或字体：先运行 python3 scripts/episode.py fetch 看大小，用户同意后加 --yes")
    log("✓ 依赖齐全")


def cmd_fetch(a) -> None:
    import assets
    files = assets.plan()
    log(f"VSCO-2-CE（CC0）：{len(files)} 个文件，{sum(s for _, s in files) / 1e6:.0f} MB")
    log("霞鹜文楷 Regular + Medium（OFL-1.1）：约 51 MB")
    log(f"缓存目录：{assets.CACHE}")
    if not a.yes:
        log("这是预览。用户同意后加 --yes 下载。")
        return
    n = assets.download(files, log)
    log(f"✓ fetch {n} 个采样和字体已就绪")


def cmd_prepare(a) -> None:
    o, w = paths(a.out)
    photos = [Path(p).expanduser() for p in a.photos]
    for p in photos:
        if not p.is_file():
            die(f"找不到照片：{p}")
    import prepare
    people = prepare.prepare([str(p) for p in photos], str(w / "photos"), log)
    n = sum(1 for v in people.values() if v > 0.002)
    log(f"✓ prepare {len(people)} 张工作副本：{w / 'photos'}" + (f"；{n} 张画面里有人" if n else ""))


def cmd_facts(a) -> None:
    o, w = paths(a.out)
    folder = Path(a.photos).expanduser()
    if not folder.is_dir():
        die(f"找不到照片文件夹：{folder}")
    import facts
    d = facts.collect(str(folder), a.mark)
    write_json(w / "facts.json", d)
    log(f"✓ facts {w / 'facts.json'}：{d['total']} 张，{len(d['days'])} 天，连拍 {len(d['bursts'])} 组，"
        f"{d['total'] - d['with_time']} 张没有拍摄时间")
    for k, v in d["marks"].items():
        if v is None:
            log(f"  · {k} 没找到拍摄时间（文件名对不上，或照片没有 EXIF）")


def cmd_ridge(a) -> None:
    o, w = paths(a.out)
    src = Path(a.photo or a.chorus).expanduser()
    if not src.is_file():
        die(f"找不到图片：{src}")
    import ridge
    if a.photo:
        ridge.extract(str(src), str(w / "ridge.npy"), width=1600, save_image=str(w / "melody.jpg"),
                      preview=str(w / "ridge_check.jpg"))
        log(f"✓ ridge {w / 'ridge.npy'}（写歌照片存成 {w / 'melody.jpg'}）")
        log(f"  检查图：{w / 'ridge_check.jpg'}，绿线要贴着山脊或屋檐的边缘")
    else:
        ridge.extract(str(src), str(w / "chorus_ridge.npy"), preview=str(w / "chorus_ridge_check.jpg"))
        log(f"✓ ridge {w / 'chorus_ridge.npy'}（检查图 {w / 'chorus_ridge_check.jpg'}）")
        log('  在 work/episode.json 的 scenes.chorus 里加上 "ridge": "chorus_ridge.npy"')


def cmd_song(a) -> None:
    o, w = paths(a.out)
    if not (w / "ridge.npy").exists() or not (w / "melody.jpg").exists():
        die("还没有山脊：先运行 ridge --photo <写歌照片>")
    import song
    tmp = o / ".song.part.wav"
    try:
        d = song.make(str(w / "ridge.npy"), str(w / "melody.jpg"), str(tmp), str(w / "song.json"),
                      a.x0f, a.x1f, a.key, a.bpm)
    except ValueError as e:
        die(str(e))
    os.replace(tmp, o / "song.wav")
    log(f"✓ song {o / 'song.wav'}：{len(d['events'])} 个音，{d['sections']['end']:.1f} 秒，{a.key} 调 {a.bpm:g} 拍")


def load_config(o: Path, w: Path, name: str = "episode.mp4") -> dict:
    p = w / "episode.json"
    if not p.exists():
        die(f"没有 {p}：复制 examples/xiangxi-2024/episode.json 过去，按 references/episode-config.md 改")
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"{p} 不是合法的 JSON：{e}")
    cfg.setdefault("song", {"json": "song.json", "wav": str(o / "song.wav")})
    cfg.setdefault("melody_photo", {"image": "melody.jpg", "ridge": "ridge.npy"})
    cfg["output"] = str(o / name)
    return cfg


def checked(a, name: str = "episode.mp4"):
    o, w = paths(a.out)
    cfg = load_config(o, w, name)
    import render
    errs = render.validate(cfg, str(w))
    for e in errs:
        print("✗", e, file=sys.stderr)
    if errs:
        die(f"{w / 'episode.json'} 有 {len(errs)} 处要改")
    return o, w, cfg, render


def cmd_validate(a) -> None:
    _, w, _, _ = checked(a)
    log(f"✓ config {w / 'episode.json'}")


def cmd_stills(a) -> None:
    o, w, cfg, render = checked(a)
    ep = render.Episode(cfg, str(w))
    for t in [float(x) for x in a.at.split(",") if x.strip()]:
        p = w / "stills" / f"still_{t:05.1f}.jpg"
        render.save_still(ep, t, str(p))
        log(f"✓ still {p}")


def cmd_render(a) -> None:
    o, w, cfg, render = checked(a, a.name)
    ep = render.Episode(cfg, str(w))
    secs = render.write_video(ep, cfg["output"], os.path.join(str(w), cfg["song"]["wav"]), a.crf)
    mb = os.path.getsize(cfg["output"]) / 1e6
    log(f"✓ episode {cfg['output']}：{secs:.1f} 秒，{mb:.0f} MB")
    if mb > 30:
        log("  超过 30 MB：要发到手机或社交平台，加 --crf 24 --name episode_small.mp4 再出一份")


def main() -> None:
    ap = argparse.ArgumentParser(prog="episode.py", description="把一个地方的旅行照片做成一集「一张画 · 一首歌 · 一封信」竖版短视频。")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if name not in ("check", "fetch"):
            p.add_argument("--out", required=True, help="输出文件夹（不要放在照片文件夹里）")
        return p

    add("check", cmd_check)
    add("fetch", cmd_fetch).add_argument("--yes", action="store_true", help="确认下载")
    add("prepare", cmd_prepare).add_argument("--photos", nargs="+", required=True, help="选好的照片：写歌、主歌、结尾、邮票")
    p = add("facts", cmd_facts)
    p.add_argument("--photos", required=True, help="这个地方全部照片所在的文件夹（只读拍摄时间）")
    p.add_argument("--mark", nargs="*", default=[], help="要单独记下拍摄时间的照片文件名")
    p = add("ridge", cmd_ridge)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--photo", help="写歌照片：先缩到 1600 宽再提取")
    g.add_argument("--chorus", help="副歌画：按原尺寸提取（可选）")
    p = add("song", cmd_song)
    p.add_argument("--from", dest="x0f", type=float, required=True, help="从山脊的哪里开始读（0 = 最左，1 = 最右）")
    p.add_argument("--to", dest="x1f", type=float, required=True, help="读到哪里")
    p.add_argument("--key", default="D", choices=KEYS, help="调性，默认 D")
    p.add_argument("--bpm", type=float, default=80.0, help="速度，60–110，默认 80")
    add("validate", cmd_validate)
    add("stills", cmd_stills).add_argument("--at", default="3,8,20,33,45", help="逗号分隔的秒数")
    p = add("render", cmd_render)
    p.add_argument("--crf", type=int, default=20, help="视频质量，越大文件越小（默认 20）")
    p.add_argument("--name", default="episode.mp4", help="输出文件名")

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
