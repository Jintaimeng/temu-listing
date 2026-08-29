#!/usr/bin/env python3
"""Build and optionally run the deterministic Temu listing action queue.

The skill owns the workflow policy (ordering, stop conditions and approval
points).  This module owns the repeatable details: resolving defaults, prices,
colors and SKU values, and emitting an auditable action queue.  A UI adapter
can be supplied with ``--executor``; it receives one JSON action on stdin and
must return a JSON result containing ``ok`` and optional ``evidence``.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any


VALIDATOR = pathlib.Path(__file__).with_name("validate_listing.py")
DEFAULT_SKU_CHUNK_SIZE = 20
DEFAULT_EXECUTOR_TIMEOUT_SECONDS = 90
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5

# These errors describe a lost/uncertain browser connection, not a business
# validation failure.  They are the only failures eligible for an automatic
# retry.  Keep the matching deliberately narrow so a real page error stops the
# queue instead of being hidden by repeated input.
TRANSIENT_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "input timeout",
    "connection reset",
    "reset by peer",
    "econnreset",
    "broken pipe",
    "target closed",
    "browser disconnected",
    "cdp",
    "websocket",
    "network error",
    "temporarily unavailable",
)


def validator_module():
    spec = importlib.util.spec_from_file_location("temu_validate_listing", VALIDATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载校验脚本: {VALIDATOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_plan(path: pathlib.Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "plan" in data:
        if not data.get("ok", True):
            raise ValueError("输入计划校验失败，不能生成操作队列")
        data = data["plan"]
    if not isinstance(data, dict) or not isinstance(data.get("brands"), list):
        raise ValueError("计划必须包含 brands 列表")
    return data


def list_values(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("-"):
            value = stripped[1:].strip().strip("\"'")
            if value:
                result.append(value)
    return result


def config_snapshot(config: pathlib.Path) -> dict[str, Any]:
    """Extract the small, stable set of defaults used by the UI adapter."""
    v = validator_module()
    lines = config.read_text(encoding="utf-8").splitlines()
    defaults = v.top_section(lines, "defaults")
    images = v.section(defaults, "images", 0)
    carousel = v.section(images, "carousel", 2)
    detail = v.section(images, "detail", 2)
    material = v.section(images, "material", 2)
    origin = v.section(defaults, "origin", 0)
    attributes = v.section(defaults, "attributes", 0)
    packaging = v.section(defaults, "packaging", 0)
    sensitive = v.section(defaults, "sensitive", 0)
    volume = v.section(defaults, "volume", 0)
    sku = v.section(defaults, "sku", 0)
    return {
        "material_language": list_values(v.section(defaults, "material_language", 0)),
        "origin": {"country": v.value(origin, "country") or "", "region": v.value(origin, "region") or ""},
        "main_material": v.value(attributes, "main_material") or "",
        "packaging": {"type": v.value(packaging, "type") or "", "shape": v.value(packaging, "shape") or ""},
        "sensitive": {
            "default": v.value(sensitive, "default") or v.value(sensitive, "all") or "",
            "longest_cm": v.value(sensitive, "longest_cm") or v.value(volume, "longest_cm") or "",
            "middle_cm": v.value(sensitive, "middle_cm") or v.value(volume, "middle_cm") or "",
            "shortest_cm": v.value(sensitive, "shortest_cm") or v.value(volume, "shortest_cm") or "",
            "weight_g": v.value(sensitive, "weight_g") or v.value(volume, "weight_g") or v.value(defaults, "weight_g") or "",
        },
        "images": {
            "carousel_count": int(v.value(carousel, "count") or 5),
            "detail_enabled": (v.value(detail, "enabled") or "false").casefold() in {"true", "yes", "1"},
            "detail_count": int(v.value(detail, "count") or 0),
            "detail_source": v.value(detail, "source") or "carousel",
            "material_skip": (v.value(material, "skip") or "false").casefold() in {"true", "yes", "1"},
            "package_outer": v.value(images, "package_outer") or "",
        },
        "sku": {"fill_total_contents": (v.value(sku, "fill_total_contents") or "false").casefold() in {"true", "yes", "1"}},
        "color_codes": v.mapping_values(lines, "color_codes"),
        "craft_code_names": v.mapping_values(lines, "craft_code_names"),
        "form_labels": v.mapping_values(lines, "form_labels"),
    }


def price_for_task(plan: dict[str, Any], task: dict[str, Any]) -> tuple[float, float, str, str]:
    pricing = plan.get("pricing") or {}
    material = str(plan.get("material_code") or "")
    craft = str(task.get("craft_code") or "")
    lookup = pricing.get("quote_lookup") or {}
    material_rows = lookup.get(material) or lookup.get(material.zfill(3)) or {}
    candidates = [float(value) for key, value in material_rows.items() if str(key).casefold() == craft.casefold()]
    if not candidates:
        raise ValueError(f"缺少报价: material_code={material}, craft_code={craft}")
    if len(set(candidates)) != 1:
        raise ValueError(f"报价存在冲突: material_code={material}, craft_code={craft}")
    declaration = candidates[0] + float(pricing.get("difference", 0))
    retail = declaration * float(pricing.get("suggested_retail_multiplier", 8))
    return declaration, retail, str(pricing.get("declaration_price_currency", "store_currency")), str(pricing.get("suggested_retail_price_currency", "USD"))


def resolved_task(plan: dict[str, Any], defaults: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    declaration, retail, declaration_currency, retail_currency = price_for_task(plan, task)
    colors = task.get("colors") or []
    color = str(task.get("color") or (colors[0] if colors else ""))
    color_code = defaults["color_codes"].get(color) or defaults["color_codes"].get(color.casefold(), "")
    if not color_code:
        color_code = next((value for key, value in defaults["color_codes"].items() if str(key).casefold() == color.casefold()), "")
    if not color_code:
        raise ValueError(f"未配置颜色编码: {color}")
    craft = str(task.get("craft_code") or "").strip()
    craft_names = plan.get("craft_code_names") or defaults.get("craft_code_names") or {}
    if not isinstance(craft_names, dict):
        raise ValueError("craft_code_names 必须是代码到工艺名的映射")
    craft_name = next(
        (
            str(value).strip()
            for key, value in craft_names.items()
            if str(key).casefold() == craft.casefold() and str(value).strip()
        ),
        "",
    )
    if not craft:
        raise ValueError(f"任务缺少工艺代码: {task.get('brand', '')} / {color}")
    if not craft_name:
        raise ValueError(f"工艺代码未配置工艺名: {craft}")
    material_code = str(plan.get("material_code") or "")
    carousel = [str(x) for x in plan.get("carousel_files") or []]
    if not carousel:
        raise ValueError("轮播图为空，不能生成 SKU")
    image_id = pathlib.Path(carousel[0]).stem
    if image_id.startswith("1_"):
        image_id = image_id[2:]
    models = task.get("phone_models") or []
    detail_enabled = bool(defaults["images"].get("detail_enabled"))
    detail_source = str(defaults["images"].get("detail_source") or "carousel").casefold()
    if detail_enabled and detail_source == "carousel" and len(carousel) != defaults["images"]["carousel_count"]:
        raise ValueError(
            f"详情图文复用轮播图时必须使用完整轮播图: 需要 {defaults['images']['carousel_count']} 张，实际 {len(carousel)} 张"
        )
    if detail_enabled and detail_source == "carousel":
        detail = list(carousel)
    elif detail_enabled:
        detail = [str(x) for x in (plan.get("detail_files") or []) if str(x)]
    else:
        detail = []
    expected_detail_count = defaults["images"]["detail_count"] or defaults["images"]["carousel_count"]
    if detail_enabled and len(detail) != expected_detail_count:
        raise ValueError(f"详情图文数量不符合配置: 需要 {expected_detail_count} 张，实际 {len(detail)} 张")
    sku_rows = []
    for model in models:
        phone_model = str(model.get("phone_model") or "")
        sku_code = f"{material_code}{craft}-{phone_model}-{color_code}-{image_id}"
        sku_rows.append({
            "phone_model": phone_model,
            "color": color,
            # ``sku_code`` is the stable machine key.  ``sku_number`` is the
            # explicit page-field alias consumed by adapters for “SKU货号”.
            "sku_code": sku_code,
            "sku_number": sku_code,
            "declaration_price": declaration,
            "declaration_price_currency": declaration_currency,
            "suggested_retail_price": retail,
            "suggested_retail_price_currency": retail_currency,
            "sku_category": "单品",
            "preview_image": carousel[0],
        })
    title = str(task.get("title") or "")
    if not title:
        template = str(plan.get("title_template") or "")
        if "{desc}" in template and not str(plan.get("title_desc") or ""):
            raise ValueError(f"任务标题缺少 AI 图片特征描述: {task.get('brand', '')} / {craft} / {color}")
        title = template.replace("{brand}", str(task.get("brand", "")))
        title = title.replace("{material}", str(plan.get("material", "")))
        # Craft codes are identifiers only.  Product names must use the
        # configured human-readable craft name; SKU codes continue to use the
        # short craft code above.
        title = title.replace("{craft_codes}", craft_name)
        title = title.replace("{craft_code}", craft_name)
        title = title.replace("{工艺}", craft_name)
        title = title.replace("{desc}", str(plan.get("title_desc", "")))
    if "{" in title or "}" in title:
        raise ValueError(f"任务标题存在未解析占位符: {title}")
    return {
        "brand": task.get("brand", ""),
        "material_cache_key": plan.get("material_cache_key", ""),
        "title": title,
        "craft_code": craft,
        "craft_name": craft_name,
        "color": color,
        "phone_models": [str(model.get("phone_model") or "") for model in models],
        "basic": {
            "material_language": defaults["material_language"],
            "origin": defaults["origin"],
            "main_material": defaults["main_material"],
            "packaging": defaults["packaging"],
            "sensitive": defaults["sensitive"],
            "package_outer": plan.get("package_outer") or defaults["images"]["package_outer"],
        },
        "images": {
            "carousel": carousel,
            # Detail source=carousel is a positional binding, not a single
            # reusable first image.  Keep all five files and expose explicit
            # bindings so adapters cannot collapse them to carousel[0].
            "detail": detail,
            "detail_images": detail,
            "detail_bindings": [
                {"position": index, "file": image}
                for index, image in enumerate(detail, 1)
            ],
            "detail_count": len(detail),
            "detail_source": detail_source,
            "detail_enabled": defaults["images"]["detail_enabled"],
        },
        "sku_batch_fields": {
            "sku_category": "单品",
            "declaration_price": declaration,
            "declaration_price_currency": declaration_currency,
            "suggested_retail_price": retail,
            "suggested_retail_price_currency": retail_currency,
        },
        "sku_row_fields": ["sku_number", "preview_image"],
        "sku_rows": sku_rows,
        "spec_counts": task.get("spec_counts", {}),
    }


def build_actions(
    plan: dict[str, Any],
    config: pathlib.Path,
    task_index: int | None = None,
    sku_chunk_size: int = DEFAULT_SKU_CHUNK_SIZE,
) -> list[dict[str, Any]]:
    if sku_chunk_size < 1:
        raise ValueError(f"sku-chunk-size 必须是正整数: {sku_chunk_size}")
    defaults = config_snapshot(config)
    tasks = plan["brands"]
    if task_index is not None:
        if task_index < 1 or task_index > len(tasks):
            raise ValueError(f"task-index 超出范围: {task_index} (总任务数 {len(tasks)})")
        tasks = [tasks[task_index - 1]]
    actions: list[dict[str, Any]] = [{
        "id": "preflight",
        "phase": "preflight",
        "type": "validate_plan",
        "requires": [],
        "expects": ["plan.ok=true", "all input files readable"],
        "payload": {"config": str(config), "material_cache_key": plan.get("material_cache_key", "")},
    }, {
        "id": "login",
        "phase": "access",
        "type": "ensure_authenticated",
        "requires": ["preflight"],
        "expects": ["authenticated", "no CAPTCHA or risk challenge"],
        "payload": {"url": "https://agentseller.temu.com/"},
    }]
    for index, task in enumerate(tasks, 1):
        resolved = resolved_task(plan, defaults, task)
        prefix = f"task-{index:03d}"
        sku_batch_id = f"{prefix}-sku-batch"
        rows = resolved["sku_rows"]
        row_payload = [
            {
                "phone_model": row["phone_model"],
                "color": row["color"],
                "sku_number": row["sku_number"],
                "preview_image": row["preview_image"],
            }
            for row in rows
        ]
        row_chunks = [
            row_payload[start : start + sku_chunk_size]
            for start in range(0, len(row_payload), sku_chunk_size)
        ]
        row_chunk_count = len(row_chunks)
        actions.extend([
            {"id": f"{prefix}-draft", "phase": "draft", "type": "open_new_product", "requires": ["login"], "expects": ["new product form visible"], "payload": {"brand": resolved["brand"]}, "timeout_recovery": {"probe": ["user.openTabs", "productDraftId", "商品轮播图"], "retry": "only_if_no_target_found", "max_attempts": 2}},
            {"id": f"{prefix}-basic", "phase": "basic", "type": "fill_basic_fields", "requires": [f"{prefix}-draft"], "expects": ["all configured basic fields accepted"], "payload": resolved},
            {
                "id": sku_batch_id,
                "phase": "sku",
                "type": "fill_sku_table",
                "operation": "batch_header",
                "requires": [f"{prefix}-basic"],
                "expects": ["SKU header fields batch-filled and verified"],
                "payload": {
                    "batch_fields": resolved["sku_batch_fields"],
                    "row_count": len(rows),
                    "fill_total_contents": defaults["sku"]["fill_total_contents"],
                },
            },
        ])
        previous_sku_action = sku_batch_id
        for chunk_index, chunk_rows in enumerate(row_chunks, 1):
            row_start = (chunk_index - 1) * sku_chunk_size + 1
            row_end = row_start + len(chunk_rows) - 1
            chunk_id = f"{prefix}-sku-rows-{chunk_index:03d}"
            actions.append({
                "id": chunk_id,
                "phase": "sku",
                "type": "fill_sku_table",
                "operation": "row_fields_chunk",
                "requires": [previous_sku_action],
                "chunk": {
                    "index": chunk_index,
                    "count": row_chunk_count,
                    "row_start": row_start,
                    "row_end": row_end,
                },
                "expects": [f"SKU rows {row_start}-{row_end} have SKU货号 and preview images"],
                "payload": {
                    "rows": chunk_rows,
                    "row_count": len(chunk_rows),
                    "row_fields": resolved["sku_row_fields"],
                    "sku_number_field": "sku_number",
                    "chunk_index": chunk_index,
                    "chunk_count": row_chunk_count,
                },
            })
            previous_sku_action = chunk_id
        actions.extend([
            {"id": f"{prefix}-detail", "phase": "detail", "type": "bind_detail_images", "requires": [previous_sku_action], "expects": ["five detail components bound one-to-one to carousel positions 1-5"], "payload": resolved["images"]},
            {"id": f"{prefix}-compliance", "phase": "compliance", "type": "confirm_compliance_statement", "requires": [f"{prefix}-detail"], "expects": ["real checkbox checked=true"], "payload": {"label_key": "compliance_statement"}},
            {"id": f"{prefix}-save", "phase": "save", "type": "save_and_reload_verify", "requires": [f"{prefix}-compliance"], "expects": ["save success", "reload verification passed"], "payload": {"task": resolved}},
        ])
    actions.append({
        "id": "human-review",
        "phase": "review",
        "type": "await_submission_confirmation",
        "requires": [action["id"] for action in actions if action["phase"] == "save"],
        "expects": ["user explicitly confirms final submission"],
        "payload": {"submission_allowed": False},
        "human_gate": True,
    })
    return actions


def _text_from_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _is_transient_error(message: str) -> bool:
    normalized = message.casefold()
    return any(marker in normalized for marker in TRANSIENT_ERROR_MARKERS)


def _invoke_executor(
    command: list[str],
    action: dict[str, Any],
    timeout_seconds: float,
) -> tuple[str, dict[str, Any] | None, str]:
    """Return (kind, response, message), where kind is success/transient/fatal."""
    try:
        result = subprocess.run(
            command,
            input=json.dumps(action, ensure_ascii=False) + "\n",
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        output = " ".join(filter(None, (_text_from_output(exc.stdout), _text_from_output(exc.stderr))))
        return "transient", None, f"执行器超时 ({timeout_seconds:g}s){': ' + output if output else ''}"
    except (OSError, subprocess.SubprocessError) as exc:
        message = f"执行器进程异常: {exc}"
        return ("transient" if _is_transient_error(message) else "fatal"), None, message

    stdout = _text_from_output(result.stdout).strip()
    stderr = _text_from_output(result.stderr).strip()
    if result.returncode:
        message = stderr or stdout or f"退出码 {result.returncode}"
        return ("transient" if _is_transient_error(message) else "fatal"), None, f"执行器失败: {message}"
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError:
        message = f"执行器未返回 JSON: {stdout or stderr or '<empty>'}"
        return ("transient" if _is_transient_error(message) else "fatal"), None, message
    if not isinstance(response, dict):
        return "fatal", None, "执行器返回 JSON 不是对象"
    if not response.get("ok"):
        message = str(response.get("error", "unknown error"))
        return ("transient" if _is_transient_error(message) else "fatal"), response, f"动作未完成: {message}"
    evidence = response.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        return "fatal", response, "执行器返回 ok=true 但缺少验收 evidence"
    return "success", response, ""


def _recovery_probe(action: dict[str, Any]) -> dict[str, Any] | None:
    recovery = action.get("timeout_recovery")
    if not isinstance(recovery, dict) or not recovery.get("probe"):
        return None
    return {
        "id": f"{action['id']}::probe",
        "phase": action.get("phase", "recovery"),
        "type": "probe_action_state",
        "operation": "probe",
        "target_action_id": action["id"],
        "probe": recovery["probe"],
        "expects": ["probe reports whether target action already took effect"],
        "payload": {
            "target_action_id": action["id"],
            "target_type": action.get("type"),
            "target_operation": action.get("operation"),
            "target_chunk": action.get("chunk"),
            "target_payload": action.get("payload", {}),
        },
    }


def _probe_applied(evidence: dict[str, Any]) -> bool | None:
    true_keys = ("already_applied", "target_present", "completed", "matched", "applied")
    for key in true_keys:
        if evidence.get(key) is True:
            return True
        if evidence.get(key) is False:
            return False
    # ``present`` is only a negative signal when explicitly false; this keeps
    # a generic ``present=true`` probe useful without treating arbitrary
    # evidence values as proof.
    if evidence.get("target_present") is False or evidence.get("already_applied") is False:
        return False
    if evidence.get("not_applied") is True or evidence.get("target_missing") is True:
        return False
    status = str(evidence.get("status", "")).casefold()
    if status in {"completed", "already_applied", "present", "matched", "success"}:
        return True
    if status in {"missing", "not_applied", "absent", "pending"}:
        return False
    return None


def _row_keys(action: dict[str, Any]) -> list[str]:
    payload = action.get("payload") or {}
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [
        "|".join(str(row.get(key, "")) for key in ("phone_model", "color", "sku_number"))
        for row in rows
        if isinstance(row, dict)
    ]


def _save_state(state: dict[str, Any], state_path: pathlib.Path) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _completed_state(action: dict[str, Any], fingerprint: str, response: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "status": "completed",
        "operation": action.get("operation"),
        "chunk": action.get("chunk"),
        "row_keys": _row_keys(action),
        "fingerprint": fingerprint,
        "evidence": response.get("evidence", {}),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def run_actions(
    actions: list[dict[str, Any]],
    executor: str,
    state_path: pathlib.Path,
    executor_timeout: float = DEFAULT_EXECUTOR_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> None:
    if executor_timeout <= 0:
        raise ValueError(f"executor-timeout 必须是正数: {executor_timeout}")
    if max_attempts < 1:
        raise ValueError(f"max-attempts 必须是正整数: {max_attempts}")
    if retry_backoff < 0:
        raise ValueError(f"retry-backoff 不能为负数: {retry_backoff}")
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"actions": {}}
    if not isinstance(state, dict):
        raise ValueError("执行状态必须是 JSON 对象")
    state.setdefault("actions", {})
    if not isinstance(state["actions"], dict):
        raise ValueError("执行状态 actions 必须是对象")
    command = shlex.split(executor, posix=False)
    for action in actions:
        action_started_at = datetime.now(timezone.utc)
        action_started_clock = time.perf_counter()
        fingerprint = hashlib.sha256(
            json.dumps(action, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        previous = state["actions"].get(action["id"], {})
        if previous.get("status") == "completed" and previous.get("fingerprint") == fingerprint:
            continue
        missing = [dependency for dependency in action.get("requires", []) if state["actions"].get(dependency, {}).get("status") != "completed"]
        if missing:
            raise RuntimeError(f"动作依赖未完成 ({action['id']}): {', '.join(missing)}")
        if action.get("human_gate"):
            state["actions"][action["id"]] = {
                "status": "blocked",
                "reason": "human_gate",
                "fingerprint": fingerprint,
                "started_at": action_started_at.isoformat(),
                "duration_ms": round((time.perf_counter() - action_started_clock) * 1000),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            _save_state(state, state_path)
            raise RuntimeError(f"到达人工作业点: {action['id']}")

        recovery = action.get("timeout_recovery") if isinstance(action.get("timeout_recovery"), dict) else {}
        # Navigation actions may create a new draft, so they can only retry
        # after a positive probe. Data-entry actions are idempotent by field
        # value and may retry directly when the executor has no probe.
        requires_probe = bool(recovery.get("probe"))
        attempts_limit = int(recovery.get("max_attempts", max_attempts) or max_attempts)
        attempts_limit = max(1, min(attempts_limit, max_attempts))
        for attempt in range(1, attempts_limit + 1):
            attempt_action = dict(action)
            attempt_action["_execution"] = {"attempt": attempt, "max_attempts": attempts_limit}
            kind, response, message = _invoke_executor(command, attempt_action, executor_timeout)
            if kind == "success" and response is not None:
                state["actions"][action["id"]] = _completed_state(action, fingerprint, response, attempts=attempt)
                state["actions"][action["id"]]["started_at"] = action_started_at.isoformat()
                state["actions"][action["id"]]["duration_ms"] = round((time.perf_counter() - action_started_clock) * 1000)
                _save_state(state, state_path)
                break
            if kind == "fatal":
                state["actions"][action["id"]] = {
                    "status": "failed",
                    "fingerprint": fingerprint,
                    "attempts": attempt,
                    "error": message,
                    "started_at": action_started_at.isoformat(),
                    "duration_ms": round((time.perf_counter() - action_started_clock) * 1000),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                _save_state(state, state_path)
                raise RuntimeError(f"{message} ({action['id']})")
            if attempt >= attempts_limit:
                state["actions"][action["id"]] = {
                    "status": "failed",
                    "fingerprint": fingerprint,
                    "attempts": attempt,
                    "error": message,
                    "started_at": action_started_at.isoformat(),
                    "duration_ms": round((time.perf_counter() - action_started_clock) * 1000),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                _save_state(state, state_path)
                raise RuntimeError(f"重试次数耗尽 ({action['id']}): {message}")

            if requires_probe:
                probe = _recovery_probe(action)
                probe_kind, probe_response, probe_message = _invoke_executor(command, probe, executor_timeout) if probe else ("fatal", None, "未配置恢复探测")
                if probe_kind != "success" or probe_response is None:
                    state["actions"][action["id"]] = {
                        "status": "uncertain",
                        "fingerprint": fingerprint,
                        "attempts": attempt,
                        "error": f"{message}; 恢复探测失败: {probe_message}",
                        "started_at": action_started_at.isoformat(),
                        "duration_ms": round((time.perf_counter() - action_started_clock) * 1000),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    _save_state(state, state_path)
                    raise RuntimeError(f"无法确认动作状态，已停止重试 ({action['id']}): {probe_message}")
                applied = _probe_applied(probe_response.get("evidence", {}))
                if applied is True:
                    recovered = dict(probe_response)
                    recovered["evidence"] = {
                        **probe_response.get("evidence", {}),
                        "recovered_from_uncertain_failure": True,
                        "original_error": message,
                    }
                    state["actions"][action["id"]] = _completed_state(
                        action, fingerprint, recovered, attempts=attempt, recovered=True
                    )
                    state["actions"][action["id"]]["started_at"] = action_started_at.isoformat()
                    state["actions"][action["id"]]["duration_ms"] = round((time.perf_counter() - action_started_clock) * 1000)
                    _save_state(state, state_path)
                    break
                if applied is None:
                    state["actions"][action["id"]] = {
                        "status": "uncertain",
                        "fingerprint": fingerprint,
                        "attempts": attempt,
                        "error": f"{message}; 恢复探测结果不明确",
                        "probe_evidence": probe_response.get("evidence", {}),
                        "started_at": action_started_at.isoformat(),
                        "duration_ms": round((time.perf_counter() - action_started_clock) * 1000),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    _save_state(state, state_path)
                    raise RuntimeError(f"恢复探测结果不明确，已停止重试 ({action['id']})")

            state["actions"][action["id"]] = {
                "status": "retrying",
                "fingerprint": fingerprint,
                "attempts": attempt,
                "error": message,
                "started_at": action_started_at.isoformat(),
                "duration_ms": round((time.perf_counter() - action_started_clock) * 1000),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            _save_state(state, state_path)
            if retry_backoff:
                time.sleep(min(retry_backoff * (2 ** (attempt - 1)), 8.0))


def main() -> int:
    parser = argparse.ArgumentParser(description="生成/执行 Temu 上架动作队列（未指定 task-index 时覆盖计划中的全部商品流程）")
    parser.add_argument("config", help="listing.yaml 路径")
    parser.add_argument("--plan", required=True, help="validate_listing.py --json 输出或 plan JSON")
    parser.add_argument("--actions-out", help="输出 JSON 动作队列")
    parser.add_argument("--state", help="执行状态 JSON 路径")
    parser.add_argument("--executor", help="接收单条 JSON action 并返回 JSON result 的执行器命令")
    parser.add_argument("--task-index", type=int, help="仅执行计划中指定的 1-based 商品任务，用于小范围测试；省略时执行全部任务")
    parser.add_argument(
        "--sku-chunk-size",
        type=int,
        default=DEFAULT_SKU_CHUNK_SIZE,
        help=f"不可批量 SKU 字段的每批行数（默认 {DEFAULT_SKU_CHUNK_SIZE}）",
    )
    parser.add_argument(
        "--executor-timeout",
        type=float,
        default=DEFAULT_EXECUTOR_TIMEOUT_SECONDS,
        help=f"单个动作执行器超时时间（秒，默认 {DEFAULT_EXECUTOR_TIMEOUT_SECONDS}）",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"连接类失败的最大总尝试次数（默认 {DEFAULT_MAX_ATTEMPTS}）",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help=f"连接类失败重试前的基础退避秒数（默认 {DEFAULT_RETRY_BACKOFF_SECONDS}）",
    )
    parser.add_argument("--title-desc", help="为本次运行注入已确认的图片特征短语，不修改 listing.yaml")
    ns = parser.parse_args()
    config = pathlib.Path(ns.config).resolve()
    plan = read_plan(pathlib.Path(ns.plan).resolve())
    if ns.title_desc is not None:
        plan["title_desc"] = ns.title_desc
    actions = build_actions(plan, config, task_index=ns.task_index, sku_chunk_size=ns.sku_chunk_size)
    output = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "config": str(config), "plan_material_cache_key": plan.get("material_cache_key", ""), "actions": actions}
    if ns.actions_out:
        destination = pathlib.Path(ns.actions_out).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"actions={destination}")
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    if ns.executor:
        if not ns.state:
            parser.error("使用 --executor 时必须同时指定 --state")
        run_actions(
            actions,
            ns.executor,
            pathlib.Path(ns.state).resolve(),
            executor_timeout=ns.executor_timeout,
            max_attempts=ns.max_attempts,
            retry_backoff=ns.retry_backoff,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
