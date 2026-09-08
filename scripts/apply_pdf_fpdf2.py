# -*- coding: utf-8 -*-
"""One-shot patch: swap PDF engine in diecut_engine.py from ReportLab to fpdf2.

Replaces the two functions (_ensure_cjk_font + geometry_to_pdf_bytes) with fpdf2
equivalents, so the engine no longer depends on reportlab and any DXF/SVG code
is left untouched.  Writes diecut_engine.py in place and then self-verifies:
py_compile + brace balance + a real generate-PDF smoke test.

Run from diecut-web/:  python scripts/apply_pdf_fpdf2.py
"""
from __future__ import annotations

import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "diecut_engine.py")

NEW_BLOCK = '''\
def _resolve_cjk_font_path() -> str | None:
    """返回可内嵌的中文字体路径（优先随项目打包的 fonts/ 下单 .ttf），无则 None。

    fpdf2 版：不再像 reportlab 那样预注册字体名，而是把 ttf 文件直接交给
    当前 FPDF 实例 add_font（内嵌 TrueType）。只收真覆盖中文的单 .ttf；
    Windows 再回退到系统已装单 .ttf 中文字库作为兜底。
    """
    global _CJK_FONT, _CJK_FONT_READY
    if _CJK_FONT_READY:
        return _CJK_FONT
    _CJK_FONT_READY = True
    candidates: list[str] = []
    app_fonts = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    if os.path.isdir(app_fonts):
        for fn in sorted(os.listdir(app_fonts)):
            low = fn.lower()
            # 先收单 .ttf（glyf 轮廓）；.ttc 集合交给 fpdf2 subfont 略复杂，此处不自动收
            if low.endswith(".ttf"):
                candidates.append(os.path.join(app_fonts, fn))
        # 用 fontTools 判中文覆盖，覆盖越多越优先；fontTools 不可用则保持原始排序
        try:
            from fontTools.ttLib import TTFont as _FT
            def _rank(path: str) -> int:
                try:
                    cmap = _FT(path, fontNumber=0).getBestCmap()
                    ok = sum(1 for c in "飞机盒刀版测试商邑" if ord(c) in cmap)
                    return -ok
                except Exception:
                    return 1
            candidates.sort(key=_rank)
        except Exception:
            pass
    if os.name == "nt":
        for fn in ("simhei.ttf", "Deng.ttf", "msjh.ttf"):
            p = os.path.join(r"C:\\Windows\\Fonts", fn)
            if os.path.exists(p):
                candidates.append(p)
    for path in candidates:
        if os.path.exists(path):
            _CJK_FONT = path
            break
    return _CJK_FONT


def geometry_to_pdf_bytes(geo: DieCutGeometry, title: str = "") -> bytes:
    """fpdf2 版：生成 1:1 毫米制 PDF，含各层线型 + 尺寸标注 + 中文字标题。

    复用 SVG/PDF 一致的 _dimension_marks 语义（断口主线 + 端部箭头 + 中点数字）。
    ReportLab 的画布 y 轴向上、fpdf2 原点在左上、y 向下，故做一次 y 翻转。
    """
    from fpdf import FPDF

    min_x, min_y, max_x, max_y = geo.bounds
    pad_mm = 15.0
    page_w = (max_x - min_x) + 2 * pad_mm
    page_h = (max_y - min_y) + 2 * pad_mm
    pdf = FPDF(unit="mm", format=(page_w, page_h))
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    tx = pad_mm - min_x
    ty = pad_mm - min_y

    def P(x: float, y: float) -> tuple[float, float]:
        # 网坐标(y 向上) -> fpdf 坐标(y 向下)
        return (x + tx, page_h - (y + ty))

    # 中文字体
    font_name = None
    cjk = _resolve_cjk_font_path()
    if cjk:
        try:
            pdf.add_font("CJK", "", cjk)
            font_name = "CJK"
        except Exception:
            font_name = None
    if font_name is None:
        font_name = "helvetica"

    def _label(x: float, y: float, text: str, size: float = 8.5) -> None:
        pdf.set_font(font_name, size=size)
        pdf.set_text_color(51, 64, 84)
        sw = pdf.get_string_width(text)
        # text 的 x 是文本左缘、y 是基线；基线略下移让数字视觉居中于断口
        pdf.text(x - sw / 2.0, y + size * 0.35, text)

    def draw_segment(seg: Segment) -> None:
        pdf.set_dash_pattern()
        if seg.kind == "cut":
            pdf.set_draw_color(0, 0, 0)
            pdf.set_line_width(0.5 + geo.thickness * 0.02)
        elif seg.kind == "halfcut":
            pdf.set_draw_color(26, 78, 216)
            pdf.set_line_width(0.2)
            pdf.set_dash_pattern(dash=1, gap=1.5)
        elif seg.kind == "dimension":
            pdf.set_draw_color(51, 64, 84)
            pdf.set_line_width(0.35)
        else:  # crease
            pdf.set_draw_color(230, 26, 26)
            pdf.set_line_width(0.35 + geo.thickness * 0.02)
            pdf.set_dash_pattern(dash=3, gap=2)
        pts = [P(x, y) for x, y in seg.points]
        pdf.polyline(pts)
        pdf.set_dash_pattern()

    def draw_dimension(seg: Segment) -> None:
        pdf.set_dash_pattern()
        pdf.set_draw_color(51, 64, 84)
        pdf.set_line_width(0.35)
        tick = 1.6
        pad = 2.0
        for (x0, y0), (x1, y1) in zip(seg.points, seg.points[1:]):
            dx, dy = x1 - x0, y1 - y0
            seg_len = math.hypot(dx, dy)
            if seg_len < 1e-9:
                continue
            ux, uy = dx / seg_len, dy / seg_len
            ang = math.atan2(dy, dx)
            # 端部箭头（45° 斜向短线）
            for (px, py, dirn) in ((x0, y0, 1), (x1, y1, -1)):
                base = ang if dirn > 0 else ang + math.pi
                for off in (math.pi * 0.8, -math.pi * 0.8):
                    ex = px + tick * math.cos(base + off)
                    ey = py + tick * math.sin(base + off)
                    pdf.line(*P(px, py), *P(ex, ey))
            label = f"{seg_len:g} mm"
            est_half = len(label) * 4.75 / 2.0 + pad
            half = min(est_half, max(0.5, seg_len / 2.0 - 1.0))
            mid_d = seg_len / 2.0
            a, b = mid_d - half, mid_d + half
            if a > 0.001:
                pdf.line(*P(x0, y0), *P(x0 + ux * a, y0 + uy * a))
            if b < seg_len - 0.001:
                pdf.line(*P(x0 + ux * b, y0 + uy * b), *P(x1, y1))
            mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            tx2, ty2 = P(mx, my)
            rot = -90 if abs(dy) > abs(dx) else 0
            if rot:
                with pdf.rotation(rot, tx2, ty2):
                    _label(tx2, ty2, label)
            else:
                _label(tx2, ty2, label)

    active = set(geo.layers)
    for seg in geo.segments:
        if LAYER_OF_KIND.get(seg.kind) not in active:
            continue
        if seg.kind == "dimension":
            draw_dimension(seg)
        else:
            draw_segment(seg)

    # 标题（灰色，顶部左侧，与 reportlab 原版位置一致）
    if title:
        pdf.set_font(font_name, size=8)
        pdf.set_text_color(77, 77, 77)
        pdf.text(10, 10, title)

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# DXF 输出（ezdxf）
# ---------------------------------------------------------------------------
'''


def brace_balance(text: str) -> bool:
    """粗校验(){}[]成对，忽略字符串；足够发现结构性破坏即可。"""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    in_str = False
    prev = ""
    for ch in text:
        if in_str:
            if ch == prev:
                in_str = False
            continue
        if ch in ("'", '"'):
            if not in_str:
                in_str = True
                prev = ch
            continue
        if ch in pairs:
            stack.append(ch)
        elif ch in pairs.values():
            if not stack or pairs[stack.pop()] != ch:
                return False
    return not stack


def main() -> int:
    src = open(SRC, encoding="utf-8").read()
    if "from fpdf import FPDF" in src:
        print("patch already applied, skipping")
        return 0
    start = src.index("def _ensure_cjk_font() -> str | None:")
    end = src.index("def geometry_to_dxf_bytes")
    new_src = src[:start] + NEW_BLOCK + "\n\n\n" + src[end:]

    with open(SRC, "w", encoding="utf-8") as f:
        f.write(new_src)

    # 验证 1: 大括号平衡
    if not brace_balance(new_src):
        print("FAIL brace_balance")
        return 1
    # 验证 2: 语法
    cp = subprocess.run([sys.executable, "-m", "py_compile", SRC], capture_output=True, text=True)
    if cp.returncode != 0:
        print("FAIL py_compile:", cp.stderr)
        return 1
    # 验证 3: 实际生成 PDF 头
    import_module = f"""
import subprocess, sys
sys.path.insert(0, {ROOT!r})
from diecut_engine import build_airplane_box, build_insertion_box, geometry_to_pdf_bytes
for name, build in (("airplane", build_airplane_box), ("insertion", build_insertion_box)):
    g = build(200,150,60,3) if name=="airplane" else build(45,45,104,0.5)
    b = geometry_to_pdf_bytes(g, f"{{name}} test")
    print(name, b[:4] == b"%PDF", len(b))
"""
    rp = subprocess.run([sys.executable, "-c", import_module], capture_output=True, text=True)
    if rp.returncode != 0:
        print("FAIL pdf-smoke:", rp.stderr)
        print("stdout:", rp.stdout)
        return 1
    print("OK patch applied; pdf smoke:")
    print(rp.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
