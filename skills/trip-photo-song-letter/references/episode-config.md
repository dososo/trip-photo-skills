# episode.json 字段

配置放在 `<输出文件夹>/work/episode.json`，所有路径都相对于 `work/`。从 `examples/xiangxi-2024/episode.json` 复制一份改。改完先校验：`python3 scripts/episode.py validate --out <输出文件夹>`。

歌（`work/song.json`、`<输出文件夹>/song.wav`）、写歌照片（`work/melody.jpg`）和它的山脊（`work/ridge.npy`）由前面几步固定生成，配置里不用写；成片固定是 `<输出文件夹>/episode.mp4`。

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | 是 | 固定为 `"1"` |
| `hook.title` | 是 | 开场大字，例如「凤凰的屋檐，写了一首歌」（≤ 12 字） |
| `hook.subtitle` | 是 | 开场小字，固定句式「<山脊/屋檐>有多高，音就有多高」 |
| `scenes.verse.image` | 是 | 主歌画面：画了就填 `gen/verse.png`，没画就填工作副本，例如 `photos/<主歌照片>.jpg`（横图会平移，竖图会缓推） |
| `scenes.verse.particles` | 否 | `{"from": [x, y], "to": [x, y]}`，0–1 的画面比例坐标；主歌里每个音从 from 飘一粒光到 to（只对竖图准确） |
| `scenes.*.pan` | 否 | 横图平移范围 `[起点, 终点]`，0 = 最左，1 = 最右；默认 `[0.5, 0.5]` |
| `scenes.*.focus` | 否 | 竖图缓推中心 `[x, y]`；默认 `[0.5, 0.6]` |
| `scenes.chorus.image` | 是 | 副歌画面：`gen/chorus.png` 或工作副本 |
| `scenes.chorus.ridge` | 否 | 副歌画面自己的山脊：`ridge --chorus` 的输出，填 `"chorus_ridge.npy"`。只有当副歌画面是写歌照片改出来的画时才填；画里有人物站在山脊上也没关系，偏离超过 20 像素的地方会自动改用照片的山脊 |
| `scenes.ending.image` | 是 | 间奏 + 高潮的画面：`gen/ending.png` 或工作副本 |
| `captions[]` | 是 | 字幕。`text`（≤ 20 字）、`section`（`verse` / `chorus` / `bridge` / `climax` / `outro`）、`at`（该段开始后第几秒出现）、`slot`（0 上行，1 下行）、`until`（可选，该段开始后第几秒淡出；不填时：`verse` 到副歌前、`chorus` 到间奏前、`bridge` 和 `climax` 都到信纸出现前）。`verse` 从开场的线飞下来那一刻算起（约第 6 秒），整段约 11 秒。每句至少留 3 秒；出静帧确认每句都看得全 |
| `letter.to` | 是 | 抬头，例如「致 拍了我二百一十三张照片的你」 |
| `letter.body` | 是 | 正文，最多 9 行，每行最多 17 字 |
| `letter.sign` | 是 | 落款（地名） |
| `letter.postmark` / `letter.date` | 是 | 邮戳上的地名（≤ 3 字最好看）和日期 `2024.09.29` |
| `letter.stamp` | 是 | 做邮票的照片：用主歌照片的工作副本，例如 `photos/<主歌照片>.jpg` |
| `letter.ps` | 是 | 落款下面一行，例如「随信附上：凤凰的屋檐写的歌」 |
| `tag` | 否 | 片尾一行小字 |

段落时间来自 `song.json`：开场（前奏，约 6 秒）→ 主歌 → 副歌 → 间奏 → 高潮 → 尾声（信纸滑上来）。
