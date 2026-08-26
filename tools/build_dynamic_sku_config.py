from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


SOURCE_CONFIG = Path(r"D:\project\temu-listing-ops\config\listing.yaml.before-material-20260826.bak")
MATERIAL_XLSX = Path(r"D:\project\材质编码.xlsx")
CRAFT_XLSX = Path(r"D:\project\工艺代码.xlsx")
COLOR_XLSX = Path(r"D:\project\颜色编码.xlsx")
OUTPUT_CONFIG = Path(r"D:\project\temu-listing\artifacts\listing.dynamic-sku.yaml")
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_sheet(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as workbook:
        shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        shared = [
            "".join(t.text or "" for t in item.iterfind(".//m:t", NS))
            for item in shared_root.findall("m:si", NS)
        ]
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        rows: list[list[str]] = []
        for row in sheet.findall(".//m:sheetData/m:row", NS):
            values: dict[str, str] = {}
            for cell in row.findall("m:c", NS):
                ref = cell.get("r") or ""
                col = re.sub(r"\d", "", ref)
                value = cell.find("m:v", NS)
                text = value.text if value is not None else ""
                if cell.get("t") == "s" and text:
                    text = shared[int(text)]
                values[col] = text.strip()
            if values:
                cols = sorted(values, key=lambda x: (len(x), x))
                rows.append([values.get(col, "") for col in cols])
        return rows


def read_material_codes(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in read_sheet(path):
        if len(row) < 2:
            continue
        match = re.search(r"(\d{3})$", row[0])
        if match and row[1]:
            result[match.group(1)] = row[1]
    return result


def read_craft_codes(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in read_sheet(path):
        if len(row) >= 2 and row[0] and row[1]:
            result[row[1]] = row[0]
    return result


def read_color_codes(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in read_sheet(path):
        if len(row) >= 2 and row[0] and row[1]:
            # The workbook keeps the optional 多鸿 suffix as [0]. The supplied
            # SKU example is the 精致 system, so the generated base code is used.
            result[row[0]] = re.sub(r"\[0\]$", "", row[1])
    return result


def yaml_quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def configured_craft_codes(lines: list[str]) -> list[str]:
    """Read the first process code from the source model SKUs, preserving order."""
    result: list[str] = []
    for line in lines:
        match = re.match(r'^\s{8}sku_code:\s*["\']?\d{3}([A-Z]{2})-', line)
        if match and match.group(1) not in result:
            result.append(match.group(1))
    return result


def inject(text: str, material_codes: dict[str, str], craft_codes: dict[str, str], color_codes: dict[str, str]) -> str:
    lines = text.splitlines()
    selected_crafts = configured_craft_codes(lines)
    if not selected_crafts:
        raise ValueError("未找到可转换的 craft_code")
    insert = [
        "# 材质名称仅用于运行时把图片编号映射到商品属性，不绑定品牌或 SKU。",
        "material_codes:",
        *[f'  "{code}": {yaml_quote(name)}' for code, name in sorted(material_codes.items())],
        "",
        "craft_codes:",
        *[f'  - "{code}"' for code in selected_crafts],
        "",
        "craft_code_names:",
        *[f'  "{code}": {yaml_quote(name)}' for code, name in sorted(craft_codes.items())],
        "",
        "color_codes:",
        *[f'  {yaml_quote(name)}: {yaml_quote(code)}' for name, code in color_codes.items()],
        "",
        "# 可单独调整 Temu 属性类别名称；编码和值映射仍分别由下方字典控制。",
        "attribute_names:",
        "  color: 颜色",
        "  craft: 工艺",
        "  material: 主要材质",
        "",
        "# SKU 货号在图片包到位后生成，不在 listing.yaml 中预填 sku_code。",
        "sku_code_rule:",
        "  enabled: true",
        "  material_source: first_image_filename_suffix",
        "  image_id_source: first_image_filename_stem",
        "  craft_source: craft_codes",
        "  color_source: brands.colors -> color_codes",
        "  color_code_system: 精致",
        "  color_code_suffix: ''",
        "  pattern: '{material_code}{craft_code}-{phone_model}-{color_code}-{image_id}'",
        "",
        "# 首图文件名末尾三位数字是材质编号；首图和后续四张图组成轮播图。",
        "material_image_rule:",
        "  enabled: true",
        "  code_length: 3",
        "  filename_suffix_pattern: '(\\d{3})$'",
        "  carousel_count: 5",
        "",
    ]
    marker = next(i for i, line in enumerate(lines) if line.startswith("# 上架表单配置"))
    lines[marker:marker] = insert

    output: list[str] = []
    in_brands = False
    replaced = 0
    current_brand = ""
    skip_unresolved = False
    for line in lines:
        if line == "unresolved_variants:":
            in_brands = False
            skip_unresolved = True
            continue
        if skip_unresolved:
            continue
        if line == "brands:":
            in_brands = True
        if in_brands and re.match(r'^    title:\s*', line):
            continue
        brand = re.match(r'^  -\s*brand:\s*(.*?)\s*$', line)
        if brand:
            current_brand = brand.group(1).strip().strip('"\'')
        if in_brands and re.match(r'^        craft_code:\s*', line):
            continue
        if in_brands and re.match(r'^        color:\s*', line):
            continue
        match = re.match(r'^(\s*)sku_code: "(\d{3})([A-Z]{2})-', line)
        if match:
            if in_brands:
                # The process code is emitted once at the top-level craft_codes list.
                replaced += 1
                continue
            output.append(line)
        else:
            output.append(line)
    text = "\n".join(output) + "\n"
    text = text.replace(
        "    主要材质: PC\n",
        "    # 主要材质由图片包首图材质编号动态填入\n",
    )
    text = text.replace(
        "# 不同型号允许填写相同 sku_code，也允许分别使用不同 sku_code。",
        "# SKU 货号由图片包首图材质编号、工艺代码、颜色编码和图片编号运行时生成。",
    )
    text = text.replace(
        "# 手机型号、SKU 货号、申报价格、建议零售价及币种统一从 brands[].phone_models 解析。",
        "# 手机型号、申报价格、建议零售价及币种统一从 brands[].phone_models 解析；工艺从顶层 craft_codes、颜色从 brands[].colors 解析；SKU 货号运行时生成。",
    )
    text = text.replace(
        "  # 4 商品名称：适用于{brand}的手机壳{desc}保护壳。desc.source=static 先用下面的词；ai 预留给之后\n"
        "  title:\n"
        "    template: \"{brand}\"\n"
        "    # {brand} 在处理每个 brands 条目时替换，模板本身必须保留该占位符\n"
        "    desc:\n"
        "      source: static\n"
        "      static: \"\"\n",
        "  # 4 商品名称：品牌和材质运行时匹配，描述由 AI 根据图片特征生成。\n"
        "  title:\n"
        "    template: \"适用于{brand}手机壳{material}{craft_codes}{desc}保护壳\"\n"
        "    desc:\n"
        "      source: ai\n"
        "      max_chars: 24\n"
        "      prompt: \"根据商品图片提取外观和设计特征，输出适合手机壳标题的简短中文描述；只输出短语，不要品牌、型号、材质、颜色、编码、标点或解释。\"\n",
    )
    if replaced == 0:
        raise ValueError("未找到可转换的 sku_code")
    return text


def main() -> None:
    materials = read_material_codes(MATERIAL_XLSX)
    crafts = read_craft_codes(CRAFT_XLSX)
    colors = read_color_codes(COLOR_XLSX)
    if len(materials) != 100 or len(crafts) != 11 or len(colors) != 53:
        raise SystemExit(f"编码表数量异常: material={len(materials)}, craft={len(crafts)}, color={len(colors)}")
    source = SOURCE_CONFIG.read_text(encoding="utf-8")
    output = inject(source, materials, crafts, colors)
    OUTPUT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_CONFIG.write_text(output, encoding="utf-8")
    print(f"materials={len(materials)} crafts={len(crafts)} colors={len(colors)}")
    print(f"output={OUTPUT_CONFIG}")


if __name__ == "__main__":
    main()
