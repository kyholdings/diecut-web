// 折叠后精确审计：输出每个面板中心位置、法线方向，判断盒体组合正确性。
// 坐标系：root 帧 = 刀版网坐标（X=网x, Y=网y, Z 向上）。
'use strict';
const THREE = require('three');
global.THREE = THREE;
global.document = { createElement: () => ({ getContext: () => ({}) }) };
global.window = {};
const fs = require('fs');
const { buildHierarchy } = require('../../static/diecut-3d.js');

// 几何数据复用 API geometry（_geo.json，diecut_engine 生成），坐标从 geometry.panels 动态读取
const geo = JSON.parse(fs.readFileSync(__dirname + '/_geo.json', 'utf8'));
const { root, hinges, panels } = buildHierarchy(geo, {});
hinges.forEach(h => { if (h.axis) h.group.rotation[h.axis] = h.to; });
root.updateMatrixWorld(true);

function centerOf(id) {
  const p = panels.find(x => x.id === id);
  const lr = p.localRect;
  const v = new THREE.Vector3((lr[0] + lr[2]) / 2, (lr[1] + lr[3]) / 2, 0);
  p.mesh.localToWorld(v);
  return [v.x, v.y, v.z].map(x => +x.toFixed(1));
}
function normalOf(id) {
  const p = panels.find(x => x.id === id);
  const m = p.mesh.matrixWorld;
  const n = new THREE.Vector3(m.elements[8], m.elements[9], m.elements[10]).normalize();
  return n.toArray().map(x => +x.toFixed(2));
}

console.log('=== 折叠后各面板审计（网坐标 root，L=300 W=200 H=60 纸厚 1.5）===');
const rows = [
  ['bottom', '底面'], ['front_wall', '前壁'], ['back_wall', '后壁'],
  ['left_wall', '左壁'], ['right_wall', '右壁'],
  ['left_gap', '左间隙段'], ['right_gap', '右间隙段'],
  ['left_insert', '左插入段'], ['right_insert', '右插入段'],
  ['lid', '盖面'], ['tuck', '插舌'],
  ['lock_left', '左锁扣翼'], ['lock_right', '右锁扣翼'],
  ['back_wing_left', '左后翼'], ['back_wing_right', '右后翼'],
  ['lid_wing_left', '左盖翼'], ['lid_wing_right', '右盖翼'],
  ['tuck_ear_left', '左耳翼'], ['tuck_ear_right', '右耳翼'],
];
rows.forEach(([id, label]) => {
  console.log(`${label.padEnd(6)} 中心[${centerOf(id).join(',')}]  法线[${normalOf(id).join(',')}]`);
});

console.log('\n=== 期望（root=网坐标：底面 y∈[60,260]）===');
console.log('底面  中心≈(156,160,0)   法线(0,0,1)');
console.log('前壁  中心≈(156,60,30)   法线(0,±1,0)');
console.log('后壁  中心≈(156,260,30)  法线(0,∓1,0)');
console.log('左壁  中心≈(6,160,30)    法线(±1,0,0)');
console.log('右壁  中心≈(306,160,30)  法线(∓1,0,0)');
console.log('盖面  中心≈(156,160,60)  法线(0,0,-1)');
console.log('外段/翼 应折入盒内（法线不指向盒外）');
