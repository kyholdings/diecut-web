# -*- coding: utf-8 -*-
"""盒型注册表（单一事实源）。

集中管理每款盒型的构建器、默认参数、参数取值规则、3D 折叠契约与元数据，
取代 app.py 里 ``if box_type == "insertion"`` 这样的硬编码分发：新增一款盒型
只需在 BOX_REGISTRY 注册一个 BoxSpec，前端下拉 / 默认值 / 参数校验 /
3D 折叠契约 / JSON box_type 全部自动跟上传，无需改动分发与前端生成逻辑。

归一化器（normalize）把表单里的裸值（含空字符串、字符串数字）按规则转成
构建器可直接接受的参数，保证折线只跑一次、与旧 app.py 行为一致。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import diecut_engine as eng
from diecut_engine import (
    DieCutGeometry,
    build_airplane_box,
    build_insertion_box,
    geometry_to_dxf_bytes,
    geometry_to_json,
    geometry_to_pdf_bytes,
    geometry_to_svg,
    estimate_sheet_utilization,
    validate_geometry,
    _insertion_fold_contract,
)

# ---------------------------------------------------------------------------
# 参数归一化（取值规则）
# ---------------------------------------------------------------------------

_NUM    = "num"       # >0 否则默认（length/width/height/thickness/ratio）
_NUMOPT = "numopt"    # None→None，否则按 num（tab_depth 可选）
_WINDOW = "window"    # None→默认，数字→max(0,x)（0=关窗语义，保留0）
_BOOL   = "bool"
_LAYERS = "layers"
_FACADE = "facade"    # ""/None→None，数字→float（制造尺寸补偿 fb_comp/side_comp）


def _num(v: Any, default: float) -> float:
    try:
        f = float(v)
        return default if f <= 0 else f
    except (TypeError, ValueError):
        return default


def _numopt(v: Any, default: float):
    if v is None:
        return None
    return _num(v, default)


def _window(v: Any, default: float) -> float:
    if v is None:
        return default
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return default


def _facade(v: Any, default: float):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _bool(v: Any, default: bool):
    if v is None:
        return default
    return bool(v)


def _layers(v: Any, default):
    if isinstance(v, list):
        return [str(x).upper() for x in v if str(x).upper() in ("CUT", "CREASE", "HALFCUT", "DIMENSION")]
    return None


_COERCERS: dict[str, Callable] = {
    _NUM: _num,
    _NUMOPT: _numopt,
    _WINDOW: _window,
    _BOOL: _bool,
    _LAYERS: _layers,
    _FACADE: _facade,
}


# ---------------------------------------------------------------------------
# 盒型规格
# ---------------------------------------------------------------------------

@dataclass
class BoxSpec:
    """一种盒型的全部描述。builder 是其几何构造器；params 描述「表单键 → 取值规则」；
    fold_contract 为该盒型的 3D 折叠契约（返回 panels/fold/fold_sequence），
    None 表示复用 geometry_to_json 的默认面板（无特殊折叠动力学，如飞机盒）。
    """
    key: str
    label: str
    builder: Callable[..., DieCutGeometry]
    defaults: dict
    json_box_type: str                       # geometry_to_json 的产物类型标签
    params: dict                             # {name: (kind, default)}
    fold_contract: Optional[Callable] = None
    param_rules: dict = field(default_factory=dict)   # 前端表单 label/min/max/step


# 数字量通用规则（供 param_rules 复用）
_DIM = {"min": 1, "max": 3000, "step": 1}
_RATIO = {"min": 0.01, "max": 1.0, "step": 0.01}
_WINDOW_RULE = {"min": 0, "max": 3000, "step": 1}


BOX_REGISTRY: dict[str, BoxSpec] = {
    "airplane": BoxSpec(
        key="airplane",
        label="自锁飞机盒",
        builder=build_airplane_box,
        defaults={"length": 200, "width": 150, "height": 60, "thickness": 3},
        json_box_type="airplane_box",
        params={
            "length": (_NUM, 200), "width": (_NUM, 150), "height": (_NUM, 60), "thickness": (_NUM, 3),
            "internal": (_BOOL, True),
            "tab_depth": (_NUMOPT, 20), "fold_ratio": (_NUM, 0.3), "lock_ratio": (_NUM, 1.0),
            "corner_radius": (_NUM, 0.0), "tab_ear_radius": (_NUM, 0.0), "hook_ratio": (_NUM, 0.33),
            "board_compensation": (_BOOL, None), "layers": (_LAYERS, None),
            "fb_comp": (_FACADE, None), "side_comp": (_FACADE, None),
        },
        fold_contract=None,   # 飞机盒用 geometry_to_json 默认面板（19 panels）
        param_rules={
            "length": {"label": "长(mm)", **_DIM}, "width": {"label": "宽(mm)", **_DIM},
            "height": {"label": "高(mm)", **_DIM}, "thickness": {"label": "纸厚(mm)", "min": 0.2, "max": 20, "step": 0.1},
            "fold_ratio": {"label": "折翼比例", **_RATIO}, "lock_ratio": {"label": "锁扣比例", **_RATIO},
            "corner_radius": {"label": "盖翼圆角(mm)", "min": 0, "max": 30, "step": 1},
            "hook_ratio": {"label": "凸起钩比例", **_RATIO},
        },
    ),
    "insertion": BoxSpec(
        key="insertion",
        label="插口盒",
        builder=build_insertion_box,
        defaults={"length": 45, "width": 45, "height": 104, "thickness": 0.5},
        json_box_type="insertion_box",
        params={
            "length": (_NUM, 45), "width": (_NUM, 45), "height": (_NUM, 104), "thickness": (_NUM, 0.5),
            "internal": (_BOOL, True),
            "window_width": (_WINDOW, 25.0), "window_height": (_WINDOW, 60.0), "window_top_offset": (_WINDOW, 20.0),
            "layers": (_LAYERS, None),
        },
        fold_contract=_insertion_fold_contract,
        param_rules={
            "length": {"label": "长(mm)", **_DIM}, "width": {"label": "宽(mm)", **_DIM},
            "height": {"label": "高(mm)", **_DIM}, "thickness": {"label": "纸厚(mm)", "min": 0.2, "max": 20, "step": 0.1},
            "window_width": {"label": "开窗宽(mm)", **_WINDOW_RULE}, "window_height": {"label": "开窗高(mm)", **_WINDOW_RULE},
            "window_top_offset": {"label": "开窗顶偏移(mm)", **_WINDOW_RULE},
        },
    ),
}


def dispatch(box_type: str, form: dict) -> DieCutGeometry:
    """按盒型从表单构造构建参数并调用构建器，返回几何。"""
    spec = BOX_REGISTRY[box_type]
    kwargs: dict[str, Any] = {}
    for name, (kind, default) in spec.params.items():
        raw = form.get(name, default)
        kwargs[name] = _COERCERS[kind](raw, default)
    return spec.builder(**kwargs)


def contract(spec: BoxSpec, geo: DieCutGeometry) -> dict:
    """带正确 3D 折叠契约的 geometry contract。

    geometry_to_json 对非 airplane_box 会一刀切套用插口盒折叠契约，这里用
    spec.fold_contract 覆盖：新增盒型只要带上自己的 fold_contract（或 None 复用
    默认面板），即不会被错误的 folding 污染。
    """
    base = geometry_to_json(geo, spec.json_box_type)
    if spec.fold_contract is not None:
        panels, mfold, fseq = spec.fold_contract(geo)
        base["panels"] = panels
        base["fold"] = mfold
        base["fold_sequence"] = fseq
    return base


def box_types_meta() -> list[dict]:
    """前端用：每款盒型的 label / defaults / 参数校验规则 / 产物标签。不含可执行函数。"""
    out = []
    for spec in BOX_REGISTRY.values():
        out.append({
            "key": spec.key,
            "label": spec.label,
            "defaults": spec.defaults,
            "json_box_type": spec.json_box_type,
            "params": [{"name": n, "kind": k, "default": d} for n, (k, d) in spec.params.items()],
            "param_rules": spec.param_rules,
        })
    return out


# ---------------------------------------------------------------------------
# 完整生成（复刻原 app.py 的 POST /api/diecut/generate 行为，改为注册表驱动）
# ---------------------------------------------------------------------------

def generate_payload(form: dict) -> dict:
    """复刻 app.py generate()：校验 → 构建 → 生成三格式 → 组装 title/geometry/meta。

    返回与旧后端一致的「API 形态」，另附 ``artifacts`` 携带三格式字节供纯静态端使用：
      ok / id / title / pdf_url / dxf_url / svg_url / geometry / meta / artifacts
    artifacts = {"svg": str, "pdf": bytes, "dxf": bytes}
    """
    box_type = form.get("box_type", "airplane")
    spec = BOX_REGISTRY[box_type]

    length = _num(form.get("length"), spec.defaults.get("length", 200))
    width = _num(form.get("width"), spec.defaults.get("width", 150))
    height = _num(form.get("height"), spec.defaults.get("height", 60))
    thickness = _num(form.get("thickness"), spec.defaults.get("thickness", 3))

    if length > 3000 or width > 3000 or height > 3000:
        return {"error": "尺寸超出合理范围（≤3000mm）"}
    if thickness > 20:
        return {"error": "纸板厚度超出合理范围（≤20mm）"}

    try:
        geo = dispatch(box_type, form)
    except ValueError as exc:
        return {"error": str(exc)}

    geometry_errors = validate_geometry(geo)
    if geometry_errors:
        return {"error": "几何校验失败", "details": geometry_errors}

    effective_internal = geo.board_compensation
    box_label = spec.label
    if effective_internal:
        title = f"{box_label}刀版 内 {length:.0f}x{width:.0f}x{height:.0f}mm 纸厚{thickness:.1f}mm"
        outer_l = length + 2 * thickness
        outer_w = width + (3 * thickness if box_type != "insertion" else 2 * thickness)
        outer_h = height + 2 * thickness
    else:
        title = f"{box_label}刀版 外 {length:.0f}x{width:.0f}x{height:.0f}mm 纸厚{thickness:.1f}mm"
        outer_l, outer_w, outer_h = length, width, height

    svg = geometry_to_svg(geo, title)
    pdf = geometry_to_pdf_bytes(geo, title)
    dxf = geometry_to_dxf_bytes(geo, title)
    fid = uuid.uuid4().hex[:12]

    min_x, min_y, max_x, max_y = geo.bounds
    cut_count = sum(1 for s in geo.segments if s.kind == "cut")
    crease_count = sum(1 for s in geo.segments if s.kind == "crease")

    nesting = None
    sheet = form.get("sheet") if isinstance(form.get("sheet"), dict) else None
    if sheet:
        try:
            nesting = estimate_sheet_utilization(
                geo,
                _num(sheet.get("width"), 0),
                _num(sheet.get("height"), 0),
                max(0.0, float(sheet.get("margin", 10))),
                max(0.0, float(sheet.get("gap", 5))),
            )
        except (TypeError, ValueError):
            return {"error": "sheet 的纸张尺寸、边距或间距无效"}

    meta = {
        "input_length": length, "input_width": width, "input_height": height, "thickness": thickness,
        "internal": effective_internal,
        "inner": {"length": geo.length, "width": geo.width, "height": geo.height},
        "outer": {"length": outer_l, "width": outer_w, "height": outer_h},
        "blank": {
            "width_mm": max_x - min_x, "height_mm": max_y - min_y, "panel_width_mm": geo.length,
            "wall_height_mm": geo.wall_height, "bottom_height_mm": geo.bottom_height, "lid_height_mm": geo.lid_height,
            "tab_depth_mm": geo.tab_depth, "wing_width_mm": geo.wing_width, "back_flap_width_mm": geo.back_flap_width,
            "lock_width_mm": geo.lock_width, "fold_seg_mm": geo.fold_seg, "side_inner_mm": geo.side_inner,
            "side_outer_mm": geo.side_outer,
        },
        "segments": {"cut": cut_count, "crease": crease_count},
        "nesting": nesting,
        "parameters": {
            "corner_radius_mm": geo.corner_radius, "hook_ratio": geo.hook_ratio,
            "hook_height_mm": geo.width * geo.hook_ratio, "board_compensation": geo.board_compensation,
            "layers": geo.layers,
        },
    }

    return {
        "ok": True, "id": fid, "title": title,
        "pdf_url": f"/api/diecut/download/{fid}.pdf",
        "dxf_url": f"/api/diecut/download/{fid}.dxf",
        "svg_url": f"/api/diecut/download/{fid}.svg",
        "geometry": contract(spec, geo),
        "meta": meta,
        "artifacts": {"svg": svg, "pdf": pdf, "dxf": dxf},
    }
