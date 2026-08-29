#!/usr/bin/env python3
"""Run one complete image-pack listing job.

``listing_workflow.py`` owns the deterministic actions for a product task.
This module is the image-pack-level entry point: it validates the pack once,
freezes the expanded brand/color/craft plan, builds the complete action queue
without ``--task-index``, and optionally executes it with one resumable state
file.  The browser adapter still performs the concrete DOM operations.

The final human review gate is intentionally preserved.  Reaching that gate
means all generated product drafts have passed the scripted save/reload
checks; final submission still requires an explicit user confirmation.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
VALIDATOR = SCRIPT_DIR / "validate_listing.py"
WORKFLOW = SCRIPT_DIR / "listing_workflow.py"


def _load_module(path: pathlib.Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载脚本: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_plan(path: pathlib.Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "plan" in data:
        if not data.get("ok", True):
            raise ValueError("输入计划校验失败，不能执行图片包任务")
        data = data["plan"]
    if not isinstance(data, dict) or not isinstance(data.get("brands"), list):
        raise ValueError("计划必须包含 brands 列表")
    return data


def validate_to_plan(
    config: pathlib.Path,
    plan_path: pathlib.Path,
    pack_dir: str | None,
    project_root: str | None,
) -> dict[str, Any]:
    """Run the canonical validator and persist its immutable plan."""
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(VALIDATOR), str(config), "--json", "--plan-out", str(plan_path)]
    if pack_dir is not None:
        command.extend(["--pack-dir", pack_dir])
    if project_root is not None:
        command.extend(["--project-root", project_root])
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        detail = (result.stdout or result.stderr).strip()
        raise RuntimeError(f"图片包校验失败: {detail or f'退出码 {result.returncode}'}")
    return read_plan(plan_path)


def task_summary(plan: dict[str, Any]) -> dict[str, Any]:
    tasks = plan.get("brands") or []
    craft_codes = sorted(
        {
            str(task.get("craft_code") or "")
            for task in tasks
            if isinstance(task, dict) and str(task.get("craft_code") or "")
        }
    )
    colors = sorted(
        {
            str(task.get("color") or (task.get("colors") or [""])[0])
            for task in tasks
            if isinstance(task, dict)
        }
    )
    return {
        "product_flow_count": len(tasks),
        "craft_codes": craft_codes,
        "color_count_in_expanded_plan": len(colors),
        "brand_count_in_expanded_plan": len(
            {str(task.get("brand") or "") for task in tasks if isinstance(task, dict)}
        ),
    }


def write_actions(
    destination: pathlib.Path,
    config: pathlib.Path,
    plan: dict[str, Any],
    actions: list[dict[str, Any]],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "workflow": "image_pack_batch",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": str(config),
        "plan_material_cache_key": plan.get("material_cache_key", ""),
        "summary": task_summary(plan),
        "actions": actions,
    }
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="执行一次完整的 Temu 图片包批量上架任务")
    parser.add_argument("config", help="listing.yaml 路径")
    parser.add_argument("--pack-dir", help="本次图片包目录；不修改 listing.yaml")
    parser.add_argument("--project-root", help="图片包/相对路径解析用项目根目录")
    parser.add_argument("--plan", help="已校验的 plan JSON；提供后跳过重新校验")
    parser.add_argument("--plan-out", default="work/listing-batch-plan.json", help="校验计划输出路径")
    parser.add_argument("--actions-out", default="work/listing-batch-actions.json", help="完整动作队列输出路径")
    parser.add_argument("--state", default="work/listing-batch-state.json", help="断点续跑状态 JSON 路径")
    parser.add_argument("--executor", help="接收单条 JSON action 并返回 JSON result 的浏览器适配器命令")
    parser.add_argument("--sku-chunk-size", type=int, default=20, help="不可批量 SKU 字段的每批行数")
    parser.add_argument("--executor-timeout", type=float, default=90, help="单个动作执行器超时时间（秒）")
    parser.add_argument("--max-attempts", type=int, default=2, help="连接类失败的最大总尝试次数")
    parser.add_argument("--retry-backoff", type=float, default=0.5, help="连接类失败重试前的基础退避秒数")
    parser.add_argument("--title-desc", help="为本次运行注入已确认的图片特征短语，不修改 listing.yaml")
    ns = parser.parse_args()

    config = pathlib.Path(ns.config).expanduser().resolve()
    plan_path = pathlib.Path(ns.plan_out).expanduser().resolve()
    if ns.plan:
        plan_path = pathlib.Path(ns.plan).expanduser().resolve()
        plan = read_plan(plan_path)
    else:
        plan = validate_to_plan(config, plan_path, ns.pack_dir, ns.project_root)
    if ns.title_desc is not None:
        plan["title_desc"] = ns.title_desc

    workflow = _load_module(WORKFLOW, "temu_listing_workflow")
    actions = workflow.build_actions(plan, config, sku_chunk_size=ns.sku_chunk_size)
    actions_path = pathlib.Path(ns.actions_out).expanduser().resolve()
    write_actions(actions_path, config, plan, actions)

    summary = task_summary(plan)
    print(f"plan={plan_path}")
    print(f"actions={actions_path}")
    print(f"product_flows={summary['product_flow_count']}")
    print(f"craft_codes={','.join(summary['craft_codes']) or '<none>'}")
    print("mode=generate-only (未提供 --executor)" if not ns.executor else "mode=execute")

    if ns.executor:
        state_path = pathlib.Path(ns.state).expanduser().resolve()
        try:
            workflow.run_actions(
                actions,
                ns.executor,
                state_path,
                executor_timeout=ns.executor_timeout,
                max_attempts=ns.max_attempts,
                retry_backoff=ns.retry_backoff,
            )
        except RuntimeError as exc:
            if str(exc) == "到达人工作业点: human-review":
                print(f"state={state_path}")
                print("result=all product flows saved; awaiting human review before final submission")
                return 0
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"state={state_path}")
        print("result=all product flows reached the review gate")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
