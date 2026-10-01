// 拓片长卷交互页 · trip-photo-rubbing-scroll
// SPDX-License-Identifier: Apache-2.0
//
// 一根手指在卷上擦：白纹从指尖显出来，擦透的地方过一会儿晕回彩色；一段擦够了自动盖印。
// 翻卷：两根手指左右拖、触控板 / 滚轮、左右方向键，或点两侧的箭头。卷首在最右（手卷读法）。
// 数据全部内嵌在页面里（window.SCROLL_DATA），不联网。

import { createScroll, LAYOUT } from './scroll-core.js';

const D = window.SCROLL_DATA;
const $ = (id) => document.getElementById(id);
const canvas = $('c');
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, x) => a + (b - a) * x;
const easeIn = (x) => x * x * x;
const easeOut = (x) => 1 - Math.pow(1 - x, 3);
const LOW = new URLSearchParams(location.search).get('q') === 'low';
const T0 = performance.now() / 1000;
const now = () => performance.now() / 1000 - T0;

const GX = 24, GY = 18;            // 每段的覆盖网格，用来判断「擦够了」
const STAMP_AT = 0.55;
const SEAL = 116;

let api = null;
let view = { pan: 0, zoom: 1, cy: innerHeight / 2 };
let target = 0;
let cur = 0;
const grids = [];
const stampTime = [];
const sealEls = [];
const pointers = new Map();
let rub = null;                    // { id, last: {x, y} }
let pending = [];
let touched = false;

function pr() { return Math.min(window.devicePixelRatio || 1, LOW ? 1 : 2); }

function fit() {
  const bandH = LAYOUT.SH + 2 * LAYOUT.MARGIN + 2 * LAYOUT.SILK;
  const widest = Math.max(...api.sections.map((s) => s.fullW));
  const zoom = Math.min((innerHeight * 0.62) / bandH, (innerWidth * 0.94) / widest);
  view.zoom = zoom;
  view.cy = innerHeight * 0.52;
}

const centerOf = (i) => api.sections[i].x0 + api.sections[i].fullW / 2;
function goTo(i) {
  cur = clamp(i, 0, api.sections.length - 1);
  target = centerOf(cur);
}
function nearest() {
  let best = 0, d = Infinity;
  api.sections.forEach((_, i) => {
    const e = Math.abs(centerOf(i) - view.pan);
    if (e < d) { d = e; best = i; }
  });
  return best;
}

function interacted() {
  if (touched) return;
  touched = true;
  $('hint').style.opacity = '0';
}

// —— 覆盖网格：与着色器同样的叠加公式，只是粗一些 ——
function markGrid(g) {
  api.sections.forEach((s, i) => {
    const pad = g.r;
    if (Math.max(g.x0, g.x1) + pad < s.imgX || Math.min(g.x0, g.x1) - pad > s.imgX + s.imgW) return;
    const cw = s.imgW / GX, ch = LAYOUT.SH / GY;
    const ax = g.x0 - s.imgX, ay = g.y0, bx = g.x1 - s.imgX, by = g.y1;
    const abx = bx - ax, aby = by - ay, L2 = Math.max(abx * abx + aby * aby, 1e-4);
    for (let gy = 0; gy < GY; gy++) {
      for (let gx = 0; gx < GX; gx++) {
        const px = (gx + 0.5) * cw, py = (gy + 0.5) * ch;
        const h = clamp(((px - ax) * abx + (py - ay) * aby) / L2);
        const d = Math.hypot(px - ax - abx * h, py - ay - aby * h);
        const cov = (1 - clamp((d - g.r * 0.3) / (g.r * 0.7))) * g.k;
        if (cov > 0) grids[i][gy * GX + gx] = 1 - (1 - grids[i][gy * GX + gx]) * (1 - cov);
      }
    }
    if (stampTime[i] === null) {
      let n = 0;
      for (const v of grids[i]) if (v >= 0.5) n++;
      if (n / (GX * GY) >= STAMP_AT) stampTime[i] = now();
    }
  });
}

function addRub(x, y) {
  const p = api.toScroll(x, y, view);
  if (rub.last) {
    // 覆盖量只看「手指在这一点上停留了多远」，和移动快慢、事件频率无关：每走 10 屏幕像素叠 0.1
    const lenPx = Math.hypot(p.x - rub.last.x, p.y - rub.last.y) * view.zoom;
    const k = 1 - Math.pow(0.9, Math.min(lenPx, 128) / 10);
    if (lenPx < 1.5) return;
    const g = { x0: rub.last.x, y0: rub.last.y, x1: p.x, y1: p.y, t: now(), r: 64 / view.zoom, k };
    pending.push(g);
    markGrid(g);
  }
  rub.last = p;
}

canvas.addEventListener('pointerdown', (e) => {
  try { canvas.setPointerCapture(e.pointerId); } catch (_) { /* 合成事件没有真实指针 */ }
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (pointers.size === 1) {
    interacted();
    rub = { id: e.pointerId, last: null };
    addRub(e.clientX, e.clientY);
  } else {
    rub = null;                                   // 第二根手指落下：改为翻卷
  }
});

canvas.addEventListener('pointermove', (e) => {
  const prev = pointers.get(e.pointerId);
  if (!prev) return;
  if (pointers.size >= 2) {
    const dx = (e.clientX - prev.x) / pointers.size;
    target -= dx / view.zoom;
    view.pan -= dx / view.zoom;
  } else if (rub && rub.id === e.pointerId) {
    addRub(e.clientX, e.clientY);
  }
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
});

function lift(e) {
  pointers.delete(e.pointerId);
  if (rub && rub.id === e.pointerId) rub = null;
  if (pointers.size === 0 && !rub) goTo(nearest());
}
canvas.addEventListener('pointerup', lift);
canvas.addEventListener('pointercancel', lift);

canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  interacted();
  const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
  target += d / view.zoom;
  clearTimeout(canvas._snap);
  canvas._snap = setTimeout(() => goTo(nearest()), 260);
}, { passive: false });

addEventListener('keydown', (e) => {
  if (!api) return;
  if (e.key === 'ArrowLeft') goTo(cur + 1);       // 往左展开 = 下一段
  else if (e.key === 'ArrowRight') goTo(cur - 1);
  else if (e.key === 'r' || e.key === 'R') reset();
});
$('prev').addEventListener('click', () => { interacted(); goTo(cur + 1); });
$('next').addEventListener('click', () => { interacted(); goTo(cur - 1); });
$('reset').addEventListener('click', () => reset());

function reset() {
  api.resetAll();
  grids.forEach((g) => g.fill(0));
  stampTime.fill(null);
}

addEventListener('resize', () => {
  if (!api || !innerWidth || !innerHeight) return;
  api.resize(innerWidth, innerHeight, pr());
  fit();
});

function sealFrame(t) {
  api.sections.forEach((s, i) => {
    const el = sealEls[i];
    const ts = stampTime[i];
    if (ts === null) { el.style.opacity = '0'; return; }
    const p = clamp((t - ts) / 0.2);
    const [sx, sy] = api.toScreen(s.imgX + 44, LAYOUT.SH - 44 - SEAL, view);
    const z = view.zoom;
    const sc = lerp(1.7, 1, easeIn(p)) * z;
    el.style.opacity = clamp(p * 3).toFixed(3);
    el.style.transform = `translate3d(${(sx + (SEAL / 2) * (z - 1)).toFixed(1)}px, ${(sy + (SEAL / 2) * (z - 1)).toFixed(1)}px, 0) scale(${sc.toFixed(4)}) rotate(${lerp(-8, -3, easeOut(p)).toFixed(2)}deg)`;
  });
}

function placeLabel() {
  const i = nearest();
  const s = D.sections[i];
  const txt = [s.name, s.date].filter(Boolean).join(' · ');
  if ($('place').textContent !== txt) $('place').textContent = txt;
  const [, by] = api.toScreen(0, LAYOUT.SH + LAYOUT.MARGIN + LAYOUT.SILK, view);
  $('place').style.top = `${(by + 18).toFixed(0)}px`;
  const lb = api.toScreen(0, LAYOUT.SH / 2, view)[1];
  $('prev').style.top = $('next').style.top = `${(lb - 28).toFixed(0)}px`;
  $('prev').style.visibility = cur < api.sections.length - 1 ? 'visible' : 'hidden';
  $('next').style.visibility = cur > 0 ? 'visible' : 'hidden';
}

let last = performance.now() / 1000;
function frame() {
  if (!innerWidth || !innerHeight) {
    requestAnimationFrame(frame);
    return;
  }
  const t = now();
  const dt = Math.min(0.05, performance.now() / 1000 - last);
  last = performance.now() / 1000;
  const minPan = centerOf(api.sections.length - 1), maxPan = centerOf(0);
  target = clamp(target, minPan, maxPan);
  if (pointers.size < 2) view.pan += (target - view.pan) * (1 - Math.exp(-dt / 0.22));
  view.pan = clamp(view.pan, minPan - 200, maxPan + 200);
  if (pending.length) {
    api.addSegs(pending);
    pending = [];
  }
  api.render({ now: t, view });
  sealFrame(t);
  placeLabel();
  requestAnimationFrame(frame);
}

createScroll({
  canvas,
  width: Math.max(1, innerWidth),
  height: Math.max(1, innerHeight),
  sections: D.sections.map((s) => ({ white: s.white, photo: s.photo, w: s.w, h: s.h })),
  pixelRatio: pr(),
})
  .then((a) => {
    api = a;
    api.resize(Math.max(1, innerWidth), Math.max(1, innerHeight), pr());
    D.sections.forEach((s, i) => {
      grids.push(new Float32Array(GX * GY));
      stampTime.push(null);
      api.setBloom(i, 0.9, 2.6);
      const el = document.createElement('div');
      el.className = 'seal';
      const big = (s.seal || '').length >= 3 ? ' p3' : '';
      el.innerHTML = `<span class="p${big}"></span><span class="d"></span><i></i>`;
      el.children[0].textContent = s.seal || '';
      el.children[1].textContent = s.sealDate || '';
      $('seals').appendChild(el);
      sealEls.push(el);
    });
    fit();
    goTo(0);
    view.pan = target;
    $('msg').style.display = 'none';
    requestAnimationFrame(frame);
  })
  .catch((err) => {
    $('msg').textContent = '这台设备没能展开这卷拓片（WebGL 不可用）';
    console.error(err);
  });
