#!/usr/bin/env python3
"""Validate configured carousel and package image paths without uploading."""
from __future__ import annotations

import argparse
import pathlib
import re

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def section(lines: list[str], key: str, parent_indent: int) -> list[str]:
    key_indent = parent_indent + 2
    start = next((i + 1 for i, line in enumerate(lines)
                  if line.strip() == f"{key}:" and len(line) - len(line.lstrip()) == key_indent), None)
    if start is None:
        return []
    result = []
    for line in lines[start:]:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped and not stripped.startswith("#") and indent <= key_indent:
            break
        result.append(line)
    return result


def value(lines: list[str], key: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(key)}:\s*(.*?)\s*(?:#.*)?$")
    for line in lines:
        match = pattern.match(line)
        if match:
            return match.group(1).strip().strip("\"'")
    return None


def files(lines: list[str]) -> list[str]:
    for i, line in enumerate(lines):
        if re.match(r"^\s*files:\s*$", line):
            base = len(line) - len(line.lstrip())
            result = []
            for child in lines[i + 1:]:
                stripped = child.strip()
                indent = len(child) - len(child.lstrip())
                if stripped and not stripped.startswith("#") and indent <= base:
                    break
                match = re.match(r"^\s*-\s*(.*?)\s*$", child)
                if match:
                    result.append(match.group(1).strip("\"'"))
            return result
        inline = re.match(r"^\s*files:\s*\[(.*?)\]\s*$", line)
        if inline:
            return [x.strip().strip("\"'") for x in inline.group(1).split(",") if x.strip()]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", nargs="?", default=r"D:\\project\\temu-listing-ops\\config\\listing.yaml")
    parser.add_argument("--project-root", default=None)
    ns = parser.parse_args()
    config = pathlib.Path(ns.config).resolve()
    lines = config.read_text(encoding="utf-8").splitlines()
    root = pathlib.Path(ns.project_root).resolve() if ns.project_root else config.parent.parent
    images = section(lines, "images", 0)
    carousel = section(images, "carousel", 2)
    pack_dir = value(images, "pack_dir")
    package_outer = value(images, "package_outer")
    count = int(value(carousel, "count") or 0)
    names = files(carousel)
    errors = []
    warnings = []

    pack = (root / pack_dir).resolve() if pack_dir else None
    if pack_dir:
        if not pack or not pack.is_dir():
            errors.append(f"图片包目录不存在: {pack}")
        elif not names:
            names = sorted(p.name for p in pack.iterdir() if p.is_file() and p.suffix.lower() in EXTS)[:count]
    elif names:
        errors.append("defaults.images.pack_dir 缺失，但 carousel.files 已配置")
    if count and len(names) < count:
        warnings.append("轮播图数量少于 count")

    resolved_names = []
    for name in names:
        path = (pack / name).resolve() if pack else pathlib.Path(name).resolve()
        resolved_names.append(path)
        if not path.is_file() or path.suffix.lower() not in EXTS:
            errors.append(f"轮播图不可读或不是图片: {path}")

    resolved_outer = None
    if package_outer:
        outer = pathlib.Path(package_outer)
        resolved_outer = (outer if outer.is_absolute() else root / outer).resolve()
        if not resolved_outer.is_file() or resolved_outer.suffix.lower() not in EXTS:
            errors.append(f"外包装图片不可读或不是图片: {resolved_outer}")

    if errors:
        print("ERROR")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("OK")
    for warning in warnings:
        print(f"WARNING: {warning}")
    print(f"config={config}")
    print(f"project_root={root}")
    print("carousel_files=")
    for path in resolved_names:
        print(f"- {path}")
    if resolved_outer:
        print(f"package_outer={resolved_outer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
