// 折叠最终态精确验证：盒体完整性 + 外段钩端/插舌下端到达盒底（z≈0）。
// 几何数据复用 API 返回的 geometry（tests/3d/_geo.json，diecut_engine 生成），
// 3D 面板坐标从 geometry.panels 动态读取，不硬编码。
'use strict';
const THREE = require('three');
global.THREE = THREE;
global.document = { createElement: () => ({ getContext: () => ({}) }) };
global.window = {};
const fs = require('fs');
const { buildHierarchy } = require('../../static/diecut-3d.js');

const geo = JSON.parse(fs.readFileSync(__dirname + '/_geo.json', 'utf8'));
const { root, hinges, panels } = buildHierarchy(geo, {});
hinges.forEach(h => { if (h.axis) h.group.rotation[h.axis] = h.to; });
root.updateMatrixWorld(true);

let pass = 0, fail = 0;
const C = (ok, name, d) => { ok ? pass++ : fail++; console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${d ? '  [' + d + ']' : ''}`); };
function wp(id, local) {
  const p = panels.find(x => x.id === id);
  const v = new THREE.Vector3(local[0], local[1], local[2]);
  p.mesh.localToWorld(v);
  return [v.x, v.y, v.z];
}

// 1. 盒体四壁 + 盖面（内 300×200×60 纸厚 1.5）
const Hw = 60, L = 300, y1 = 60, y2 = 260;
const ft = wp('front_wall', [L / 2, -Hw, 0]);   // 前壁顶部
C(Math.abs(ft[2] - Hw) < 2, '前壁立起 z=Hw', ft.map(v => +v.toFixed(1)).join(','));
const bt = wp('back_wall', [L / 2, Hw, 0]);
C(Math.abs(bt[2] - Hw) < 2, '后壁立起 z=Hw', bt.map(v => +v.toFixed(1)).join(','));
const lt = wp('left_wall', [-Hw, (y1 + y2) / 2, 0]);
C(Math.abs(lt[2] - Hw) < 2, '左壁立起 z=Hw', lt.map(v => +v.toFixed(1)).join(','));
const rt = wp('right_wall', [Hw, (y1 + y2) / 2, 0]);
C(Math.abs(rt[2] - Hw) < 2, '右壁立起 z=Hw', rt.map(v => +v.toFixed(1)).join(','));
const lid = wp('lid', [L / 2, Hw, 0]);
C(Math.abs(lid[2] - Hw) < 2, '盖面覆盖盒顶 z=Hw', lid.map(v => +v.toFixed(1)).join(','));

// 2. 外段钩端到达盒底（z≈0）
// 左外段 mesh 局部 x 最小端 = 钩端
const p = panels.find(x => x.id === 'left_insert');
const hookL = wp('left_insert', [p.localRect[0], (p.localRect[1] + p.localRect[3]) / 2, 0]);
C(Math.abs(hookL[2]) < 8, '左外段钩端到达盒底 z≈0', hookL.map(v => +v.toFixed(1)).join(','));
const p2 = panels.find(x => x.id === 'right_insert');
const hookR = wp('right_insert', [p2.localRect[2], (p2.localRect[1] + p2.localRect[3]) / 2, 0]);
C(Math.abs(hookR[2]) < 8, '右外段钩端到达盒底 z≈0', hookR.map(v => +v.toFixed(1)).join(','));

// 3. 插舌下端到达盒内（z 接近盒底，且在前壁内 y=60）
const tp = panels.find(x => x.id === 'tuck');
const tuckLow = wp('tuck', [L / 2, tp.localRect[3], 0]);   // 插舌垂下端（局部 y 最大）
C(tuckLow[2] < Hw && tuckLow[2] > -5, '插舌垂下到盒内近盒底', tuckLow.map(v => +v.toFixed(1)).join(','));
C(Math.abs(tuckLow[1] - y1) < 2, '插舌在前壁内侧 y=60', tuckLow.map(v => +v.toFixed(1)).join(','));

// 4. 外段在盒内（x 方向不越界；盒内空间 = 底面面板网区域）
const bottomP = panels.find(x => x.id === 'bottom');
const boxMinX = bottomP.localRect[0] + bottomP.tex.origin[0];
const boxMaxX = boxMinX + bottomP.size[0];
C(hookL[0] > boxMinX - 2 && hookL[0] < boxMaxX + 2, '左外段在盒内 x', hookL.map(v => +v.toFixed(1)).join(','));
C(hookR[0] > boxMinX - 2 && hookR[0] < boxMaxX + 2, '右外段在盒内 x', hookR.map(v => +v.toFixed(1)).join(','));

console.log(`\n===== 折叠插入盒底验证: ${pass}/${pass + fail} =====`);
process.exit(fail === 0 ? 0 : 1);
