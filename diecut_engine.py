# -*- coding: utf-8 -*-
"""
自锁式飞机盒（Self-locking Mailer Box）刀版生成引擎。

参考图结构（从上到下 5 层竖排）：
  1. 插舌 Tuck Flap        —— 顶部，两侧带半圆形防尘耳
  2. 盖子顶面 Lid Panel    —— 两侧为梯形斜切盖翼（机翼）
  3. 后壁 Back Wall        —— 两侧小防尘翼
  4. 底面 Bottom Panel     —— 两侧巨大的左右侧壁，外缘台阶状锯齿互锁
  5. 前壁 Front Wall       —— 两侧矩形锁扣翼，插入侧壁切口，自锁免胶

输出：
  - cut    : 模切线（外轮廓 / 分刀线）
  - crease : 压痕线（折叠线）
PDF / DXF / SVG 三种输出，均为 1:1 毫米制，不需要 LibreOffice。
"""

from __future__ import annotations

import io
import math
import os
from dataclasses import dataclass, field
from typing import List, Tuple

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    """一段刀线。kind: 'cut' 或 'crease'，points 为折线顶点。

    cut 标记仅用于折痕线：当某段折痕与分刀线共线时，cut=True 表示该处
    同时也是模切线（在 SVG 中按 <g> 分层渲染，避免视觉重叠）。
    """
    kind: str
    points: List[Tuple[float, float]]
    cut: bool = False


@dataclass
class DieCutGeometry:
    """完整的刀版几何。"""
    length: float          # 内尺寸长 L（mm，横向列宽）
    width: float           # 内尺寸宽 W（mm，底面高度）
    height: float          # 内尺寸高 H（mm，盒高）
    thickness: float       # 纸板厚度 t（mm）
    wall_height: float     # 壁展开尺寸 = 盒内高 H（前/后壁、大侧壁内段，厚度在盒外）
    bottom_height: float   # 底面高度 = 制造宽 W + side_comp（盒宽方向）
    lid_height: float      # 盖面高度（盖宽） = 制造宽 + 纸厚（内盖式，盖沿超盒宽每侧纸厚/2）
    tab_depth: float       # 插舌高度 = H（盒高，梯形圆角 + 两侧耳翼）
    wing_width: float      # 盖翼宽度 = H - t（两折）
    back_flap_width: float # 后壁矩形翼宽度 = H - t（腰部翼）
    lock_width: float      # 前壁锁扣翼宽度 = H - t（底部翼，与腰部翼同尺寸）
    side_inner: float      # 大侧壁内段 = 盒内高 H（侧壁主体，一折）
    side_outer: float      # 大侧壁外段 = 盒高 H（折叠后插入盒底，末端钩 = t）
    fold_seg: float        # 两折翼的插入段长度
    segments: List[Segment] = field(default_factory=list)
    # —— 新增参数化字段（Step 2）——
    corner_radius: float = 0.0        # 盖翼圆角半径（只作用于盒盖两盖翼顶角）
    hook_ratio: float = 0.33          # 凸起钩高度比例（hook_h = W * hook_ratio）
    board_compensation: bool = True   # 是否启用纸厚补偿（内外尺寸换算）
    layers: List[str] = field(default_factory=lambda: ["CUT", "CREASE"])  # 活跃图层
    column_width: float = 0.0         # 前壁/后壁/插舌宽 = 制造长 - 2t
    lid_width: float = 0.0            # 盖面宽 = 制造长 - 8t
    side_height: float = 0.0          # 侧壁内段制造宽（网坐标 Y 方向）= width + side_comp
    window_rect: List[Tuple[float, float]] | None = None  # 段3开窗矩形（网坐标闭合多边形，4 角）；None=无窗/关窗

    @property
    def bounds(self) -> Tuple[float, float, float, float]:
        """返回 (min_x, min_y, max_x, max_y)。"""
        xs = [p[0] for s in self.segments for p in s.points]
        ys = [p[1] for s in self.segments for p in s.points]
        return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# 几何计算
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def build_airplane_box(
    length: float,
    width: float,
    height: float,
    thickness: float,
    internal: bool = True,
    tab_depth: float | None = None,
    fold_ratio: float = 0.3,
    lock_ratio: float = 1.0,
    corner_radius: float = 0.0,
    tab_ear_radius: float = 0.0,
    hook_ratio: float = 0.33,
    board_compensation: bool | None = None,
    layers: List[str] | None = None,
    fb_comp: float | None = None,
    side_comp: float | None = None,
) -> DieCutGeometry:
    """
    构建自锁式飞机盒刀版几何（按折叠逻辑严格推算尺寸）。

    折叠逻辑：
      - 列宽（横向） = 盒长 L
      - 底面高度 = 制造宽 W + side_comp（盒底与后腰折线 → 盒底与前端折线）
      - 前壁 / 后壁 = 盒内高 H（厚度在盒外，从底面折起 90°，一折）
      - 盖子顶面高度 = 盒宽 W（平面盖面，盖住盒顶）
      - 插舌高度 = 盒高 H，直角三角形 + 顶角圆弧化
      - 底面大侧壁宽度 = 盒高 H（内段 / 外段 = H，末端凸起钩 = 纸厚 t）
      - 盖翼（机翼）/ 后壁矩形翼宽度 = H - t（两折）

     参数：
      length / width / height : 长 / 宽 / 高（mm）
      thickness               : 纸板厚度（mm）
      internal                : True 内尺寸，False 外尺寸
      tab_depth               : 插舌深度（mm），默认 20
      fold_ratio              : 两折翼插入段占总宽比例，默认 0.3
      lock_ratio              : 锁扣翼宽度比例，默认 1.0
      corner_radius           : 盖翼圆角半径（mm），默认 0（关闭，保持直角）
      hook_ratio              : 凸起钩高度比例，默认 0.33（= W/3.0 行为）
      board_compensation      : 纸厚补偿开关，默认 None（沿用 internal）
      layers                  : 活跃图层列表，默认 ["CUT", "CREASE"]
    """
    L = float(length)
    W = float(width)
    H = float(height)
    t = float(thickness)

    if min(L, W, H, t) <= 0:
        raise ValueError("长、宽、高、纸厚必须为正数")

    # 纸厚补偿开关：若显式给出则覆盖 internal
    if board_compensation is not None:
        internal = bool(board_compensation)

    if not internal:
        # 外尺寸 → 制造尺寸：长扣 2t、宽扣 3t（含顶部插舌+前后腰三层材料厚）、高扣 2t（顶底两层）
        L = max(L - 2 * t, 1.0)
        W = max(W - 3 * t, 1.0)
        H = max(H - 2 * t, 1.0)

    # 立体几何尺寸（内尺寸 L×W×H 为基准，纸厚 t，壁从底面内表面折起）：
    #   - 前壁/后壁展开高 = 盒内高 H（壁内表面 z∈[0,H]，厚度在盒外）
    #   - 侧壁内段高 = H；外段 = H - t（折入贴盒底，厚度占用 t）
    #   - 盖翼/后壁翼/锁扣翼 = H - t（折入贴壁，厚度占用 t）
    Hw = H                                      # 前后壁展开高度 = 盒内高
    tab = max(float(tab_depth) if tab_depth else H, 4.0)   # 插舌深度 = 盒高 H
    wing_w = max(H - t, 4.0)                    # 盖翼宽度（两折 = H - t）
    back_w = max(H - t, 4.0)                    # 后壁矩形翼宽度（腰部翼 = H - t）
    lock_w = max((H - t) * float(lock_ratio), 4.0)   # 前壁锁扣翼（底部翼 = 腰部翼尺寸 H - t）
    side_inner = Hw                             # 大侧壁内段（侧壁主体 = 盒内高）
    gap = 3.0 * t                               # 间隙段（3t，含侧壁厚度；折叠后净空隙 2t 容纳腰部翼/耳翼厚度）
    side_outer = side_inner - t                 # 大侧壁插入段（始终比内段窄 t，折回后钩端到达盒底）
    side_total = side_inner + gap + side_outer  # 大侧壁总宽（内段 + 间隙段 + 插入段）
    fold_seg = max(6.0, wing_w * float(fold_ratio))   # 两折翼插入段
    fold_seg = min(fold_seg, wing_w - 2.0)
    wing_fold = wing_w - fold_seg               # 盖翼折线位置
    back_fold = back_w - fold_seg               # 后壁翼折线位置
    slant_w = min(wing_w * 0.3, 0.15 * L)       # 盖翼梯形斜切
    tab_slant = min(0.08 * L, 12.0)             # 插舌梯形斜切
    tab_r = _clamp(min(4.0, tab * 0.25), 1.5, 6.0)   # 插舌顶部圆角半径
    tab_ear_w = wing_w                          # 插舌翼横向 = 盖翼 = 锁扣翼 = H - t（等腰梯形）
    tab_ear_slant = min(12.0, tab * 0.2)        # 插舌翼等腰梯形斜切
    back_slant = min(15.0, back_w * 0.3)        # （后壁翼已改矩形，此值保留备用）
    hook_ratio = _clamp(float(hook_ratio), 0.2, 0.5)
    hook_d = t                                  # 大侧壁外段插舌长度 = 材料厚度（穿过盒底插口）
    hook_h = W * hook_ratio                    # 插舌末端凸起钩高度（居中 1 个，可调比例）

    # 圆角半径安全钳制：不能大于插舌翼宽，也不能让插舌顶边变成负宽
    len_ear = math.hypot(tab_ear_w, tab_ear_slant)
    corner_radius = _clamp(
        float(corner_radius),
        0.0,
        max(0.0, min(tab_ear_w, len_ear * 0.9, L / 2.0 - 0.5)),
    )

    side_comp = float(side_comp) if side_comp is not None else 0.0
    # 翼根交汇处外凸圆喙圆角半径：约纸厚量级，两翼根部"水平+竖直"转折处圆成朝外的小圆喙
    # （左右镜像共用同一半径，保证轴对称；无折线、无燕尾尖刺）
    beak_r = _clamp(t * 0.7, 1.0, 3.5)

    # 纵向分层（从下到上）
    y0 = 0.0
    bottom_h = W + side_comp              # 盒底制造宽（盒底与后腰折线 → 盒底与前端折线）
    lid_h = bottom_h + t                  # 盖面制造宽 = 盒底宽 + 1t（内盖式，盖沿超盒宽每侧 t/2）
    y1 = Hw                       # 前壁顶 / 盒底前端折线
    y2 = Hw + bottom_h            # 底面顶 = 制造宽（盒底与后腰折线），与侧壁内段顶边共线
    y3 = y2 + Hw                  # 后壁顶
    y4 = y3 + lid_h               # 盖子顶面顶（高度 = 盖面制造宽 lid_h，盖住盒口含纸厚）
    y5 = y4 + tab                 # 插舌顶（高度 = 盒高 H）
    y2_side = y2                  # 内段顶边 = 底面顶（制造宽），不侵入后腰翼
    y_in_lo = y1 + side_comp / 2.0    # 外段底边（相对内段居中缩进 side_comp/2）
    y_in_hi = y2 - side_comp / 2.0    # 外段顶边（相对内段居中缩进 side_comp/2）
    y2_base = y_in_hi             # 兼容引用：外段顶边

    fb_comp = float(fb_comp) if fb_comp is not None else 0.0
    Lx = L + fb_comp                 # 制造长基准（盒外）= 内长 + 补偿（默认 0 = 内盖式制造长）
    colW = Lx - 2.0 * t              # 前壁/后壁/插舌宽（腰部）= 制造长 - 2t
    lidW = Lx - 8.0 * t              # 盖面宽 = 制造长 - 8t
    # 各面板居中偏置（左右对称，两侧同时伸缩）
    # 底面 = 制造尺寸 Lx（含纸厚补偿），前/后壁 = Lx-2t，盖面 = Lx-8t
    ofs_b = (colW - Lx) / 2.0           # 底面居中（底面 = 制造尺寸 Lx）
    ofs_l = (colW - lidW) / 2.0         # 盖面居中

    segments: List[Segment] = []

    def poly(kind: str, pts: List[Tuple[float, float]], cut: bool = False) -> None:
        clean: List[Tuple[float, float]] = []
        for point in pts:
            if not clean or point != clean[-1]:
                clean.append(point)
        if len(clean) >= 2:
            segments.append(Segment(kind, clean, cut=cut))

    def arc_pts(cx: float, cy: float, r: float, t0: float, t1: float, n: int = 10) -> List[Tuple[float, float]]:
        """以 (cx,cy) 为圆心、半径 r 的圆弧，t0..t1 弧度。"""
        out = []
        for i in range(n + 1):
            th = t0 + (t1 - t0) * i / n
            out.append((cx + r * math.cos(th), cy + r * math.sin(th)))
        return out

    def rounded_corner(prev: Tuple[float, float], corner: Tuple[float, float],
                       nxt: Tuple[float, float], r: float) -> List[Tuple[float, float]]:
        """凸角圆弧化（外轮廓为 CW 走向，材料在右侧）。

        返回替换 corner 的圆弧点列（含两端点 p1/p2）；若 r<=0 或几何退化则原样返回 [corner]。
        圆弧中心 C 满足到两边距离均为 r（内移法线），两端点为 C 到两边的垂足（切点）。
        """
        if r <= 0:
            return [corner]
        v1x, v1y = corner[0] - prev[0], corner[1] - prev[1]
        v2x, v2y = nxt[0] - corner[0], nxt[1] - corner[1]
        len1 = math.hypot(v1x, v1y)
        len2 = math.hypot(v2x, v2y)
        if len1 < 1e-9 or len2 < 1e-9:
            return [corner]
        u1 = (v1x / len1, v1y / len1)
        u2 = (v2x / len2, v2y / len2)
        # 右侧法向（CW 路径，材料在右）：旋转 90° → (dy, -dx)
        n1 = (u1[1], -u1[0])
        n2 = (u2[1], -u2[0])
        # 求圆心 C：(C-corner)·n1 = r 且 (C-corner)·n2 = r
        det = n1[0] * n2[1] - n1[1] * n2[0]
        if abs(det) < 1e-9:
            return [corner]
        dx = (r * n2[1] - r * n1[1]) / det
        dy = (n1[0] * r - n2[0] * r) / det
        cx = corner[0] + dx
        cy = corner[1] + dy
        # 切点：C 到两边的垂足
        t1 = (cx - corner[0]) * u1[0] + (cy - corner[1]) * u1[1]
        t2 = (cx - corner[0]) * u2[0] + (cy - corner[1]) * u2[1]
        if t1 > 0 or t2 < 0:
            # 切点落在角外侧（凹角），不圆角化
            return [corner]
        p1 = (corner[0] + t1 * u1[0], corner[1] + t1 * u1[1])
        p2 = (corner[0] + t2 * u2[0], corner[1] + t2 * u2[1])
        if abs(math.hypot(cx - p1[0], cy - p1[1]) - r) > 0.05:
            return [corner]
        a1 = math.atan2(p1[1] - cy, p1[0] - cx)
        a2 = math.atan2(p2[1] - cy, p2[0] - cx)
        # 凸角：圆弧走 p1→p2 较短一侧（材料内部）。CP>0 走 CCW，CP<0 走 CW。
        cp = (p1[0] - cx) * (p2[1] - cy) - (p1[1] - cy) * (p2[0] - cx)
        if cp > 0:
            if a1 > a2:
                a2 += 2 * math.pi
            return arc_pts(cx, cy, r, a1, a2, 12)

        else:
            if a1 < a2:
                a1 += 2 * math.pi
            return arc_pts(cx, cy, r, a1, a2, 12)

    def rounded_polyline(points: List[Tuple[float, float]], radius: float,
                         rounded_indices: set[int]) -> List[Tuple[float, float]]:
        """对折线指定拐角圆角化，并去除相邻重复点。"""
        result: List[Tuple[float, float]] = []
        last_index = len(points) - 1
        for index, point in enumerate(points):
            if 0 < index < last_index and index in rounded_indices:
                replacement = rounded_corner(
                    points[index - 1], point, points[index + 1], radius
                )
            else:
                replacement = [point]
            for replacement_point in replacement:
                if not result or replacement_point != result[-1]:
                    result.append(replacement_point)
        return result

    def side_hooks(x_out: float, y_a: float, y_b: float, up: bool) -> List[Tuple[float, float]]:
        """大侧壁外段（插入底部的插舌）末端：一个居中凸起钩（左右对称）。"""
        pts: List[Tuple[float, float]] = []
        ylo, yhi = min(y_a, y_b), max(y_a, y_b)
        span = yhi - ylo
        c = ylo + span / 2.0
        pts.append((x_out, y_a))
        if up:
            pts.append((x_out, c - hook_h / 2.0))
            pts.append((x_out - hook_d, c - hook_h / 2.0))
            pts.append((x_out - hook_d, c + hook_h / 2.0))
            pts.append((x_out, c + hook_h / 2.0))
        else:
            pts.append((x_out, c + hook_h / 2.0))
            pts.append((x_out + hook_d, c + hook_h / 2.0))
            pts.append((x_out + hook_d, c - hook_h / 2.0))
            pts.append((x_out, c - hook_h / 2.0))
        pts.append((x_out, y_b))
        return pts

    def left_side_points() -> List[Tuple[float, float]]:
        """左侧轮廓，从 (0,y0) 到 (0,y4)。"""
        pts: List[Tuple[float, float]] = []
        # 前壁锁扣翼（底部翼 = 腰部翼尺寸 H-t；沿主面板方向上下各缩 t 顺畅插入盒内）
        pts += [(0.0, y0), (0.0, y0 + t), (-lock_w, y0 + t), (-lock_w, y1 - t), (0.0, y1 - t), (0.0, y1)]
        pts.append((0.0, y_in_lo))                  # 前壁顶 → 外段底边台阶（竖直，避免斜线）
        # 底面左侧壁（内段 + 外段插舌，末端凸起钩；外段相对内段垂直居中缩进 side_comp/2）
        pts.append((ofs_b - side_total, y_in_lo))   # 外段底边（水平）
        pts += side_hooks(ofs_b - side_total, y_in_lo, y_in_hi, up=True)
        pts.append((ofs_b - side_inner, y_in_hi))   # 外段顶边（居中缩进）
        pts.append((ofs_b - side_inner, y2_side))   # 内段台阶（内段全高）
        pts.append((ofs_b, y2_side))                # 内段顶边（= 制造宽顶）
        pts.append((0.0, y2_side))                  # 内段顶边 → 后壁左缘（水平）
        pts.append((0.0, y2 + t))                   # 后壁左缘底部缺口（竖直）
        # 后壁矩形翼（腰部翼，矩形 = 前壁锁扣翼；上下各缩 t，竖直升/降边避免斜线）
        pts += [(-back_w, y2 + t), (-back_w, y3 - t), (0.0, y3 - t)]
        # 外凸圆喙交汇（轴对称）：后腰翼顶边(0,y3-t) → 水平到盖翼左缘(ofs_l,y3-t) → 竖直到盖翼底边(ofs_l,y3+t)
        pts += rounded_polyline([(0.0, y3 - t), (ofs_l, y3 - t), (ofs_l, y3 + t)], beak_r, {1})
        # 盖面盖翼：左右外侧拐角统一圆角化，形成完整等腰梯形（上下各缩 t）
        pts += rounded_polyline(
            [(ofs_l, y3 + t), (ofs_l - wing_w, y3 + t + slant_w),
             (ofs_l - wing_w, y4 - t - slant_w), (ofs_l, y4 - t)],
            corner_radius,
            {1, 2},
        )
        # 外凸圆喙交汇（朝盒内 +x 凸，轴对称）：盖翼顶边(ofs_l,y4-t) → 耳翼底边(0,y4+t)
        pts += rounded_polyline([(ofs_l, y4 - t), (ofs_l, y4 + t), (0.0, y4 + t)], beak_r, {1})
        return pts

    def tuck_outline() -> List[Tuple[float, float]]:
        """插舌本体与左右耳翼的统一对称外轮廓（耳翼为直角三角形，底边水平，顶角圆角由 tab_ear_radius 控制）。"""
        apex_r = _clamp(tab_ear_radius, 0.0, tab_ear_w * 0.5)
        outline = [
            (0.0, y4 + t),                            # 左耳翼底角（直角，插舌左壁底）
            (-tab_ear_w, y4 + t),                     # 左耳翼外底角（水平伸出）
            (0.0, y5 - t),                            # 左耳翼顶角（收回插舌左壁顶）
            (colW, y5 - t),                           # 右耳翼顶角
            (colW + tab_ear_w, y4 + t),               # 右耳翼外底角（水平伸出）
            (colW, y4 + t),                           # 右耳翼底角（直角，插舌右壁底）
        ]
        return rounded_polyline(outline, apex_r, {1, 4})

    def right_side_points() -> List[Tuple[float, float]]:
        """右侧轮廓，从 (Lx,y4) 到 (Lx,y0)。"""
        pts: List[Tuple[float, float]] = []
        # 外凸圆喙交汇（轴对称）：耳翼底边(colW,y4+t) → 水平到盖翼右缘(ofs_l+lidW,y4+t) → 竖直到盖翼顶边(ofs_l+lidW,y4-t)
        pts += rounded_polyline([(colW, y4 + t), (ofs_l + lidW, y4 + t), (ofs_l + lidW, y4 - t)], beak_r, {1})
        # 盖面盖翼：左右外侧拐角统一圆角化，形成完整等腰梯形（上下各缩 t）
        pts += rounded_polyline(
            [(ofs_l + lidW, y4 - t), (ofs_l + lidW + wing_w, y4 - t - slant_w),
             (ofs_l + lidW + wing_w, y3 + t + slant_w), (ofs_l + lidW, y3 + t)],
            corner_radius,
            {1, 2},
        )
        # 后壁矩形翼（右，矩形 = 前壁锁扣翼；上下各缩 t，竖直升/降边避免斜线）
        pts.append((ofs_l + lidW, y3 + t))          # 盖翼底边内端
        # 外凸圆喙交汇（朝盒内 -x 凸，轴对称）：盖翼底边(ofs_l+lidW,y3+t) → 后腰翼顶边(colW,y3-t)
        pts += rounded_polyline([(ofs_l + lidW, y3 + t), (ofs_l + lidW, y3 - t), (colW, y3 - t)], beak_r, {1})
        pts += [(colW + back_w, y3 - t), (colW + back_w, y2 + t), (colW, y2 + t)]   # 后腰翼
        pts.append((colW, y2_side))                 # 后壁右缘底部缺口（竖直）
        # 底面右侧壁（内段顶边到外段顶角，向下走到外段底角，末端凸起钩；外段垂直居中缩进）
        pts.append((ofs_b + Lx, y2_side))               # 内段顶角
        pts.append((ofs_b + Lx + side_inner, y2_side))  # 内段|外段分界线顶
        pts.append((ofs_b + Lx + side_inner, y_in_hi))  # 外段顶角（居中缩进）
        pts += side_hooks(ofs_b + Lx + side_total, y_in_hi, y_in_lo, up=False)
        pts.append((colW, y_in_lo))                 # 外段底边 → 前壁顶台阶（水平，避免斜线）
        pts.append((colW, y1))                      # 台阶（竖直）
        # 前壁锁扣翼（右；上下各缩 t，严格镜像左翼：含前壁右缘上下缺口 + 前壁底边角）
        pts += [(colW, y1 - t), (colW + lock_w, y1 - t), (colW + lock_w, y0 + t), (colW, y0 + t), (colW, y0)]
        return pts

    # ---- 外轮廓（模切线）----
    outline = left_side_points() + tuck_outline() + right_side_points() + [(0.0, y0)]
    poly("cut", outline)

    # ---- 压痕线（折叠线）----
    # 主列横向折痕：前壁|底面|后壁|盖面|插舌
    # y1/y2/y3 处与分刀线共线（侧翼切口延伸），标记 cut=True 供分层渲染
    for yy in (y1, y2, y3, y4):
        poly("crease", [(0.0, yy), (colW, yy)], cut=(yy in (y1, y2, y3)))
    # 左/右侧翼与主列连接折痕（一折；折线只画在翼根部范围内，翼上下各缩 t）
    poly("crease", [(0.0, y0 + t), (0.0, y1 - t)])      # 前壁|锁扣翼
    poly("crease", [(ofs_b, y1), (ofs_b, y2)])      # 底面|侧壁
    poly("crease", [(0.0, y2 + t), (0.0, y3 - t)])      # 后壁|矩形翼
    poly("crease", [(ofs_l, y3 + t), (ofs_l, y4 - t)])      # 盖面|盖翼
    poly("crease", [(0.0, y4 + t), (0.0, y5 - t)])      # 插舌|左翼（翼内边，闭合）
    poly("crease", [(colW, y0 + t), (colW, y1 - t)])      # 前壁|锁扣翼（右）
    poly("crease", [(ofs_b + Lx, y1), (ofs_b + Lx, y2)])      # 底面|侧壁（右）
    poly("crease", [(colW, y2 + t), (colW, y3 - t)])      # 后壁|矩形翼（右）
    poly("crease", [(ofs_l + lidW, y3 + t), (ofs_l + lidW, y4 - t)])      # 盖面|盖翼（右）
    poly("crease", [(colW, y4 + t), (colW, y5 - t)])      # 插舌|右翼（翼内边，闭合）
    # 两折翼内部折线（随翼高收缩）
    poly("crease", [(ofs_l - wing_fold, y3 + t), (ofs_l - wing_fold, y4 - t)])
    poly("crease", [(ofs_l + lidW + wing_fold, y3 + t), (ofs_l + lidW + wing_fold, y4 - t)])
    poly("crease", [(-back_fold, y2 + t), (-back_fold, y3 - t)])
    poly("crease", [(colW + back_fold, y2 + t), (colW + back_fold, y3 - t)])
    # 大侧壁内部折线（内段|间隙段|插入段，折叠后外段距内段 2t 空隙）
    poly("crease", [(ofs_b - side_inner, y1), (ofs_b - side_inner, y2_side)])
    poly("crease", [(ofs_b + Lx + side_inner, y1), (ofs_b + Lx + side_inner, y2_side)])
    poly("crease", [(ofs_b - side_inner - gap, y_in_lo), (ofs_b - side_inner - gap, y_in_hi)])
    poly("crease", [(ofs_b + Lx + side_inner + gap, y_in_lo), (ofs_b + Lx + side_inner + gap, y_in_hi)])

    # ---- 分刀线（相邻侧翼之间切开）----
    # 两翼收缩后，翼边界已由外轮廓定义，分刀线在原 y1/y2/y3 悬空，故不再生成

    # ---- 底面侧壁凸钩对应的插口 ----
    # 插口沿盒宽方向开刀，位置与左右侧壁凸钩中心对齐，并关于底面中心镜像。
    # 插口为封闭矩形，短边使用纸板厚度，避免单刀线无法形成实际开口。
    slot_center = (y1 + y2) / 2.0
    slot_low = slot_center - hook_h / 2.0
    slot_high = slot_center + hook_h / 2.0
    slot_center_x = min(hook_d, colW / 2.0)
    slot_half_width = min(t / 2.0, slot_center_x, (colW / 2.0) - slot_center_x)
    # 插孔对齐插入段折叠后的落点（底面内移 gap，即距内段 gap 处，关于刀版中心对称）
    left_slot_c = ofs_b + gap
    right_slot_c = ofs_b + Lx - gap
    left_slot_x0 = left_slot_c - slot_half_width
    left_slot_x1 = left_slot_c + slot_half_width
    right_slot_x0 = right_slot_c - slot_half_width
    right_slot_x1 = right_slot_c + slot_half_width
    poly("cut", [
        (left_slot_x0, slot_low), (left_slot_x1, slot_low),
        (left_slot_x1, slot_high), (left_slot_x0, slot_high),
        (left_slot_x0, slot_low),
    ])
    poly("cut", [
        (right_slot_x0, slot_low), (right_slot_x1, slot_low),
        (right_slot_x1, slot_high), (right_slot_x0, slot_high),
        (right_slot_x0, slot_low),
    ])

    # ---- 可选图层：防尘耳半切线（HALFCUT）----
    if layers and "HALFCUT" in layers:
        # 左右插舌耳翼中线半切线，便于撕除防尘耳（随翼收缩）
        poly("halfcut", [(-tab_ear_w * 0.5, y4 + t), (-tab_ear_w * 0.5, y5 - t)])
        poly("halfcut", [(colW + tab_ear_w * 0.5, y4 + t), (colW + tab_ear_w * 0.5, y5 - t)])

    # ---- 可选图层：制造尺寸标注线（DIMENSION）----
    # 长(盒底左右折线间)=制造长 Lx；宽(盒底上下折线间)=制造宽 W+side_comp；
    # 高(后腰上下折线间)=后腰展开高 H。
    # 线画在对应面板内/边缘，中央断口嵌生产尺寸数字(mm)+两端箭头，颜色蓝。
    if layers and "DIMENSION" in layers:
        dim_margin = 6.0
        # 长（制造长 Lx）：盒底左右两条折线之间，水平线横跨盒底下缘外侧
        poly("dimension", [(ofs_b, y1 - dim_margin), (ofs_b + Lx, y1 - dim_margin)])
        # 宽（制造宽 = W + side_comp）：盒底上下两条折线之间，竖直线贯穿盒底中央
        poly("dimension", [(ofs_b + Lx / 2.0, y1), (ofs_b + Lx / 2.0, y2)])
        # 高（后腰展开高 H）：后腰上下两条折线之间，竖直线贯穿后腰中央
        poly("dimension", [(colW / 2.0, y2), (colW / 2.0, y3)])
        # 盖长（盖面制造宽 = lidW，内盖式）：盖面两条竖折线之间，水平线置于盖面下缘外侧
        poly("dimension", [(ofs_l, y3 + dim_margin), (ofs_l + lidW, y3 + dim_margin)])
        # 盖宽（盖面制造宽 = lid_height）：盖面两条横折线（y3→y4）之间，竖直线贯穿盖面中央
        poly("dimension", [(ofs_l + lidW / 2.0, y3), (ofs_l + lidW / 2.0, y4)])

    return DieCutGeometry(
        length=L,
        width=W,
        height=H,
        thickness=t,
        wall_height=Hw,
        bottom_height=bottom_h,   # 盒底宽度 = 制造宽（盒底与后腰折线 → 盒底与前端折线）
        lid_height=lid_h,
        tab_depth=tab,
        wing_width=wing_w,
        back_flap_width=back_w,
        lock_width=lock_w,
        side_inner=side_inner,
        side_outer=side_outer,
        fold_seg=fold_seg,
        segments=segments,
        corner_radius=corner_radius,
        hook_ratio=hook_ratio,
        board_compensation=internal,
        layers=list(layers) if layers else ["CUT", "CREASE"],
        column_width=colW,
        lid_width=lidW,
        side_height=bottom_h,
    )


# ---------------------------------------------------------------------------
# 插口盒（插入式圆筒盒，45×45×104 内尺寸样例）
# ---------------------------------------------------------------------------

def _dedupe(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """去除相邻重复点（贝塞尔拼接端点重合会产生），避免 validate_geometry 报连续重复点。"""
    out: List[Tuple[float, float]] = []
    for p in points:
        if not out or abs(out[-1][0] - p[0]) > 1e-9 or abs(out[-1][1] - p[1]) > 1e-9:
            out.append(p)
    return out


def _arc(cx: float, cy: float, r: float, a0: float, a1: float, n: int = 10):
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / n),
             cy + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]


def _bez(p0, p1, p2, p3, n: int = 12):
    """三次贝塞尔：盖翼顶弧用，复刻原图曲率（凸/凹与原图一致）。"""
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        x = u**3 * p0[0] + 3*u*u*t * p1[0] + 3*u*t*t * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3*u*u*t * p1[1] + 3*u*t*t * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def _cchain(cubics, n: int = 12):
    """连段三次贝塞尔（跨界 S 弧用）。cubics: [(p0,c1,c2,p1),(p1,c3,c4,p2),...]。"""
    pts = []
    for (p0, c1, c2, p1) in cubics:
        for i in range(n + 1):
            t = i / n; u = 1 - t
            x = u**3 * p0[0] + 3*u*u*t * c1[0] + 3*u*t*t * c2[0] + t**3 * p1[0]
            y = u**3 * p0[1] + 3*u*u*t * c1[1] + 3*u*t*t * c2[1] + t**3 * p1[1]
            if i > 0 or pts == []:
                pts.append((x, y))
    return pts


def build_insertion_box(
    length: float = 45.0,
    width: float = 45.0,
    height: float = 104.0,
    thickness: float = 2.0,
    internal: bool = True,
    window_width: float = 25.0,
    window_height: float = 60.0,
    window_top_offset: float = 20.0,
    layers: List[str] | None = None,
) -> DieCutGeometry:
    """构建插入式插口盒（Insertion Slot Box）刀版几何。

    参考原图（42×45×104mm 挂烫熨盒，CAD 刀线 SVG）逐点复刻，几何以主柱局部
    mm 计：主柱底 = y0（0，向上为正），主柱左缘 = x0。四段周向边界
    s0..s4 = 0, L, L+W, 2L+W, 2L+2W（段1/段3=长 L，段2/段4=宽 W）。
    段1=锁舌/顶部插舌，段2/段4=斜切镜像翼，段3=开窗 + 底部圆弧穹。
    参考基准段宽 45，45×45 时缩放因子=1 逐点复刻不变；长/宽变化时舌/翼水平线
    跟随所在段宽伸缩，**底翼深度参数化**：段1/段3 最深=W/2+插舌、段2/段4 最深
    略小于 L/2，使折叠后三段底翼分别在宽度中线、长度中线交叉封底。

    参数：
      length / width / height : 长 / 宽 / 高（mm）
      thickness               : 纸板厚度（mm）
      internal                : True 内尺寸（原样出），False 外尺寸（扣 2t 折算制造尺寸）
    """
    L = float(length); W = float(width); H = float(height); t = float(thickness)
    if min(L, W, H, t) <= 0:
        raise ValueError("长、宽、高、纸厚必须为正数")
    if not internal:
        L = max(L - 2 * t, 1.0); W = max(W - 2 * t, 1.0); H = max(H - 2 * t, 1.0)

    # 周向四段：段1/段3 = 长(段宽 L)，段2/段4 = 宽(段宽 W)。长=内长、宽=内宽，高=H。
    s0, s1, s2, s3, s4 = 0.0, L, L + W, 2 * L + W, 2 * L + 2 * W
    # 参考基准段宽 45：45×45 参考盒下各缩放因子=1，逐点复刻不变；长/宽变化时
    # 舌/翼水平线跟随所在段宽伸缩，盖翼 wrap 深度跟随盒宽 W，**底翼深度随 W/2、L/2**
    # 参数化（段1/段3=W/2+插舌、段2/段4=L/2-1，见各底部块）。圆角保持不动。
    sf_len = L / 45.0
    sf_wid = W / 45.0
    segs = []
    def crease(pts): segs.append(Segment("crease", _dedupe(pts)))
    def cut(pts):    segs.append(Segment("cut", _dedupe(pts)))
    def poly(kind, pts):
        clean = [p for p in pts if p[0] is not None]
        if len(clean) >= 2:
            segs.append(Segment(kind, clean))

    # ============ 主柱（4 段圆周）============
    for xi in (s0, s1, s2, s3):           # 含段1左缘：与粘边交界的竖折线
        crease([(xi, 0.0), (xi, H)])
    cut([(s4, 0.0), (s4, H)])
    crease([(s0, 0.0), (s4, 0.0)])
    # 主柱顶：段1/段2/段4为折线，段3为刀线（原图段3顶 str0 blue）
    # 段1顶盖折线比段2/段4高一个纸厚 t（原图实测 104.00 vs 103.50）：盖折叠在段2/段4两上翼之上，需让出纸厚
    crease([(s0, H + t), (s1, H + t)])
    crease([(s1, H), (s2, H)])
    cut([(s2, H), (s3, H)])
    crease([(s3, H), (s4, H)])

    # ============ 左侧周向粘边（梯形，上下斜切收口）============
    STUB, TAPER = 15.0, 5.0
    if STUB > 0:
        sx = s0 - STUB
        cut([(s0, H), (sx, H - TAPER), (sx, TAPER), (s0, 0.0)])

    # ============ 段3 开窗（左右居中；宽/高自定义、垂直位置以段3顶为基线）============
    # 宽度上限 = 段3壁宽 L - 20mm（不顶到两侧边距）；高度不越出段3底（窗口底>=主柱底0）。
    # default 25/60/顶偏移20 → 窗口(24..84) 与复刻原图一致，45×45×104 参考盒 bit-identical。
    wcx = (s2 + s3) / 2.0
    WW = max(0.0, min(float(window_width), L - 20.0))
    top_off = float(window_top_offset)           # 窗口上边到段3顶(y=H)的距离，以顶端为基线
    WH = max(0.0, min(float(window_height), H - top_off))
    WY0 = H - top_off - WH                        # 窗口底到主柱底（顶端基线换算）
    window_rect = None
    if WW > 0.5 and WH > 0.5 and WY0 >= -0.01:
        # 开窗：矩形窗帘孔。写进 segments 供 SVG/刀线渲染；同时记到 geometry.window_rect
        # 供 3D 折叠预览把窗内材料真正 cut 成透孔（见 _insertion_fold_contract 的 i_w3 holes）。
        cut([(wcx - WW / 2, WY0), (wcx - WW / 2, WY0 + WH),
             (wcx + WW / 2, WY0 + WH), (wcx + WW / 2, WY0), (wcx - WW / 2, WY0)])
        window_rect = [(wcx - WW / 2, WY0), (wcx - WW / 2, WY0 + WH),
                       (wcx + WW / 2, WY0 + WH), (wcx + WW / 2, WY0)]

    # ============ 段1 底部凹槽锁舌 ============
    # 凹槽水平线到底部折线的距离=宽一半 W/2（折叠后与段3下翼在宽度中线交叉）；
    # 锁舌再伸出 lock_notch 嵌入段3下翼的凹槽。
    # 凹槽内宽 = 段3插舌平台宽 + 纸厚间隙 = L/2：lock_toe+lock_ls = L/4（参考盒 L=45 → 8.25）
    lock_notch = 9.0; lock_ls = 3.0
    lock_toe = L / 4.0 - lock_ls
    lock_dep = W / 2.0 + lock_notch
    # 左缘 + 左脚底
    cut([(s0, 0.0), (s0, -lock_dep), (s0 + lock_toe, -lock_dep)])
    # 左脚内圆角：凸向下（圆心 (toe,-dep+ls)，从 (toe,-dep) 到 (toe+ls,-dep+ls)）
    cut(_arc(s0 + lock_toe, -lock_dep + lock_ls, lock_ls, math.pi * 1.5, math.pi * 2.0, n=6))
    # 左壁 + 凹槽底 + 右壁
    cut([(s0 + lock_toe + lock_ls, -lock_dep + lock_ls), (s0 + lock_toe + lock_ls, -lock_dep + lock_notch),
         (s1 - lock_toe - lock_ls, -lock_dep + lock_notch), (s1 - lock_toe - lock_ls, -lock_dep + lock_ls)])
    # 右脚内圆角：凸向下（圆心 (s1-toe,-dep+ls)，从 (s1-toe-ls,-dep+ls) 到 (s1-toe,-dep)）
    cut(_arc(s1 - lock_toe, -lock_dep + lock_ls, lock_ls, math.pi, math.pi * 1.5, n=6))
    # 右脚底 + 右缘回主柱底（右缘内缩 0.65，顶端 (s1-0.65,-1.30) 即 s1 跨界弧起点——闭合无悬空）
    cut([(s1 - lock_toe, -lock_dep), (s1 - 0.65, -lock_dep), (s1 - 0.65, -1.30)])

    # ============ 段2/段4 底部单边缓坡斜切镜像翼 ============
    # 折叠后沿盒长 L 方向铺入，长度『略小于 L/2』（留 1mm 缝隙）→ 左右两翼在长度中线交叉封底
    # 插舌段竖线 x=s1+fl_hl（段4 镜像 x=s4-fl_hl）降到水平底 y=-fl_dep，竖线到盒内缘(s2-0.65)
    # 的水平宽度 = W/2 - t（折叠后留 1 纸厚间隙与段3下翼插舌咬合；参考盒 45×45×0.5 → 22.0）。
    # 反推 fl_hl = (W-0.65) - (W/2-t) = W/2 - 0.65 + t；钳制防极窄盒斜肩反转/圆角越界。
    fl_dep = L / 2.0; fl_vd = 12.0; fl_r = 3.0
    fl_hl = max(W / 2.0 - 0.65 + t, 1.43 + fl_r)
    fl_hl = min(fl_hl, W - 0.65 - fl_r)
    # 段1/段2交界：跨界S弧，逐点复刻原图 M2724.6...（浅S贴折线，峰值仅~+0.05）
    xl = s1
    _arc12 = _cchain([((xl - 0.65, -1.30), (xl - 0.65, -1.01), (xl - 0.56, -0.74), (xl - 0.39, -0.52)),
                      ((xl - 0.39, -0.52), (xl + 0.04, 0.055), (xl + 0.86, 0.17), (xl + 1.43, -0.26))])
    cut(_arc12 + [(xl + 1.43, -0.26), (xl + fl_hl, -fl_vd), (xl + fl_hl, -fl_dep + fl_r)])
    cut(_bez((xl + fl_hl, -fl_dep + fl_r), (xl + fl_hl, -fl_dep + fl_r - 1.66),
             (xl + fl_hl + 1.34, -fl_dep), (xl + fl_hl + fl_r, -fl_dep)))         # 凹贝塞尔 腿->底横
    cut([(xl + fl_hl + fl_r, -fl_dep), (s2 - 0.65, -fl_dep), (s2 - 0.65, -0.97)]) # 右壁内缩(89.35)，顶端(89.35,-0.97)
    # 段4：右缘缓坡斜切（镜像）-> 左缘直立
    xr = s4
    cut([(xr - 0.7, 0.0), (xr - fl_hl, -fl_vd), (xr - fl_hl, -fl_dep + fl_r)])
    cut(_bez((xr - fl_hl, -fl_dep + fl_r), (xr - fl_hl, -fl_dep + fl_r - 1.66),
             (xr - fl_hl - 1.34, -fl_dep), (xr - fl_hl - fl_r, -fl_dep)))        # 凹贝塞尔 腿->底横
    # 段3/段4交界：跨界S弧，逐点复刻原图 M6237.35...
    cut(_arc34 := _cchain([((s3 + 0.65, -0.97), (s3 + 0.65, -0.43), (s3 - 0.28, -0.00), (s3 - 0.32, -0.00)),
                           ((s3 - 0.32, -0.00), (s3 - 0.63, -0.00), (s3 - 0.91, -0.15), (s3 - 1.09, -0.40))]))
    cut([(xr - fl_hl - fl_r, -fl_dep), (s3 + 0.65, -fl_dep), (s3 + 0.65, -0.97)] + _arc34[1:])

    # ============ 段3 底部圆弧穹深翼（参数化：竖/横直、圆角相切）============
    # 主体铺到宽度中线 W/2，中央穹(插舌)再伸出 9mm → r_dep=W/2+9；与段1下翼在宽度中线交叉。
    # 插舌 = 两条竖线(颈壁) + 一条水平线(平台) + 两段过渡圆角。L/W 变化时：
    #   ① 颈壁横移 r_rise 随段宽(长) L 同比例伸缩 → 平台宽 = 颈口 - 2r_r 随之伸缩(恒平直)；
    #   ② 肩深 r_sh 固定为参考值 22.5，颈长 = (W/2+9-r_r) - r_sh 随宽 W **伸长**，颈壁竖线
    #      本身随宽变长、插舌占位随 r_dep 跨宽度中线（参考盒 45×45 → 颈长 6 复刻不变）；
    #   ③ 颈壁与平台之间用『定半径四分之一圆弧』相切过渡，r_r 固定 → 竖线恒竖、横线恒平、
    #      圆弧恒切，缩放不弯。W 过小时钳制 r_sh 防止颈长≤0 反转。
    #   （原实现把 r_sh/r_neck/r_rise/r_half 固定为 45×45 参考值、用三次贝塞尔连接，
    #     L/W 偏离参考后穹被拉弯、小盒甚至反转——此处重参数化修复。）
    xm, xM = s2, s3
    r_r    = 3.0                    # 过渡圆角半径：四分之一圆弧，切于颈壁竖线/平台横线
    # 插舌平台宽 = L - 2*(r_rise+r_r) = L/2 - t（留纸厚间隙，凹槽=L/2 插入）：r_rise = L/4 + t/2 - r_r
    # （参考盒 L=45,t=0.5 → 8.5；不再是 12*sf_len 固定，宽度随 L 伸缩、随纸厚留隙）
    r_rise = L / 4.0 + t / 2.0 - r_r
    r_sh   = 22.5                   # 肩深基准（固定；颈长随宽 W 伸长，45→22.5 复刻不变）
    r_dep  = W / 2.0 + 9.0          # 插舌总深度（跨宽度中线 + 9mm 锁扣）
    xnl, xnr = xm + r_rise, xM - r_rise
    # 自适应钳制：两侧圆角不重叠（平台保持正宽），避免长/宽极端时插舌反转
    r_r = max(0.4, min(r_r, (xnr - xnl) / 2.0 - 0.4))
    neck_bot = r_dep - r_r          # 颈壁底部 y = -(r_dep - r_r)（左/右圆角起点）
    r_sh = min(r_sh, neck_bot - 1.0)   # 宽过小致颈长≤0 时压低肩深，保底 1mm 颈长
    # 段2/段3交界：跨界S弧，逐点复刻原图 M4483.15...
    _arc23 = _cchain([((xm - 0.65, -0.97), (xm - 0.65, -0.66), (xm - 0.50, -0.37), (xm - 0.26, -0.19)),
                      ((xm - 0.26, -0.19), (xm + 0.27, 0.13), (xm + 0.80, 0.03), (xm + 1.09, -0.40))])
    # 左侧：S弧 -> 斜肩 -> 竖直颈壁 -> 相切圆角 -> 水平平台
    cut(_arc23 + [(xm + 1.09, -0.40), (xnl, -r_sh), (xnl, -neck_bot)]
        + _arc(xnl + r_r, -neck_bot, r_r, math.pi, math.pi * 1.5, n=6)
        + [(xnl + r_r, -r_dep), (xnr - r_r, -r_dep)])          # 中央平台（水平，随 L 伸缩）
    # 右侧：相切圆角 -> 竖直颈壁 -> 斜肩（镜像）
    cut(_arc(xnr - r_r, -neck_bot, r_r, math.pi * 1.5, math.pi * 2.0, n=6)
        + [(xnr, -neck_bot), (xnr, -r_sh), (xM - 1.09, -0.40)])

    # ============ 段1 顶部插舌（宽颈 + 圆头顶 + 中段折线）============
    neck_rise = 44.5 * sf_wid; neck_cut = 7.5 * sf_len; lid_w2 = 13.0 * sf_len
    lid_rad = 9.0 * sf_len; lid_sh = 6.0; dent = 0.7
    cx1 = (s0 + s1) / 2.0
    nt = H + neck_rise
    # 颈部两侧竖线（宽 = 整段）
    cut([(s0, H), (s0, nt)])
    cut([(s1, H), (s1, nt)])
    # 底部收口缺口（左右各从边缘向内切 neck_cut，位于颈顶）
    cut([(s0, nt), (s0 + neck_cut, nt)])
    cut([(s1 - neck_cut, nt), (s1, nt)])
    # 插舌折线左右端两条竖向刀线：从凹口内角下穿折线到 nt-1.7，形成贴附缝
    cut([(s0 + neck_cut, nt), (s0 + neck_cut, nt - dent - 1.0)])
    cut([(s1 - neck_cut, nt), (s1 - neck_cut, nt - dent - 1.0)])
    # 圆头本体：左肩竖 -> 左弧(顶切线竖直) -> 顶平段 -> 右弧(顶切线竖直) -> 右肩竖
    lsh = cx1 - lid_w2 - lid_rad
    rsh = cx1 + lid_w2 + lid_rad
    # 圆头两侧为外凸三次贝塞尔（复刻原图 M1270.82... 控制点）
    body = [(lsh, nt), (lsh, nt + lid_sh)]
    body += _bez((lsh, nt + lid_sh), (lsh, nt + lid_sh + 5.0),
                 (lsh + 4.03 * sf_len, nt + lid_sh + lid_rad), (cx1 - lid_w2, nt + lid_sh + lid_rad))
    body += [(cx1 + lid_w2, nt + lid_sh + lid_rad)]
    body += _bez((cx1 + lid_w2, nt + lid_sh + lid_rad),
                 (cx1 + lid_w2 + 4.03 * sf_len, nt + lid_sh + lid_rad),
                 (rsh, nt + lid_sh + 5.0), (rsh, nt + lid_sh))
    body += [(rsh, nt + lid_sh), (rsh, nt)]
    cut(body)
    # 中段折线（颈顶下 dent，跨越圆头基部）
    crease([(s0 + neck_cut, nt - dent), (s1 - neck_cut, nt - dent)])

    # ============ 段2/段4 顶部盖翼（不对称鸡冠形，顶弧为三次贝塞尔）============
    def top_wing(xi, xo, mirror):
        sh = H + 14.0                 # 肩横 y
        top = H + 21.5                # 顶平 y
        bw = (xo - xi) / 45.0         # 水平跟随段宽伸缩（45=参考段宽），参考盒 factor=1 复刻不变
        if not mirror:
            pts = _bez((xi, H), (xi, H - 0.55), (xi + 1.5 * bw, H - 0.5),
                       (xi + 2.07 * bw, H + 0.17))                                # 凹谷下凹复刻
            pts += [(xi + 3.0 * bw, H + 2.5), (xi + 3.0 * bw, top), (xi + 29.07 * bw, top),
                    (xi + 29.07 * bw, top - 2.0)]
            pts += _bez((xi + 29.07 * bw, top - 2.0), (xi + 29.07 * bw, top - 5.0),
                        (xi + 31.5 * bw, sh), (xi + 34.57 * bw, sh))
            pts += [(xi + 40.52 * bw, sh), (xi + 42.0 * bw, H + 8.5), (xi + 44.5 * bw, H + 5.5),
                    (xi + 44.5 * bw, H)]
        else:
            pts = [(xo - 0.67 * bw, H), (xo - 3.0 * bw, H + 2.5), (xo - 3.0 * bw, top),
                   (xo - 29.07 * bw, top), (xo - 29.07 * bw, top - 2.0)]
            pts += _bez((xo - 29.07 * bw, top - 2.0), (xo - 29.07 * bw, top - 5.0),
                        (xo - 31.5 * bw, sh), (xo - 34.57 * bw, sh))
            pts += [(xo - 40.52 * bw, sh), (xo - 42.0 * bw, H + 8.5), (xo - 44.5 * bw, H + 5.5),
                    (xo - 44.5 * bw, H)]
        cut(pts)
    top_wing(s1, s2, False)
    top_wing(s3, s4, True)

    # ---- 可选图层：制造尺寸标注线（DIMENSION）----
    # 插口盒水平四段（段1/段3=长 L、段2/段4=宽 W）；主柱高 H。
    # 尺寸线置于主柱下方空白带（避开底翼下探区）与左侧粘边外（避让刀线）：
    #   长 L=段1 span（s0→s1）、宽 W=段2 span（s1→s2）、高 H=主柱竖向（0→H）。
    # 线画在对应面板外沿，中央断口嵌生产尺寸数字(mm)+两端箭头，颜色蓝。
    if layers and "DIMENSION" in layers:
        dim_margin = 6.0
        dim_y = -(W / 2.0 + 9.0) - dim_margin   # 主柱下方，低于底翼最深处 (fl_dep=W/2+9)
        poly("dimension", [(s0, dim_y), (s1, dim_y)])          # 长 L
        poly("dimension", [(s1, dim_y), (s2, dim_y)])          # 宽 W
        poly("dimension", [(s0 - STUB - dim_margin, 0.0), (s0 - STUB - dim_margin, H)])  # 高 H（左侧粘边外）

    return DieCutGeometry(
        length=L, width=W, height=H, thickness=t,
        wall_height=H, bottom_height=W, lid_height=W, tab_depth=0,
        wing_width=0, back_flap_width=0, lock_width=0,
        side_inner=H, side_outer=0, fold_seg=0,
        segments=segs,
        corner_radius=0, hook_ratio=0, board_compensation=internal,
        layers=list(layers) if layers else ["CUT", "CREASE"],
        column_width=L, lid_width=W, side_height=W,
        window_rect=window_rect,
    )


def validate_geometry(geo: DieCutGeometry) -> List[str]:
    """检查几何对象是否适合导出和拼版。"""
    errors: List[str] = []
    for index, segment in enumerate(geo.segments):
        if len(segment.points) < 2:
            errors.append(f"segment[{index}] 少于两个点")
        for point in segment.points:
            if not all(math.isfinite(value) for value in point):
                errors.append(f"segment[{index}] 包含非有限坐标")
        if any(first == second for first, second in zip(segment.points, segment.points[1:])):
            errors.append(f"segment[{index}] 包含连续重复点")
    min_x, min_y, max_x, max_y = geo.bounds
    if not (min_x < max_x and min_y < max_y):
        errors.append("刀版边界无效")
    return errors


def _near_pt(a, b, tol=1e-5):
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def _chain_cut(polys):
    """把若干 cut 折线段按端点连续性串成一条全局坐标闭合多边形（供底翼多段拼合）。

    若某段没有端点与当前链衔接，则按原始顺序顺连兜底。所有背链段共用同一切段结构，
    因而总能串出一条封闭轮廓。
    """
    n = len(polys)
    order = None
    for start in range(n):
        seq = [start]
        used = [False] * n
        used[start] = True
        cur = polys[start][-1]
        changed = True
        while changed:
            changed = False
            for j in range(n):
                if used[j]:
                    continue
                if _near_pt(polys[j][0], cur):
                    cur = polys[j][-1]
                    seq.append(j)
                    used[j] = True
                    changed = True
                    break
                if _near_pt(polys[j][-1], cur):
                    cur = polys[j][0]
                    seq.append(j)
                    used[j] = True
                    changed = True
                    break
        if all(used):
            order = seq
            break
    if order is None:
        order = list(range(n))
    out = []
    for j in order:
        for pt in polys[j]:
            if not out or not _near_pt(pt, out[-1]):
                out.append((pt[0], pt[1]))
    if len(out) > 1 and not _near_pt(out[-1], out[0]):
        out.append(out[0])
    return out


def _region_cut_polygon(geo, x0, y0, x1, y1):
    """取质心落在 region 内的所有 cut 段，串成一条全局坐标闭合多边形（含闭合点）。

    用于把插口盒各非矩形面板的**真实刀线轮廓**提取出来（翼的斜切、插舌圆头、
    底翼锁舌/穹、粘合梯形、四墙以外的废料剔除）。单段面板直接取切段并补闭合点，
    多段（底翼）用 _chain_cut 串接。region 内无 cut 段时返回 None（该面板保持矩形）。
    """
    polys = []
    for seg in geo.segments:
        if seg.kind != "cut":
            continue
        cx = sum(pt[0] for pt in seg.points) / len(seg.points)
        cy = sum(pt[1] for pt in seg.points) / len(seg.points)
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            polys.append(list(seg.points))
    if not polys:
        return None
    if len(polys) == 1:
        poly = list(polys[0])
        if not _near_pt(poly[-1], poly[0]):
            poly.append(poly[0])
        return poly
    return _chain_cut(polys)


def _net_bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return [min(xs), min(ys), max(xs), max(ys)]


def _insertion_fold_contract(geo: DieCutGeometry):
    """插口盒的 3D 折叠描述：panels(面板几何) + fold(折叠动力学) + fold_sequence(顺序)。

    仅供 3D 折叠预览（diecut-3d 读 geometry.fold 驱动）。刀线真值仍在 segments；
    非矩形面板（翼的斜切、插舌圆头、底翼锁舌/穹、粘合梯形）以 shape 携带**真实刀线轮廓**
    实现清废（刀线外材料不渲染）；四墙保持矩形。结构（段1/段3=长 L、段2/段4=宽 W）：
      · 四墙 i_w1..w4 绕竖折线(axis='y')卷成方筒（段宽 L/L/W/W 由 geo.length/width 推导）
      · 左侧周向粘合片 i_glue（x[-15,0] 梯形，归 seg1 左边，绕竖折 x=0 封筒缝内壁）
      · 底部向下翻转的 i_b1..b4（绕底折 axis='x'），按 段1→段2/4→段3 顺序封底
      · 段1 顶为两级铰链：盖翼 i_cover1（绕顶折 y=H）+ 插舌 i_tongue（绕舌折 y=nt）
      · 段2/4 顶盖翼 i_wing2/wing4（绕顶折，先折）
    折叠顺序（见 fold.range）：卷筒 → 底部 b1→b2/b4→b3 → 顶部 翼2/4→舌→盖翼。
    符号经模拟校准：盖翼与插舌均 -90° 下折，使插舌沿段3内壁没入盒内（z≈-W+t）。"""
    L = geo.length; W = geo.width; H = geo.height
    s1, s2, s3, s4 = L, L + W, 2 * L + W, 2 * L + 2 * W
    HALF = abs(math.pi / 2)
    sf_len = L / 45.0; sf_wid = W / 45.0
    bot = W / 2.0 + 9.0    # 段1/段3 底翼深（锁舌/穹）= 宽一半 + 插舌伸出（宽度中线交叉）
    bot22 = L / 2.0        # 段2/段4 斜切翼深 = 盒长一半（长度中线交叉；与构造器 fl_dep=L/2 一致）
    neck_rise = 44.5 * sf_wid; neck_cut = 7.5 * sf_len
    lid_rad = 9.0 * sf_len; lid_sh = 6.0; lid_w2 = 13.0 * sf_len
    nt = H + neck_rise                       # 舌折线 y（= 盖翼颈顶）
    tongue_top = nt + lid_sh + lid_rad       # 插舌圆头顶
    wing_top = H + 21.5                      # 盖翼顶平（AABB）
    glue = 15.0                              # 左侧粘合片宽

    cx1 = s1 / 2.0
    lsh = cx1 - lid_w2 - lid_rad           # 插舌圆头左缘
    rsh = cx1 + lid_w2 + lid_rad           # 插舌圆头右缘

    def to_shape(poly, ax, ay):
        # shape 为「局部坐标（=网坐标 - 锚点）」，diecut-3d 用其构建真正多边形
        return [[round(px - ax, 3), round(py - ay, 3)] for (px, py) in poly] if poly else None

    def p(id_, x0, y0, x1, y1, ax, ay, shp=None, holes=None):
        d = {"id": id_, "bounds": [x0, y0, x1, y1], "anchor": [ax, ay], "shape": shp}
        if holes:
            d["holes"] = holes
        return d

    # 段3 开窗：窗内材料不渲染，3D 里 cut 成透孔（局部坐标 = 网坐标 - i_w3 锚点 (s2,0)）。
    # 走与 shape 相同的局部坐标约定，diecut-3d 据此在面板几何里真正镂空 → 透视。
    window_holes = []
    if geo.window_rect:
        window_holes.append(to_shape(geo.window_rect, s2, 0))

    # 各非矩形面板的真实刀线轮廓（全局坐标）；翼/插舌/底翼/粘合片据此清废
    glue_p = _region_cut_polygon(geo, -glue - 0.6, -1, 0.6, H + 1)
    b1_p = _region_cut_polygon(geo, -0.6, -bot - 1, s1 + 0.4, 1.0)
    b2_p = _region_cut_polygon(geo, s1 - 0.4, -bot22 - 1, s2 + 0.4, 1.0)
    b3_p = _region_cut_polygon(geo, s2 - 0.7, -bot - 1, s3 - 0.4, 1.0)
    b4_p = _region_cut_polygon(geo, s3 - 0.7, -bot22 - 1, s4 + 0.4, 1.0)
    wing2_p = _region_cut_polygon(geo, s1 + 0.5, H - 1, s2 + 0.5, wing_top + 1)
    wing4_p = _region_cut_polygon(geo, s3 + 0.5, H - 1, s4 + 0.5, wing_top + 1)
    tongue_p = _region_cut_polygon(geo, lsh - 0.4, nt + 0.5, rsh + 0.4, tongue_top + 1)

    panels = [
        p("i_glue", *_net_bbox(glue_p), 0, 0, to_shape(glue_p, 0, 0)),
        p("i_w1", 0, 0, s1, H, 0, 0),
        p("i_w2", s1, 0, s2, H, s1, 0),
        p("i_w3", s2, 0, s3, H, s2, 0, holes=window_holes),
        p("i_w4", s3, 0, s4, H, s3, 0),
        p("i_b1", *_net_bbox(b1_p), 0, 0, to_shape(b1_p, 0, 0)),
        p("i_b2", *_net_bbox(b2_p), s1, 0, to_shape(b2_p, s1, 0)),
        p("i_b3", *_net_bbox(b3_p), s2, 0, to_shape(b3_p, s2, 0)),
        p("i_b4", *_net_bbox(b4_p), s3, 0, to_shape(b4_p, s3, 0)),
        p("i_cover1", 0, H, s1, nt, 0, H),
        p("i_tongue", *_net_bbox(tongue_p), s1 / 2.0, nt, to_shape(tongue_p, s1 / 2.0, nt)),
        p("i_wing2", *_net_bbox(wing2_p), s1, H, to_shape(wing2_p, s1, H)),
        p("i_wing4", *_net_bbox(wing4_p), s3, H, to_shape(wing4_p, s3, H)),
    ]
    F_WALL = "#efe5cf"; F_SIDE = "#eae2cb"; F_WING = "#efe7d0"; F_TUCK = "#f0e6cf"; F_BASE = "#f2ead6"
    F_GLUE = "#e6ddc6"
    fold = [
        # 卷筒（竖折 axis y）
        {"id": "i_w1", "parent": "root", "axis": None, "to": 0, "range": [0.0, 1.0], "fill": F_WALL},
        {"id": "i_w2", "parent": "i_w1", "axis": "y", "to": HALF, "range": [0.0, 0.16], "fill": F_SIDE},
        {"id": "i_w3", "parent": "i_w2", "axis": "y", "to": HALF, "range": [0.16, 0.32], "fill": F_WALL},
        {"id": "i_w4", "parent": "i_w3", "axis": "y", "to": HALF, "range": [0.32, 0.46], "fill": F_SIDE},
        {"id": "i_glue", "parent": "i_w1", "axis": "y", "to": -HALF, "range": [0.0, 0.16], "fill": F_GLUE},
        # 底部：段1 -> 段2/段4 -> 段3（顺序封底，axis x 折向筒内 -z）
        {"id": "i_b1", "parent": "i_w1", "axis": "x", "to": HALF, "range": [0.48, 0.58], "fill": F_BASE},
        {"id": "i_b2", "parent": "i_w2", "axis": "x", "to": HALF, "range": [0.58, 0.68], "fill": F_BASE},
        {"id": "i_b4", "parent": "i_w4", "axis": "x", "to": HALF, "range": [0.58, 0.68], "fill": F_BASE},
        {"id": "i_b3", "parent": "i_w3", "axis": "x", "to": HALF, "range": [0.68, 0.78], "fill": F_BASE},
        # 顶部：翼2/4 内折 -> 插舌折 90° -> 盖翼整体下折（axis x）
        {"id": "i_wing2", "parent": "i_w2", "axis": "x", "to": -HALF, "range": [0.80, 0.88], "fill": F_WING},
        {"id": "i_wing4", "parent": "i_w4", "axis": "x", "to": -HALF, "range": [0.80, 0.88], "fill": F_WING},
        {"id": "i_cover1", "parent": "i_w1", "axis": "x", "to": -HALF, "range": [0.94, 1.0], "fill": F_WALL},
        {"id": "i_tongue", "parent": "i_cover1", "axis": "x", "to": -HALF, "range": [0.88, 0.94], "fill": F_TUCK},
    ]
    fseq = [
        {"order": 1, "from": "i_w2", "to": "i_w1", "axis_y": 0.0},
        {"order": 2, "from": "i_w3", "to": "i_w2", "axis_y": 0.0},
        {"order": 3, "from": "i_w4", "to": "i_w3", "axis_y": 0.0},
        {"order": 4, "from": "i_b1", "to": "i_w1", "axis_y": 0.0},
    ]
    return panels, fold, fseq


def geometry_to_json(geo: DieCutGeometry, box_type: str = "airplane_box") -> dict:
    """将几何转换为稳定的 API geometry contract。box_type 为产物类型标签（airplane_box/...）。"""
    y0 = 0.0
    y1 = geo.wall_height
    y2 = y1 + geo.bottom_height
    y3 = y2 + geo.wall_height
    y4 = y3 + geo.lid_height
    y5 = y4 + geo.tab_depth
    colW = geo.column_width or geo.length
    lidW = geo.lid_width or geo.length
    Lx = colW + 2.0 * geo.thickness      # 制造尺寸 = 制造宽 + 2t
    ofs_b = (colW - Lx) / 2.0            # 底面居中（底面 = 制造尺寸 Lx）
    ofs_l = (colW - lidW) / 2.0          # 盖面居中
    wing_w = geo.wing_width
    lock_w = geo.lock_width
    back_w = geo.back_flap_width
    tab_ear_w = wing_w                       # 插舌翼横向 = 盖翼 = 锁扣翼 = H - t
    tab_ear_slant = min(12.0, geo.tab_depth * 0.2)
    slant_w = min(wing_w * 0.3, 0.15 * geo.length)
    t = geo.thickness                        # 纸板厚度
    hook_d = geo.thickness                   # 大侧壁外段插舌长度 = 材料厚度
    hook_h = geo.width * geo.hook_ratio      # 凸起钩高度（居中，可调比例）
    side_comp = (geo.side_height or geo.width) - geo.width   # 侧壁宽补偿
    outer_h = geo.width                      # 大侧壁外段高度 = 盒宽 W（内宽，相对内段居中缩进 side_comp/2）
    outer_lo = side_comp / 2.0               # 外段局部 y 起点（居中）
    outer_c = outer_lo + outer_h / 2.0
    gap = 3.0 * geo.thickness                # 间隙段（3t，含侧壁厚度；折叠后净空隙 2t 容纳腰部翼/耳翼）
    side_inner = geo.side_inner
    side_outer = geo.side_outer
    side_total = side_inner + gap + side_outer
    y2_side = y2                             # 内段顶边 = 底面顶（制造宽，与后腰翼底边共线）
    y_in_lo = y1 + side_comp / 2.0           # 外段底边（居中缩进）
    y_in_hi = y2 - side_comp / 2.0           # 外段顶边（居中缩进）
    contract = {
        "schema_version": "1.0",
        "type": box_type,
        "units": "mm",
        "dimensions": {
            "length": geo.length,
            "width": geo.width,
            "height": geo.height,
            "thickness": geo.thickness,
        },
        "derived": {
            "wall_height": geo.wall_height,
            "bottom_height": geo.bottom_height,
            "lid_height": geo.lid_height,
            "tab_depth": geo.tab_depth,
            "wing_width": geo.wing_width,
            "side_inner": geo.side_inner,
            "side_outer": geo.side_outer,
            "column_width": geo.column_width or geo.length,
            "lid_width": geo.lid_width or geo.length,
            "side_height": geo.side_height or geo.width,
        },
        "bounds": dict(zip(("min_x", "min_y", "max_x", "max_y"), geo.bounds)),
        "layers": list(geo.layers),
        "panels": [
            # 主面板（竖排 5 层）：bounds = 网区域，anchor = 折痕锚点，shape = 梯形翼局部顶点（null = 矩形）
            {"id": "front_wall", "bounds": [0.0, y0, colW, y1], "anchor": [0.0, y1], "shape": None},
            {"id": "bottom", "bounds": [ofs_b, y1, ofs_b + Lx, y2], "anchor": [ofs_b, y1], "shape": None},
            {"id": "back_wall", "bounds": [0.0, y2, colW, y3], "anchor": [0.0, y2], "shape": None},
            {"id": "lid", "bounds": [ofs_l, y3, ofs_l + lidW, y4], "anchor": [ofs_l, y3], "shape": None},
            {"id": "tuck", "bounds": [0.0, y4, colW, y5], "anchor": [0.0, y4], "shape": None},
            # 前壁锁扣翼（矩形 = 腰部翼尺寸 H - t；沿主面板方向上下各缩 t）
            {"id": "lock_left", "bounds": [-lock_w, y0 + t, 0.0, y1 - t], "anchor": [0.0, y1], "shape": None},
            {"id": "lock_right", "bounds": [colW, y0 + t, colW + lock_w, y1 - t], "anchor": [colW, y1], "shape": None},
            # 后壁矩形翼（腰部翼；上下各缩 t）
            {"id": "back_wing_left", "bounds": [-back_w, y2 + t, 0.0, y3 - t], "anchor": [0.0, y2], "shape": None},
            {"id": "back_wing_right", "bounds": [colW, y2 + t, colW + back_w, y3 - t], "anchor": [colW, y2], "shape": None},
            # 盖翼（等腰梯形，从盖面左右缘伸出；上下各缩 t）
            {"id": "lid_wing_left", "bounds": [ofs_l - wing_w, y3 + t, ofs_l, y4 - t], "anchor": [ofs_l, y3],
             "shape": [[0, t], [0, geo.lid_height - t], [-wing_w, geo.lid_height - t - slant_w], [-wing_w, t + slant_w]]},
            {"id": "lid_wing_right", "bounds": [colW - ofs_l, y3 + t, colW - ofs_l + wing_w, y4 - t], "anchor": [colW - ofs_l, y3],
             "shape": [[0, t], [0, geo.lid_height - t], [wing_w, geo.lid_height - t - slant_w], [wing_w, t + slant_w]]},
            # 插舌耳翼（直角三角形；底边水平，直角靠插舌侧壁，上下各缩 t）
            {"id": "tuck_ear_left", "bounds": [-tab_ear_w, y4 + t, 0.0, y5 - t], "anchor": [0.0, y4],
             "shape": [[0, t], [-tab_ear_w, t], [0, geo.tab_depth - t]]},
            {"id": "tuck_ear_right", "bounds": [colW, y4 + t, colW + tab_ear_w, y5 - t], "anchor": [colW, y4],
             "shape": [[0, t], [tab_ear_w, t], [0, geo.tab_depth - t]]},
            # 大侧壁内段（成盒壁）
            {"id": "left_wall", "bounds": [ofs_b - side_inner, y1, ofs_b, y2_side], "anchor": [ofs_b, y1], "shape": None},
            {"id": "right_wall", "bounds": [ofs_b + Lx, y1, ofs_b + Lx + side_inner, y2_side], "anchor": [ofs_b + Lx, y1], "shape": None},
            # 大侧壁间隙段（2t，折叠后形成内段与外段之间的空隙，容纳腰部翼/耳翼厚度；随外段居中缩进）
            {"id": "left_gap", "bounds": [ofs_b - side_inner - gap, y_in_lo, ofs_b - side_inner, y_in_hi], "anchor": [ofs_b - side_inner, y1], "shape": None},
            {"id": "right_gap", "bounds": [ofs_b + Lx + side_inner, y_in_lo, ofs_b + Lx + side_inner + gap, y_in_hi], "anchor": [ofs_b + Lx + side_inner, y1], "shape": None},
            # 大侧壁插入段（折叠后插入盒底，末端凸起钩=插舌，两侧清废；shape 裁剪出插舌，避免 3D 显示多余材料）
            {"id": "left_insert", "bounds": [ofs_b - side_total - hook_d, y_in_lo, ofs_b - side_inner - gap, y_in_hi], "anchor": [ofs_b - side_inner - gap, y1],
             "shape": [[-side_outer, outer_lo], [-side_outer, outer_c - hook_h / 2.0], [-side_outer - hook_d, outer_c - hook_h / 2.0],
                       [-side_outer - hook_d, outer_c + hook_h / 2.0], [-side_outer, outer_c + hook_h / 2.0],
                       [-side_outer, outer_lo + outer_h], [0.0, outer_lo + outer_h], [0.0, outer_lo]]},
            {"id": "right_insert", "bounds": [ofs_b + Lx + side_inner + gap, y_in_lo, ofs_b + Lx + side_total + hook_d, y_in_hi], "anchor": [ofs_b + Lx + side_inner + gap, y1],
             "shape": [[0.0, outer_lo], [0.0, outer_lo + outer_h], [side_outer, outer_lo + outer_h], [side_outer, outer_c + hook_h / 2.0],
                       [side_outer + hook_d, outer_c + hook_h / 2.0], [side_outer + hook_d, outer_c - hook_h / 2.0],
                       [side_outer, outer_c - hook_h / 2.0], [side_outer, outer_lo]]},
        ],
        "fold_sequence": [
            {"order": 1, "from": "front_wall", "to": "bottom", "axis_y": y1},
            {"order": 2, "from": "back_wall", "to": "bottom", "axis_y": y2},
            {"order": 3, "from": "lid", "to": "back_wall", "axis_y": y3},
            {"order": 4, "from": "tuck", "to": "lid", "axis_y": y4},
        ],
        "fold_lines": [
            {"points": [list(point) for point in segment.points], "cut": segment.cut}
            for segment in geo.segments
            if segment.kind == "crease"
        ],
        "segments": [
            {
                "kind": segment.kind,
                "cut": segment.cut,
                "points": [list(point) for point in segment.points],
            }
            for segment in geo.segments
        ],
    }

    # 非飞机盒：输出该盒型的 3D 折叠描述（panels + fold 动力学），diecut-3d 据此折叠
    if box_type != "airplane_box":
        panels, mfold, fseq = _insertion_fold_contract(geo)
        contract["panels"] = panels
        contract["fold"] = mfold
        contract["fold_sequence"] = fseq

    return contract


def estimate_sheet_utilization(
    geo: DieCutGeometry,
    sheet_width: float,
    sheet_height: float,
    margin: float = 10.0,
    gap: float = 5.0,
) -> dict:
    """用刀版包围盒估算直放/横放的拼版数量和利用率。"""
    if min(sheet_width, sheet_height) <= 0 or min(margin, gap) < 0:
        raise ValueError("纸张尺寸、边距和间距必须有效")
    min_x, min_y, max_x, max_y = geo.bounds
    blank_width = max_x - min_x
    blank_height = max_y - min_y
    sheet_area = sheet_width * sheet_height
    candidates = []
    for rotation, item_width, item_height in (
        (0, blank_width, blank_height),
        (90, blank_height, blank_width),
    ):
        usable_width = sheet_width - 2 * margin
        usable_height = sheet_height - 2 * margin
        columns = int((usable_width + gap) // (item_width + gap)) if item_width else 0
        rows = int((usable_height + gap) // (item_height + gap)) if item_height else 0
        count = max(0, columns) * max(0, rows)
        used_area = count * blank_width * blank_height
        candidates.append({
            "rotation": rotation,
            "columns": max(0, columns),
            "rows": max(0, rows),
            "count": count,
            "utilization": used_area / sheet_area if sheet_area else 0.0,
        })
    best = max(candidates, key=lambda item: (item["count"], item["utilization"]))
    return {
        "sheet_width_mm": sheet_width,
        "sheet_height_mm": sheet_height,
        "margin_mm": margin,
        "gap_mm": gap,
        "blank_width_mm": blank_width,
        "blank_height_mm": blank_height,
        "rotation": best["rotation"],
        "columns": best["columns"],
        "rows": best["rows"],
        "count": best["count"],
        "utilization": best["utilization"],
        "candidates": candidates,
        "nesting_hint": "优先采用包围盒利用率最高的旋转方向",
    }


# ---------------------------------------------------------------------------
# SVG 输出
# ---------------------------------------------------------------------------

def _format_pt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


LAYER_OF_KIND = {
    "cut": "CUT",
    "crease": "CREASE",
    "halfcut": "HALFCUT",
    "dimension": "DIMENSION",
}


def _dimension_marks(points):
    """为一条 dimension 折线生成制造尺寸标注：中央断口的两段主线 + 端部箭头 + 中点数字文本。

    返回 (seg_paths, arrow_paths, text|None)。
    seg_paths  : 断口左右两段主线 <path>（class="dimension"，线在数字处断开）
    arrow_paths: 两端 45° 斜箭头 <path>
    text       : (x, y, label, rotate) 标注文本，label 含 "mm"，置于断口中央
    """
    seg_paths = []
    arrows = []
    texts = []
    tick = 1.6  # 端部箭头长度（mm）
    pad = 2.0   # 数字两侧留白（mm）
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        dx, dy = x1 - x0, y1 - y0
        seg_len = math.hypot(dx, dy)
        if seg_len < 1e-9:
            continue
        ux, uy = dx / seg_len, dy / seg_len
        ang = math.atan2(dy, dx)
        # 端部箭头（45° 斜向短线，指向线段端点，对称不受翻转影响）
        for (px, py, dirn) in ((x0, y0, 1), (x1, y1, -1)):
            base = ang if dirn > 0 else ang + math.pi
            for off in (math.pi * 0.8, -math.pi * 0.8):
                ex = px + tick * math.cos(base + off)
                ey = py + tick * math.sin(base + off)
                arrows.append(
                    f'<path class="dimension" d="M{_format_pt(px)} {_format_pt(py)} '
                    f'L{_format_pt(ex)} {_format_pt(ey)}"/>'
                )
        # 标注值 = 线段长度（mm），自动去尾零，如 "230 mm" / "135.5 mm"
        label = f"{seg_len:g} mm"
        # 断口半宽：按文本估算宽度，钳制到长度一半以内，保证不断开反向
        est_half = len(label) * 4.75 / 2.0 + pad   # 字号 8.5mm → 每字符约 4.75mm 宽
        half = min(est_half, max(0.5, seg_len / 2.0 - 1.0))
        mid_d = seg_len / 2.0
        a = mid_d - half
        b = mid_d + half
        if a > 0.001:
            seg_paths.append(
                f'<path class="dimension" d="M{_format_pt(x0)} {_format_pt(y0)} '
                f'L{_format_pt(x0 + ux * a)} {_format_pt(y0 + uy * a)}"/>'
            )
        if b < seg_len - 0.001:
            seg_paths.append(
                f'<path class="dimension" d="M{_format_pt(x0 + ux * b)} {_format_pt(y0 + uy * b)} '
                f'L{_format_pt(x1)} {_format_pt(y1)}"/>'
            )
        texts.append(
            ((x0 + x1) / 2.0, (y0 + y1) / 2.0, label,
             -90 if abs(dy) > abs(dx) else 0)
        )
    return seg_paths, arrows, texts[0] if texts else None


def geometry_to_svg(geo: DieCutGeometry, title: str = "") -> str:
    min_x, min_y, max_x, max_y = geo.bounds
    pad = 10.0
    vb_x = min_x - pad
    vb_y = min_y - pad
    vb_w = (max_x - min_x) + 2 * pad
    vb_h = (max_y - min_y) + 2 * pad

    # 线条宽度随纸板厚度动态调整（Step 1 项 5）
    t = geo.thickness
    cut_w = 0.25 + t * 0.02
    crease_w = 0.20 + t * 0.02
    halfcut_w = 0.15 + t * 0.02
    dim_w = 0.35

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_format_pt(vb_w)}mm" '
        f'height="{_format_pt(vb_h)}mm" viewBox="{_format_pt(vb_x)} {_format_pt(vb_y)} {_format_pt(vb_w)} {_format_pt(vb_h)}">',
        '<defs>',
        '  <style>',
        f'    .cut {{ stroke: #000; stroke-width: {_format_pt(cut_w)}; fill: none; }}',
        f'    .crease {{ stroke: #e02020; stroke-width: {_format_pt(crease_w)}; stroke-dasharray: 4 2; fill: none; }}',
        f'    .halfcut {{ stroke: #1d4ed8; stroke-width: {_format_pt(halfcut_w)}; stroke-dasharray: 1 1.5; fill: none; }}',
        f'    .dimension {{ stroke: #0d6efd; stroke-width: {_format_pt(dim_w)}; fill: none; }}',
        '  </style>',
        '</defs>',
    ]
    if title:
        parts.append(
            f'<text x="{_format_pt(vb_x + 2)}" y="{_format_pt(vb_y + 8)}" '
            f'font-size="4" fill="#666" font-family="sans-serif">{title}</text>'
        )
    # 翻转 Y 轴：让插舌(顶)显示在上方，与参考图方向一致
    flip_t = 2 * vb_y + vb_h
    parts.append(f'<g transform="translate(0,{_format_pt(flip_t)}) scale(1,-1)">')
    active = set(geo.layers)
    dim_texts = []
    # 按图层分组渲染（Step 5）：CUT / CREASE / 可选 HALFCUT / DIMENSION
    for layer in ("CUT", "CREASE", "HALFCUT", "DIMENSION"):
        if layer not in active:
            continue
        parts.append(f'  <g id="layer-{layer}">')
        for seg in geo.segments:
            if LAYER_OF_KIND.get(seg.kind) != layer:
                continue
            if seg.kind == "dimension":
                gap_paths, arrows, lbl = _dimension_marks(seg.points)
                parts.extend(gap_paths)
                parts.extend(arrows)
                if lbl:
                    dim_texts.append(lbl)
            else:
                d_parts = []
                for i, (x, y) in enumerate(seg.points):
                    cmd = "M" if i == 0 else "L"
                    d_parts.append(f"{cmd}{_format_pt(x)} {_format_pt(y)}")
                parts.append(f'    <path class="{seg.kind}" d="{" ".join(d_parts)}"/>')
        parts.append("  </g>")
    parts.append("</g>")
    # 尺寸标注文字：组内坐标被 scale(1,-1) 倒置，故在翻转组外按翻转还原后输出，
    # 文字本身保持正向可读。
    for (tx, ty, label, rot) in dim_texts:
        ty2 = flip_t - ty
        rot_attr = f' transform="rotate({rot} {_format_pt(tx)} {_format_pt(ty2)})"' if rot else ""
        parts.append(
            f'<text x="{_format_pt(tx)}" y="{_format_pt(ty2)}" font-size="8.5" '
            f'fill="#0d6efd" stroke="#ffffff" stroke-width="1.2" paint-order="stroke" '
            f'font-family="sans-serif" text-anchor="middle"'
            f'{rot_attr}>{label}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# PDF 输出（ReportLab，无需 LibreOffice）
# ---------------------------------------------------------------------------

_CJK_FONT: str | None = None
_CJK_FONT_READY = False


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
        # 用 fontTools 判中文覆盖，覆盖越多越优先；"subset" 命名的子集字体优先
        # （体积小得多、且由 dix_subset_font.py 保证覆盖所有标题字符）
        try:
            from fontTools.ttLib import TTFont as _FT
            def _rank(path: str) -> tuple:
                name = os.path.basename(path).lower()
                try:
                    cmap = _FT(path, fontNumber=0).getBestCmap()
                    ok = sum(1 for c in "飞机盒刀版测试商邑" if ord(c) in cmap)
                except Exception:
                    ok = 0
                is_subset = "subset" in name
                return (0 if is_subset else 1, -ok)
            candidates.sort(key=_rank)
        except Exception:
            pass
    if os.name == "nt":
        for fn in ("simhei.ttf", "Deng.ttf", "msjh.ttf"):
            p = os.path.join(r"C:\Windows\Fonts", fn)
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



def geometry_to_dxf_bytes(geo: DieCutGeometry, title: str = "") -> bytes:
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # 毫米
    msp = doc.modelspace()

    if "CUT" not in doc.layers:
        doc.layers.add("CUT", color=1)
    if "CREASE" not in doc.layers:
        doc.layers.add("CREASE", color=5)
    if "HALFCUT" not in doc.layers:
        doc.layers.add("HALFCUT", color=4)
    if "DIMENSION" not in doc.layers:
        doc.layers.add("DIMENSION", color=2)

    try:
        if "DASHED" not in doc.linetypes:
            doc.linetypes.add("DASHED", pattern=[0.2, 0.1, -0.1])
    except Exception:
        pass

    dxf_layer = {
        "cut": "CUT",
        "crease": "CREASE",
        "halfcut": "HALFCUT",
        "dimension": "DIMENSION",
    }

    def add_dxf_dimension(msp, pts, layer="DIMENSION") -> None:
        """复刻 SVG/PDF 的 _dimension_marks：断口主线 + 端部箭头 + 白底 MTEXT 标注(带 mm)。

        DXF 原本只导出 DIMENSION 直线、漏了数字与单位，这里补齐为自包含标注。
        """
        tick = 1.6   # 端部箭头长度
        pad = 2.0    # 数字两侧留白
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
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
                    msp.add_line((px, py), (ex, ey), dxfattribs={"layer": layer})
            # 断口：主线在数字处断开
            label = f"{seg_len:g} mm"
            est_half = len(label) * 4.75 / 2.0 + pad
            half = min(est_half, max(0.5, seg_len / 2.0 - 1.0))
            mid_d = seg_len / 2.0
            a, b = mid_d - half, mid_d + half
            if a > 0.001:
                msp.add_line((x0, y0), (x0 + ux * a, y0 + uy * a), dxfattribs={"layer": layer})
            if b < seg_len - 0.001:
                msp.add_line((x0 + ux * b, y0 + uy * b), (x1, y1), dxfattribs={"layer": layer})
            # 中点 MTEXT（垂直段转 90°，白底遮断口）
            mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            m = msp.add_mtext(label, dxfattribs={
                "layer": layer,
                "insert": (mx, my),
                "char_height": 2.5,
                "rotation": 90 if abs(dy) > abs(dx) else 0,
            })
            m.dxf.attachment_point = 5   # 中中对齐
            try:
                m.set_bg_color((1, 1, 1))   # 白底遮住断口下方线条
            except Exception:
                pass

    active = set(geo.layers)
    for seg in geo.segments:
        if dxf_layer.get(seg.kind) not in active:
            continue
        layer = dxf_layer.get(seg.kind, "CREASE")
        attribs = {"layer": layer}
        if seg.kind == "crease":
            try:
                attribs["linetype"] = "DASHED"
            except Exception:
                pass
        pts = seg.points
        if seg.kind == "dimension":
            add_dxf_dimension(msp, pts, layer)
            continue
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            msp.add_line((x0, y0), (x1, y1), dxfattribs=attribs)

    if title:
        msp.add_text(
            title,
            dxfattribs={
                "height": 3.0,
                "layer": "CUT" if "CUT" in active else (geo.layers[0] if geo.layers else "CUT"),
                "insert": (geo.bounds[0], geo.bounds[1] - 8.0),
            },
        )

    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")
