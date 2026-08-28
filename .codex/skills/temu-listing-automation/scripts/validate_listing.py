#!/usr/bin/env python3
"""Validate configured carousel and package image paths without uploading."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
PACK_IMAGE_RE = re.compile(r"^([1-5])(?:_(.*))?$")


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


def top_section(lines: list[str], key: str) -> list[str]:
    """Read a mapping whose key is at YAML column zero."""
    start = next(
        (i + 1 for i, line in enumerate(lines) if line.strip() == f"{key}:" and not line.startswith((" ", "\t"))),
        None,
    )
    if start is None:
        return []
    result: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not line.startswith((" ", "\t")):
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
    current_group_id = ""
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
                current_group_id = current_brand
                current = None
                continue
            group_id = re.match(r"^\s{4}group_id:\s*(.*?)\s*$", line)
            if group_id:
                current_group_id = group_id.group(1).strip().strip("\"'")
                continue
            model = re.match(r"^\s{6}-\s*phone_model:\s*(.*?)\s*$", line)
            if model:
                current = {
                    "phone_model": model.group(1).strip().strip("\"'"),
                    "_brand": current_brand,
                }
                result.append((current_group_id, current))
                continue
            child = re.match(r"^\s{8}([\w\u4e00-\u9fff]+):\s*(.*?)\s*$", line)
            if child and current is not None:
                current[child.group(1)] = child.group(2).strip().strip("\"'")
    return result


def brand_names(lines: list[str]) -> dict[str, str]:
    """Map each internal group_id to the display brand name."""
    result: dict[str, str] = {}
    current_brand = ""
    current_group_id = ""
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
                current_group_id = current_brand
                continue
            group_id = re.match(r"^\s{4}group_id:\s*(.*?)\s*$", line)
            if group_id:
                current_group_id = group_id.group(1).strip().strip("\"'")
                result[current_group_id.casefold()] = current_brand
    return result


def brand_titles(lines: list[str]) -> dict[str, str]:
    """Read optional brands[].title values keyed by case-insensitive group_id."""
    result: dict[str, str] = {}
    current_brand = ""
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
                continue
            group_id = re.match(r"^\s{4}group_id:\s*(.*?)\s*$", line)
            if group_id:
                current_brand = group_id.group(1).strip().strip("\"'")
                continue
            title = re.match(r"^\s{4}title:\s*(.*?)\s*$", line)
            if title and current_brand:
                result[current_brand.casefold()] = title.group(1).strip().strip("\"'")
    return result


def brand_colors(lines: list[str]) -> dict[str, list[str]]:
    """Read brands[].colors; color is configured once per listing group, not per model."""
    result: dict[str, list[str]] = {}
    current_brand = ""
    in_brands = False
    in_colors = False
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped == "brands:":
            in_brands = True
            continue
        if not in_brands:
            continue
        if stripped and indent == 0:
            break
        brand = re.match(r"^\s{2}-\s*brand:\s*(.*?)\s*$", line)
        if brand:
            current_brand = brand.group(1).strip().strip("\"'")
            result[current_brand.casefold()] = []
            in_colors = False
            continue
        group_id = re.match(r"^\s{4}group_id:\s*(.*?)\s*$", line)
        if group_id:
            current_brand = group_id.group(1).strip().strip("\"'")
            result.setdefault(current_brand.casefold(), [])
            in_colors = False
            continue
        if current_brand and re.match(r"^\s{4}colors:\s*$", line):
            in_colors = True
            continue
        if in_colors:
            item = re.match(r"^\s{6}-\s*(.*?)\s*$", line)
            if item:
                value = item.group(1).strip().strip("\"'")
                if value:
                    result[current_brand.casefold()].append(value)
                continue
            if stripped and indent <= 4:
                in_colors = False
    return result


def material_codes(lines: list[str]) -> dict[str, str]:
    """Read the top-level three-digit material-code dictionary."""
    result: dict[str, str] = {}
    in_codes = False
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped == "material_codes:":
            in_codes = True
            continue
        if in_codes and stripped and indent == 0:
            break
        if in_codes:
            match = re.match(r'^\s{2}["\']?(\d{3})["\']?:\s*["\']?(.*?)["\']?\s*$', line)
            if match:
                result[match.group(1)] = match.group(2).strip()
    return result


def mapping_values(lines: list[str], section_name: str) -> dict[str, str]:
    """Read a simple top-level mapping such as color_codes or craft_codes."""
    result: dict[str, str] = {}
    in_section = False
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped == f"{section_name}:":
            in_section = True
            continue
        if in_section and stripped and indent == 0:
            break
        if not in_section:
            continue
        match = re.match(r'^\s{2}["\']?(.*?)["\']?:\s*["\']?(.*?)["\']?\s*$', line)
        if match:
            result[match.group(1).strip()] = match.group(2).strip()
    return result


def top_list_values(lines: list[str], section_name: str) -> list[str]:
    """Read a top-level YAML list such as craft_codes."""
    result: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped == f"{section_name}:":
            in_section = True
            continue
        if in_section and stripped and indent == 0:
            break
        if in_section:
            item = re.match(r"^\s{2}-\s*(.*?)\s*$", line)
            if item:
                value = item.group(1).strip().strip("\"'")
                if value:
                    result.append(value)
    return result


def pricing_config(lines: list[str]) -> dict[str, object]:
    """Read quote rows and pricing formulas without requiring PyYAML."""
    block = top_section(lines, "pricing")
    result: dict[str, object] = {
        "difference": float(value(block, "difference") or 0),
        "suggested_retail_multiplier": float(value(block, "suggested_retail_multiplier") or 8),
        "declaration_price_currency": value(block, "declaration_price_currency") or "store_currency",
        "suggested_retail_price_currency": value(block, "suggested_retail_price_currency") or "USD",
        "source": {
            "url": value(section(block, "source", 0), "url") or "",
            "sheet_tab": value(section(block, "source", 0), "sheet_tab") or "",
            "synced_at": value(section(block, "source", 0), "synced_at") or "",
            "sync_frequency": value(section(block, "source", 0), "sync_frequency") or "daily",
        },
        "quote_rows": [],
    }
    table: dict[str, dict[str, float]] = {}
    rows: list[dict[str, object]] = []
    current_material = ""
    current_craft = ""
    for line in block:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped == "quote_rows:":
            current_material = ""
            current_craft = ""
            continue
        material = re.match(r'^\s{4}-\s*material_code:\s*["\']?(\d{3})["\']?\s*$', line)
        if not material:
            material = re.match(r'^\s{6}material_code:\s*["\']?(\d{3})["\']?\s*$', line)
        if material:
            current_material = material.group(1)
            current_craft = ""
            rows.append({"material_code": current_material})
            continue
        craft = re.match(r'^\s{6}craft_code:\s*["\']?([A-Z]{2})["\']?\s*$', line)
        if craft and rows:
            current_craft = craft.group(1)
            rows[-1]["craft_code"] = current_craft
            continue
        label = re.match(r'^\s{6}label:\s*["\']?(.*?)["\']?\s*$', line)
        if label and rows:
            rows[-1]["label"] = label.group(1).strip().strip("\"'")
            continue
        price = re.match(r'^\s{6}price:\s*([0-9]+(?:\.[0-9]+)?)\s*$', line)
        if price and rows and current_craft:
            numeric = float(price.group(1))
            rows[-1]["price"] = numeric
            table.setdefault(current_material, {}).setdefault(current_craft, numeric)
    result["quote_rows"] = rows
    result["quote_lookup"] = table
    return result


def brand_materials(lines: list[str]) -> dict[str, tuple[str, str]]:
    """Read brands[].material_code/material metadata keyed by group_id."""
    result: dict[str, tuple[str, str]] = {}
    current = ""
    in_brands = False
    code = ""
    name = ""
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped == "brands:":
            in_brands = True
            continue
        if not in_brands:
            continue
        if stripped and indent == 0:
            break
        brand = re.match(r'^\s{2}-\s*brand:\s*(.*?)\s*$', line)
        if brand:
            if current:
                result[current.casefold()] = (code, name)
            current = brand.group(1).strip().strip("\"'")
            code = ""
            name = ""
            continue
        group_id = re.match(r'^\s{4}group_id:\s*(.*?)\s*$', line)
        if group_id:
            if current:
                result[current.casefold()] = (code, name)
            current = group_id.group(1).strip().strip("\"'")
            code = ""
            name = ""
            continue
        if current:
            match = re.match(r'^\s{4}material_code:\s*(.*?)\s*$', line)
            if match:
                code = match.group(1).strip().strip("\"'").zfill(3)
            match = re.match(r'^\s{4}material:\s*(.*?)\s*$', line)
            if match:
                name = match.group(1).strip().strip("\"'")
    if current:
        result[current.casefold()] = (code, name)
    return result


def ordered_brand_payloads(
    rows: list[tuple[str, dict[str, str]]],
    title_template: str,
    title_desc: str,
    explicit_brand_names: dict[str, str] | None = None,
    explicit_titles: dict[str, str] | None = None,
    explicit_colors: dict[str, list[str]] | None = None,
    explicit_crafts: list[str] | None = None,
    explicit_materials: dict[str, tuple[str, str]] | None = None,
    dynamic_title: bool = False,
) -> list[dict[str, object]]:
    """Group validated model rows into immutable per-brand execution payloads."""
    payloads: list[dict[str, object]] = []
    grouped: dict[str, tuple[str, list[dict[str, str]]]] = {}
    for group_id, model in rows:
        key = group_id.casefold()
        brand = (explicit_brand_names or {}).get(key, model.get("_brand", group_id))
        if key not in grouped:
            grouped[key] = (brand, [])
        grouped[key][1].append({k: v for k, v in model.items() if not k.startswith("_")})
    crafts = list(explicit_crafts or []) or [""]
    for key, (brand, models) in grouped.items():
        colors = (explicit_colors or {}).get(key, [])
        code, material = (explicit_materials or {}).get(key, ("", ""))
        for craft in crafts:
            # Each brand/color pair is a separate product task.  Keeping one
            # color in the payload is important: downstream page filling can
            # select a single color and Temu will then materialize only the
            # model x color SKU combinations for that product.
            color_variants = list(colors) or [""]
            for color in color_variants:
                title = (explicit_titles or {}).get(key, "")
                if not title and not dynamic_title:
                    title = title_template.replace("{brand}", brand).replace("{品牌}", brand)
                    title = title.replace("{craft_codes}", craft).replace("{craft_code}", craft)
                    title = title.replace("{desc}", title_desc).replace("{描述}", title_desc)
                payload: dict[str, object] = {
                    "brand": brand,
                    "title": title,
                    "phone_models": [dict(row) for row in models],
                }
                if color:
                    payload["color"] = color
                    payload["colors"] = [color]
                # Temu exposes two specification dimensions (model and color),
                # while its SKU table materializes their combinations. Since
                # each task now owns one color, color_count is always 1 for a
                # colored product and the task count is brand x color.
                color_count = 1 if color else 0
                payload["spec_counts"] = {
                    "phone_model_count": len(models),
                    "color_count": color_count,
                    "spec_value_count": len(models) + color_count,
                    "sku_combination_count": len(models) * color_count,
                }
                if craft:
                    payload["craft_code"] = craft
                    payload["craft_codes"] = [craft]
                if dynamic_title:
                    payload["title_template"] = title_template
                if code:
                    payload["material_code"] = code
                if material:
                    payload["material"] = material
                payloads.append(payload)
    return payloads


def material_cache_key(paths: list[pathlib.Path]) -> str:
    """Fingerprint the ordered carousel content so one task can safely reuse it."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.casefold().encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def execution_plan(
    *,
    config: pathlib.Path,
    root: pathlib.Path,
    carousel_files: list[pathlib.Path],
    package_outer: pathlib.Path | None,
    brands: list[dict[str, object]],
    title_template: str,
    title_desc: str,
    material_code: str = "",
    material: str = "",
    main_material: str = "PC",
    sku_code_rule: dict[str, str] | None = None,
    pricing: dict[str, object] | None = None,
) -> dict[str, object]:
    carousel = [str(path) for path in carousel_files]
    return {
        "schema_version": 2,
        "config": str(config),
        "project_root": str(root),
        "material_cache_key": material_cache_key(carousel_files),
        "material_search_terms": [path.name for path in carousel_files],
        "carousel_files": carousel,
        "detail_files": carousel,
        "package_outer": str(package_outer) if package_outer else None,
        "title_template": title_template,
        "title_desc": title_desc,
        "material_code": material_code,
        "material": material,
        "main_material": main_material,
        "sku_code_rule": sku_code_rule or {},
        "pricing": pricing or {},
        "brands": brands,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", nargs="?", default=r"D:\\project\\temu-listing-ops\\config\\listing.yaml")
    parser.add_argument("--project-root", default=None)
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the validated immutable image-pack execution plan as JSON",
    )
    parser.add_argument(
        "--plan-out",
        default=None,
        help="write the validated execution plan JSON to this path",
    )
    ns = parser.parse_args()
    config = pathlib.Path(ns.config).resolve()
    lines = config.read_text(encoding="utf-8").splitlines()
    root = pathlib.Path(ns.project_root).resolve() if ns.project_root else config.parent.parent
    images = section(lines, "images", 0)
    carousel = section(images, "carousel", 2)
    pack_dir = value(images, "pack_dir")
    if pack_dir and pack_dir.casefold() in {"null", "none", "~"}:
        pack_dir = None
    task_injected = (value(images, "task_injected") or "").casefold() in {"true", "yes", "1"}
    package_outer = value(images, "package_outer")
    specs = section(lines, "specs", 0)
    static_specs = section(specs, "static", 2)
    default_phone_model = value(static_specs, "手机型号")
    configured_models = brand_rows(lines)
    configured_brand_names = brand_names(lines)
    configured_titles = brand_titles(lines)
    configured_brand_colors = brand_colors(lines)
    configured_codes = material_codes(lines)
    configured_crafts = top_list_values(lines, "craft_codes")
    configured_craft_names = mapping_values(lines, "craft_code_names")
    configured_colors = mapping_values(lines, "color_codes")
    configured_attribute_names = mapping_values(lines, "attribute_names")
    pricing = pricing_config(lines)
    attributes = section(lines, "attributes", 0)
    main_material = value(attributes, "main_material") or ""
    sku_rule = top_section(lines, "sku_code_rule")
    sku_enabled = (value(sku_rule, "enabled") or "").casefold() in {"true", "yes", "1"}
    material_rule = top_section(lines, "material_image_rule")
    material_enabled = (value(material_rule, "enabled") or "").casefold() in {"true", "yes", "1"}
    material_carousel_count = int(value(material_rule, "carousel_count") or 5)
    title = section(lines, "title", 0)
    title_desc_lines = section(title, "desc", 2)
    title_template = value(title, "template") or "适用于{brand}的手机壳{desc}保护壳"
    title_desc = value(title_desc_lines, "static") or ""
    title_desc_source = (value(title_desc_lines, "source") or "static").casefold()
    dynamic_title = "{material}" in title_template or "{craft_codes}" in title_template or title_desc_source == "ai"
    count = int(value(carousel, "count") or 0)
    names = files(carousel)
    errors = []
    warnings = []

    if not configured_models:
        errors.append("brands.phone_models 缺失或为空")
    seen_variants: set[tuple[str, str]] = set()
    seen_groups: set[str] = set()
    for group_id, row in configured_models:
        brand = configured_brand_names.get(group_id.casefold(), row.get("_brand", group_id))
        if not brand:
            errors.append("brands 存在未填写 brand 的规则")
        seen_groups.add(group_id.casefold())
        phone_model = row.get("phone_model", "").strip()
        variant_key = (group_id.casefold(), phone_model.casefold())
        if not phone_model:
            errors.append("brands.phone_models 存在未填写 phone_model 的规则")
            continue
        if variant_key in seen_variants:
            errors.append(f"同一品牌的手机型号重复（颜色应配置在品牌级 colors）: {brand} / {phone_model}")
        seen_variants.add(variant_key)
        if "color" in row:
            errors.append(f"brands[{brand}].phone_models[{phone_model}] 不应配置 color；请移到 brands.colors")
        for legacy_key in ("declaration_price", "suggested_retail_price", "suggested_retail_price_currency"):
            if legacy_key in row:
                errors.append(f"brands[{brand}].phone_models[{phone_model}] 不应配置旧价格字段 {legacy_key}；价格由 pricing.quote_rows + difference 计算")
        if "craft_code" in row:
            errors.append(f"brands[{brand}].phone_models[{phone_model}] 不应配置 craft_code；请移到 brands.craft_codes")
    configured_craft_keys = {key.upper() for key in configured_craft_names}
    if not configured_crafts:
        errors.append("顶层 craft_codes 缺失或为空")
    for craft in configured_crafts:
        if craft.upper() not in configured_craft_keys:
            errors.append(f"craft_codes 未在 craft_code_names 中配置: {craft}")
    configured_color_keys = {key.casefold() for key in configured_colors}
    for group_key in seen_groups:
        brand = configured_brand_names.get(group_key, group_key)
        colors = configured_brand_colors.get(group_key, [])
        if not colors:
            errors.append(f"brands[{brand}] 缺少非空 colors；颜色必须配置在品牌级")
        for color in colors:
            if color.casefold() not in configured_color_keys:
                errors.append(f"brands[{brand}].colors 未在 color_codes 中配置: {color}")
    if sku_enabled and not configured_crafts:
        errors.append("sku_code_rule 已启用，但顶层 craft_codes 缺失")
    if sku_enabled and not configured_craft_names:
        errors.append("sku_code_rule 已启用，但 craft_code_names 缺失")
    if sku_enabled and not configured_colors:
        errors.append("sku_code_rule 已启用，但 color_codes 缺失")
    for key in ("color", "craft", "material"):
        if key not in {name.casefold() for name in configured_attribute_names}:
            errors.append(f"attribute_names 缺少类别名: {key}")
    if main_material.casefold() != "pc":
        errors.append("defaults.attributes.main_material 必须固定配置为 PC")
    if not pricing.get("quote_lookup"):
        errors.append("pricing.quote_rows 缺失或为空")
    try:
        if float(pricing.get("suggested_retail_multiplier", 0)) <= 0:
            errors.append("pricing.suggested_retail_multiplier 必须为正数")
    except (TypeError, ValueError):
        errors.append("pricing.difference 和 suggested_retail_multiplier 必须为数字")
    if dynamic_title and task_injected:
        warnings.append("商品标题需在首图材质解析和 AI 图片特征描述完成后生成")
    if default_phone_model and not any(key[1] == default_phone_model.casefold() for key in seen_variants):
        errors.append(f"defaults.specs.static.手机型号 未配置价格规则: {default_phone_model}")

    pack = (root / pack_dir).resolve() if pack_dir else None
    if pack_dir:
        if not pack or not pack.is_dir():
            errors.append(f"图片包目录不存在: {pack}")
        elif not names:
            names = sorted(p.name for p in pack.iterdir() if p.is_file() and p.suffix.lower() in EXTS)[:count]
    elif names:
        errors.append("defaults.images.pack_dir 缺失，但 carousel.files 已配置")
    elif task_injected:
        warnings.append("轮播图由图片包任务注入；开始浏览器操作前必须重新校验实际本地文件")
    else:
        errors.append("defaults.images.pack_dir 缺失；若由任务提供图片，请设置 images.task_injected: true")
    if count and len(names) < count and not task_injected:
        warnings.append("轮播图数量少于 count")
    if names:
        numbered: dict[int, str] = {}
        for name in names:
            match = PACK_IMAGE_RE.match(pathlib.Path(name).stem)
            if not match:
                errors.append(f"图片文件名必须为 1_<图片编码>、2、3、4、5: {name}")
                continue
            position = int(match.group(1))
            suffix = match.group(2)
            if position == 1 and not suffix:
                errors.append(f"首图文件名必须为 1_<图片编码>: {name}")
                continue
            if position > 1 and suffix is not None:
                errors.append(f"第 {position} 张图片文件名只能是数字 {position}: {name}")
                continue
            if position in numbered:
                errors.append(f"图片包存在重复序号 {position}: {numbered[position]}, {name}")
            else:
                numbered[position] = name
        missing_positions = [str(position) for position in range(1, 6) if position not in numbered]
        if missing_positions:
            errors.append("图片包缺少序号: " + ", ".join(missing_positions))
        if not missing_positions and len(numbered) == 5:
            names = [numbered[position] for position in range(1, 6)]
    first_material_code = ""
    if material_enabled and names:
        first_stem = pathlib.Path(names[0]).stem
        match = re.search(r"(\d{3})$", first_stem)
        if not match:
            errors.append(f"图片包首图文件名缺少末尾三位材质编号: {names[0]}")
        else:
            first_material_code = match.group(1)
            if first_material_code not in configured_codes:
                errors.append(f"图片包首图材质编号未配置: {first_material_code}")
            if len(names) < material_carousel_count:
                errors.append(f"图片包至少需要 {material_carousel_count} 张图，实际 {len(names)} 张")
    elif material_enabled and task_injected:
        warnings.append("材质编号需在任务注入图片后校验首图文件名，并作为生成 SKU 的前三位")
    if first_material_code and configured_crafts:
        quote_lookup = pricing.get("quote_lookup", {})
        material_quotes = quote_lookup.get(first_material_code, {}) if isinstance(quote_lookup, dict) else {}
        for craft in configured_crafts:
            if not isinstance(material_quotes, dict) or craft.upper() not in {str(key).upper() for key in material_quotes}:
                errors.append(f"pricing.quote_rows 缺少材质 {first_material_code} / 工艺 {craft} 的报价")

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
        if ns.json:
            print(json.dumps({"ok": False, "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
        else:
            print("ERROR")
            print("\n".join(f"- {error}" for error in errors))
        return 1
    plan = execution_plan(
        config=config,
        root=root,
        carousel_files=resolved_names,
        package_outer=resolved_outer,
        brands=ordered_brand_payloads(
            configured_models,
            title_template,
            title_desc,
            explicit_brand_names=configured_brand_names,
            explicit_titles=configured_titles,
            explicit_colors=configured_brand_colors,
            explicit_crafts=configured_crafts,
            dynamic_title=dynamic_title,
        ),
        title_template=title_template,
        title_desc=title_desc,
        material_code=first_material_code,
        material=(configured_codes.get(first_material_code) if first_material_code else ""),
        main_material=main_material,
        pricing=pricing,
        sku_code_rule={
            key: value(sku_rule, key) or ""
            for key in (
                "enabled",
                "material_source",
                "image_id_source",
                "craft_source",
                "color_source",
                "color_code_system",
                "color_code_suffix",
                "pattern",
            )
        },
    )
    if ns.plan_out:
        destination = pathlib.Path(ns.plan_out).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps({"ok": True, "warnings": warnings, "plan": plan}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"plan={destination}")
    if ns.json:
        print(json.dumps({"ok": True, "warnings": warnings, "plan": plan}, ensure_ascii=False, indent=2))
        return 0
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
    print(f"material_cache_key={plan['material_cache_key']}")
    print(f"brands={len(plan['brands'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
