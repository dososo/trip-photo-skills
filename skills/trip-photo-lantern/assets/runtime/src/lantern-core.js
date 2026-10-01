// 走马灯渲染核心 · trip-photo-lantern
// SPDX-License-Identifier: Apache-2.0
// 依赖：three.js（MIT）。不联网、不读时钟：画面完全由传入的 state 决定，可逐帧复现。
//
// 光照不用阴影贴图，而是解析计算：从烛焰出发的每一条光线，
// 依次穿过「内筒剪纸 → 灯罩宣纸与木骨 → 墙面」。
// 因此灯罩上的剪影清晰、墙上的影子柔和，两者严格对应同一张剪纸。

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

export const GEO = {
  R: 0.46,            // 灯罩（八角）外接圆半径
  shadeY0: -0.39,
  shadeY1: 0.39,
  r: 0.30,            // 内筒半径
  drumY0: -0.30,
  drumH: 0.4712,      // 使剪纸带的物理宽高比与贴图一致
  band: [0.3125, 0.8125],
  flame: [0, -0.19, 0],
  wallZ: -2.6,
  floorY: -1.55,
  ceilY: 5.5,
  sideX: 2.4,
  panels: 8,
};

// 色板：取自 2024 年茶峒竹编灯笼与蓝调江面的实拍
export const PALETTE = {
  candle: [1.0, 0.76, 0.50],
  paperTint: [1.0, 0.86, 0.66],
  plaster: [0.80, 0.72, 0.60],
  lacquer: 0x1b120d,
  tassel: 0x9a2b22,
  ambient: [0.010, 0.015, 0.028],   // 阴影里偏冷的蓝调（取自茶峒蓝调江面）
};

const COMMON = /* glsl */ `
  #define PI 3.141592653589793
  uniform sampler2D uPaper;
  uniform vec3 uLight;
  uniform float uRot;
  uniform float uFlame;
  uniform float uIntensity;
  uniform float uR;
  uniform float uDrumR;
  uniform float uDrumY0;
  uniform float uDrumH;
  uniform float uShadeY0;
  uniform float uShadeY1;
  uniform float uTexPerUnit;

  float octRadius(float th) {
    float seg = PI * 0.25;
    float a = th - floor(th / seg + 0.5) * seg;
    return uR * cos(PI / 8.0) / cos(a);
  }

  // 剪纸遮挡：返回 0（透光）~ 1（纸）。同时给出内筒展开坐标 u、v。
  float drumOcc(vec3 P, float extraLod, out float oU, out float oV) {
    vec3 dir = P - uLight;
    float hl = max(length(dir.xz), 1e-4);
    float th = atan(dir.z, dir.x);
    float tD = uDrumR / hl;
    float yD = uLight.y + dir.y * tD;
    float v = (yD - uDrumY0) / uDrumH;
    float u = (th - uRot) / (2.0 * PI);
    oU = u; oV = v;
    if (v < 0.0 || v > 1.0) return 0.0;
    float distP = length(dir);
    float distD = distP * tD;
    float k = uFlame * max(distP - distD, 0.0) / distP;   // 半影在剪纸上的宽度（世界单位）
    float lod = clamp(log2(max(k * uTexPerUnit, 1.0)) + extraLod, 0.0, 7.0);
    return textureLod(uPaper, vec2(u, v), lod).r;
  }

  // 灯罩宣纸、木骨、上下边框对光线的透过率
  vec3 shadeTrans(vec3 P, vec3 paperTint) {
    vec3 dir = P - uLight;
    float hl = max(length(dir.xz), 1e-4);
    float th = atan(dir.z, dir.x);
    float tS = octRadius(th) / hl;
    float yS = uLight.y + dir.y * tS;
    if (yS < uShadeY0) return vec3(0.0);
    if (yS > uShadeY1 + 0.012) return vec3(1.0);
    float rim = max(smoothstep(uShadeY1 - 0.030, uShadeY1 - 0.022, yS), 1.0 - smoothstep(uShadeY0 + 0.022, uShadeY0 + 0.030, yS));
    float b = mod(th - PI / 8.0, PI * 0.25);
    float cd = min(b, PI * 0.25 - b);
    float rib = 1.0 - smoothstep(0.008, 0.040, cd);
    return paperTint * (1.0 - rim * 0.70) * (1.0 - rib * 0.35);
  }

  float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
  }
  float vnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    float a = hash12(i), b = hash12(i + vec2(1, 0)), c = hash12(i + vec2(0, 1)), d = hash12(i + vec2(1, 1));
    vec2 w = f * f * (3.0 - 2.0 * f);
    return mix(mix(a, b, w.x), mix(c, d, w.x), w.y);
  }
`;

const ROOM_VS = /* glsl */ `
  varying vec3 vWorld;
  varying vec3 vNormal;
  varying vec2 vUv;
  void main() {
    vec4 w = modelMatrix * vec4(position, 1.0);
    vWorld = w.xyz;
    vNormal = normalize(mat3(modelMatrix) * normal);
    vUv = uv;
    gl_Position = projectionMatrix * viewMatrix * w;
  }
`;

const ROOM_FS = /* glsl */ `
  ${COMMON}
  uniform sampler2D uPhoto;
  uniform vec3 uAlbedo;
  uniform vec3 uCandle;
  uniform vec3 uPaperTint;
  uniform vec3 uAmbient;
  uniform float uPower;
  uniform float uBand0;
  uniform float uBand1;
  uniform float uRevealAmt;
  uniform vec3 uRevealCenter;
  uniform float uRevealRad;
  uniform float uRevealU0;
  uniform float uRevealU1;
  uniform float uPhotoGain;
  uniform float uMirror;
  varying vec3 vWorld;
  varying vec3 vNormal;
  varying vec2 vUv;

  void main() {
    float u, v;
    float occ = drumOcc(vWorld, 0.0, u, v);
    vec3 T = shadeTrans(vWorld, uPaperTint) * (1.0 - occ * 0.985);
    vec3 Ld = uLight - vWorld;
    float d = length(Ld);
    float lam = max(dot(normalize(vNormal), Ld / d), 0.0);
    float fall = uPower / (d * d + 0.25);
    vec3 light = uCandle * uIntensity * lam * fall;
    // 墙面灰泥的细微起伏
    float grain = 0.93 + 0.07 * vnoise(vWorld.xy * 9.0 + vWorld.z * 5.0);
    vec3 col = uAlbedo * grain * light * T;

    // 「影子变回照片」：在点按处圆形展开，把对应那一格原照片投在墙上
    if (uRevealAmt > 0.001 && v > uBand0 && v < uBand1) {
      float pu = fract(u);
      float edge = 0.010;
      float inPanel = smoothstep(uRevealU0, uRevealU0 + edge, pu) * (1.0 - smoothstep(uRevealU1 - edge, uRevealU1, pu));
      // 照片带的上下沿羽化，避免墙上出现一道硬切线
      float inBand = smoothstep(uBand0, uBand0 + 0.03, v) * smoothstep(uBand1, uBand1 - 0.09, v);
      float m = 1.0 - smoothstep(uRevealRad * 0.72, uRevealRad, distance(vWorld, uRevealCenter));
      m *= inPanel * inBand * uRevealAmt;
      vec3 photo = textureLod(uPhoto, vec2(pu, v), 1.5).rgb;
      vec3 shade = shadeTrans(vWorld, uPaperTint);
      vec3 proj = photo * uCandle * uIntensity * lam * fall * shade * uPhotoGain;
      col = mix(col, proj, m);
    }
    col += uAmbient * uAlbedo * grain;
    gl_FragColor = vec4(col, 1.0);
  }
`;

const SHADE_FS = /* glsl */ `
  ${COMMON}
  uniform vec3 uCandle;
  uniform vec3 uPaperTint;
  uniform float uPowerShade;
  varying vec3 vWorld;
  varying vec3 vNormal;
  varying vec2 vUv;

  void main() {
    float u, v;
    float occ = drumOcc(vWorld, 0.4, u, v);
    vec3 dir = vWorld - uLight;
    float d = length(dir);
    vec3 V = normalize(cameraPosition - vWorld);
    float fwd = pow(max(dot(V, dir / d), 0.0), 2.5);
    float fall = uPowerShade / (d * d + 0.03);
    // 宣纸纤维：横向长纤维 + 细颗粒
    float fib = 0.86 + 0.10 * vnoise(vec2(vUv.x * 60.0, vUv.y * 300.0)) + 0.06 * vnoise(vUv * vec2(900.0, 900.0));
    float lit = (1.0 - occ * 0.92) * fall * uIntensity * (0.45 + 1.1 * fwd);
    vec3 col = uPaperTint * uCandle * lit * fib;
    col += vec3(0.020, 0.012, 0.007) * uIntensity;     // 纸内散射的底光
    gl_FragColor = vec4(col, 1.0);
  }
`;

const GRADE_SHADER = {
  uniforms: {
    tDiffuse: { value: null },
    uTime: { value: 0 },
    uVignette: { value: 0.55 },
    uGrain: { value: 0.014 },
  },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
  `,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform float uTime;
    uniform float uVignette;
    uniform float uGrain;
    varying vec2 vUv;
    float h(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
    void main() {
      vec4 c = texture2D(tDiffuse, vUv);
      vec2 q = vUv - 0.5;
      q.x *= 0.62;
      float vig = smoothstep(0.85, 0.18, length(q) * (1.0 + uVignette));
      c.rgb *= mix(1.0 - uVignette, 1.0, vig);
      float g = h(vUv * 1024.0 + floor(uTime * 24.0) * 7.31) - 0.5;
      c.rgb += g * uGrain * (0.35 + c.rgb);
      gl_FragColor = c;
    }
  `,
};

function lacquer(color) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.42, metalness: 0.05 });
}

export async function createLantern({ canvas, width, height, paperUrl, photoUrl, pixelRatio = 1 }) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, preserveDrawingBuffer: true });
  renderer.setPixelRatio(pixelRatio);
  renderer.setSize(width, height, false);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const loader = new THREE.TextureLoader();
  const [paper, photo] = await Promise.all([loader.loadAsync(paperUrl), loader.loadAsync(photoUrl)]);
  paper.colorSpace = THREE.NoColorSpace;
  paper.wrapS = THREE.RepeatWrapping;
  paper.wrapT = THREE.ClampToEdgeWrapping;
  paper.minFilter = THREE.LinearMipmapLinearFilter;
  paper.magFilter = THREE.LinearFilter;
  paper.anisotropy = 4;
  paper.generateMipmaps = true;
  paper.needsUpdate = true;
  photo.colorSpace = THREE.SRGBColorSpace;
  photo.wrapS = THREE.RepeatWrapping;
  photo.minFilter = THREE.LinearMipmapLinearFilter;
  photo.generateMipmaps = true;
  photo.needsUpdate = true;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x050403);
  const camera = new THREE.PerspectiveCamera(56, width / height, 0.05, 40);

  const shared = {
    uPaper: { value: paper },
    uLight: { value: new THREE.Vector3(...GEO.flame) },
    uRot: { value: 0 },
    uFlame: { value: 0.0045 },
    uIntensity: { value: 1 },
    uR: { value: GEO.R },
    uDrumR: { value: GEO.r },
    uDrumY0: { value: GEO.drumY0 },
    uDrumH: { value: GEO.drumH },
    uShadeY0: { value: GEO.shadeY0 },
    uShadeY1: { value: GEO.shadeY1 },
    uTexPerUnit: { value: 8192 / (2 * Math.PI * GEO.r) },
    uCandle: { value: new THREE.Vector3(...PALETTE.candle) },
    uPaperTint: { value: new THREE.Vector3(...PALETTE.paperTint) },
  };

  const reveal = {
    uRevealAmt: { value: 0 },
    uRevealCenter: { value: new THREE.Vector3(0, 0.6, GEO.wallZ) },
    uRevealRad: { value: 0.5 },
    uRevealU0: { value: 0 },
    uRevealU1: { value: 0.125 },
    uPhotoGain: { value: 1.45 },
  };

  const roomMat = (albedo, power) => new THREE.ShaderMaterial({
    uniforms: {
      ...shared,
      ...reveal,
      uPhoto: { value: photo },
      uAlbedo: { value: new THREE.Vector3(...albedo) },
      uAmbient: { value: new THREE.Vector3(...PALETTE.ambient) },
      uPower: { value: power },
      uBand0: { value: GEO.band[0] },
      uBand1: { value: GEO.band[1] },
      uMirror: { value: 0 },
    },
    vertexShader: ROOM_VS,
    fragmentShader: ROOM_FS,
  });

  // 房间：背墙、两侧墙、地面、天花
  const room = new THREE.Group();
  const W = GEO.sideX * 2, Hh = GEO.ceilY - GEO.floorY, D = 6.0;
  const back = new THREE.Mesh(new THREE.PlaneGeometry(W, Hh), roomMat(PALETTE.plaster, 3.3));
  back.position.set(0, (GEO.ceilY + GEO.floorY) / 2, GEO.wallZ);
  const left = new THREE.Mesh(new THREE.PlaneGeometry(D, Hh), roomMat(PALETTE.plaster, 3.3));
  left.position.set(-GEO.sideX, (GEO.ceilY + GEO.floorY) / 2, GEO.wallZ + D / 2);
  left.rotation.y = Math.PI / 2;
  const right = left.clone();
  right.material = roomMat(PALETTE.plaster, 3.3);
  right.position.x = GEO.sideX;
  right.rotation.y = -Math.PI / 2;
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(W, D), roomMat([0.42, 0.34, 0.27], 2.4));
  floor.rotation.x = -Math.PI / 2;
  floor.position.set(0, GEO.floorY, GEO.wallZ + D / 2);
  const ceil = new THREE.Mesh(new THREE.PlaneGeometry(W, D), roomMat([0.55, 0.48, 0.40], 2.4));
  ceil.rotation.x = Math.PI / 2;
  ceil.position.set(0, GEO.ceilY, GEO.wallZ + D / 2);
  room.add(back, left, right, floor);   // 不放天花：顶部自然沉入黑暗
  scene.add(room);

  // 灯
  const lantern = new THREE.Group();
  const H = GEO.shadeY1 - GEO.shadeY0;
  const shadeMat = new THREE.ShaderMaterial({
    uniforms: { ...shared, uPowerShade: { value: 0.15 } },
    vertexShader: ROOM_VS,
    fragmentShader: SHADE_FS,
  });
  const shade = new THREE.Mesh(new THREE.CylinderGeometry(GEO.R, GEO.R, H, 8, 1, true, Math.PI / 8), shadeMat);
  lantern.add(shade);
  const lac = lacquer(PALETTE.lacquer);
  for (let k = 0; k < 8; k++) {
    const a = Math.PI / 8 + k * Math.PI / 4;   // 角点（atan2(z, x) 约定）
    const rib = new THREE.Mesh(new THREE.BoxGeometry(0.024, H + 0.05, 0.024), lac);
    rib.position.set(GEO.R * Math.cos(a), 0, GEO.R * Math.sin(a));
    rib.rotation.y = -a;
    lantern.add(rib);
  }
  const rimGeo = new THREE.CylinderGeometry(GEO.R + 0.016, GEO.R + 0.016, 0.034, 8, 1, false, Math.PI / 8);
  const rimTop = new THREE.Mesh(rimGeo, lac); rimTop.position.y = GEO.shadeY1 - 0.012;
  const rimBot = new THREE.Mesh(rimGeo, lac); rimBot.position.y = GEO.shadeY0 + 0.012;
  const cap = new THREE.Mesh(new THREE.CylinderGeometry(GEO.R * 0.55, GEO.R + 0.02, 0.07, 8, 1, false, Math.PI / 8), lac);
  cap.position.y = GEO.shadeY1 + 0.045;
  const base = new THREE.Mesh(new THREE.CylinderGeometry(GEO.R + 0.02, GEO.R * 0.6, 0.06, 8, 1, false, Math.PI / 8), lac);
  base.position.y = GEO.shadeY0 - 0.035;
  const finial = new THREE.Mesh(new THREE.SphereGeometry(0.035, 16, 12), lac);
  finial.position.y = GEO.shadeY1 + 0.11;
  const cord = new THREE.Mesh(new THREE.CylinderGeometry(0.0022, 0.0022, GEO.ceilY - GEO.shadeY1, 6), lac);
  cord.position.y = (GEO.ceilY + GEO.shadeY1) / 2;
  const tasselMat = new THREE.MeshStandardMaterial({ color: PALETTE.tassel, roughness: 0.62 });
  const knot = new THREE.Mesh(new THREE.SphereGeometry(0.03, 16, 12), tasselMat);
  knot.position.y = GEO.shadeY0 - 0.13;
  const tassel = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.055, 0.26, 24, 1, true), tasselMat);
  tassel.position.y = GEO.shadeY0 - 0.28;
  const tStem = new THREE.Mesh(new THREE.CylinderGeometry(0.004, 0.004, 0.08, 6), tasselMat);
  tStem.position.y = GEO.shadeY0 - 0.085;
  lantern.add(rimTop, rimBot, cap, base, finial, cord, knot, tassel, tStem);
  scene.add(lantern);

  // 给漆木与流苏的弱补光（来自墙面反射）
  scene.add(new THREE.AmbientLight(0x6a4a34, 0.55));
  const fill = new THREE.PointLight(0xffb070, 0.9, 6, 2);
  fill.position.set(0.0, -0.2, 1.2);
  scene.add(fill);

  const composer = new EffectComposer(renderer, new THREE.WebGLRenderTarget(width * pixelRatio, height * pixelRatio, { type: THREE.HalfFloatType }));
  composer.setPixelRatio(pixelRatio);
  composer.setSize(width, height);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(width, height), 0.7, 0.55, 0.62);
  composer.addPass(bloom);
  const grade = new ShaderPass(GRADE_SHADER);
  composer.addPass(grade);
  composer.addPass(new OutputPass());

  const tmp = new THREE.Vector3();

  // 由时间推出的烛焰：亮度与位置的细微抖动，纯函数，可复现
  function flicker(t) {
    const s = Math.sin;
    return 1.0 + 0.055 * s(2 * Math.PI * 4.1 * t) + 0.04 * s(2 * Math.PI * 6.7 * t + 1.1) + 0.035 * s(2 * Math.PI * 1.3 * t + 0.3) + 0.02 * s(2 * Math.PI * 9.3 * t + 2.0);
  }
  function flamePos(t) {
    const s = Math.sin;
    return [GEO.flame[0] + 0.003 * s(2 * Math.PI * 0.9 * t) + 0.002 * s(2 * Math.PI * 2.3 * t + 1.3),
      GEO.flame[1] + 0.004 * s(2 * Math.PI * 1.7 * t),
      GEO.flame[2] + 0.002 * s(2 * Math.PI * 1.3 * t + 0.4)];
  }

  function setCam(c) {
    camera.fov = c.fov || 56;
    camera.position.set(c.x, c.y, c.z);
    camera.lookAt(tmp.set(c.tx, c.ty, c.tz));
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld();
  }

  function render(state) {
    const t = state.time || 0;
    const I = flicker(t) * (state.intensity ?? 1);
    const fp = flamePos(t);
    shared.uLight.value.set(fp[0], fp[1], fp[2]);
    shared.uRot.value = state.rot || 0;
    shared.uIntensity.value = I;
    fill.intensity = 0.9 * I;
    const rv = state.reveal || { amount: 0 };
    reveal.uRevealAmt.value = rv.amount || 0;
    if (rv.center) reveal.uRevealCenter.value.set(...rv.center);
    if (rv.radius) reveal.uRevealRad.value = rv.radius;
    if (rv.u0 !== undefined) { reveal.uRevealU0.value = rv.u0; reveal.uRevealU1.value = rv.u1; }
    setCam(state.cam);
    renderer.toneMappingExposure = state.exposure ?? 1.25;
    grade.uniforms.uTime.value = t;
    lantern.rotation.y = 0;
    composer.render();
  }

  // 屏幕坐标 → 墙面世界坐标（交互页点按用）
  const ray = new THREE.Raycaster();
  function pick(ndcX, ndcY) {
    ray.setFromCamera(new THREE.Vector2(ndcX, ndcY), camera);
    const hits = ray.intersectObjects([back, left, right], false);
    return hits.length ? hits[0].point : null;
  }

  // 世界坐标 → 该点所对应的剪纸格与展开坐标
  function panelAt(world, rot) {
    const L = new THREE.Vector3(...GEO.flame);
    const dir = new THREE.Vector3().subVectors(world, L);
    const th = Math.atan2(dir.z, dir.x);
    let u = (th - rot) / (2 * Math.PI);
    u = u - Math.floor(u);
    const idx = Math.floor(u * GEO.panels);
    return { u, idx, u0: idx / GEO.panels, u1: (idx + 1) / GEO.panels };
  }

  // 传入 cam 时先摆好机位再投影（用于提前算出某一时刻灯在画面里的位置）
  function project(world, cam) {
    if (cam) setCam(cam);
    tmp.copy(world).project(camera);
    return [(tmp.x * 0.5 + 0.5) * width, (-tmp.y * 0.5 + 0.5) * height];
  }

  // 交互页随窗口变化重设画布尺寸
  function resize(w, h, pr = pixelRatio) {
    renderer.setPixelRatio(pr);
    renderer.setSize(w, h, false);
    composer.setPixelRatio(pr);
    composer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  return { render, pick, panelAt, project, resize, renderer, camera };
}
