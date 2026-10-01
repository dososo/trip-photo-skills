# trip-photo-skills

Turn trip photos into small Chinese objects you can play with. 01: a revolving lantern (走马灯). 02: an ink-rubbing handscroll (拓片长卷) — rub the black paper with a finger and the photo appears like a stone rubbing, then bleeds back into color; each finished section gets a cinnabar seal. Install it with `npx skills add dososo/trip-photo-skills --skill trip-photo-rubbing-scroll`; it needs only Pillow and numpy.

[中文](README.md) · English

<p align="center"><img src="docs/lantern-demo.gif" width="300" alt="Drag the lantern; tap a shadow on the wall and it turns back into the photo"></p>

**Input:** a folder of trip photos. **Output:** a single-file web page with a revolving paper lantern. The skylines of 8 photos are cut into paper silhouettes and wrapped around the inner drum; candlelight throws their shadows on the walls. Drag to spin it. Tap a shadow and it turns back into the original photo.

Not a filter, and no image generation.

- **No generation credits.** Sky segmentation runs locally with an open model; the lantern and its shadows are computed in real time.
- **Your photos stay on your machine.** The page contains no GPS, camera model or original file names.
- **One file.** About 1.5 MB of HTML. Double-click to open; works in mobile browsers.

## Install

```bash
npx skills add dososo/trip-photo-skills --skill trip-photo-lantern
```

Then ask your agent: "Use trip-photo-lantern to turn ~/Pictures/trip into a lantern."

Manual install: copy `skills/trip-photo-lantern` to `~/.claude/skills/` (Claude Code) or `~/.codex/skills/` (Codex).

Requirements: Python 3.10+ and `python3 -m pip install pillow numpy torch transformers`. The segmentation model `openmmlab/upernet-convnext-tiny` (MIT, about 240 MB) downloads on first run.

Without an agent:

```bash
cd skills/trip-photo-lantern
python3 scripts/lantern.py all --photos ~/Pictures/trip --out ~/Desktop/lantern
```

## Example

[`skills/trip-photo-lantern/examples/xiangxi-2024/lantern.html`](skills/trip-photo-lantern/examples/xiangxi-2024/lantern.html): 8 photos from Xiangxi, Xiushan and Dongjiang Lake, October 2024. Download and open it.

## Privacy note

The scripts never upload photos. If you ask an AI agent to look at an image, that image is sent to the agent's model provider as part of the conversation, so the skill hands you file paths instead of opening images itself.

## License

Code: [Apache-2.0](LICENSE). Example photos and images derived from them: © dososo, not covered by the code license; see [ASSET-LICENSE.md](ASSET-LICENSE.md). Third-party components: [NOTICE](NOTICE).
