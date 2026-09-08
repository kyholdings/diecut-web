// 验证 diecut-3d.buildHierarchy 对插口盒 fold contract 建层级 + p=1 折叠成方筒
global.THREE = require('three');
const D = require('../../static/diecut-3d.js');
const fs = require('fs');
const path = require('path');
const data = JSON.parse(fs.readFileSync(path.join(__dirname, '_insertion_contract.json'), 'utf8'));
const h = D.buildHierarchy(data.geometry, data.meta);

// 1) 所有 fold id 都被 materialize（panels 数量一致、无 skip）
const defTypes = (data.geometry.fold || []).length;
const made = h.panels.length;
console.log('fold defs:', defTypes, 'materialized panels:', made);
if (defTypes !== made) throw new Error('有 fold 未 materialize');

// 2) 应用 p=1：把每个 hinge 的 rotation 设到 to（照 applyProgress 逻辑）
const HALF = Math.PI / 2;
const ease = x => { const c = Math.min(1, Math.max(0, x)); return c * c * (3 - 2 * c); };
h.hinges.forEach(hn => {
  if (!hn.axis) return;
  const raw = (1 - hn.range[0]) / (hn.range[1] - hn.range[0]);
  const e = ease(raw);
  hn.group.rotation[hn.axis] = hn.from + (hn.to - hn.from) * e;
});
h.root.updateMatrixWorld(true);

// 3) 用 four.js 数学读四个墙面中心的 world 坐标，检查成方筒
function center(nm) {
  let p = null;
  h.panels.forEach(pn => { if (pn.id === nm) p = pn; });
  if (!p) return { x: NaN, y: NaN, z: NaN };
  const V = new THREE.Vector3();
  p.group.getWorldPosition(V);          // hinge group 原点 = 该面板锚点
  return { x: V.x, y: V.y, z: V.z };
}
['i_w1', 'i_w2', 'i_w3', 'i_w4'].forEach(nm => console.log('anchor@world', nm, center(nm)));

// 4) 各面板 mesh 中心世界坐标（更直观）
function meshCenter(nm) {
  let p = null;
  h.panels.forEach(pn => { if (pn.id === nm) p = pn; });
  if (!p) return NaN;
  const box = new THREE.Box3().setFromObject(p.group);
  const c = box.getCenter(new THREE.Vector3());
  return { x: +c.x.toFixed(1), y: +c.y.toFixed(1), z: +c.z.toFixed(1) };
}
['i_w1', 'i_w2', 'i_w3', 'i_w4', 'i_b1', 'i_b2', 'i_tongue', 'i_wing2'].forEach(nm => {
  console.log('meshCenter', nm, meshCenter(nm));
});
console.log('SMOKE OK');
