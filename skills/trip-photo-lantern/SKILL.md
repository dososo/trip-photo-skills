---
name: trip-photo-lantern
description: 把一个文件夹的旅行照片做成一盏会转的中式走马灯网页。自动挑出天际线清楚的 8 张，在本机抠掉天空、剪成剪纸、贴进灯筒；烛光把影子投到四面墙上，拖动能转，点墙上的影子会变回原来那张照片。输出一个双击就能打开的单文件 HTML。触发词：走马灯、把旅行照片做成灯、照片剪影、剪纸灯、相册做成纪念品、trip photos lantern、revolving lantern、photo silhouette lantern。不适用：AI 生图或绘画、人像修图、视频剪辑、需要把照片上传到云端的处理。
license: Apache-2.0
metadata:
  version: "0.1.0"
  author: dososo
  repository: https://github.com/dososo/trip-photo-skills
---

# 走马灯 · trip-photo-lantern

输入：一个旅行照片文件夹（JPG / PNG / HEIC 都行，会读子文件夹）。
输出：`<输出文件夹>/lantern.html`，一个单文件网页，约 1.5 MB，不联网也能打开。

处理都在本机完成：不调用生图模型，也不调用云端视觉接口。只有第一次运行会从 Hugging Face 下载分割模型权重（约 240 MB）。

## 第 0 步：开工前（必须做）

1. 向用户要两个路径：照片文件夹、输出文件夹。输出文件夹不要放在照片文件夹里面。
2. 运行依赖检查：

   ```bash
   python3 scripts/lantern.py check
   ```

3. 如果报缺依赖：先把要装的东西和大小告诉用户，用户同意后再装。

   ```bash
   python3 -m pip install pillow numpy torch transformers
   ```

   torch 约 200 MB；分割模型 `openmmlab/upernet-convnext-tiny`（MIT）首次运行时下载，约 240 MB。
4. 告诉用户隐私边界（原话可以照抄）：「照片只在你这台电脑上处理；网页里不会写入 GPS、拍摄设备和原文件名。」

## 第 1 步：一条龙生成

```bash
python3 scripts/lantern.py all --photos "<照片文件夹>" --out "<输出文件夹>"
```

- 照片超过 240 张时，只分割预筛分最高的 240 张。想多看一些，加 `--limit 600`（每张约 1 秒）。
- 看到 `✓ 5 html` 才算成功。任何一步报 `✗`，按提示处理，不要跳过。

## 第 2 步：让用户确认 8 张

1. 把 `<输出文件夹>/work/picks_sheet.jpg` 的路径给用户，请用户自己打开看。每张左上角有编号。
2. 用户想换哪张，就从 `<输出文件夹>/work/src/` 里让用户挑编号，凑满 8 个后运行：

   ```bash
   python3 scripts/lantern.py pick --out "<输出文件夹>" --ids 0003,0017,0021,0030,0042,0051,0066,0070
   python3 scripts/lantern.py strip --out "<输出文件夹>"
   python3 scripts/lantern.py html --out "<输出文件夹>"
   ```

   用户指定的照片如果还没分割过，先运行 `mask --out "<输出文件夹>" --limit 2000`，再执行上面三行。
3. 隐私提醒：你（AI）若要亲自查看这些图片，图片会作为对话内容发给你的模型服务商。用户没同意之前，只给路径，不要自己打开图片。

## 第 3 步：填地名（可选，强烈建议）

1. 问用户这 8 张分别是哪里。地名只写到城市或景区，例如「凤凰古城」「吉首 · 矮寨」，不要写住址、酒店。
2. 把地名写进 `<输出文件夹>/work/names.json`（键是照片编号，值是地名），保持 JSON 合法。
3. 重新生成：

   ```bash
   python3 scripts/lantern.py strip --out "<输出文件夹>"
   python3 scripts/lantern.py html --out "<输出文件夹>" --title "走马灯" --subtitle "湘西 · 2024 国庆"
   ```

   不传 `--subtitle` 时，副标题默认用照片的日期范围。

## 第 4 步：交付前校验（全部满足才算完成）

- [ ] `<输出文件夹>/lantern.html` 存在，大小在 0.5–5 MB 之间。
- [ ] `<输出文件夹>/panels.json` 里 `panels` 正好 8 项。
- [ ] `names.json` 里的地名没有门牌号、酒店名、住址。
- [ ] 告诉用户：双击 `lantern.html` 用浏览器打开；拖动转灯，点墙上的影子看原照片，再点一下收回；手机上可以把这个文件发过去，用浏览器打开。

## 参数速查

| 参数 | 默认 | 作用 |
|---|---|---|
| `--limit` | 240 | 最多分割多少张 |
| `--max-person` | 0.002 | 人物面积超过 0.2% 的照片不选；设成 1 就不过滤 |
| `--hole-gain` | 0.86 | 地面里比天空亮多少的地方刻成镂空；越小镂空越多 |
| `--title` / `--subtitle` | 走马灯 / 日期范围 | 网页左上角的标题 |
| `--device` | 自动 | `cpu` 或 `mps`；MPS 不可用时自动回退 CPU |

## 不做什么

- 不生成新图片，不调用任何生图或云端视觉接口。
- 不把 EXIF、GPS、机型、原文件名写进网页；`work/` 里的工作副本也没有任何元数据。`work/manifest.json` 记录了原文件路径和 GPS，只在本机，用完可以整个删掉 `work/`。
- 默认不选有人的照片。

细节见 `references/pipeline.md`（每一步怎么算）和 `references/privacy.md`（数据去向）。
