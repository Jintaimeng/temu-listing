from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


MATERIAL_XLSX = Path(r"D:\project\材质编码.xlsx")
SOURCE_CONFIG = Path(r"D:\project\temu-listing-ops\config\listing.yaml.before-material-20260826.bak")
OUTPUT_CONFIG = Path(r"D:\project\temu-listing\artifacts\listing.yaml")
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_material_codes(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as workbook:
        shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in item.iterfind(".//m:t", NS)) for item in shared_root.findall("m:si", NS)]
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        result: dict[str, str] = {}
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
            code_match = re.search(r"(\d{3})$", values.get("A", ""))
            name = values.get("B", "")
            if code_match and name:
                result[code_match.group(1)] = name
        return result


def inject(text: str, codes: dict[str, str]) -> str:
    lines = text.splitlines()
    material_block = [
        "material_codes:",
        *[f'  "{code}": "{name}"' for code, name in sorted(codes.items())],
        "",
        "# 图片包首图材质编码规则：首图文件名末尾三位数字必须与 SKU 前三位一致。",
        "material_image_rule:",
        "  enabled: true",
        "  code_length: 3",
        "  sku_prefix_length: 3",
        "  filename_suffix_pattern: '(\\d{3})$'",
        "  carousel_count: 5",
        "",
    ]
    marker = next(i for i, line in enumerate(lines) if line.startswith("# 上架表单配置"))
    lines[marker:marker] = material_block

    in_brands = False
    output: list[str] = []
    for index, line in enumerate(lines):
        if line == "brands:":
            in_brands = True
        elif line == "unresolved_variants:":
            in_brands = False
        brand = re.match(r'^  - brand: "(.*)"$', line)
        if brand and in_brands:
            output.append(line)
            code = ""
            for lookahead in lines[index + 1 :]:
                if re.match(r"^  - brand:", lookahead) or lookahead == "unresolved_variants:":
                    break
                sku = re.search(r'^\s{8}sku_code: "(\d{3})', lookahead)
                if sku:
                    code = sku.group(1)
                    break
            if not code or code not in codes:
                raise ValueError(f"品牌缺少有效 SKU 材质编号: {brand.group(1)}")
            output.append(f'    material_code: "{code}"')
            output.append(f'    material: "{codes[code]}"')
            continue
        output.append(line)
    text = "\n".join(output) + "\n"
    text = text.replace(
        "    主要材质: PC\n",
        "    # 主要材质由 brands[].material 与图片包首图材质编号动态填入\n",
    )
    return text


def main() -> None:
    codes = read_material_codes(MATERIAL_XLSX)
    if len(codes) != 100:
        raise SystemExit(f"材质编码表应有 100 条，实际 {len(codes)} 条")
    source = SOURCE_CONFIG.read_text(encoding="utf-8")
    output = inject(source, codes)
    OUTPUT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_CONFIG.write_text(output, encoding="utf-8")
    print(f"codes={len(codes)}")
    print(f"brands={len(re.findall(r'^  - brand:', output, flags=re.M))}")
    print(f"output={OUTPUT_CONFIG}")


if __name__ == "__main__":
    main()
