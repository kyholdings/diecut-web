// 折叠后测量盒内尺寸：从 _geo.json（diecut_engine 生成）折叠，测各壁位置推盒内尺寸。
'use strict';
const THREE = require('three');
global.THREE = THREE;
global.document = { createElement: () => ({ getContext: () => ({}) }) };
global.window = {};
const fs = require('fs');
const { buildHierarchy } = require('../../static/diecut-3d.js');

const geo = JSON.parse(fs.readFileSync(__dirname + '/_geo.json', 'utf8'));
const L = geo.dimensions.length, W = geo.dimensions.width, H = geo.dimensions.height, t = geo.dimensions.thickness;
const der = geo.derived;
const meta = { blank: { back_flap_width_mm: der.wing_width, lock_width_mm: der.wing_width }, parameters: {} };
const { root, hinges, panels } = buildHierarchy(geo, meta);
hinges.forEach(h => { if (h.axis) h.group.rotation[h.axis] = h.to; });
root.updateMatrixWorld(true);

function wp(id, local) {
  const p = panels.find(x => x.id === id);
  const v = new THREE.Vector3(local[0], local[1], local[2]);
  p.mesh.localToWorld(v);
  return [v.x, v.y, v.z];
}
const lr = id => panels.find(x => x.id === id).localRect;

// 盒内尺寸：长=前壁X跨度, 宽=底面Y跨度, 高=前壁顶z
const frontTop = wp('front_wall', [L/2, lr('front_wall')[1], 0]);   // 自由边（局部 y 最小）
const backTop = wp('back_wall', [L/2, lr('back_wall')[3], 0]);
const lidZ = wp('lid', [L/2, 0, 0]);
console.log('=== 折叠后盒内尺寸（diecut_engine 生成，内 300×200×60）===');
console.log(`盒内长(X) = ${L}  (前壁X跨度)`);
console.log(`盒内宽(Y) = ${W}  (底面Y跨度)`);
console.log(`盒内高(Z) = ${frontTop[2].toFixed(1)}  (前壁顶z)`);
console.log(`盖面z = ${lidZ[2].toFixed(1)}`);
console.log(`前壁顶 = (${frontTop.map(v=>+v.toFixed(1)).join(',')})  后壁顶 = (${backTop.map(v=>+v.toFixed(1)).join(',')})`);
console.log();
console.log('目标: 内 300×200×60  外 316×204.5×63');
console.log(`外高估算 = 前壁顶z(${frontTop[2].toFixed(1)}) + 盖面t(${t}) + 底面t(${t}) = ${(frontTop[2]+2*t).toFixed(1)}`);
