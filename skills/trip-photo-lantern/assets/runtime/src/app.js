// 走马灯交互页 · trip-photo-lantern
// SPDX-License-Identifier: Apache-2.0
//
// 拖动：转灯，松手带惯性，慢慢回到匀速。
// 点墙上的影子：灯停住，那一格的原照片从点按处圆形展开、投在墙上；再点一下或 8 秒后收回。
// 空格键：揭开背墙正中那一格。左右方向键：拨一下灯。
// 数据全部内嵌在页面里（window.LANTERN_DATA），不联网。

import { createLantern, GEO } from './lantern-core.js';

const D = window.LANTERN_DATA;
const $ = (id) => document.getElementById(id);
const canvas = $('c');
const TAU = Math.PI * 2;
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, x) => a + (b - a) * x;
const easeOut = (x) => 1 - Math.pow(1 - clamp(x), 3);

const params = new URLSearchParams(location.search);
const LOW = params.get('q') === 'low';
const REDUCED = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
const W0 = REDUCED ? 0.08 : 0.24;        // 无人操作时的转速（弧度/秒），方向与视频一致
const PANELS = (D.panels || []).map((p) => ({ name: p.name || '', date: p.date || '' }));
const NP = GEO.panels;

function camFor(aspect) {
  if (aspect < 0.8) return { x: 0.45, y: 0.04, z: 2.95, tx: -0.08, ty: 0.62, tz: 0, fov: 56 };
  if (aspect < 1.3) return { x: 0.25, y: 0.08, z: 3.25, tx: -0.04, ty: 0.55, tz: 0, fov: 50 };
  return { x: 0.0, y: 0.1, z: 3.7, tx: 0.0, ty: 0.32, tz: 0, fov: 42 };
}

let lantern = null;
let cam = camFor(innerWidth / innerHeight);
let rPx = 200;                            // 灯罩半径对应的屏幕像素，用来把拖动距离换成转角
let rot = -1.75 - TAU * 0.8125;           // 开场：边城茶峒那一格的影子落在背墙
let vel = -W0;
let hold = 0;                             // 1 = 揭开照片时让灯停住
let drag = null;                          // { id, x, y, t, x0, y0, t0, moved, samples }
let reveal = null;                        // { t0, center, u0, u1, idx, tOut }
let touched = false;
let last = performance.now() / 1000;

function size() {
  const w = innerWidth, h = innerHeight;
  if (!w || !h) return;                   // 隐藏的标签页 / 尚未布局时尺寸为 0，跳过
  const pr = Math.min(window.devicePixelRatio || 1, LOW ? 1 : 2);
  cam = camFor(w / h);
  if (lantern) {
    lantern.resize(w, h, pr);
    const a = lantern.project({ x: -GEO.R, y: 0, z: 0 }, cam);
    const b = lantern.project({ x: GEO.R, y: 0, z: 0 }, cam);
    rPx = Math.max(60, Math.abs(b[0] - a[0]) / 2);
  }
}

function panelText(idx) {
  const p = PANELS[idx];
  if (!p || (!p.name && !p.date)) return '影子里，是这一格的原照片';
  if (p.name && p.date) return `影子里，是 ${p.date} 的${p.name}`;
  return `影子里，是${p.name || p.date}`;
}

function startReveal(world, t) {
  const pa = lantern.panelAt(world, rot);
  vel = 0;
  hold = 1;
  reveal = { t0: t, center: [world.x, world.y, world.z], u0: pa.u0, u1: pa.u1, idx: pa.idx, tOut: null };
  $('cap').textContent = panelText(pa.idx);
}

function endReveal(t) {
  if (reveal && reveal.tOut === null) reveal.tOut = t;
}

function revealAmount(t) {
  if (!reveal) return 0;
  const a = easeOut((t - reveal.t0) / 1.1);
  if (reveal.tOut === null) return a;
  const k = clamp((t - reveal.tOut) / 0.7);
  if (k >= 1) {
    reveal = null;
    hold = 0;
    return 0;
  }
  return a * (1 - k * k);
}

function interacted() {
  if (touched) return;
  touched = true;
  $('hint').style.opacity = '0';
}

// —— 指针：拖动转灯 / 轻点揭影 ——
canvas.addEventListener('pointerdown', (e) => {
  interacted();
  const t = performance.now() / 1000;
  if (reveal) endReveal(t);
  canvas.setPointerCapture(e.pointerId);
  canvas.classList.add('dragging');
  drag = { id: e.pointerId, x: e.clientX, y: e.clientY, t, x0: e.clientX, y0: e.clientY, t0: t, moved: 0, samples: [] };
});

canvas.addEventListener('pointermove', (e) => {
  if (!drag || e.pointerId !== drag.id) return;
  const t = performance.now() / 1000;
  const dx = e.clientX - drag.x;
  drag.moved += Math.abs(dx) + Math.abs(e.clientY - drag.y);
  if (drag.moved > 6) {
    hold = 0;
    rot += dx / rPx;
    drag.samples.push([t, dx / rPx]);
    while (drag.samples.length && t - drag.samples[0][0] > 0.12) drag.samples.shift();
  }
  drag.x = e.clientX;
  drag.y = e.clientY;
  drag.t = t;
});

function release(e) {
  if (!drag || e.pointerId !== drag.id) return;
  const t = performance.now() / 1000;
  canvas.classList.remove('dragging');
  const tap = drag.moved < 10 && t - drag.t0 < 0.4;
  if (tap && lantern && !reveal) {
    const rect = canvas.getBoundingClientRect();
    const nx = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    const ny = -(((e.clientY - rect.top) / rect.height) * 2 - 1);
    const hit = lantern.pick(nx, ny);
    if (hit) startReveal(hit, t);
  } else if (!tap) {
    const s = drag.samples;
    if (s.length > 1) {
      const span = Math.max(0.016, s[s.length - 1][0] - s[0][0]);
      const sum = s.reduce((a, v) => a + v[1], 0);
      vel = clamp(sum / span, -8, 8);
    }
  }
  drag = null;
}
canvas.addEventListener('pointerup', release);
canvas.addEventListener('pointercancel', release);

addEventListener('keydown', (e) => {
  if (!lantern) return;
  const t = performance.now() / 1000;
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    interacted();
    if (reveal) endReveal(t);
    hold = 0;
    vel += e.key === 'ArrowLeft' ? -1.6 : 1.6;
  } else if (e.key === ' ' || e.key === 'Enter') {
    e.preventDefault();
    interacted();
    if (reveal) endReveal(t);
    else startReveal({ x: 0, y: 1.1, z: GEO.wallZ }, t);
  }
});

addEventListener('resize', size);

// 竖排时阿拉伯数字会横躺，日期改成中文数字：2024.10.4 → 二〇二四年十月四日
const CN = '〇一二三四五六七八九';
function cnNum(n) {
  if (n <= 10) return n === 10 ? '十' : CN[n];
  if (n < 20) return '十' + CN[n - 10];
  return CN[Math.floor(n / 10)] + '十' + (n % 10 ? CN[n % 10] : '');
}
function cnDate(s) {
  const m = /^(\d{4})\.(\d{1,2})\.(\d{1,2})$/.exec(s || '');
  if (!m) return s || '';
  return [...m[1]].map((d) => CN[+d]).join('') + '年' + cnNum(+m[2]) + '月' + cnNum(+m[3]) + '日';
}

// —— 地名竖签：背墙正中那片影子来自哪一格 ——
const thBack = -Math.PI / 2;
let shownIdx = -1;
function place() {
  if (!PANELS.some((p) => p.name || p.date)) return;
  let u = (thBack - rot) / TAU;
  u -= Math.floor(u);
  const f = u * NP, idx = Math.floor(f) % NP, fr = f - Math.floor(f);
  const edge = Math.min(clamp(fr / 0.08), clamp((1 - fr) / 0.08));
  if (idx !== shownIdx) {
    shownIdx = idx;
    $('placeName').textContent = PANELS[idx] ? PANELS[idx].name : '';
    $('placeDate').textContent = PANELS[idx] ? cnDate(PANELS[idx].date) : '';
  }
  $('place').style.opacity = (reveal ? 0 : edge).toFixed(3);
}

function frame() {
  if (!innerWidth || !innerHeight) {
    requestAnimationFrame(frame);
    return;
  }
  const now = performance.now() / 1000;
  const dt = Math.min(0.05, now - last);
  last = now;
  if (!drag) {
    const target = -W0 * (1 - hold);
    vel += (target - vel) * (1 - Math.exp(-dt / (hold ? 0.12 : 1.1)));
    rot += vel * dt;
  }
  const amt = revealAmount(now);
  if (reveal && reveal.tOut === null && now - reveal.t0 > 8) endReveal(now);
  $('cap').style.opacity = amt.toFixed(3);
  place();
  lantern.render({
    time: now,
    rot,
    cam,
    exposure: 1.25,
    reveal: reveal
      ? { amount: amt, center: reveal.center, radius: lerp(0.05, 2.8, easeOut((now - reveal.t0) / 1.6)), u0: reveal.u0, u1: reveal.u1 }
      : { amount: 0 },
  });
  requestAnimationFrame(frame);
}

createLantern({
  canvas,
  width: Math.max(1, innerWidth),
  height: Math.max(1, innerHeight),
  paperUrl: D.paper,
  photoUrl: D.photo,
  pixelRatio: Math.min(window.devicePixelRatio || 1, LOW ? 1 : 2),
})
  .then((l) => {
    lantern = l;
    size();
    $('msg').style.display = 'none';
    last = performance.now() / 1000;
    requestAnimationFrame(frame);
  })
  .catch((err) => {
    $('msg').textContent = '这台设备没能点亮这盏灯（WebGL 不可用）';
    console.error(err);
  });
