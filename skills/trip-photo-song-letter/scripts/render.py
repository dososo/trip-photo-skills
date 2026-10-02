# SPDX-License-Identifier: Apache-2.0
"""出片：一张画 + 一首歌 + 一封信，渲染成 9:16 竖版视频。

时间轴全部来自 song.py 输出的段落时间：
  开场（前奏）：写歌的那张真实照片，山脊线跟着八音盒一个音一个音画出来，配两行大字；
  主歌：第一张画（可选光点：每个音一粒光）；山脊线飘下来变成画面下方的「乐谱带」，每个音同步点亮；
  副歌：第二张画（如果它是写歌照片改出来的，就在画里的山脊上同步亮点）；乐谱带播放点倒着走；
  间奏 + 高潮：第三张画；高潮时整条乐谱带变暖；
  尾声：信纸滑上来，落款下附这首歌的山脊乐谱。

由 episode.py validate / stills / render 调用。配置字段见 references/episode-config.md。
"""
from __future__ import annotations

import json
import math
import os
import subprocess

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy.ndimage import uniform_filter1d

W, H, IH, FPS = 1080, 1920, 1440, 30
PAPER = np.array([243, 238, 228], np.float32)
INK = (52, 46, 40)
WARM = (206, 146, 72)
CINNABAR = (196, 72, 52)
CACHE = os.environ.get("SONGLETTER_CACHE", os.path.expanduser("~/.cache/trip-photo-song-letter"))
FONT = os.path.join(CACHE, "fonts", "LXGWWenKai-Regular.ttf")
FONT_M = os.path.join(CACHE, "fonts", "LXGWWenKai-Medium.ttf")
SECTIONS = ("verse", "chorus", "bridge", "climax", "outro")


# ---------------------------------------------------------------- 配置校验
def validate(cfg: dict, base: str) -> list[str]:
    errs = []

    def need(path, d, keys):
        for k in keys:
            if k not in d:
                errs.append(f"缺字段 {path}{k}")

    if cfg.get("schema_version") != "1":
        errs.append("schema_version 必须是 \"1\"")
    need("", cfg, ["song", "melody_photo", "hook", "scenes", "captions", "letter", "output"])
    if errs:
        return errs
    need("song.", cfg["song"], ["json", "wav"])
    need("melody_photo.", cfg["melody_photo"], ["image", "ridge"])
    need("hook.", cfg["hook"], ["title", "subtitle"])
    for s in ("verse", "chorus", "ending"):
        if s not in cfg["scenes"] or "image" not in cfg["scenes"][s]:
            errs.append(f"缺字段 scenes.{s}.image")
    for i, c in enumerate(cfg["captions"]):
        need(f"captions[{i}].", c, ["text", "section", "at", "slot"])
        if c.get("section") not in SECTIONS:
            errs.append(f"captions[{i}].section 只能是 {SECTIONS}")
        if c.get("slot") not in (0, 1):
            errs.append(f"captions[{i}].slot 只能是 0 或 1")
        if len(c.get("text", "")) > 20:
            errs.append(f"captions[{i}].text 超过 20 字，手机上会放不下：{c.get('text')}")
    need("letter.", cfg["letter"], ["to", "body", "sign", "postmark", "date", "stamp", "ps"])
    if len(cfg["letter"].get("body", [])) > 9:
        errs.append("letter.body 最多 9 行")
    for ln in cfg["letter"].get("body", []):
        if len(ln) > 17:
            errs.append(f"letter.body 每行最多 17 字：{ln}")
    files = [cfg["song"]["json"], cfg["song"]["wav"], cfg["melody_photo"]["image"], cfg["melody_photo"]["ridge"],
             cfg["letter"]["stamp"]] + [cfg["scenes"][s]["image"] for s in ("verse", "chorus", "ending") if s in cfg["scenes"]]
    if cfg["scenes"].get("chorus", {}).get("ridge"):
        files.append(cfg["scenes"]["chorus"]["ridge"])
    for f in files:
        if not os.path.exists(os.path.join(base, f)):
            errs.append(f"文件不存在：{f}")
    for f in (FONT, FONT_M):
        if not os.path.exists(f):
            errs.append(f"缺字体 {f}：先运行 python3 scripts/episode.py fetch，用户同意后加 --yes")
    return errs


# ---------------------------------------------------------------- 工具
def load_rgb(p, size=None):
    im = Image.open(p).convert("RGB")
    if size:
        im = im.resize(size, Image.LANCZOS)
    return np.asarray(im, np.float32)


def fit_portrait(img: Image.Image) -> np.ndarray:
    """竖图：裁成 3:4 后缩放到 1080×1440。"""
    w, h = img.size
    if w / h > 0.75:
        nw = int(h * 0.75); img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w / 0.75); img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return np.asarray(img.resize((W, IH), Image.LANCZOS), np.float32)


def ease(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def lerp(a, b, u):
    return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)


def fbm(h, w, seed):
    r = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for k in range(5):
        s = 2 ** (k + 2)
        n = r.random((h // s + 2, w // s + 2)).astype(np.float32)
        acc += amp * cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)
        tot += amp
        amp *= 0.55
    acc /= tot
    return (acc - acc.min()) / (acc.max() - acc.min())


NOISE = fbm(IH, W, 3)
YN = np.linspace(1, 0, IH, dtype=np.float32)[:, None]


def wash(progress, softness=0.06):
    field = YN * 0.82 + NOISE * 0.18
    return np.clip((progress * 1.1 - field) / softness, 0, 1)[..., None]


def align(src, dst, region=(0.0, 0.55)):
    a = cv2.cvtColor(src.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    b = cv2.cvtColor(dst.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    s = 0.5
    a2, b2 = cv2.resize(a, None, fx=s, fy=s), cv2.resize(b, None, fx=s, fy=s)
    mask = np.zeros_like(b2)
    mask[int(region[0] * b2.shape[0]):int(region[1] * b2.shape[0])] = 255
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        _, warp = cv2.findTransformECC(b2, a2, warp, cv2.MOTION_AFFINE,
                                       (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-5), mask, 5)
    except cv2.error:
        pass
    warp[:, 2] /= s
    return warp


def kenburns(img, s, cx, cy):
    M = cv2.getRotationMatrix2D((cx * img.shape[1], cy * img.shape[0]), 0, s)
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT), M


# ---------------------------------------------------------------- 主体
class Episode:
    def __init__(self, cfg: dict, base: str):
        self.cfg, self.base = cfg, base
        P = lambda p: os.path.join(base, p)  # noqa: E731
        rng = np.random.default_rng(11)
        self.paper = np.clip(PAPER[None, None, :] + rng.normal(0, 3.0, (H, W, 1)).astype(np.float32), 0, 255)
        song = json.load(open(P(cfg["song"]["json"])))
        self.EV = song["events"]
        sec = song["sections"]
        self.T = dict(morph=sec["morph"], chorus=sec["chorus"], bridge=sec["bridge"], climax=sec["climax"], outro=sec["outro"],
                      end=sec["end"])
        self.T_END = sec["end"] + 4.0
        self.TM0, self.TM1 = self.T["morph"], self.T["morph"] + 1.4
        self.PASS = {p: [e for e in self.EV if e["pass_"] == p] for p in range(4)}
        # 写歌照片与它的山脊
        mel = Image.open(P(cfg["melody_photo"]["image"])).convert("RGB")
        self.mel = np.asarray(mel, np.float32)
        self.SW = self.mel.shape[1]
        self.R = uniform_filter1d(np.load(P(cfg["melody_photo"]["ridge"])).astype(float), size=3)
        if len(self.R) != self.SW:
            raise SystemExit(f"山脊宽度 {len(self.R)} 与写歌照片宽度 {self.SW} 不一致：请对同一张图运行 ridge.py")
        self.x0, self.x1 = song["x0"], song["x1"]
        seg = self.R[self.x0:self.x1]
        self.SMIN, self.SMAX = float(seg.min()), float(seg.max())
        self.XS = list(range(self.x0, self.x1, 2))
        self._hook_layout()
        # 乐谱带
        self.RB = (96, W - 96, IH + 175, 120)
        self.RIB_LINE = [self.rib_pt(x) for x in self.XS]
        self.HOOK_LINE = [self.hook_pt(x) for x in self.XS]
        # 场景
        sc = cfg["scenes"]
        self.scenes = {k: self._prep_scene(sc[k], P) for k in ("verse", "chorus", "ending")}
        self.chorus_ridge = None
        if sc["chorus"].get("ridge"):
            self._prep_chorus_ridge(P(sc["chorus"]["image"]), P(sc["chorus"]["ridge"]))
        self.fL = ImageFont.truetype(FONT, 50)
        self.fTag = ImageFont.truetype(FONT, 34)
        self.fHook = ImageFont.truetype(FONT_M, 76)
        self.fHookS = ImageFont.truetype(FONT, 42)
        self.LETTER = self._letter_page(P)
        self.lines = self._captions()

    # ----- 布局
    def _hook_layout(self):
        h0, w0 = self.mel.shape[:2]
        if w0 >= h0:
            self.HS = 1150 / h0
        else:
            self.HS = W / w0
        hw, hh = int(round(w0 * self.HS)), int(round(h0 * self.HS))
        img = cv2.resize(self.mel, (hw, hh), interpolation=cv2.INTER_AREA)
        cx = (self.x0 + self.x1) / 2 * self.HS
        self.HOFF = int(min(max(cx - W / 2, 0), max(0, hw - W)))
        cy = float(np.mean(self.R[self.x0:self.x1])) * self.HS
        self.VOFF = int(min(max(cy - 1150 * 0.42, 0), max(0, hh - 1150)))
        crop = img[self.VOFF:self.VOFF + 1150, self.HOFF:self.HOFF + W]
        self.HOOK_Y = 140
        canvas = self.paper.copy()
        canvas[self.HOOK_Y:self.HOOK_Y + crop.shape[0], :crop.shape[1]] = crop
        self.hook_canvas = canvas

    def hook_pt(self, x):
        x = int(min(self.SW - 1, max(0, x)))
        return (x * self.HS - self.HOFF, self.HOOK_Y + self.R[x] * self.HS - self.VOFF)

    def rib_pt(self, x, base=None, hh=None, xa=None, xb=None):
        xa = self.RB[0] if xa is None else xa
        xb = self.RB[1] if xb is None else xb
        base = self.RB[2] if base is None else base
        hh = self.RB[3] if hh is None else hh
        x = int(min(self.x1 - 1, max(self.x0, x)))
        k = (self.SMAX - self.R[x]) / (self.SMAX - self.SMIN + 1e-6)
        return (xa + (x - self.x0) / (self.x1 - self.x0) * (xb - xa), base - k * hh)

    def _prep_scene(self, s, P):
        img = Image.open(P(s["image"])).convert("RGB")
        out = dict(cfg=s)
        if img.width > img.height:                               # 横图：放大到 1440 高，横移
            bw = int(IH * img.width / img.height)
            out.update(mode="pan", arr=np.asarray(img.resize((bw, IH), Image.LANCZOS), np.float32), bw=bw,
                       pan=s.get("pan", [0.5, 0.5]), src_size=img.size)
        else:
            out.update(mode="kb", arr=fit_portrait(img), focus=s.get("focus", [0.5, 0.6]), src_size=img.size)
        return out

    def _prep_chorus_ridge(self, img_path, ridge_path):
        sc = self.scenes["chorus"]
        src = Image.open(img_path).convert("RGB")
        bw_, bh_ = src.size
        r = np.load(ridge_path).astype(float)
        if len(r) != bw_:
            raise SystemExit("副歌画的山脊宽度与图片宽度不一致：请对这张画运行 ridge.py")
        r = uniform_filter1d(r, size=3)
        mel_s = cv2.resize(self.mel, (bw_, bh_))
        warp = align(mel_s, np.asarray(src, np.float32))
        Ainv = np.linalg.inv(np.vstack([warp, [0, 0, 1]]))
        if sc["mode"] == "pan":
            scl = IH / bh_
            ox = 0.0
        else:                                                    # 竖图：fit_portrait 的裁切与缩放
            if bw_ / bh_ > 0.75:
                nw = int(bh_ * 0.75); ox = (bw_ - nw) / 2; scl = W / nw
            else:
                ox = 0.0; scl = W / bw_

        # 保护：画里如果有人物站在山脊上，自动提取的线会翻过人物头顶。
        # 把写歌照片的山脊映射到画里，偏差超过 20 像素的地方改用映射线。
        mx, my = [], []
        for x in range(0, self.SW, 2):
            q = Ainv @ np.array([x * bw_ / self.SW, self.R[x] * bh_ / self.mel.shape[0], 1.0])
            mx.append(q[0]); my.append(q[1])
        order = np.argsort(mx)
        mapped = np.interp(np.arange(bw_), np.array(mx)[order], np.array(my)[order])
        dev = np.abs(r - mapped) > 20
        dev = np.convolve(dev.astype(float), np.ones(15) / 15, mode="same") > 0      # 偏差区两侧各扩一点
        r = np.where(dev, mapped, r)
        self.chorus_guarded = int(dev.sum())

        def pt(x):
            p = np.array([x * bw_ / self.SW, self.R[int(min(self.SW - 1, max(0, x)))] * bh_ / self.mel.shape[0], 1.0])
            q = Ainv @ p
            bx = int(min(bw_ - 1, max(0, q[0])))
            return ((bx - ox) * scl, r[bx] * scl)
        self.chorus_pt = pt
        self.chorus_line = [pt(x) for x in self.XS]
        self.chorus_ridge = True

    # ----- 字幕
    def _captions(self):
        T = self.T
        start = dict(verse=self.TM1 - 1.4, chorus=T["chorus"], bridge=T["bridge"], climax=T["climax"], outro=T["outro"])
        until = dict(verse=T["chorus"] - 0.5, chorus=T["bridge"] - 0.6, bridge=T["outro"] - 0.5, climax=T["outro"] - 0.5,
                     outro=self.T_END)
        out = []
        for c in self.cfg["captions"]:
            t0 = start[c["section"]] + float(c["at"])
            t1 = start[c["section"]] + float(c["until"]) if "until" in c else until[c["section"]]
            out.append((c["text"], t0, int(c["slot"]), t1))
        return out

    def text_layer(self, t):
        im = Image.new("RGBA", (W, H - IH), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for text, t0, slot, t1 in self.lines:
            if t < t0:
                continue
            a = min(1.0, (t - t0) / 0.9)
            if t > t1:
                a *= max(0.0, 1 - (t - t1) / 0.6)
            if a <= 0:
                continue
            tw = d.textlength(text, font=self.fL)
            d.text(((W - tw) / 2, 250 + slot * 92), text, font=self.fL, fill=INK + (int(255 * a),))
        return im

    # ----- 点与播放点
    @staticmethod
    def dot(d, x, y, age, scale=1.0, white=False):
        g = max(0.0, 1 - age / 1.0)
        r = (5.5 + 8 * g) * scale
        if g > 0:
            R = r + 10 * scale
            d.ellipse([x - R, y - R, x + R, y + R], fill=(255, 196, 110, int(120 * g)))
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 250, 238, 255) if white else WARM + (255,))

    @staticmethod
    def playhead(t, evs, pt):
        if not evs or t < evs[0]["t"]:
            return None
        k = max(i for i, e in enumerate(evs) if e["t"] <= t)
        if k == len(evs) - 1:
            return pt(evs[k]["x"])
        a, b = evs[k], evs[k + 1]
        u = min(1.0, (t - a["t"]) / max(1e-6, b["t"] - a["t"]))
        return lerp(pt(a["x"]), pt(b["x"]), u)

    def ridge_overlay(self, t):
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        T, TM0, TM1 = self.T, self.TM0, self.TM1
        if t < TM0:
            ph = self.playhead(t, self.PASS[0], self.hook_pt)
            if ph is not None:
                pts = [p for p in self.HOOK_LINE if p[0] <= ph[0]] or self.HOOK_LINE[:2]
                d.line(pts, fill=INK + (120,), width=9, joint="curve")      # 深色描边：亮天空上也看得见
                d.line(pts, fill=(255, 246, 228, 235), width=4)
                d.ellipse([ph[0] - 9, ph[1] - 9, ph[0] + 9, ph[1] + 9], fill=INK + (120,))
                d.ellipse([ph[0] - 7, ph[1] - 7, ph[0] + 7, ph[1] + 7], fill=(255, 246, 228, 255))
            for e in self.PASS[0]:
                if e["t"] <= t:
                    x, y = self.hook_pt(e["x"])
                    self.dot(d, x, y, t - e["t"], 1.4)
            return lay
        if t < TM1:
            u = ease((t - TM0) / (TM1 - TM0))
            reach = self.playhead(TM0, self.PASS[0], self.hook_pt)
            idx = max(2, len([p for p in self.HOOK_LINE if p[0] <= (reach[0] if reach else 0)]))
            line = [lerp(a, b, u) for a, b in zip(self.HOOK_LINE[:idx], self.RIB_LINE[:idx])]
            if u < 0.6:
                d.line(line, fill=INK + (int(120 * (1 - u / 0.6)),), width=9, joint="curve")
            d.line(line, fill=(int(255 - 195 * u), int(246 - 192 * u), int(228 - 180 * u), 235), width=4)
            if u > 0.6:
                d.line(self.RIB_LINE, fill=(60, 54, 48, int(210 * (u - 0.6) / 0.4)), width=3)
            for e in self.PASS[0]:
                if e["t"] <= t:
                    p = lerp(self.hook_pt(e["x"]), self.rib_pt(e["x"]), u)
                    self.dot(d, p[0], p[1], t - e["t"], 1.4 - 0.4 * u)
            return lay
        LET = T["outro"]
        fade = 1.0 if t < LET - 0.4 else max(0.0, 1 - (t - (LET - 0.4)) / 0.6)
        if fade <= 0:
            return lay
        xa, xb, base, _ = self.RB
        d.polygon(self.RIB_LINE + [(xb, base + 6), (xa, base + 6)], fill=(60, 54, 48, int(16 * fade)))
        d.line(self.RIB_LINE, fill=(60, 54, 48, int(210 * fade)), width=3)
        if t >= T["climax"]:
            w_ = ease((t - T["climax"]) / max(0.5, LET - T["climax"] - 0.5))
            n = max(2, int(len(self.RIB_LINE) * w_))
            d.line(self.RIB_LINE[:n], fill=WARM + (int(255 * fade),), width=4)
        seen = set(e["x"] for e in self.EV if e["t"] <= t)
        for p in range(4):
            for e in self.PASS[p]:
                x, y = self.rib_pt(e["x"])
                if e["t"] > t:
                    if (p == 0 or (p == 1 and t >= T["chorus"])) and e["x"] not in seen:
                        d.ellipse([x - 4.5, y - 4.5, x + 4.5, y + 4.5], outline=(60, 54, 48, int(150 * fade)), width=2)
                    continue
                self.dot(d, x, y, t - e["t"])
        evs = self.PASS[0] if t < T["chorus"] else self.PASS[1] if t < T["bridge"] else self.PASS[2] if t < T["climax"] else self.PASS[3]
        ph = self.playhead(t, evs, self.rib_pt)
        if ph is not None and t < LET - 0.3:
            d.line([(ph[0], base - self.RB[3] - 26), (ph[0], base + 14)], fill=CINNABAR + (int(230 * fade),), width=2)
        if fade < 1:
            a = np.asarray(lay).copy()
            a[..., 3] = (a[..., 3] * fade).astype(np.uint8)
            lay = Image.fromarray(a)
        return lay

    # ----- 场景画面
    def scene_img(self, key, t, t0, t1):
        s = self.scenes[key]
        u = ease((t - t0) / max(1e-6, t1 - t0))
        if s["mode"] == "pan":
            a, b = s["pan"]
            off = int(round((s["bw"] - W) * (a + (b - a) * u)))
            off = min(max(off, 0), s["bw"] - W)
            arr = s["arr"]
            if key == "chorus" and self.chorus_ridge:
                arr = self._chorus_overlay(arr, t)
            return arr[:, off:off + W], None
        f = s["focus"]
        img, M = kenburns(s["arr"], 1.0 + 0.06 * u, f[0], f[1])
        if key == "chorus" and self.chorus_ridge:
            img = self._chorus_overlay(img, t, M)
        return img, M

    def _chorus_overlay(self, arr, t, M=None):
        im = Image.fromarray(arr.astype(np.uint8)).convert("RGBA")
        over = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        tr = (lambda p: p) if M is None else (lambda p: (M[0, 0] * p[0] + M[0, 1] * p[1] + M[0, 2], M[1, 0] * p[0] + M[1, 1] * p[1] + M[1, 2]))
        d.line([tr(p) for p in self.chorus_line], fill=(255, 244, 220, 140), width=3)
        for e in self.PASS[1]:
            x, y = tr(self.chorus_pt(e["x"]))
            if t < e["t"]:
                d.ellipse([x - 5, y - 5, x + 5, y + 5], outline=(255, 244, 220, 170), width=2)
            else:
                self.dot(d, x, y, t - e["t"], 1.0, white=True)
        return np.asarray(Image.alpha_composite(im, over).convert("RGB"), np.float32)

    def particles(self, img, t, M):
        pc = self.scenes["verse"]["cfg"].get("particles")
        if not pc:
            return img
        P0 = np.array([pc["from"][0] * W, pc["from"][1] * IH])
        P1 = np.array([pc["to"][0] * W, pc["to"][1] * IH])
        tr = (lambda p: p) if M is None else (lambda p: (M[0, 0] * p[0] + M[0, 1] * p[1] + M[0, 2], M[1, 0] * p[0] + M[1, 1] * p[1] + M[1, 2]))
        im = Image.fromarray(img.astype(np.uint8)).convert("RGBA")
        glow = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(glow)
        for e in self.PASS[0]:
            if not (e["t"] <= t < e["t"] + 1.9):
                continue
            u = (t - e["t"]) / 1.9
            r = 3.5 + 3.5 * e["h"]
            p = P0 + (P1 - P0) * u + np.array([((e["x"] % 37) - 18) * math.sin(u * math.pi), 0])
            x, y = tr(p)
            a = math.sin(u * math.pi)
            d.ellipse([x - r * 2.4, y - r * 2.4, x + r * 2.4, y + r * 2.4], fill=(255, 196, 110, int(80 * a)))
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 234, 176, int(240 * a)))
        return np.asarray(Image.alpha_composite(im, glow.filter(ImageFilter.GaussianBlur(1.2))).convert("RGB"), np.float32)

    # ----- 信
    def _letter_page(self, P):
        L = self.cfg["letter"]
        im = Image.fromarray(self.paper.astype(np.uint8))
        d = ImageDraw.Draw(im)
        M = 96
        ph = Image.open(P(L["stamp"])).convert("RGB")
        sw, sh = 300, 380
        ar = (sw - 40) / (sh - 40)
        w0, h0 = ph.size
        if w0 / h0 > ar:
            nw = int(h0 * ar); ph = ph.crop(((w0 - nw) // 2, 0, (w0 - nw) // 2 + nw, h0))
        else:
            nh = int(w0 / ar); ph = ph.crop((0, (h0 - nh) // 2, w0, (h0 - nh) // 2 + nh))
        st = Image.new("RGBA", (sw, sh), (252, 250, 245, 255))
        st.paste(ph.resize((sw - 40, sh - 40), Image.LANCZOS), (20, 20))
        sd = ImageDraw.Draw(st)
        for x in range(10, sw, 22):
            for y in (0, sh):
                sd.ellipse([x - 8, y - 8, x + 8, y + 8], fill=(0, 0, 0, 0))
        for y in range(10, sh, 22):
            for x in (0, sw):
                sd.ellipse([x - 8, y - 8, x + 8, y + 8], fill=(0, 0, 0, 0))
        sx, sy = W - M - sw + 10, 150
        im.paste((0, 0, 0), (sx + 6, sy + 8), st.split()[3].point(lambda v: 38 if v > 0 else 0))
        im.paste(st, (sx, sy), st)
        pm = Image.new("RGBA", (560, 300), (0, 0, 0, 0))
        pd = ImageDraw.Draw(pm)
        col = (168, 62, 48, 205)
        c, R = (150, 150), 136
        pd.ellipse([c[0] - R, c[1] - R, c[0] + R, c[1] + R], outline=col, width=5)
        pd.ellipse([c[0] - R + 16, c[1] - R + 16, c[0] + R - 16, c[1] + R - 16], outline=col, width=2)
        f1 = ImageFont.truetype(FONT_M, 50 if len(L["postmark"]) <= 3 else 40)
        f2 = ImageFont.truetype(FONT, 28)
        tw = pd.textlength(L["postmark"], font=f1); pd.text((c[0] - tw / 2, c[1] - 58), L["postmark"], font=f1, fill=col)
        tw = pd.textlength(L["date"], font=f2); pd.text((c[0] - tw / 2, c[1] + 12), L["date"], font=f2, fill=col)
        for k in range(4):
            y0 = c[1] - 44 + k * 30
            pd.line([(300 + x, y0 + 10 * math.sin(x / 26.0)) for x in range(-14, 250, 4)], fill=col, width=4)
        pm = pm.rotate(-12, resample=Image.BICUBIC, expand=True)
        a = np.asarray(pm).copy()
        mk = np.random.default_rng(7).random(a.shape[:2]) < 0.18
        a[..., 3][mk] = (a[..., 3][mk] * 0.35).astype(np.uint8)
        pm = Image.fromarray(a)
        im.paste(pm, (sx - pm.width // 2 - 10, sy + sh - pm.height // 2 - 50), pm)
        d = ImageDraw.Draw(im)
        d.text((M, 210), L["to"], font=ImageFont.truetype(FONT, 38), fill=(120, 108, 96))
        fB = ImageFont.truetype(FONT, 52)
        y, lh = 600, 94
        for k in range(len(L["body"]) + 1):
            d.line([(M, y + k * lh + 72), (W - M, y + k * lh + 72)], fill=(228, 219, 204), width=1)
        for ln in L["body"]:
            d.text((M, y), ln, font=fB, fill=INK)
            y += lh
        s = "—— " + L["sign"]
        fS = ImageFont.truetype(FONT, 56)
        tw = d.textlength(s, font=fS)
        d.text((W - M - tw, y + 24), s, font=fS, fill=INK)
        d.text((M, y + 130), L["ps"], font=ImageFont.truetype(FONT, 34), fill=(150, 96, 62))
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(lay)
        base, hh, xa, xb = y + 310, 90, 110, W - 110
        pts = [self.rib_pt(x, base, hh, xa, xb) for x in self.XS]
        dl.polygon(pts + [(xb, base + 4), (xa, base + 4)], fill=(60, 54, 48, 14))
        dl.line(pts, fill=WARM + (255,), width=3)
        for xx in sorted(set(e["x"] for e in self.EV)):
            px_, py_ = self.rib_pt(xx, base, hh, xa, xb)
            dl.ellipse([px_ - 5, py_ - 5, px_ + 5, py_ + 5], fill=WARM + (255,))
        im = Image.alpha_composite(im.convert("RGBA"), lay)
        return np.asarray(im.convert("RGB"), np.float32)

    # ----- 合成一帧
    def frame(self, t):
        T, TM0, TM1 = self.T, self.TM0, self.TM1
        paper = self.paper
        if t < TM0:
            canvas = self.hook_canvas.copy()
        else:
            canvas = paper.copy()
            fd = np.linspace(0, 1, 90, dtype=np.float32)[:, None, None]
            if t < T["chorus"] + 0.8:
                v, M = self.scene_img("verse", t, TM0, T["chorus"])
                v = self.particles(v, t, M)
                vc = paper.copy()
                vc[:IH] = v
                vc[IH - 90:IH] = vc[IH - 90:IH] * (1 - fd) + paper[IH - 90:IH] * fd
                if t < TM1 + 0.2:
                    m = np.zeros((H, W, 1), np.float32)
                    m[:IH] = wash(ease((t - TM0) / (TM1 + 0.2 - TM0)))
                    m[IH:] = m[IH - 1:IH]
                    canvas = self.hook_canvas * (1 - m) + vc * m
                else:
                    canvas = vc
            if T["chorus"] - 0.8 <= t < T["bridge"] + 0.8:
                c, _ = self.scene_img("chorus", t, T["chorus"], T["bridge"])
                if t < T["chorus"] + 0.8:
                    m = wash(ease((t - (T["chorus"] - 0.8)) / 1.6))
                    canvas[:IH] = canvas[:IH] * (1 - m) + c * m
                else:
                    canvas[:IH] = c
            if t >= T["bridge"] - 0.8:
                e, _ = self.scene_img("ending", t, T["bridge"], T["outro"])
                if t < T["bridge"] + 0.8:
                    m = wash(ease((t - (T["bridge"] - 0.8)) / 1.6))
                    canvas[:IH] = canvas[:IH] * (1 - m) + e * m
                else:
                    canvas[:IH] = e
            if t >= T["chorus"] - 0.8:
                canvas[IH - 90:IH] = canvas[IH - 90:IH] * (1 - fd) + paper[IH - 90:IH] * fd
        im = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8)).convert("RGBA")
        if t < TM0 + 0.4:
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dd = ImageDraw.Draw(lay)
            out_a = 1.0 if t < TM0 - 0.5 else max(0.0, 1 - (t - (TM0 - 0.5)) / 0.7)
            a1 = min(1.0, max(0.0, (t - 0.3) / 0.8)) * out_a
            a2 = min(1.0, max(0.0, (t - 2.0) / 0.8)) * out_a
            hk = self.cfg["hook"]
            for txt, f, y, a, col in ((hk["title"], self.fHook, self.HOOK_Y + 1150 + 110, a1, INK),
                                      (hk["subtitle"], self.fHookS, self.HOOK_Y + 1150 + 230, a2, (120, 108, 96))):
                tw = dd.textlength(txt, font=f)
                dd.text(((W - tw) / 2, y), txt, font=f, fill=col + (int(255 * a),))
            im.alpha_composite(lay)
        im.alpha_composite(self.ridge_overlay(t))
        if t >= TM1:
            im.alpha_composite(self.text_layer(t), (0, IH))
        out = np.asarray(im.convert("RGB"), np.float32)
        LET = T["outro"]
        if t >= LET:
            u = ease((t - LET) / 1.1)
            off = int((1 - u) * H)
            if off < H:
                sh = out.copy()
                sh[off:] = self.LETTER[: H - off]
                if off - 40 >= 0:
                    shadow = np.clip(1 - 0.25 * np.exp(-np.arange(0, 40) / 12.0), 0, 1)[:, None, None]
                    sh[off - 40:off] = out[off - 40:off] * shadow
                out = sh
            tag = self.cfg.get("tag")
            if tag and t > LET + 3.0:
                a = min(1.0, (t - LET - 3.0) / 1.0)
                im2 = Image.fromarray(out.astype(np.uint8)).convert("RGBA")
                lay = Image.new("RGBA", (W, 120), (0, 0, 0, 0))
                dd = ImageDraw.Draw(lay)
                tw = dd.textlength(tag, font=self.fTag)
                dd.text(((W - tw) / 2, 30), tag, font=self.fTag, fill=(150, 96, 62, int(255 * a)))
                im2.alpha_composite(lay, (0, H - 130))
                out = np.asarray(im2.convert("RGB"), np.float32)
        if t > self.T_END - 1.2:
            k = max(0.0, (self.T_END - t) / 1.2)
            out = out * k + paper * (1 - k)
        return np.clip(out, 0, 255).astype(np.uint8)


def save_still(ep: Episode, t: float, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    Image.fromarray(ep.frame(t)).save(path, quality=90)


def write_video(ep: Episode, out: str, wav: str, crf: int = 20) -> float:
    """逐帧渲染，经 ffmpeg 编码成 H.264 + AAC（响度归一到 -15 LUFS，结尾淡出），返回时长（秒）。
    先写 <out>.part 再改名，中断不会留下半个视频。"""
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = out + ".part"
    n = int(ep.T_END * FPS)
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-i", wav,
                          "-af", f"loudnorm=I=-15:TP=-1.5:LRA=11,afade=t=out:st={ep.T_END - 1.5:.2f}:d=1.5",
                          "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
                          "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-f", "mp4", tmp],
                         stdin=subprocess.PIPE)
    for i in range(n):
        p.stdin.write(ep.frame(i / FPS).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError("ffmpeg 失败")
    os.replace(tmp, out)
    return n / FPS
