// 折叠顺序验证：检查各进度阶段只有对应面板在折、未到阶段的面板保持平铺。
// 顺序：前后壁→腰部两翼→侧壁+外段→盖翼→盖面→插舌两翼→插舌。
// 关键：盖子和插舌都是"先折两翼、再摇动主面板"——两翼先折入盒中，面板再垂下。
'use strict';
const THREE = require('three');
global.THREE = THREE;
global.document = { createElement: () => ({ getContext: () => ({}) }) };
global.window = {};
const fs = require('fs');
const { buildHierarchy } = require('../../static/diecut-3d.js');

// 几何数据复用 API geometry（_geo.json，diecut_engine 生成），坐标从 geometry.panels 动态读取
const geo = JSON.parse(fs.readFileSync(__dirname + '/_geo.json', 'utf8'));
const { hinges } = buildHierarchy(geo, {});

function ease(x) { x = x < 0 ? 0 : x > 1 ? 1 : x; return x * x * (3 - 2 * x); }
function applyProgress(p) {
  hinges.forEach(h => {
    if (!h.axis) return;
    const raw = (p - h.range[0]) / (h.range[1] - h.range[0]);
    h.group.rotation[h.axis] = h.from + (h.to - h.from) * ease(raw);
  });
}
function angleOf(id) {
  const h = hinges.find(x => x.id === id);
  if (!h) return 0;
  return h.group.rotation[h.axis] || 0;
}
function frac(id) {
  const h = hinges.find(x => x.id === id);
  if (!h || !h.axis || !h.to) return 0;
  return Math.abs(angleOf(id) / h.to);
}

let pass = 0, fail = 0;
const C = (ok, name, d) => { ok ? pass++ : fail++; console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${d ? '  [' + d + ']' : ''}`); };

// 阶段1 中期 p=0.08：前后壁约 50%，其余不动
applyProgress(0.08);
C(Math.abs(frac('front_wall') - 0.5) < 0.1 && Math.abs(frac('back_wall') - 0.5) < 0.1, 'p=0.08 前后壁折 ~50%', `f=${frac('front_wall').toFixed(2)}`);
C(frac('lock_left') < 0.01 && frac('left_wall') < 0.01 && frac('lid') < 0.01 && frac('lid_wing_left') < 0.01 && frac('left_insert') < 0.01, 'p=0.08 腰部翼/侧壁/盖翼/盖面/外段未动', `lock=${frac('lock_left').toFixed(3)} side=${frac('left_wall').toFixed(3)} lid=${frac('lid').toFixed(3)}`);

// 阶段2 中期 p=0.24：腰部两翼 ~50%，前后壁已全折，侧壁未动
applyProgress(0.24);
C(Math.abs(frac('front_wall') - 1) < 0.01, 'p=0.24 前后壁已全折', frac('front_wall').toFixed(2));
C(Math.abs(frac('lock_left') - 0.5) < 0.1 && Math.abs(frac('back_wing_left') - 0.5) < 0.1, 'p=0.24 腰部两翼折 ~50%', `lock=${frac('lock_left').toFixed(2)}`);
C(frac('left_wall') < 0.01 && frac('lid') < 0.01, 'p=0.24 侧壁/盖面未动', `side=${frac('left_wall').toFixed(3)}`);

// 阶段3 中期 p=0.40：侧壁内段 ~50%，外段/盖面未动
applyProgress(0.40);
C(Math.abs(frac('left_wall') - 0.5) < 0.1 && Math.abs(frac('right_wall') - 0.5) < 0.1, 'p=0.40 侧壁内段折 ~50%', frac('left_wall').toFixed(2));
C(frac('left_insert') < 0.01 && frac('lid') < 0.01, 'p=0.40 外段/盖面未动', `outer=${frac('left_insert').toFixed(3)}`);

// 阶段4 中期 p=0.55：外段折 ~50%（180° 的 50%），盖翼/盖面未动
applyProgress(0.55);
C(Math.abs(frac('left_insert') - 0.5) < 0.1, 'p=0.55 外段折 ~50%', frac('left_insert').toFixed(2));
C(frac('lid_wing_left') < 0.01 && frac('lid') < 0.01, 'p=0.55 盖翼/盖面未动', `lidWing=${frac('lid_wing_left').toFixed(3)}`);

// 阶段5 中期 p=0.69：盖翼先折 ~50%，盖面未落
applyProgress(0.69);
C(Math.abs(frac('lid_wing_left') - 0.5) < 0.1 && Math.abs(frac('lid_wing_right') - 0.5) < 0.1, 'p=0.69 盖翼先折 ~50%', frac('lid_wing_left').toFixed(2));
C(frac('lid') < 0.01, 'p=0.69 盖面未动（盖翼先折入盒内）', frac('lid').toFixed(3));

// 阶段6 中期 p=0.79：盖面摇下盖住，插舌/插舌两翼未动
applyProgress(0.79);
C(Math.abs(frac('lid') - 0.5) < 0.1, 'p=0.79 盖面折 ~50%', frac('lid').toFixed(2));
C(frac('tuck') < 0.01 && frac('tuck_ear_left') < 0.01, 'p=0.79 插舌/两翼未动', `tuck=${frac('tuck').toFixed(3)}`);

// 阶段7 中期 p=0.895：插舌两翼先折 ~50%，插舌未动
applyProgress(0.895);
C(Math.abs(frac('tuck_ear_left') - 0.5) < 0.1 && Math.abs(frac('tuck_ear_right') - 0.5) < 0.1, 'p=0.895 插舌两翼先折 ~50%', frac('tuck_ear_left').toFixed(2));
C(frac('tuck') < 0.01, 'p=0.895 插舌未动（两翼先折）', frac('tuck').toFixed(3));

// 阶段8 中期 p=0.965：插舌垂下与前壁平行，两翼已全折
applyProgress(0.965);
C(Math.abs(frac('tuck') - 0.5) < 0.1, 'p=0.965 插舌折 ~50%', frac('tuck').toFixed(2));

console.log(`\n===== 折叠顺序验证: ${pass}/${pass + fail} =====`);
process.exit(fail === 0 ? 0 : 1);
