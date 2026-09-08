# ADR-001 — 盒型注册表（BOX_REGISTRY / BoxSpec）＝盒型可扩展性的单一事实源

- **状态**：已采纳（2026-09）
- **范围**：`diecut-web`（fork 自 `diecut_generator`，独立仓库、独立演进）
- **关联**：`box_registry.py` · `diecut_engine.py` · `generate_cli.py` · `static/index.html` · `static/diecut-3d.js`

---

## 背景

`diecut_generator` 原版用 `app.py` 的 `if box_type == "insertion"` 与 `geometry_to_json` 里 `if box_type != "airplane_box"` **硬编码分发**：新增一款盒型要同时改 `app.py` 分发、`geometry_to_json` 折叠契约分支、前端盒型下拉与 `BOX_DEFAULTS` 常量等多处，漏一处就产生「前端有入口但后端不认」或「折叠契约被污染」的分叉。每次只能猜 `box_type` 字符串，重构极易回退。

`diecut-web` 的目标是「为未来的更多款式盒型做可扩展设计」（当前仅 `airplane` / `insertion` 两款）。

## 决策

把「盒型描述」收敛到一个声明式注册表 `box_registry.py::BOX_REGISTRY`，每款盒型一个 `BoxSpec` dataclass 实例，作为**唯一事实源**。分发、折叠契约、前端下拉/默认值/取值规则全部由注册表驱动，剔除硬编码分支。

### BoxSpec 字段

| 字段 | 作用 | 示例（insertion） |
|---|---|---|
| `key` | 盒型字符串主键 | `"insertion"` |
| `label` | 前端中文名 | `"插口盒"` |
| `builder` | 几何构造器 `Callable[..., DieCutGeometry]` | `build_insertion_box` |
| `defaults` | 表单默认值 | `{"length": 45, ...}` |
| `params` | `{name: (kind, default)}` 取值规则声明 | `"length": (_NUM, 45)` |
| `json_box_type` | `geometry_to_json` 产物类型标签 | `"insertion_box"` |
| `fold_contract` | 3D 折叠契约；`None`=复用 `geometry_to_json` 默认面板 | `_insertion_fold_contract` |
| `param_rules` | 前端表单 `label / min / max / step` | `{"length": {"label": "长(mm)", ...}}` |

### 参数取值规则（kind）

`_COERCERS` 把 kind 映射成纯函数，把表单裸值（空串、字符串数字）按规则转成构建器参数，**归一化只跑一次**：

- `_NUM`：数字 >0，否则默认
- `_NUMOPT`：`None→None`，否则按 `_NUM`
- `_WINDOW`：`None→默认`，数字→`max(0, x)`（**0 = 关窗语义，保留 0**）
- `_BOOL`
- `_LAYERS`：只留 `CUT / CREASE / HALFCUT / DIMENSION`
- `_FACADE`：制造尺寸补偿（`""` / `None→None`）

### 分发与契约

- `dispatch(box_type, form)`：从 `spec.params` + 表单构造 kwargs → `spec.builder(**kwargs)`。
- `contract(spec, geo)`：`geometry_to_json(geo, spec.json_box_type)` 后，用 `spec.fold_contract` 覆盖 `panels / fold / fold_sequence`。**修复** `geometry_to_json` 里 `if box_type != "airplane_box"` 一刀切套插口盒折叠契约的 bug——新盒型带自己的 `fold_contract` 即不被污染；`fold_contract=None` 复用默认面板（飞机盒 19 面板）。
- `box_types_meta()`：序列化 `label / defaults / params / param_rules` 供前端（不含函数），前端盒型下拉/默认值由此渲染，不再写死 `BOX_DEFAULTS`。

### 端到端一致性

`generate_payload(form)` 复刻原 `app.py` 的 `POST /api/diecut/generate`：校验（尺寸/厚度范围）→ `dispatch` → `validate_geometry` → `geometry_to_svg/pdf/dxf` → `meta` → `artifacts`。

几何引擎 `diecut_engine.py` 是 geometry 的**唯一真值源**——浏览器经 Pyodide 跑**同一份** engine，注册表只是其上的薄声明层——由此保证 CLI oracle（`generate_cli.py`）与浏览器 `generate()` 结果 **bit-identical**。

## 新增一款盒型的全部改动（只动两处）

1. `diecut_engine.py` 加 `build_<new>_box(...) -> DieCutGeometry`（真几何）；若 3D 折叠有别于默认，另加 `<new>_fold_contract(geo)`。
2. `box_registry.py::BOX_REGISTRY` 注册一个 `BoxSpec`（`key / label / builder / defaults / params / fold_contract / json_box_type / param_rules`）。

前端下拉、默认值、参数校验、3D 折叠契约、JSON box_type、生成链路**全部自动跟上**。无需改：`box_registry.py` 的 `dispatch/contract`、`pyodide-bridge.js`、`index.html` 生成逻辑、`diecut-3d.js`。

（可选第三处）：`tests/` + `test_diecut_engine.py` 加构建器冒烟用例。

## 为什么否决其它方案

- **前端硬编码**：Vue 组件里再写一份 `BOX_DEFAULTS` 常量 → 双份事实源，改盒型易失同步。否决。
- **引擎内 if/else 链**：每盒型一分支 → N 处耦合、几何与折叠契约交织、加盒型要碰引擎核心分支。否决。
- **数据表驱动（DB）**：盒型要带 Callable 构建器与 Python 几何契约，纯数据表须序列化函数限定；且离线纯静态（Pyodide）无 DB。否决。

## 影响

- 加盒型从「改 4~6 处」降到「改 2 处」。
- 折叠契约不再被 `geometry_to_json` 一刀切污染。
- `param_rules` 让前端校验规则与后端归一化同源，避免前后端规则漂移。
- 代价：注册表是声明式 Python 代码，新增盒型需一定的 Python 能力；README「如何新增一款盒型」清单与之配套。

## 落点索引

| 符号 | 位置 |
|---|---|
| `BoxSpec` / `_COERCERS` / `BOX_REGISTRY` / `dispatch` / `contract` / `box_types_meta` / `generate_payload` | `box_registry.py` |
| `geometry_to_json`（L976） | `diecut_engine.py` |
| `build_insertion_box`（L538） | `diecut_engine.py` |
| `build_airplane_box`（L86） | `diecut_engine.py` |
| `_insertion_fold_contract`（L876） | `diecut_engine.py` |
| `main`（纯 Python 回归 oracle） | `generate_cli.py` |
