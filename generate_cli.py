# -*- coding: utf-8 -*-
"""纯 Python 命令行长：调用 box_registry.generate_payload 输出三格式文件。

用途（不依赖 Flask，作回归 oracle + Pyodide 冒烟对照基准）：
    python generate_cli.py airplane  [out_dir]
    python generate_cli.py insertion [out_dir]
    python generate_cli.py airplane --verify
生成文件：out_dir/<id>.svg / .pdf / .dxf，并打印 JSON 摘要（含 meta）。
"""
from __future__ import annotations

import json
import os
import sys

from box_registry import BOX_REGISTRY, dispatch, generate_payload


def _dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def main(argv: list[str]) -> int:
    box_type = argv[1] if len(argv) > 1 else "airplane"
    if box_type == "--verify":
        box_type = "airplane"
    out_dir = argv[2] if len(argv) > 2 else "outputs"
    verify = "--verify" in argv

    if box_type not in BOX_REGISTRY:
        print("未知盒型:", box_type, "可用:", list(BOX_REGISTRY))
        return 1

    form = dict(BOX_REGISTRY[box_type].defaults)
    form["box_type"] = box_type
    if verify:
        form["length"], form["width"], form["height"], form["thickness"] = 200, 150, 60, 3

    payload = generate_payload(form)
    if not payload.get("ok"):
        print("ERROR:", _dumps(payload))
        return 1

    os.makedirs(out_dir, exist_ok=True)
    artifacts = payload["artifacts"]
    fid = payload["id"]
    for ext, data in (("svg", artifacts["svg"]), ("pdf", artifacts["pdf"]), ("dxf", artifacts["dxf"])):
        path = os.path.join(out_dir, f"{fid}.{ext}")
        mode = "w" if isinstance(data, str) else "wb"
        with open(path, mode, encoding="utf-8" if isinstance(data, str) else None) as f:
            f.write(data)
        print(f"已写出 {path}  ({len(data)} 字节)")

    # 打印摘要（geometry 太长，只打 meta + geometry.type/panels 数）
    geo = payload.get("geometry", {})
    summary = {k: payload.get(k) for k in ("id", "title", "ok")}
    summary["meta"] = payload.get("meta", {})
    summary["geometry_contract"] = {"type": geo.get("type"), "panels": len(geo.get("panels", []))}
    print(_dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
