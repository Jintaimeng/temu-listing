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


def mapping_rows(lines: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        first = re.match(r"^\s*-\s*([\w\u4e00-\u9fff]+):\s*(.*?)\s*$", line)
        if first:
            current = {first.group(1): first.group(2).strip().strip("\"'")}
            rows.append(current)
            continue
        child = re.match(r"^\s+([\w\u4e00-\u9fff]+):\s*(.*?)\s*$", line)
        if child and current is not None:
            current[child.group(1)] = child.group(2).strip().strip("\"'")
    return rows


def brand_rows(lines: list[str]) -> list[tuple[str, dict[str, str]]]:
    """Read brands[].phone_models rows without requiring PyYAML."""
    result: list[tuple[str, dict[str, str]]] = []
    current_brand = ""
    current: dict[str, str] | None = None
    in_brands = False
    for line in lines:
        stripped = line.strip()
        if stripped == "brands:":
            in_brands = True
            continue
        if not in_brands:
            continue
        if stripped and not stripped.startswith("#"):
            indent = len(line) - len(line.lstrip())
            if indent == 0:
                break
            brand = re.match(r"^\s{2}-\s*brand:\s*(.*?)\s*$", line)
            if brand:
                current_brand = brand.group(1).strip().strip("\"'")
                current = None
                continue
            model = re.match(r"^\s{6}-\s*phone_model:\s*(.*?)\s*$", line)
            if model:
                current = {"phone_model": model.group(1).strip().strip("\"'")}
                result.append((current_brand, current))
                continue
            child = re.match(r"^\s{8}([\w\u4e00-\u9fff]+):\s*(.*?)\s*$", line)
            if child and current is not None:
                current[child.group(1)] = child.group(2).strip().strip("\"'")
    return result


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
    specs = section(lines, "specs", 0)
    static_specs = section(specs, "static", 2)
    default_phone_model = value(static_specs, "手机型号")
    configured_models = brand_rows(lines)
    count = int(value(carousel, "count") or 0)
    names = files(carousel)
    errors = []
    warnings = []

    if not configured_models:
        errors.append("brands.phone_models 缺失或为空")
    seen_models: set[str] = set()
    seen_brands: set[str] = set()
    for brand, row in configured_models:
        if not brand:
            errors.append("brands 存在未填写 brand 的规则")
        seen_brands.add(brand.casefold())
        phone_model = row.get("phone_model", "").strip()
        model_key = phone_model.casefold()
        if not phone_model:
            errors.append("phone_model_prices 存在未填写 phone_model 的规则")
            continue
        if model_key in seen_models:
            errors.append(f"brands 手机型号重复: {phone_model}")
        seen_models.add(model_key)
        for key in ("sku_code", "declaration_price", "suggested_retail_price", "suggested_retail_price_currency"):
            if not row.get(key, "").strip():
                errors.append(f"brands[{brand}].phone_models[{phone_model}].{key} 缺失或为空")
        currency = row.get("suggested_retail_price_currency", "").strip()
        if currency and not re.fullmatch(r"[A-Z]{3}", currency):
            errors.append(f"brands[{brand}].phone_models[{phone_model}].suggested_retail_price_currency 必须是三位大写币种代码")
    if default_phone_model and default_phone_model.casefold() not in seen_models:
        errors.append(f"defaults.specs.static.手机型号 未配置价格规则: {default_phone_model}")

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
