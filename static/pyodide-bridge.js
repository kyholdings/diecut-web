/* pyodide-bridge.js — 纯静态前端与 Python 引擎之间的桥。
 *
 * 加载 Pyodide(CDN) + Pyodide 原生 wasm 包(numpy/Pillow) + 本地 vendored 纯 python
 * wheel(ezdxf/fpdf2/fonttools/pyparsing/typing_extensions/defusedxml)，再把
 * diecut_engine.py + box_registry.py 写进 wasm 文件系统，暴露 window.DiecutEngine：
 *
 *   DiecutEngine.init()         加载全部运行时（幂等，可被 index 在 mount 前 await）
 *   DiecutEngine.boxTypes       序列化的盒型元数据（label/defaults/params/param_rules）
 *   DiecutEngine.ready          bool
 *   DiecutEngine.error          初始化失败信息
 *   DiecutEngine.generate(form) -> {ok,id,title,geometry,meta,svg,pdf,dxf}
 *                                svg=string；pdf/dxf=Blob
 *
 * 引擎代码仍是同一份 diecut_engine.py/box_registry.py（与命令行 generate_cli.py 同源），
 * 只是换运行时，故浏览器端与 oracle 结果 bit-identical（见 scripts/ 冒烟）。
 */
(function () {
  'use strict';

  const PYODIDE_URL = 'https://cdn.jsdelivr.net/pyodide/v314.0.6/full/';

  // 需 vendored 的纯 python wheel（依赖顺序：叶子依赖在前）。
  // numpy / Pillow 是 C 扩展，由 Pyodide 原生包提供，不在此列。
  const WHEELS = [
    'fonttools-4.64.0-py3-none-any.whl',
    'pyparsing-3.3.2-py3-none-any.whl',
    'typing_extensions-4.16.0-py3-none-any.whl',
    'defusedxml-0.7.1-py2.py3-none-any.whl',
    'ezdxf-1.4.4-py3-none-any.whl',
    'fpdf2-2.8.8-py3-none-any.whl',
  ];

  // 引擎源码（与 diecut-web 根目录同文件），部署时随 publish 目录一起提供。
  const ENGINE_FILES = ['diecut_engine.py', 'box_registry.py', 'diecut_schema.json'];
  const FONT_REL = 'fonts/NotoSansSC-subset.ttf';

  const state = {
    py: null,
    ready: false,
    error: '',
    boxTypes: [],
  };

  // 把一个 python dict 结果转成 JSON 安全对象（geometry/meta 均为 JSON 原生类型）。
  async function _pyJson(py, code) {
    // runPythonAsync 返回 PyProxy；用 toJs() 深转换以避开 PyProxy 生命周期问题
    return await py.runPythonAsync(code).then((o) => (o && o.toJs ? o.toJs({ depth: 20 }) : o));
  }

  async function _loadEngine(py) {
    await py.FS.mkdirTree('/work');
    await py.FS.mkdirTree('/work/fonts');
    for (const fn of ENGINE_FILES) {
      const r = await fetch('/' + fn);
      if (!r.ok) throw new Error('engine file fetch failed: ' + fn + ' ' + r.status);
      const text = await r.text();
      await py.FS.writeFile('/work/' + fn, new TextEncoder().encode(text));
    }
    const fr = await fetch('/' + FONT_REL);
    if (!fr.ok) throw new Error('font fetch failed: ' + FONT_REL + ' ' + fr.status);
    await py.FS.writeFile('/work/' + FONT_REL, new Uint8Array(await fr.arrayBuffer()));
    // 让引擎能 import box_registry（其内部 import diecut_engine）、找到 fonts/
    await py.runPythonAsync('import os; os.chdir("/work")');
  }

  async function init() {
    if (state.ready) return;
    if (state.error || state._init_promise) {
      if (state._init_promise) return await state._init_promise;
      return;
    }
    state._init_promise = (async () => {
      try {
        const { loadPyodide } = await import(PYODIDE_URL + 'pyodide.mjs');
        const py = await loadPyodide({ indexURL: PYODIDE_URL });
        await py.loadPackage(['numpy', 'pillow']);

        // 安装 vendored 纯 python wheel：直接解包到 site-packages（免 micropip/网络）
        await py.FS.mkdirTree('/pkg');
        for (const fn of WHEELS) {
          const r = await fetch('/static/vendor/' + fn);
          if (!r.ok) throw new Error('wheel fetch failed: ' + fn + ' ' + r.status);
          await py.FS.writeFile('/pkg/' + fn, new Uint8Array(await r.arrayBuffer()));
        }
        await py.runPythonAsync([
          'import zipfile, os, sys',
          'sp = next(p for p in sys.path if "site-packages" in p)',
          'for w in os.listdir("/pkg"): zipfile.ZipFile("/pkg/" + w).extractall(sp)',
        ].join('\n'));

        await _loadEngine(py);

        state.boxTypes = await _pyJson(py,
          'import json, box_registry; json.dumps(box_registry.box_types_meta())',
        ).then((s) => JSON.parse(s));

        state.py = py;
        state.ready = true;
        state.error = '';
      } catch (e) {
        state.error = String((e && e.message) || e);
        state.ready = false;
        throw e;
      } finally {
        state._init_promise = null;
      }
    })();
    return state._init_promise;
  }

  async function generate(form) {
    if (!state.ready) throw new Error('引擎尚未初始化');
    const py = state.py;
    const formJson = JSON.stringify(form);
    // 返回 JSON 安全 dict（不含 bytes，artifacts 走 FS 读取）
    const payload = await _pyJson(py, [
      'import json, os, sys',
      'sys.path.insert(0, "/work")',
      'os.chdir("/work")',
      'from box_registry import generate_payload',
      'form = json.loads(' + JSON.stringify(formJson) + ')',
      'p = generate_payload(form)',
      'if p.get("ok"):',
      '    a = p["artifacts"]',
      '    for ext, v in a.items():',
      '        with open("/work/_out." + ext, "wb") as f:',
      '            f.write(v.encode() if isinstance(v, str) else v)',
      '    del p["artifacts"]',
      'p',
    ].join('\n'));

    if (!payload || !payload.ok) return payload || { ok: false, error: '未知错误' };

    const svgU8 = await py.FS.readFile('/work/_out.svg');
    const pdfU8 = await py.FS.readFile('/work/_out.pdf');
    const dxfU8 = await py.FS.readFile('/work/_out.dxf');
    return {
      ok: true,
      id: payload.id,
      title: payload.title,
      geometry: payload.geometry,
      meta: payload.meta,
      svg: new TextDecoder('utf-8').decode(new Uint8Array(svgU8)),
      pdf: new Blob([pdfU8], { type: 'application/pdf' }),
      dxf: new Blob([dxfU8], { type: 'application/dxf' }),
    };
  }

  window.DiecutEngine = {
    init,
    generate,
    get boxTypes() { return state.boxTypes; },
    get ready() { return state.ready; },
    get error() { return state.error; },
  };
})();
