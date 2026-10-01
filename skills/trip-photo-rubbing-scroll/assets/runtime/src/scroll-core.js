// 拓片长卷渲染核心 · trip-photo-rubbing-scroll
// SPDX-License-Identifier: Apache-2.0
// 依赖：three.js（MIT）。不联网、不读时钟：画面完全由传入的时间和笔触决定，可逐帧复现。
//
// 一段 = 一张照片。卷首在最右，往左展开（手卷的读法）。
// 每段有一张「擦拭蒙版」：R = 擦了多少（多次擦叠加），G = exp(-第一次擦透的时刻 / 30)，0 表示还没擦透。
// 用 exp 编码而不是直接存时刻：线性插值时边缘会偏向「更晚」，晕染从里往外走，不会在边上提前冒色。
// 白纹按 R 显出；擦透之后过 delay 秒，彩色从噪声决定的边界一圈圈晕开，前沿留一道水迹。

import * as THREE from 'three';

export const LAYOUT = {
  SH: 760,        // 拓片高（卷内像素）
  MARGIN: 26,     // 拓片四周的留白纸边
  SILK: 34,       // 上下绫边
  MW: 384,        // 擦拭蒙版分辨率
  MH: 288,
};

const NOISE = /* glsl */ `
  float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
  float vnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
  }
  float fbm(vec2 p) { float a = 0.5, s = 0.0; for (int i = 0; i < 5; i++) { s += a * vnoise(p); p *= 2.03; a *= 0.5; } return s; }
`;

const QUAD_VS = /* glsl */ `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }
`;

const MESH_VS = /* glsl */ `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
`;

// 一次最多处理 32 段笔触：R' = 1 - (1-R)(1-cov)，次序无关，所以重放与累积结果一致
const STROKE_FS = /* glsl */ `
  uniform sampler2D uPrev;
  uniform vec4 uSeg[32];
  uniform vec4 uInfo[32];
  uniform int uCount;
  uniform vec2 uSize;
  varying vec2 vUv;
  void main() {
    vec4 m = texture2D(uPrev, vUv);
    float R = m.r, T = m.g;
    vec2 p = vec2(vUv.x, 1.0 - vUv.y) * uSize;
    for (int k = 0; k < 32; k++) {
      if (k >= uCount) break;
      vec4 s = uSeg[k];
      vec4 inf = uInfo[k];
      vec2 a = s.xy, ab = s.zw - s.xy;
      float h = clamp(dot(p - a, ab) / max(dot(ab, ab), 1e-4), 0.0, 1.0);
      float d = length(p - a - ab * h);
      float cov = (1.0 - smoothstep(inf.y * 0.3, inf.y, d)) * inf.z;
      float R2 = 1.0 - (1.0 - R) * (1.0 - cov);
      if (R < 0.5 && R2 >= 0.5) T = max(T, exp(-inf.x / 30.0));
      R = R2;
    }
    gl_FragColor = vec4(R, T, 0.0, 1.0);
  }
`;

const SECTION_FS = /* glsl */ `
  ${NOISE}
  uniform sampler2D uWhite;
  uniform sampler2D uPhoto;
  uniform sampler2D uMask;
  uniform float uNow;
  uniform float uSeed;
  uniform float uDelay;
  uniform float uDur;
  uniform float uColor;     // 1 = 直接显示彩色（片尾总览用）
  uniform vec2 uSize;
  uniform float uMargin;
  varying vec2 vUv;
  void main() {
    vec2 full = uSize + 2.0 * uMargin;
    vec2 p = vec2(vUv.x, 1.0 - vUv.y) * full - uMargin;
    vec2 iuv = p / uSize;
    // 拓片边缘：石头的边不齐，留白纸边
    vec2 de = min(p, uSize - p);
    float edge = min(de.x, de.y) + (fbm(p * 0.045 + uSeed) * 2.0 - 1.0) * 7.0;
    float inside = smoothstep(-1.0, 1.5, edge);
    vec3 paperCol = vec3(0.905, 0.878, 0.823) * (0.955 + 0.07 * fbm(p * 0.5 + 3.1));
    // 墨地：拓包一下下扑出来的云团 + 纸纤维
    float ink = fbm(p * 0.011 + uSeed * 1.7);
    float fib = vnoise(p * 0.9 + uSeed);
    vec3 ground = vec3(0.04 + 0.055 * ink * ink + 0.012 * fib);
    vec3 whiteCol = vec3(0.885 + 0.05 * fib) * vec3(1.0, 0.982, 0.94);
    vec2 tuv = vec2(clamp(iuv.x, 0.0, 1.0), 1.0 - clamp(iuv.y, 0.0, 1.0));
    float w = texture2D(uWhite, tuv).r;
    vec4 m = texture2D(uMask, tuv);
    float grain = vnoise(p * 0.32 + uSeed * 3.0);
    float rev = smoothstep(0.1 + 0.32 * grain, 0.78, m.r);
    vec3 rub = mix(ground, whiteCol, w * rev);
    // 墨色晕回彩色：按噪声决定先后，前沿留一道水迹
    // 擦透时刻取周围 9 点的平均（连「没擦透」的 0 一起平均）：场是连续的，
    // 晕色边界跟着噪声走，不会沿笔迹走出竖条，也不会在边缘断出锯齿；越靠近没擦到的地方晕得越晚
    float eSum = texture2D(uMask, tuv).g;
    for (int k = 0; k < 5; k++) {
      float a = float(k) * 1.2566 + 0.3;
      eSum += texture2D(uMask, tuv + vec2(cos(a), sin(a)) * 18.0 / uSize).g;
    }
    for (int k = 0; k < 6; k++) {
      float a = float(k) * 1.0472 + 0.52;
      eSum += texture2D(uMask, tuv + vec2(cos(a), sin(a)) * 40.0 / uSize).g;
    }
    eSum /= 12.0;
    float never = 1.0 - smoothstep(0.02, 0.2, eSum);
    float tFirst = -30.0 * log(max(eSum, 1e-4));
    float age = (uNow - tFirst - uDelay) / uDur - never * 100.0;
    float n = fbm(p * 0.006 + uSeed * 5.0);
    float front = 0.06 + 0.86 * n + (vnoise(p * 0.045 + uSeed) - 0.5) * 0.12;
    float bloom = max(smoothstep(front, front + 0.07, age), uColor);
    vec3 photo = texture2D(uPhoto, tuv).rgb;
    photo = mix(photo, photo * vec3(1.0, 0.97, 0.9), 0.25);
    vec3 col = mix(rub, photo, bloom);
    float rim = exp(-pow((age - front) / 0.022, 2.0)) * (1.0 - bloom) * (1.0 - never) * (1.0 - uColor);
    col = mix(col, col * 0.5 + vec3(0.015), rim * 0.65);
    col = mix(paperCol, col, inside);
    gl_FragColor = vec4(col, 1.0);
  }
`;

const SILK_FS = /* glsl */ `
  ${NOISE}
  uniform vec3 uColor;
  uniform vec2 uSize;
  varying vec2 vUv;
  void main() {
    vec2 p = vUv * uSize;
    float weave = 0.5 + 0.5 * sin(p.x * 2.4) * sin(p.y * 2.4);
    float cloud = fbm(p * 0.02);
    vec3 c = uColor * (0.9 + 0.08 * weave + 0.1 * cloud);
    float rim = smoothstep(0.0, 2.0, min(p.y, uSize.y - p.y));
    gl_FragColor = vec4(c * (0.75 + 0.25 * rim), 1.0);
  }
`;

const ROD_FS = /* glsl */ `
  uniform vec3 uColor;
  varying vec2 vUv;
  void main() {
    float x = vUv.x * 2.0 - 1.0;
    float shade = sqrt(max(0.0, 1.0 - x * x));
    float spec = pow(max(0.0, 1.0 - abs(x - 0.35) * 3.0), 6.0);
    gl_FragColor = vec4(uColor * (0.35 + 0.75 * shade) + vec3(spec * 0.25), 1.0);
  }
`;

export async function createScroll({ canvas, width, height, sections, pixelRatio = 1, base = './' }) {
  const L = LAYOUT;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(pixelRatio);
  renderer.setSize(width, height, false);
  renderer.outputColorSpace = THREE.LinearSRGBColorSpace;   // 着色器里直接按显示值计算

  const loader = new THREE.TextureLoader();
  const load = async (url) => {
    const t = await loader.loadAsync(url.startsWith('data:') ? url : base + url);
    t.colorSpace = THREE.NoColorSpace;
    t.minFilter = THREE.LinearMipmapLinearFilter;
    t.magFilter = THREE.LinearFilter;
    t.anisotropy = 4;
    t.generateMipmaps = true;
    t.needsUpdate = true;
    return t;
  };

  const scene = new THREE.Scene();
  scene.background = null;   // 背景交给页面（CSS），画布只画卷子
  const camera = new THREE.OrthographicCamera(0, width, height, 0, -10, 10);
  const scroll = new THREE.Group();
  scene.add(scroll);

  const pitch = [];
  const secs = [];
  let x = 0;                              // 卷首（最右）为 0，往左为负
  const SW = sections.map((s) => Math.round((L.SH * s.w) / s.h));
  for (let i = 0; i < sections.length; i++) {
    const fullW = SW[i] + 2 * L.MARGIN;
    pitch.push(fullW);
    x -= fullW;
    const [white, photo] = await Promise.all([load(sections[i].white), load(sections[i].photo)]);
    const rtOpts = { type: THREE.HalfFloatType, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, depthBuffer: false };
    const rtA = new THREE.WebGLRenderTarget(L.MW, L.MH, rtOpts);
    const rtB = new THREE.WebGLRenderTarget(L.MW, L.MH, rtOpts);
    const mat = new THREE.ShaderMaterial({
      uniforms: {
        uWhite: { value: white },
        uPhoto: { value: photo },
        uMask: { value: rtA.texture },
        uNow: { value: 0 },
        uSeed: { value: 3.1 + i * 7.7 },
        uDelay: { value: 1.2 },
        uDur: { value: 3.2 },
        uColor: { value: 0 },
        uSize: { value: new THREE.Vector2(SW[i], L.SH) },
        uMargin: { value: L.MARGIN },
      },
      vertexShader: MESH_VS,
      fragmentShader: SECTION_FS,
    });
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(fullW, L.SH + 2 * L.MARGIN), mat);
    mesh.position.set(x + fullW / 2, 0, 0);
    scroll.add(mesh);
    secs.push({ x0: x, fullW, imgX: x + L.MARGIN, imgW: SW[i], rt: [rtA, rtB], cur: 0, mat, dirty: true });
  }
  const total = -x;

  // 卷子落在桌上的影子
  const bandH = L.SH + 2 * L.MARGIN;
  const shadowMat = new THREE.ShaderMaterial({
    uniforms: { uSize: { value: new THREE.Vector2(total + 120, bandH + 2 * L.SILK) } },
    vertexShader: MESH_VS,
    fragmentShader: `
      uniform vec2 uSize;
      varying vec2 vUv;
      void main() {
        vec2 p = (vUv - 0.5) * (uSize + 160.0);
        vec2 d = max(abs(p) - uSize * 0.5, 0.0);
        float a = exp(-dot(d, d) / 1800.0) * 0.55;
        gl_FragColor = vec4(0.0, 0.0, 0.0, a);
      }`,
    transparent: true,
    depthWrite: false,
  });
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(total + 280, bandH + 2 * L.SILK + 160), shadowMat);
  shadow.position.set(-total / 2, -14, -1);
  scroll.add(shadow);

  // 上下绫边、两头的轴
  const silkMat = new THREE.ShaderMaterial({
    uniforms: { uColor: { value: new THREE.Vector3(0.70, 0.64, 0.52) }, uSize: { value: new THREE.Vector2(total + 40, L.SILK) } },
    vertexShader: MESH_VS,
    fragmentShader: SILK_FS,
  });
  for (const sgn of [1, -1]) {
    const silk = new THREE.Mesh(new THREE.PlaneGeometry(total + 40, L.SILK), silkMat);
    silk.position.set(-total / 2, sgn * (bandH / 2 + L.SILK / 2), 0);
    scroll.add(silk);
  }
  const rodMat = new THREE.ShaderMaterial({ uniforms: { uColor: { value: new THREE.Vector3(0.36, 0.2, 0.12) } }, vertexShader: MESH_VS, fragmentShader: ROD_FS });
  for (const rx of [22, -total - 22]) {
    const rod = new THREE.Mesh(new THREE.PlaneGeometry(26, bandH + 2 * L.SILK + 40), rodMat);
    rod.position.set(rx, 0, 0.1);
    scroll.add(rod);
  }

  // 擦拭蒙版的更新通道
  const quadScene = new THREE.Scene();
  const quadCam = new THREE.Camera();
  const strokeMat = new THREE.ShaderMaterial({
    uniforms: {
      uPrev: { value: null },
      uSeg: { value: Array.from({ length: 32 }, () => new THREE.Vector4()) },
      uInfo: { value: Array.from({ length: 32 }, () => new THREE.Vector4()) },
      uCount: { value: 0 },
      uSize: { value: new THREE.Vector2(1, 1) },
    },
    vertexShader: QUAD_VS,
    fragmentShader: STROKE_FS,
    depthTest: false,
    depthWrite: false,
  });
  quadScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), strokeMat));
  const clearCol = new THREE.Color(0, 0, 0);

  function clearMask(s) {
    renderer.setRenderTarget(s.rt[s.cur]);
    renderer.setClearColor(clearCol, 1);
    renderer.clear(true, false, false);
    renderer.setRenderTarget(null);
  }

  // segs：[{ x0, y0, x1, y1, t, r, k }]，卷内坐标（x 同卷，y 以拓片上沿为 0、向下为正）
  function applySegs(s, segs) {
    for (let off = 0; off < segs.length; off += 32) {
      const chunk = segs.slice(off, off + 32);
      chunk.forEach((g, k) => {
        strokeMat.uniforms.uSeg.value[k].set(g.x0 - s.imgX, g.y0, g.x1 - s.imgX, g.y1);
        strokeMat.uniforms.uInfo.value[k].set(g.t, g.r, g.k, 0);
      });
      strokeMat.uniforms.uCount.value = chunk.length;
      strokeMat.uniforms.uSize.value.set(s.imgW, L.SH);
      strokeMat.uniforms.uPrev.value = s.rt[s.cur].texture;
      const next = 1 - s.cur;
      renderer.setRenderTarget(s.rt[next]);
      renderer.render(quadScene, quadCam);
      renderer.setRenderTarget(null);
      s.cur = next;
    }
    s.mat.uniforms.uMask.value = s.rt[s.cur].texture;
  }

  function touches(s, g) {
    const pad = g.r + 2;
    return Math.max(g.x0, g.x1) + pad >= s.imgX && Math.min(g.x0, g.x1) - pad <= s.imgX + s.imgW;
  }

  // 交互页：只追加新笔触
  function addSegs(segs) {
    for (const s of secs) {
      const mine = segs.filter((g) => touches(s, g));
      if (mine.length) applySegs(s, mine);
    }
  }

  // 视频：每帧从空白重放到时刻 t（结果只取决于 t，可乱序渲染）
  function replay(segs, t, only = null) {
    secs.forEach((s, i) => {
      if (only && !only.includes(i)) return;
      clearMask(s);
      const mine = segs.filter((g) => g.t <= t && touches(s, g));
      if (mine.length) applySegs(s, mine);
      else s.mat.uniforms.uMask.value = s.rt[s.cur].texture;
    });
  }

  function resetAll() {
    for (const s of secs) {
      s.cur = 0;
      clearMask(s);
      s.mat.uniforms.uMask.value = s.rt[0].texture;
    }
  }
  resetAll();

  // view：{ pan（卷内 x，对准画面中心）, cy（卷带中线的屏幕 y，自上而下）, zoom }
  function placeScroll(view) {
    scroll.scale.set(view.zoom, view.zoom, 1);
    scroll.position.set(width / 2 - view.pan * view.zoom, height - view.cy, 0);
  }

  function setBloom(i, delay, dur) {
    secs[i].mat.uniforms.uDelay.value = delay;
    secs[i].mat.uniforms.uDur.value = dur;
  }

  function render(state) {
    placeScroll(state.view);
    renderer.setClearColor(0x000000, 0);
    for (const s of secs) {
      s.mat.uniforms.uNow.value = state.now;
      s.mat.uniforms.uColor.value = Array.isArray(state.color) ? state.color[secs.indexOf(s)] || 0 : state.color || 0;
    }
    renderer.setRenderTarget(null);
    renderer.render(scene, camera);
  }

  // 屏幕坐标（像素，左上原点）→ 卷内坐标
  function toScroll(sx, sy, view) {
    return { x: (sx - width / 2) / view.zoom + view.pan, y: (sy - (view.cy - (L.SH / 2) * view.zoom)) / view.zoom };
  }
  // 卷内坐标 → 屏幕坐标
  function toScreen(x, y, view) {
    return [width / 2 + (x - view.pan) * view.zoom, view.cy - (L.SH / 2) * view.zoom + y * view.zoom];
  }

  function resize(w, h, pr = pixelRatio) {
    width = w;
    height = h;
    renderer.setPixelRatio(pr);
    renderer.setSize(w, h, false);
    camera.right = w;
    camera.top = h;
    camera.updateProjectionMatrix();
  }

  return {
    render, replay, addSegs, resetAll, setBloom, toScroll, toScreen, resize, renderer,
    sections: secs.map((s) => ({ imgX: s.imgX, imgW: s.imgW, x0: s.x0, fullW: s.fullW })),
    total, layout: L,
  };
}
