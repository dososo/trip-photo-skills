---
name: trip-photo-rubbing-scroll
description: 把几张旅行照片做成一卷可以用手指「拓」的长卷网页。整卷是墨黑的拓片纸，手指擦过的地方，照片像碑拓一样显出白色纹路；擦透之后墨色慢慢晕回彩色；一段擦够了自动盖一方朱砂印（地名 + 干支年季）。按拍摄时间从右往左排成手卷。输出一个双击就能打开的单文件 HTML。触发词：拓片、碑拓、拓片长卷、把旅行照片做成长卷、手卷、rubbing scroll、ink rubbing、handscroll。不适用：AI 生图或绘画、人像修图、视频剪辑、需要把照片上传到云端的处理。
license: Apache-2.0
metadata:
  version: "0.1.0"
  author: dososo
  repository: https://github.com/dososo/trip-photo-skills
---

# 拓片长卷 · trip-photo-rubbing-scroll

输入：一个放了 2–8 张旅行照片的文件夹（JPG / PNG / HEIC）。每张照片是卷上的一段。
输出：`<输出文件夹>/scroll.html`，单文件网页，6 段约 6 MB，不联网也能打开。

只需要 Pillow 和 numpy，不需要 GPU，也不下载模型。处理都在本机，不调用生图或云端接口。

## 第 0 步：开工前（必须做）

1. 向用户要两个路径：照片文件夹、输出文件夹。
2. 建议用户自己挑 4–8 张：横构图、有建筑或山脊这种纹理的效果最好；纯天空、纯水面会是一大片黑。
   文件夹里超过 `--max`（默认 6）张时，脚本会按「纹理丰富度」和日期分散自动挑，并提醒用户检查有没有人像。
3. 运行依赖检查；缺依赖就告诉用户，同意后安装 `python3 -m pip install pillow numpy`。

   ```bash
   python3 scripts/scroll.py check
   ```

## 第 1 步：生成

```bash
python3 scripts/scroll.py all --photos "<照片文件夹>" --out "<输出文件夹>"
```

看到 `✓ html` 才算成功。

## 第 2 步：填地名和印文（强烈建议）

1. 问用户每一段是哪里（只写到城市或景区），印上刻几个字（2–3 个字最好看，例如「凤凰」「东江湖」）。
2. 写进 `<输出文件夹>/work/names.json`，键是段号 `00`、`01`…，按拍摄时间从早到晚：

   ```json
   { "00": { "name": "凤凰古城", "seal": "凤凰" }, "01": { "name": "吉首 · 矮寨", "seal": "矮寨" } }
   ```

   照片没有拍摄时间（EXIF 被删过）时，可以补 `"date": "2024-10-01"`，印上的「甲辰秋」这类年季就靠它算。
3. 重新打包：

   ```bash
   python3 scripts/scroll.py html --out "<输出文件夹>" --title "拓片长卷" --subtitle "湘西 · 2024 国庆"
   ```

## 第 3 步：交付前校验（全部满足才算完成）

- [ ] `<输出文件夹>/scroll.html` 存在，大小在 1–12 MB 之间。
- [ ] `<输出文件夹>/sections.json` 的段数等于用户想要的张数。
- [ ] `names.json` 里没有门牌号、酒店名、住址。
- [ ] 告诉用户怎么玩：一根手指擦；两根手指左右拖、滚轮或两侧箭头翻卷；右上角「重拓」清空重来；键盘 ← → 翻段，R 重拓。

## 不做什么

- 不生成新图片，不调用任何生图或云端视觉接口。
- 网页里不写 EXIF、GPS、机型、原文件名；`work/sources.json` 记录了原文件路径，只在本机，用完可以删掉整个 `work/`。
- 不做人像检测：分享前请用户自己确认画面里没有不想公开的人。

细节见 `references/pipeline.md`。
