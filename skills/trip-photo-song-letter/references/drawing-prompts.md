# 画：小人回到照片里（可选）

这一步会把照片发给生图服务，**必须先征得用户同意**。不同意就跳过，`work/episode.json` 里的场景直接用 `work/photos/` 下的工作副本。

## 角色（每张都一样）

`assets/character_ref.png`：一个钢笔淡彩的小旅人，圆眼镜、短黑发、浅蓝衬衫、米色裤子、棕色小背包。每次生成都把它作为第二张参考图附上，并在提示词里写：「Draw the SAME character, same proportions and colors.」

## 用 Codex 调用（ChatGPT 账号自带的生图工具）

```bash
codex exec --skip-git-repo-check -s workspace-write -i "<工作副本>.jpg" -i "assets/character_ref.png" -- "$(cat prompt.txt)"
```

- `-i` 可以带多个文件，**提示词前必须加 `--`**，否则提示词会被当成图片路径。
- 如果报「model is not supported when using Codex with a ChatGPT account」，加 `-m <你账号可用的模型>`。
- 每张约消耗 5–6 万 token，从用户的套餐额度里扣，先告诉用户。
- 也可以用 OpenAI 图像 API（需要 `OPENAI_API_KEY` 环境变量；密钥只放环境变量，不要写进命令行参数或 URL）。

## 提示词模板

把尖括号里的内容换掉。三张图各写一件**不同的、和这个地方有关**的小事；不要照抄下面样例里的动作。

```text
You are producing ONE image for an art project. Use your built-in image generation / image editing tool. Do not write code to draw it.

Inputs (attached, in this order):
1. a real photo: <用一句英文描述照片内容>.
2. a character reference: a tiny hand-drawn traveler (round glasses, short black hair, light-blue shirt, beige trousers, small brown backpack). Draw the SAME character, same proportions and colors.

Task: <landscape 4:3 | portrait 3:4> image, same framing as photo 1, travel-sketchbook style "half real photo, half hand drawing":
- keep the upper ~62% a real photograph — essentially unchanged; do not change the shape of the skyline or roofline
- the bottom ~38% gently turns into fine black-ink line drawing with light watercolor wash on warm white paper: <下半部分画什么>
- the tiny traveler <做一件什么小事；和照片里的真实元素有互动>
- character size: about one sixth of the image height, clearly visible
<如果照片里有人或车：- remove every person and every vehicle; fill in with the matching scenery>

Strict rules:
- NO text, NO letters, NO signature, NO watermark
- no other people
- <landscape | portrait> orientation, aspect ratio <4:3 | 3:4>

Save the final PNG exactly to: <输出文件夹>/work/gen/<verse|chorus|ending>.png
Then reply with only the saved path.
```

## 样例里用过的小事（只作参考，不要重复）

| 地方 | 场景 | 小事 |
|---|---|---|
| 边城茶峒 | 傍晚的竹编灯笼 | 举着玻璃瓶，接住从真灯笼里飘下来的光 |
| 边城茶峒 | 蓝调的河 | 把一只载着光的纸船放进河里 |
| 边城茶峒 | 中午的老街 | 提着那瓶光走远，回头挥手 |
| 凤凰古城 | 夜里的河岸 | 用毛笔把河上的灯影画成水彩 |
| 凤凰古城 | 早上的马头墙 | 坐在翘起的檐角上吹竹笛 |
| 凤凰古城 | 沱江跳岩 | 踩着跳岩过河，回头挥手 |

## 验收

- 上半部分还是那张照片：山脊、屋檐的形状没变（第 5 步会在画里点亮山脊，形状变了就对不上）。
- 小人和参考图是同一个人。
- 没有文字、没有别的人。
- 用户看过并满意。
