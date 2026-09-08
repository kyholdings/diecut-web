# diecut-web — 刀版生成器（纯静态 / Cloudflare Pages）

把原 `diecut_generator`（Flask 版）**另辟一个独立项目**改造成**纯静态站**：几何引擎与 SVG/PDF/DXF 生成
**全在浏览器里的 Pyodide 跑同一份 `diecut_engine.py`**，不再有 Python 后端。

- 原 `diecut_generator/` **保留不动**，本目录是独立 git 仓库（后续独立演进）。
- 浏览器生成的产物与命令行 oracle（`generate_cli.py`）**bit-identical**（同一份代码，只换运行时）。
- 部署 **Cloudflare Pages**（免费额度：带宽无限制、单文件 < 25MiB）——见文末「部署」。

## 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 几何引擎 | `diecut_engine.py`（fork 自原项目） | 唯一事实源，产出 SVG/PDF/DXF/JSON |
| 运行环境 | **Pyodide**（浏览器内 CPython 3.14，WASM） | 跑同一份 Python，无需后端 |
| PDF | **fpdf2** | 原 reportlab 进不了 Pyodide，已换 |
| DXF | ezdxf | 纯 python，已 vendored |
| 前端 | Vue 3 (CDN) + Bootstrap 5 | `static/index.html`，盒型下拉由注册表驱动 |
| 3D 折叠预览 | `static/diecut-3d.js` | 读 `geometry.fold/panels`，不变 |

## 本地运行

```bash
# 命令行 oracle（回归基准，不依赖浏览器）
python generate_cli.py airplane   # 输出 outputs/<id>.svg/.pdf/.dxf
python generate_cli.py insertion

# 浏览器预览（独立静态服务器 + Pyodide 从 CDN 加载）
cd static && python -m http.server 8000
# 用现代浏览器打开 http://127.0.0.1:8000/index.html
```

> 首次打开会从 jsdelivr CDN 加载 Pyodide（~10MB）与 numpy/Pillow wasm 包，稍等即可；离线环境已加载过则命中缓存。

## 目录结构

```
diecut-web/
├── diecut_engine.py          # 几何引擎（fork）+ 各盒型 build_<box> 构造器
├── box_registry.py           # 盒型注册表 BOX_REGISTRY（单一事实源）+ generate_payload
├── generate_cli.py           # 纯 Python 命令行长（oracle / 冒烟对照）
├── scripts/
│   ├── build_pyodide_whl.py  # 本地构建 vendored 纯 python wheel（ezdxf/fpdf2/...）
│   └── dix_subset_font.py    # 用 fontTools 抽标题子集字体 fonts/NotoSansSC-subset.ttf
├── static/
│   ├── index.html            # 前端 SPA（盒型下拉由 boxTypes 驱动）
│   ├── pyodide-bridge.js     # 暴露 window.DiecutEngine = {init, boxTypes, generate}
│   ├── diecut-3d.js          # 3D 折叠预览（不变）
│   └── vendor/               # vendored 纯 python wheels
├── fonts/NotoSansSC-subset.ttf  # CJK 子集字体（标题/FPDF 内嵌用）
├── wrangler.toml             # Cloudflare Pages 配置
├── _headers                  # 缓存/安全头
├── tests/ + test_diecut_engine.py
└── diecut_schema.json
```

## 架构核心：盒型注册表（可扩展点）

「加一款盒型只改引擎一处、前端自动适配」靠 `box_registry.py` 的 `BOX_REGISTRY: dict[str, BoxSpec]`。

每款盒型一个 `BoxSpec`，描述它的**构造器 / 默认参数 / 参数取值规则 / JSON 产物标签 / 3D 折叠契约**：

```python
BOX_REGISTRY = {
    "airplane": BoxSpec(
        key="airplane",
        label="自锁飞机盒",
        builder=build_airplane_box,              # diecut_engine 里的构造器
        defaults={"length":200,"width":150,"height":60,"thickness":3},
        json_box_type="airplane_box",            # geometry_to_json 产物类型标签
        params={                                 # 表单键 → (取值规则, 默认)
            "length": (_NUM, 200), "width": (_NUM, 150), ...
            "corner_radius": (_NUM, 0.0), "hook_ratio": (_NUM, 0.33),
        },
        fold_contract=None,                      # 3D 折叠契约函数；None=复用默认面板
        param_rules={                            # 前端表单 label/min/max/step
            "length": {"label":"长(mm)","min":1,"max":3000,"step":1},
            ...
        },
    ),
    ...
}
```

- **引擎分发**：`dispatch(box_type, form)` 从 `BOX_REGISTRY` 取 `builder` 并按 `params` 归一化裸值、
  调用构造器——替代原 `app.py` 里的 `if box_type == "insertion"` 硬编码分支。
- **前端动态化**：Pyodide 桥暴露 `window.DiecutEngine.boxTypes`（序列化的 `label/defaults/param_rules`，
  不含函数）。`index.html` 的盒型下拉、默认值、参数校验**全部从 `boxTypes` 渲染**，不写死。
- **3D**：`geometry_to_json` 输出 `fold/panels`，`diecut-3d.js` 读它做折叠预览——前端无需感知注册表。

## 如何新增一款盒型（清单）

新增一款盒型只需 **两处改动**：

1. **写构造器** —— 在 `diecut_engine.py` 写 `build_<new>_box(...) -> DieCutGeometry`，按内尺寸
   生成 `segments`（CUT/CREASE 刀线真值），并设置 `length/width/height/thickness/...` 与 `bounds`。
   可参照 `build_airplane_box` / `build_insertion_box`。

2. **注册 BoxSpec** —— 在 `box_registry.py` 的 `BOX_REGISTRY` 里加一条（并 `from diecut_engine import build_<new>_box`）：

| 字段 | 必填 | 含义 |
|---|---|---|
| `key` | ✅ | 盒型唯一键（下拉 / dispatch 用，如 `"apple"`） |
| `label` | ✅ | 中文名（下拉显示 + PDF 标题） |
| `builder` | ✅ | 你写的 `build_<new>_box` 函数 |
| `defaults` | ✅ | 前端初始表单默认值 / 命令行默认 |
| `json_box_type` | ✅ | `geometry_to_json` 的产物类型标签（如 `"apple_box"`） |
| `params` | ✅ | `{表单键: (取值规则kind, 默认)}`——`dispatch` 用它把前端裸值（含空串）转成构造器参数 |
| `fold_contract` | 视盒型 | 3D 折叠契约函数，返回 `(panels, fold, fold_sequence)`；普通盒型写 `None` 复用默认面板 |
| `param_rules` | 前端显示 | 每参数 `{label, min, max, step}`，控制前端表单校验 |

**取值规则 kind**（`params` 里用）：
- `_NUM` — 数值；`<=0` 取默认（长度/宽度/厚度）
- `_NUMOPT` — 可空数值（`None → None`）
- `_WINDOW` — `None→默认`，数字 `max(0, x)`（`0` = 关窗语义，保留 0）
- `_BOOL` — 布尔
- `_LAYERS` — 图层列表（只认 `CUT/CREASE/HALFCUT/DIMENSION`）
- `_FACADE` — 制造尺寸补偿；`""/None → None`

**改完后无需改动**：`pyodide-bridge.js`、前端 `generate()`、`diecut-3d.js`、`geometry_to_json` 分发。
前端下拉会自动出现新盒型；切到它会自动带默认值并出 SVG/PDF/DXF/3D。

### 附加两件事（视情况）

- **标题字体子集**：PDF/SVG 标题依赖 `fonts/NotoSansSC-subset.ttf`（由 `BOX_REGISTRY` 的 label +
  固定标题词生成）。若新盒型 label 含**子集里没有的字**，重跑字体子集脚本：
  ```bash
  python scripts/dix_subset_font.py
  ```
  它读所有 label 重新抽取字符，产出新的 `NotoSansSC-subset.ttf`（几 KB ~ 数百 KB）。
- **命令行 oracle** `generate_cli.py` 自动支持新盒型（从 `BOX_REGISTRY` 读），无需改。

## 部署到 Cloudflare Pages（纯静态）

仓库已推至 **`https://github.com/kyholdings/diecut-web`**（分支 `main`，独立 git 仓库）。

> **发布根 = 项目根目录 `.`**（不是 `static/`）。原因：`pyodide-bridge.js` 以根相对路径
> `fetch('/diecut_engine.py')` / `fetch('/fonts/...')` / `fetch('/static/vendor/*.whl')` 拉取引擎、
> 字体与 wheel，所以引擎源码、字体必须在发布根下，App 入口在 `/static/index.html`。
> 项目根已有 `index.html` 轻量跳转到 `/static/index.html`，故访问 `https://<domain>/` 即可用。

**Cloudflare Pages 面板连接（Connect to git）：**
1. **Repository** → 选 `kyholdings/diecut-web`。
2. **Build command** → 留空（纯静态，无需构建）。
3. **Build output directory** → 填 `.`。
4. **Production branch** → `main`。
5. 首次部署会吃 500 构建分钟/月的免费额度，但构建秒级（无 build 步骤）。

**本地再推 + 自动部署：** 改完 `git add -A && git commit && git push`，Pages 自动重新部署。

**大文件注意：** Pyodide 原生包与 wasm 走 CDN（jsdelivr），**不打包**进站点；vendored wheels 共 ~2.9MB、
字体子集 13.5KB、引擎 ~84KB——全部远低于 Cloudflare Pages 的 **25MiB 单文件上限**。

**`_headers`** 已内置 `Content-Security-Policy`（放行 jsdelivr / cdnjs）+ 缓存头，随发布根自动带上。

上线后：`curl https://<pages-domain>/static/index.html` 应返回 200。

## 从原项目 fork 的边界

`diecut_engine.py` / `static/diecut-3d.js` / `tests/` / `diecut_schema.json` 均 fork 自原
`diecut_generator/`，在本目录独立演进。**原项目不再改**。本目录不受主项目
`webdav_preview_launcher` 的双入口校验等规范约束（独立子仓库）。
