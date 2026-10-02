---
name: trip-photo-song-letter
description: 把一个地方的旅行照片做成一集「一张画 · 一首歌 · 一封信」竖版短视频（1080×1920 MP4）。一首歌：在本机沿照片里的山脊或屋檐读出旋律，用小乐队编曲（八音盒、钢琴、弦乐、木吉他、贝斯、轻鼓），画面上山脊有多高、音就有多高；一张画：一个手绘小人回到照片里做一件温柔的小事（可选，需要用户自己的生图额度，照片会上传给生图服务）；一封信：这个地方用用户那天真实的拍摄时间和张数写一封短信。触发词：一张画一首歌一封信、照片写歌、天际线旋律、山脊旋律、屋檐的旋律、照片变成音乐、风景来信、城市给我写信、回到照片里、旅行照片做成音乐视频、trip photo song letter、skyline melody。不适用：只想给照片换画风或做插画海报、AI 写歌词或人声演唱、处理人像和自拍、剪辑已有视频、需要联网地图或定位的功能。
license: Apache-2.0
metadata:
  version: "0.1.0"
  author: dososo
  repository: https://github.com/dososo/trip-photo-skills
---

# 一张画 · 一首歌 · 一封信 · trip-photo-song-letter

输入：同一个地方的旅行照片（一个文件夹，JPG / PNG / HEIC 都行）。
输出：`<输出文件夹>/episode.mp4`（约 50 秒，1080×1920）和这首歌 `<输出文件夹>/song.wav`；中间文件都在 `<输出文件夹>/work/`。

歌、信、出片都在本机完成，不调用云端接口；只有第 4 步「画」是可选的，要用用户自己的生图服务。三样东西各自都能单独用：只要歌就做到第 5 步；不想生图就跳过第 4 步，用工作副本当画面。

## 第 0 步：开工前（必须做）

1. 向用户要三样：照片文件夹、输出文件夹（不要放在照片文件夹里）、地名（只写到城市或景区，例如「凤凰古城」「边城茶峒」，不要住址、酒店）。
2. 运行依赖检查：

   ```bash
   python3 scripts/episode.py check
   ```

3. 报缺 Python 包或 ffmpeg：先把要装的东西和大小告诉用户（torch 约 200 MB），用户同意后再装。

   ```bash
   python3 -m pip install numpy scipy pillow opencv-python soundfile torch transformers
   ```

   ffmpeg 在 macOS 上用 `brew install ffmpeg`。
4. 报缺乐器采样或字体：先预览大小，用户同意后再下载。

   ```bash
   python3 scripts/episode.py fetch          # 只列出文件和总大小（约 236 MB 采样 + 51 MB 字体）
   python3 scripts/episode.py fetch --yes    # 用户同意后再运行
   ```

   采样来自 VSCO 2 Community Edition（CC0），字体是霞鹜文楷（OFL-1.1），都下载到 `~/.cache/trip-photo-song-letter/`，不进仓库。分割模型 `openmmlab/upernet-convnext-tiny`（MIT）第一次运行时自动下载，约 240 MB。
5. 告诉用户隐私边界（原话可以照抄）：「写歌、写信、出片都在你这台电脑上完成；只有第 4 步『画』会把你选的 3 张照片发给生图服务，你不同意就不做这一步。」

## 第 1 步：选照片，做工作副本

按下面的要求选，选不出来就请用户自己挑，告诉用户每张的用途：

| 用途 | 要求 |
|---|---|
| 写歌照片 | 天空和山脊（或屋檐）的分界清楚，横图最好；分界线横跨大半个画面 |
| 主歌画面 | 这个地方最早的一张好照片（例如刚到时）；也拿来做信上的邮票 |
| 副歌画面 | 最好就是写歌照片本身：画面里的山脊会跟着副歌一个音一个音亮起来 |
| 结尾画面 | 离开前后的一张（街道、河、路） |

- 不要选有人脸、儿童、车牌、门牌的照片。
- 你（AI）若要亲自打开照片看，照片会作为对话内容发给你的模型服务商。用户同意前，只给路径，让用户自己看。

```bash
python3 scripts/episode.py prepare --photos "<写歌照片>" "<主歌照片>" "<结尾照片>" --out "<输出文件夹>"
```

- 工作副本在 `<输出文件夹>/work/photos/`：转 sRGB，去掉全部 EXIF / GPS / 机型，长边 2400 像素。
- 看到 `⚠ … 是人` 的照片：换一张，或在第 4 步明确要求去掉人物。
- 看到 `✓ prepare` 才算成功。写歌照片如果也用作副歌画面，不用重复列。

## 第 2 步：取事实（写信只能用这些）

```bash
python3 scripts/episode.py facts --photos "<照片文件夹>" --out "<输出文件夹>" --mark "<写歌照片文件名>" "<主歌照片文件名>" "<结尾照片文件名>"
```

`work/facts.json` 里有：总张数、每天第一张和最后一张的时间、连拍（20 秒内 ≥ 6 张）、所标照片的拍摄时间。读不到拍摄时间的照片列在 `no_time` 里。

## 第 3 步：写信（规则必须遵守）

用这个地方的口吻，给拍照的人写一封短信。

1. 信里每一个时间、每一个数字，都必须能在 `work/facts.json` 里找到，或者用户亲口说过。找不到就不写，不许估计。
2. 照片时间只能证明「那一刻你在拍照」，证明不了你几点到、几点走。写成「夜里八点，你第一次对着我按下快门」「第二天中午，你拍下最后一张」；「你几点到的」「你几点走的」只有用户亲口说过才能写。
3. 张数后面不加内容词：写「那一晚，一共拍了二十五张」，不写「二十五张夜景」，因为那些照片里可能有合影和饭菜。用户确认过照片内容才能加。
4. 开头一句要能单独成立，例如「你在我这儿，拍了 213 张照片。」（数字写成汉字：二百一十三）。
5. 不写「两年前」「好久不见」这类时间感叹；不煽情，不写「我很想你」。
6. 正文最多 9 行，每行最多 17 个字；落款就是地名。
7. 写完给用户过目，用户改过再用。

格式照样例里的 `letter` 字段：`examples/xiangxi-2024/fenghuang/episode.json`（凤凰古城）、`examples/xiangxi-2024/chadong/episode.json`（边城茶峒）。

## 第 4 步：画（可选：小人回到照片里）

1. 先征得用户同意上传 3 张工作副本（不含 EXIF）。不同意就跳过，第 6 步直接用 `work/photos/` 里的工作副本当画面。
2. 提示词模板、角色参考图和调用命令都在 `references/drawing-prompts.md`；角色参考图是 `assets/character_ref.png`。每张画写一件不同的小事，和这个地方有关，不要和样例雷同。
3. 每张生成后让用户看，用户不满意就重来；不要自己判断「够好了」。
4. 生成的画存成 `<输出文件夹>/work/gen/verse.png`、`chorus.png`、`ending.png`。

## 第 5 步：一首歌

1. 提取山脊。写歌照片会先缩到 1600 宽，存成 `work/melody.jpg`：

   ```bash
   python3 scripts/episode.py ridge --photo "<输出文件夹>/work/photos/<写歌照片>.jpg" --out "<输出文件夹>"
   ```

   看到 `✓ ridge` 才算成功。把 `work/ridge_check.jpg` 的路径给用户看：绿线要贴着山脊或屋檐的边缘。贴不上：换一张写歌照片。
2. 决定读哪一段（0 = 最左，1 = 最右）：
   - 两端有树、招牌、电线：用起点、终点把它们排除（例如 0.12 和 0.80）。
   - 开场约 6 秒只画得到这一段的前三分之一，所以起点要落在有起伏的地方，不要从一段平屋顶开始（例如马头墙从第一级台阶读起）。
3. 写歌（调性和速度按地方的气质选；同一个系列里每集换一个，不要都一样）：

   ```bash
   python3 scripts/episode.py song --from 0.12 --to 0.80 --key D --bpm 80 --out "<输出文件夹>"
   ```

   看到 `✓ song` 才算成功。只要歌：把 `<输出文件夹>/song.wav` 给用户，到此结束。
4. 如果副歌画面是写歌照片改出来的画，也给它提取山脊（画里的山脊会跟着副歌亮）：

   ```bash
   python3 scripts/episode.py ridge --chorus "<输出文件夹>/work/gen/chorus.png" --out "<输出文件夹>"
   ```

   然后在第 6 步的配置里给 `scenes.chorus` 加上 `"ridge": "chorus_ridge.npy"`。

## 第 6 步：写配置

复制一份样例到 `<输出文件夹>/work/episode.json`，按 `references/episode-config.md` 逐项改：主歌画面是横图，复制 `examples/xiangxi-2024/fenghuang/episode.json`；是竖图，复制 `examples/xiangxi-2024/chadong/episode.json`（带每个音一粒光）。路径都相对于 `work/`。改完先校验：

```bash
python3 scripts/episode.py validate --out "<输出文件夹>"
```

看到 `✓ config` 才继续；有 `✗` 就按提示改。

## 第 7 步：出片

1. 先出几张静帧，给用户看：

   ```bash
   python3 scripts/episode.py stills --out "<输出文件夹>"
   ```

   静帧在 `work/stills/`（默认第 3、8、20、33、45 秒）。检查：小人在每张静帧里都看得见（横图会平移，看不见就改 `pan`；竖图改 `focus`）；字幕没有被挡；第 3 秒照片上的线贴着山脊。
2. 用户满意后出片：

   ```bash
   python3 scripts/episode.py render --out "<输出文件夹>"
   ```

   看到 `✓ episode` 才算成功。超过 30 MB 又要发到手机或社交平台时，加 `--crf 24 --name episode_small.mp4` 再出一份。

## 第 8 步：交付前校验（全部满足才算完成）

- [ ] `<输出文件夹>/episode.mp4` 存在，时长 45–60 秒，1080×1920。
- [ ] 信里每个时间、数字都能在 `work/facts.json` 里找到；用户已过目。
- [ ] 地名只到城市或景区；没有门牌、酒店、住址；画面里没有可辨认的人脸。
- [ ] 如果用了生图：用户同意过上传；在作品说明里注明「画面含 AI 生成内容」。
- [ ] 告诉用户：这首歌的旋律来自哪张照片的哪一段山脊；歌和视频可以自由用在自己的作品里（乐器采样是 CC0）。

## 参数速查

| 参数 | 默认 | 作用 |
|---|---|---|
| `song --from` / `--to` | 必填 | 读山脊的哪一段（0–1） |
| `song --key` | D | 调性：C、D、E、F、G、A、Bb 等 |
| `song --bpm` | 80 | 速度，60–110 |
| `stills --at` | 3,8,20,33,45 | 出哪几秒的静帧 |
| `render --crf` | 20 | 视频质量，越大文件越小 |
| `render --name` | episode.mp4 | 输出文件名 |
| `episode.json` 的 `pan` | [0.5, 0.5] | 横图从哪平移到哪（0 = 最左，1 = 最右） |
| `episode.json` 的 `focus` | [0.5, 0.6] | 竖图缓推的中心 |

## 不做什么

- 不用 AI 写歌、不用现成曲目：旋律只由山脊的起伏和固定的乐理规则决定，同一张照片每次结果一样。
- 不读 GPS，不写入任何元数据；`work/photos/` 里的工作副本没有 EXIF。用完可以整个删掉 `work/`。
- 不编造事实：信里没有出处的数字一律删掉。
- 不替用户决定上传照片。

细节见 `references/pipeline.md`（每一步怎么算）、`references/episode-config.md`（配置字段）和 `references/privacy.md`（数据去向）。
