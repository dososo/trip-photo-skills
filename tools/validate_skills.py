#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""检查 skills/ 下每个 skill 是否符合 Agent Skills 规范和本仓库的硬规则。

  python3 tools/validate_skills.py        全部通过返回 0，否则列出问题并返回 1
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {"name", "description", "license", "metadata", "compatibility", "allowed-tools"}


def frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    out, key = {}, None
    for line in m.group(1).splitlines():
        if re.match(r"^[A-Za-z][\w-]*:", line):
            key, _, val = line.partition(":")
            out[key.strip()] = val.strip()
        elif key and line.startswith("  "):
            out[key] += "\n" + line
    return out


def check(skill: Path) -> list[str]:
    errs = []
    md = skill / "SKILL.md"
    if not md.exists():
        return [f"{skill.name}: 缺 SKILL.md"]
    text = md.read_text(encoding="utf-8")
    fm = frontmatter(text)
    if not fm:
        return [f"{skill.name}: SKILL.md 没有 frontmatter"]
    for k in fm:
        if k not in ALLOWED:
            errs.append(f"{skill.name}: frontmatter 有非标准字段 {k}")
    name = fm.get("name", "")
    if name != skill.name:
        errs.append(f"{skill.name}: name「{name}」必须等于目录名")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name or "-"):
        errs.append(f"{skill.name}: name 只能用小写字母、数字和连字符")
    desc = fm.get("description", "")
    if not desc:
        errs.append(f"{skill.name}: 缺 description")
    elif len(desc) > 1024:
        errs.append(f"{skill.name}: description {len(desc)} 字符，超过 1024")
    if len(text.splitlines()) > 500:
        errs.append(f"{skill.name}: SKILL.md 超过 500 行")
    for p in skill.rglob("*"):
        if p.is_symlink():
            errs.append(f"{skill.name}: 不允许符号链接 {p.relative_to(skill)}")
    for ref in re.findall(r"`((?:scripts|references|assets|examples)/[^`\s]+)`", text):
        if not (skill / ref).exists():
            errs.append(f"{skill.name}: SKILL.md 提到的 {ref} 不存在")
    return errs


def main() -> int:
    skills = sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir())
    errs = [e for s in skills for e in check(s)]
    for s in skills:
        fm = frontmatter((s / "SKILL.md").read_text(encoding="utf-8")) if (s / "SKILL.md").exists() else {}
        print(f"{'✓' if not any(e.startswith(s.name + ':') for e in errs) else '✗'} {s.name}（description {len(fm.get('description', ''))} 字符）")
    for e in errs:
        print("  ✗", e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
