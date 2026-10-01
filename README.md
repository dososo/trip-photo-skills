# trip-photo-skills

把旅行照片做成能玩的中国物件。「我有一座园」系列，每期一件。

中文 · [English](README.en.md)

![license](https://img.shields.io/badge/license-Apache--2.0-b23a2e) ![agents](https://img.shields.io/badge/Claude%20Code%20%C2%B7%20Codex-skill-e9a648)

| 01 · 走马灯 | 02 · 拓片长卷 |
|---|---|
| <img src="docs/lantern-demo.gif" width="280" alt="拖动走马灯，点墙上的影子，影子变回原来的照片"> | <img src="docs/scroll-demo.gif" width="280" alt="手指擦过墨黑的拓片，照片像碑拓一样显出来，再晕回彩色"> |
| 照片的天际线剪成剪纸贴进灯筒，烛光把影子投满四面墙；点一下影子，它变回原来那张照片。 | 整卷是墨黑的拓片纸，手指擦过的地方照片像碑拓一样显出来，墨色再晕回彩色，擦完一段盖一方印。 |

都不是滤镜，也不是 AI 生图：

- **不花生图额度**：照片在本机处理，画面是代码实时算出来的。
- **照片在你自己电脑上处理**：网页里不写 GPS、机型、原文件名。
- **一个文件**：输出单文件 HTML，双击就能打开，手机浏览器也能玩。

## 安装

```bash
npx skills add dososo/trip-photo-skills --skill trip-photo-lantern
npx skills add dososo/trip-photo-skills --skill trip-photo-rubbing-scroll
```

装好后对 AI 说：「用 trip-photo-lantern，把 ~/Pictures/2024国庆 做成走马灯。」或者「用 trip-photo-rubbing-scroll，把这 6 张照片拓成一卷。」

也可以手动安装：把 `skills/` 下对应的文件夹整个复制到

- Claude Code：`~/.claude/skills/<名字>/`
- Codex：`~/.codex/skills/<名字>/`

## 01 · 走马灯 `trip-photo-lantern`

- **输入**一个旅行照片文件夹，自动挑出天际线清楚的 8 张；**输出** `lantern.html`（约 1.5 MB）。
- 依赖：Python 3.10+，`python3 -m pip install pillow numpy torch transformers`。抠天空用的分割模型 `openmmlab/upernet-convnext-tiny`（MIT）第一次运行时下载，约 240 MB。
- 不用 AI 助手也能直接跑：

  ```bash
  cd skills/trip-photo-lantern
  python3 scripts/lantern.py all --photos ~/Pictures/2024国庆 --out ~/Desktop/lantern
  ```

- 示例：[`examples/xiangxi-2024/lantern.html`](skills/trip-photo-lantern/examples/xiangxi-2024/lantern.html)。拖动转灯；点墙上的影子看原照片，再点一下收回；空格揭开背墙正中那一格。
- 原理：挑片按天空占比、天际线起伏与锯齿、天空亮度打分，再按时间、地点、画面相似度去重；光照不用阴影贴图，逐条光线解析计算「烛焰 → 内筒剪纸 → 八角灯罩 → 墙面」。细节见 [pipeline.md](skills/trip-photo-lantern/references/pipeline.md)。

## 02 · 拓片长卷 `trip-photo-rubbing-scroll`

- **输入**一个放了 2–8 张照片的文件夹；**输出** `scroll.html`（6 段约 6 MB）。
- 依赖：只要 `pillow` 和 `numpy`，不下载模型，不需要 GPU。

  ```bash
  cd skills/trip-photo-rubbing-scroll
  python3 scripts/scroll.py all --photos ~/Pictures/挑好的6张 --out ~/Desktop/scroll
  ```

- 示例：[`examples/xiangxi-2024/scroll.html`](skills/trip-photo-rubbing-scroll/examples/xiangxi-2024/scroll.html)。一根手指擦；两根手指左右拖、滚轮或两侧箭头翻卷；右上角「重拓」重来。
- 原理：拓片上石面上墨是黑的，刻线碰不到墨留白——照片的细节高通（瓦楞、窗格、山脊）就是刻线，天空和水面是石面；擦拭蒙版按「1 −（1 − 已有）×（1 − 本次）」叠加，擦透后按噪声决定的先后晕回彩色。细节见 [pipeline.md](skills/trip-photo-rubbing-scroll/references/pipeline.md)。

## 常见问题

**没有生图能用吗？** 能。两个 skill 都不需要生图。

**有人的照片会被用上吗？** 走马灯默认跳过人物面积超过 0.2% 的照片；拓片长卷不做人像检测。远处很小的人影都可能留在画面里，分享前自己看一眼。

**让 AI 助手看图安全吗？** 处理脚本不上传照片；但如果你让 AI 助手打开图片，图片会作为对话内容发给对应的模型服务商。skill 默认只给你路径，由你自己打开。

**手机上能做吗？** 生成需要电脑（Python）；生成的网页手机能打开。

## 许可

- 代码：[Apache-2.0](LICENSE)
- 示例照片及其衍生图：© dososo，不在代码许可范围内，见 [ASSET-LICENSE.md](ASSET-LICENSE.md)
- 第三方组件：见 [NOTICE](NOTICE)

## 作者

**爆裂队长 NEXT（BLCaptain）**

- GitHub：[dososo](https://github.com/dososo)
- X：[@thinkszyg](https://x.com/thinkszyg)
- 邮箱：[blteam2026@outlook.com](mailto:blteam2026@outlook.com)
