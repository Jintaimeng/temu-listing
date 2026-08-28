#!/usr/bin/env python3
"""Synchronize the Tencent Docs phone-case quote sheet into listing.yaml.

The sheet is public but its workbook payload is a zlib-compressed ultrabuf.  This
script intentionally keeps the decoder small and only reads the active quote tab:
text cells are extracted from the workbook text stream and prices from the fixed64
price column.  It updates only the pricing block and removes legacy per-model
price fields; brand/model and Temu attribute configuration are preserved.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
import struct
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SOURCE_URL = "https://docs.qq.com/sheet/DTW14SlpFT3l1eldC?tab=BB08J2"
TAB_ID = "BB08J2"


def _request(url: str, opener: urllib.request.OpenerDirector, referer: str | None = None) -> str:
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with opener.open(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_sheet_rows(url: str) -> list[tuple[int, bytes]]:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    html = _request(url, opener)
    match = re.search(r'href="([^" ]*dop-api/opendoc[^" ]*)"', html)
    if not match:
        raise RuntimeError("腾讯文档页面未找到 opendoc 预加载接口")
    api_url = urllib.parse.unquote(match.group(1)).replace("&amp;", "&")
    if api_url.startswith("//"):
        api_url = "https:" + api_url
    raw = _request(api_url, opener, url)
    raw = re.sub(r"^clientVarsCallback\(", "", raw)
    raw = re.sub(r"\)\s*$", "", raw)
    data = json.loads(raw)
    client_vars = data["clientVars"]["collab_client_vars"]
    global_pad_id = client_vars["globalPadId"]
    max_row = int(client_vars.get("maxRow") or 0)

    def fetch_row(row_index: int) -> tuple[int, bytes]:
        query = urllib.parse.urlencode({
            "padId": global_pad_id,
            "subId": TAB_ID,
            "startrow": row_index,
            "endrow": row_index,
            "outformat": 1,
            "normal": 1,
            "nowb": 1,
        })
        payload = json.loads(_request(f"https://docs.qq.com/dop-api/get/sheet?{query}", opener, url))
        if payload.get("retcode") != 0:
            raise RuntimeError(f"报价单第 {row_index + 1} 行读取失败: {payload}")
        texts = payload.get("data", {}).get("initialAttributedText", {}).get("text", [])
        if not texts or not texts[0].get("related_sheet"):
            return row_index + 1, b""
        packed = base64.b64decode(texts[0]["related_sheet"])
        return row_index + 1, zlib.decompress(packed)

    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(fetch_row, range(max_row + 1)))
    return rows


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while pos < len(buf):
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, pos
        shift += 7
    raise ValueError("truncated varint")


def utf8_strings(buf: bytes) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for start, byte in enumerate(buf):
        if byte != 0x0A:
            continue
        try:
            size, pos = read_varint(buf, start + 1)
        except ValueError:
            continue
        if size <= 0 or size > 2000 or pos + size > len(buf):
            continue
        try:
            text = buf[pos : pos + size].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if text and all(ord(char) >= 32 or char in "\r\n\t" for char in text):
            result.append((start, text))
    return result


def extract_row_price(buf: bytes) -> float | None:
    prices: list[float] = []
    for pos in range(0, len(buf) - 11):
        if buf[pos : pos + 3] != b"\x1a\x09\x09":
            continue
        value = struct.unpack("<d", buf[pos + 3 : pos + 11])[0]
        if 0 < value < 1000:
            prices.append(round(value, 2))
    return prices[0] if prices else None


def material_codes(config_text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    in_section = False
    for line in config_text.splitlines():
        if line.strip() == "material_codes:":
            in_section = True
            continue
        if in_section and line and not line.startswith(" "):
            break
        if in_section:
            match = re.match(r'^\s+"?(\d{3})"?:\s*"(.*)"\s*$', line)
            if match:
                result[match.group(1)] = match.group(2)
    return result


def configured_crafts(config_text: str) -> set[str]:
    result: set[str] = set()
    lines = config_text.splitlines()
    in_section = False
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped == "craft_codes:":
            in_section = True
            continue
        if in_section and stripped and indent == 0:
            break
        if in_section:
            match = re.match(r'^\s{2}-\s*["\']?([A-Za-z]{2})["\']?\s*$', line)
            if match:
                result.add(match.group(1).upper())
    return result


def craft_code(label: str) -> str:
    if "单侧打印" in label:
        return "CM"
    if "支架" in label:
        return "ZH"
    if "腕带" in label and "不打印" in label:
        return "SW"
    if "腕带" in label:
        return "WD"
    if "DIY" in label and "不打印" in label:
        return "DS"
    if "DIY" in label and "光油" in label:
        return "DG"
    if "DIY" in label:
        return "DC"
    if "光油" in label:
        return "GY"
    if "镜面" in label:
        return "JM"
    if "不打印" in label:
        return "SC"
    return "CH"


def normalize_material_name(text: str) -> str:
    return re.sub(r"[\s\[\]［］()（）#]", "", text).casefold()


def quote_rows(config_text: str, sheet_rows: list[tuple[int, bytes]]) -> list[dict[str, object]]:
    codes = material_codes(config_text)
    crafts = configured_crafts(config_text)
    if not crafts:
        raise RuntimeError("listing.yaml 的 craft_codes 缺失或为空")
    normalized_codes = sorted(
        ((code, normalize_material_name(name)) for code, name in codes.items()),
        key=lambda item: len(item[1]),
        reverse=True,
    )
    rows: list[dict[str, object]] = []
    for source_row, row_data in sheet_rows:
        if not row_data:
            continue
        strings = utf8_strings(row_data)
        code = ""
        label = ""
        for _, candidate in strings:
            normalized = normalize_material_name(candidate.strip())
            match = next(((item_code, name) for item_code, name in normalized_codes if normalized.startswith(name)), None)
            if match:
                code = match[0]
                # A leading # is a sheet formatting marker, not part of the label.
                label = candidate.strip().lstrip("#").strip()
                break
        if not code or not label:
            continue
        price = extract_row_price(row_data)
        if price is None:
            continue
        current_craft = craft_code(label)
        if current_craft in crafts:
            rows.append({"material_code": code, "craft_code": current_craft, "label": label, "price": price})
    return rows


def filter_rows_by_configured_crafts(config_text: str, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep only rows whose craft_code is enabled in the current listing config."""
    crafts = configured_crafts(config_text)
    return [row for row in rows if str(row.get("craft_code", "")).upper() in crafts]


def extract_update_date(sheet_rows: list[tuple[int, bytes]]) -> str | None:
    """Find the latest YYYY-MM-DD/中文日期 marker in the 2A-2I header area."""
    dates: list[str] = []
    for source_row, row_data in sheet_rows:
        if source_row > 12 or not row_data:
            continue
        for _, value in utf8_strings(row_data):
            for match in re.findall(r"(?:20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", value):
                y = re.search(r"20\d{2}", value)
                if y:
                    dates.append(f"{y.group(0)}-{int(match[0]):02d}-{int(match[1]):02d}")
    return max(dates) if dates else None


def existing_pricing_values(text: str) -> dict[str, str]:
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "pricing:" and not line.startswith((" ", "\t"))), None)
    if start is None:
        return {}
    values: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith((" ", "\t")):
            break
        match = re.match(r'^\s{2}(difference|suggested_retail_multiplier|declaration_price_currency|suggested_retail_price_currency):\s*(.*?)\s*$', line)
        if match:
            values[match.group(1)] = match.group(2).strip().strip('"\'')
        match = re.match(r'^\s{4}synced_at:\s*["\']?(.*?)["\']?\s*$', line)
        if match:
            values["synced_at"] = match.group(1)
    return values


def yaml_quote_block(rows: list[dict[str, object]], synced_at: str, existing: dict[str, str]) -> str:
    out = [
        "pricing:",
        "  # 报价单价格 + difference = declaration_price；申报价 * suggested_retail_multiplier = suggested_retail_price。",
        "  source:",
        f'    url: "{SOURCE_URL}"',
        f'    sheet_tab: "{TAB_ID}"',
        f'    synced_at: "{synced_at}"',
        "    sync_frequency: daily",
        f'  difference: {existing.get("difference", "0")}',
        f'  suggested_retail_multiplier: {existing.get("suggested_retail_multiplier", "8")}',
        f'  declaration_price_currency: {existing.get("declaration_price_currency", "store_currency")}',
        f'  suggested_retail_price_currency: {existing.get("suggested_retail_price_currency", "USD")}',
        "  # quote_rows 保留报价单中每个实际行，使用 label 区分同材质同工艺的不同版本。",
        "  quote_rows:",
    ]
    for row in rows:
        label = str(row["label"]).replace('"', '\\"').replace("\n", " ")
        out += [
            f'    - material_code: "{row["material_code"]}"',
            f'      craft_code: "{row["craft_code"]}"',
            f'      label: "{label}"',
            f'      price: {float(row["price"]):g}',
        ]
    return "\n".join(out) + "\n"


def replace_top_block(text: str, key: str, block: str) -> str:
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.strip() == f"{key}:" and not line.startswith((" ", "\t"))), None)
    if start is None:
        insert = next((i for i, line in enumerate(lines) if line.strip() == "attribute_names:"), len(lines))
        lines[insert:insert] = [block, "\n"]
        return "".join(lines)
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end].startswith((" ", "\t"))):
        end += 1
    lines[start:end] = [block]
    return "".join(lines)


def remove_legacy_prices(text: str) -> str:
    text = re.sub(r'^\s{8}(?:declaration_price|suggested_retail_price|suggested_retail_price_currency):.*\r?\n', '', text, flags=re.MULTILINE)
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", nargs="?", default=r"D:\project\temu-listing-ops\config\listing.yaml")
    parser.add_argument("--dry-run", action="store_true")
    ns = parser.parse_args()
    config = Path(ns.config)
    text = config.read_text(encoding="utf-8")
    sheet_rows = fetch_sheet_rows(SOURCE_URL)
    update_date = extract_update_date(sheet_rows)
    previous = existing_pricing_values(text)
    previous_date = previous.get("synced_at")
    if update_date and previous_date == update_date:
        print(json.dumps({"config": str(config), "updated": False, "reason": "source_unchanged", "update_date": update_date, "rows": 0}, ensure_ascii=False))
        return 0
    rows = filter_rows_by_configured_crafts(text, quote_rows(text, sheet_rows))
    now = update_date or dt.date.today().isoformat()
    updated = replace_top_block(remove_legacy_prices(text), "pricing", yaml_quote_block(rows, now, previous))
    if ns.dry_run:
        print(json.dumps({"rows": len(rows), "synced_at": now}, ensure_ascii=False))
    else:
        config.write_text(updated, encoding="utf-8", newline="\n")
        print(json.dumps({"config": str(config), "updated": True, "rows": len(rows), "craft_codes": sorted({str(r["craft_code"]) for r in rows}), "synced_at": now}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
