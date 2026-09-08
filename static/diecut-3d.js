/* =====================================================================
 * diecut-3d.js — 自锁飞机盒刀版 3D 折叠预览
 *
 * 核心思路（正向运动学，借鉴 dieline-fold）：
 *   - 每个面板的 mesh 直接用"刀版网坐标"几何（netRect），展开态天然
 *     精确等于原始刀线图，杜绝方向/镜像/纹理错位。
 *   - 铰链组 position = 折线的网坐标锚点；mesh 局部 = 网坐标 - 锚点。
 *   - 折叠 = 铰链绕局部轴（水平折线绕 x、竖直折线绕 y）旋转，子面板
 *     继承父面板旋转。
 *   - 纹理 = 真实刀线（CUT 实线 / CREASE 虚线）绘制到面板局部画布。
 *
 * API: window.Diecut3D = { mount, update, setProgress, toggleFold, play,
 *                          dispose, getProgress, setCamera, hideGrid, setSolid }
 * ===================================================================== */
(function () {
  'use strict';

  var HALF = Math.PI / 2;

  // ---------- 折叠顺序（0..1 进度） ----------
  // 真实飞机盒折叠次序（两翼先折、再摇动主面板，翼才能插入盒中）：
  //   1. 前后壁折 90°（盖面+插舌随竖）→ 2. 腰部两翼（锁扣翼+后壁翼）折 90°
  //   3. 侧壁内段立起 + 外段折入（包裹腰部两翼）
  //   4. 盖翼先折 90° → 5. 盒盖摇下盖住（盖翼随盖面插入盒内两侧）
  //   6. 插舌两翼先折 90° → 7. 插舌垂下与前壁平行（两翼插入侧壁空隙）
  var STAGES = {
    frontBack: [0.00, 0.16],   // 前后壁折起（盖面+插舌随竖）
    waistWings: [0.16, 0.32],  // 腰部两翼（锁扣翼+后壁翼）折入
    sides: [0.32, 0.48],       // 侧壁内段立起
    outer: [0.48, 0.62],       // 外段折入包裹腰部两翼
    lidWings: [0.64, 0.74],    // 盖翼先折（盖面仍竖立）
    lid: [0.74, 0.84],         // 盖面摇下盖住（盖翼随盖面插入盒内两侧）
    tuckEars: [0.86, 0.93],    // 插舌两翼先折
    tuck: [0.93, 1.00],        // 插舌垂下与前壁平行（两翼插入侧壁空隙）
  };

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function ease(x) { x = clamp(x, 0, 1); return x * x * (3 - 2 * x); }

  // 面板纸板色（轻微色调差异便于区分结构）
  var FILL = {
    base: '#f2ead6',
    wall: '#efe5cf',
    side: '#eae2cb',
    wing: '#efe7d0',
    outer: '#e6ddc6',
    lid: '#f4ecda',
    tuck: '#f0e6cf',
  };

  // =====================================================================
  // 构建 3D 层级（纯 three，可离线测试）
  //
  // 坐标系：3D X=网x, Y=网y, Z=0（展开态）。root 帧 = 网坐标。
  // 每个面板：anchor 为折线锚点（网坐标），mesh 局部 = 网坐标 - anchor。
  // 铰链绕局部轴旋转：水平折线（沿网 x）→ axis 'x'；竖直折线（沿网 y）→ 'y'。
  // =====================================================================
  function buildHierarchy(geometry, meta) {
    var g = geometry, m = meta || {};
    var L = g.dimensions.length;
    var der = g.derived || {};
    var Hw = der.wall_height, Wb = der.bottom_height, lidH = der.lid_height, tab = der.tab_depth;
    var wing = der.wing_width, sideInner = der.side_inner, sideOuter = der.side_outer;
    var blank = m.blank || {};
    var back = blank.back_flap_width_mm || wing;
    var lock = blank.lock_width_mm || wing;
    var hookD = Math.max(8, g.dimensions.height * 0.15);   // 大侧壁外段末端凸起钩深度
    var outerLen = sideOuter + hookD;                       // 外段面板含钩的实际净长
    // 面板坐标一律来自 geometry.panels，不在此推导；y1/y5 仅用于相机取景。
    var y1 = Hw;                        // 前壁顶（底面底）
    var y5 = Hw + Wb + Hw + lidH + tab; // 插舌顶（展开总高）

    var root = new THREE.Group();
    var hinges = [];
    var panels = [];
    var hingesById = {};

    // 面板坐标（bounds / anchor / shape）完全来自 API geometry.panels —— 此处不硬编码坐标。
    // DEFS 只保留折叠逻辑：id / parent / axis(旋转轴) / to / range(折叠阶段) / fill(纸板色)。
    var byId = {};
    (g.panels || []).forEach(function (p) { byId[p.id] = p; });
    // 折叠动力学优先读 geometry.fold（后端按盒型输出，如插口盒）；无则回退飞机盒 DEFS。
    var AIRBASE = [
      { id: 'bottom', parent: 'root', axis: null, to: 0, range: [0, 1], fill: FILL.base },
      { id: 'front_wall', parent: 'bottom', axis: 'x', to: -HALF, range: STAGES.frontBack, fill: FILL.wall },
      { id: 'back_wall', parent: 'bottom', axis: 'x', to: HALF, range: STAGES.frontBack, fill: FILL.wall },
      { id: 'left_wall', parent: 'bottom', axis: 'y', to: HALF, range: STAGES.sides, fill: FILL.side },
      { id: 'right_wall', parent: 'bottom', axis: 'y', to: -HALF, range: STAGES.sides, fill: FILL.side },
      { id: 'left_gap', parent: 'left_wall', axis: 'y', to: HALF, range: STAGES.outer, fill: FILL.outer },
      { id: 'right_gap', parent: 'right_wall', axis: 'y', to: -HALF, range: STAGES.outer, fill: FILL.outer },
      { id: 'left_insert', parent: 'left_gap', axis: 'y', to: HALF, range: STAGES.outer, fill: FILL.outer },
      { id: 'right_insert', parent: 'right_gap', axis: 'y', to: -HALF, range: STAGES.outer, fill: FILL.outer },
      { id: 'lid', parent: 'back_wall', axis: 'x', to: HALF, range: STAGES.lid, fill: FILL.lid },
      { id: 'tuck', parent: 'lid', axis: 'x', to: HALF, range: STAGES.tuck, fill: FILL.tuck },
      { id: 'lock_left', parent: 'front_wall', axis: 'y', to: HALF, range: STAGES.waistWings, fill: FILL.wing },
      { id: 'lock_right', parent: 'front_wall', axis: 'y', to: -HALF, range: STAGES.waistWings, fill: FILL.wing },
      { id: 'back_wing_left', parent: 'back_wall', axis: 'y', to: HALF, range: STAGES.waistWings, fill: FILL.wing },
      { id: 'back_wing_right', parent: 'back_wall', axis: 'y', to: -HALF, range: STAGES.waistWings, fill: FILL.wing },
      { id: 'lid_wing_left', parent: 'lid', axis: 'y', to: HALF, range: STAGES.lidWings, fill: FILL.wing },
      { id: 'lid_wing_right', parent: 'lid', axis: 'y', to: -HALF, range: STAGES.lidWings, fill: FILL.wing },
      { id: 'tuck_ear_left', parent: 'tuck', axis: 'y', to: HALF, range: STAGES.tuckEars, fill: FILL.wing },
      { id: 'tuck_ear_right', parent: 'tuck', axis: 'y', to: -HALF, range: STAGES.tuckEars, fill: FILL.wing },
    ];
    var DEFS = g.fold ? g.fold : AIRBASE;

    function makePanel(def) {
      var pd = byId[def.id];
      if (!pd) return;   // API geometry 未提供该面板坐标则跳过
      var ax = pd.anchor[0], ay = pd.anchor[1];
      var x0 = pd.bounds[0], y0n = pd.bounds[1], x1 = pd.bounds[2], y1n = pd.bounds[3];
      var shape = pd.shape || null;
      var holes = pd.holes || [];   // 开窗孔（局部坐标多边形，= 网坐标 - 锚点），用于把窗内材料镂空成透孔
      // mesh 局部坐标 = 网坐标 - 锚点
      var lx0 = x0 - ax, lx1 = x1 - ax, ly0 = y0n - ay, ly1 = y1n - ay;
      var localRect = [Math.min(lx0, lx1), Math.min(ly0, ly1), Math.max(lx0, lx1), Math.max(ly0, ly1)];

      var parent = def.parent === 'root' ? root : hingesById[def.parent].group;
      var hinge = new THREE.Group();
      // 铰链锚点相对父面板 mesh 原点：父帧坐标 = 网坐标 - 父锚点。
      // 由于父 hinge 组就在父锚点（网坐标），子 hinge 位置 = 网坐标差。
      // 父面板无缩放，因此子 hinge 在父帧 = 本面板锚点 - 父面板锚点。
      var parentDef = def.parent === 'root' ? null : DEFS.find(function (d) { return d.id === def.parent; });
      if (parentDef) {
        var pa = byId[parentDef.id].anchor;
        hinge.position.set(ax - pa[0], ay - pa[1], 0);
      } else {
        hinge.position.set(ax, ay, 0);
      }
      parent.add(hinge);

      var boardT = g.dimensions && g.dimensions.thickness ? g.dimensions.thickness : 0;
      var mesh;
      if (shape || holes.length) {
        // 带轮廓（梯形翼/斜切翼/插舌/粘合片）或开窗孔：ShapeGeometry / ExtrudeGeometry。
        // 矩形面板无 shape 但带孔（插口盒段3开窗墙）时，以外接局部矩形为外形、窗孔为镂空
        // （three.ExtrudeGeometry 的 hole），窗内材料被真正切掉 → 折叠后可透视。
        var shp = new THREE.Shape();
        if (shape) {
          shp.moveTo(shape[0][0], shape[0][1]);
          for (var s = 1; s < shape.length; s++) shp.lineTo(shape[s][0], shape[s][1]);
        } else {
          shp.moveTo(localRect[0], localRect[1]);
          shp.lineTo(localRect[2], localRect[1]);
          shp.lineTo(localRect[2], localRect[3]);
          shp.lineTo(localRect[0], localRect[3]);
        }
        shp.closePath();
        for (var hi = 0; hi < holes.length; hi++) {
          var hp = holes[hi];
          var hpath = new THREE.Path();
          hpath.moveTo(hp[0][0], hp[0][1]);
          for (var hk = 1; hk < hp.length; hk++) hpath.lineTo(hp[hk][0], hp[hk][1]);
          hpath.closePath();
          shp.holes.push(hpath);
        }
        if (boardT > 0) {
          mesh = new THREE.Mesh(new THREE.ExtrudeGeometry(shp, { depth: boardT, bevelEnabled: false }), null);
          mesh.geometry.translate(0, 0, -boardT / 2);   // 纸板厚度，中性面居中
        } else {
          mesh = new THREE.Mesh(new THREE.ShapeGeometry(shp, 24), null);
        }
        if (holes.length) {
          // 孔面板（插口盒段3开窗墙）：Shape/Extrude 默认用网坐标原值做 UV（原始 mm 值，采样被边缘
          // 钳制→呈扁平色）；这里把 UV 归一化到局部矩形 [0,1]，与 BoxGeometry/PlaneGeometry 一致，
          // 保住墙面刀线/开窗轮廓纹理而不再一块纯色。
          var pa = mesh.geometry.attributes.position, uva = mesh.geometry.attributes.uv;
          var rw = localRect[2] - localRect[0], rh = localRect[3] - localRect[1];
          for (var vi = 0; vi < pa.count; vi++) {
            uva.setXY(vi, (pa.getX(vi) - localRect[0]) / rw, (pa.getY(vi) - localRect[1]) / rh);
          }
          uva.needsUpdate = true;
        }
      } else {
        var w = x1 - x0, h = y1n - y0n;
        if (boardT > 0) {
          var boxG = new THREE.BoxGeometry(w, h, boardT);   // 体现纸板厚度
          boxG.translate((x0 + x1) / 2 - ax, (y0n + y1n) / 2 - ay, 0);
          mesh = new THREE.Mesh(boxG, null);
        } else {
          var planeG = new THREE.PlaneGeometry(w, h);
          planeG.translate((x0 + x1) / 2 - ax, (y0n + y1n) / 2 - ay, 0);
          mesh = new THREE.Mesh(planeG, null);
        }
      }
      hinge.add(mesh);

      var rec = {
        id: def.id, group: hinge, mesh: mesh, axis: def.axis, from: 0, to: def.to || 0,
        range: def.range || [0, 1], localRect: localRect, fill: def.fill,
        holes: holes,   // 开窗孔（局部坐标多边形），供纹理取景/后续判定复用
        size: [x1 - x0, y1n - y0n],
        tex: { origin: [ax, ay], xAxis: [1, 0], yAxis: [0, 1], size: [x1 - x0, y1n - y0n] },
      };
      hinges.push(rec);
      hingesById[def.id] = rec;
      panels.push(rec);
      return rec;
    }

    DEFS.forEach(makePanel);

    return { root: root, hinges: hinges, panels: panels, dims: { L: L, Wb: Wb, Hw: Hw, lidH: lidH, tab: tab, wing: wing, sideInner: sideInner, sideOuter: sideOuter, outerLen: outerLen, back: back, lock: lock, y1: y1, y5: y5 } };
  }

  // =====================================================================
  // 把刀版网坐标 segment 变换到面板局部坐标并绘制纹理
  // =====================================================================
  function netToLocal(nx, ny, tex) {
    var dx = nx - tex.origin[0], dy = ny - tex.origin[1];
    return [dx * tex.xAxis[0] + dy * tex.xAxis[1], dx * tex.yAxis[0] + dy * tex.yAxis[1]];
  }

  function drawPanelTexture(panel, segments, scale) {
    var lr = panel.localRect;   // [minX, minY, maxX, maxY]
    var rw = lr[2] - lr[0], rh = lr[3] - lr[1];
    var px = Math.max(4, Math.round(rw * scale));
    var py = Math.max(4, Math.round(rh * scale));
    var canvas = document.createElement('canvas');
    canvas.width = px;
    canvas.height = py;
    var ctx = canvas.getContext('2d');

    // 面板形状填充（矩形整幅 / 梯形按顶点），其余透明
    ctx.beginPath();
    if (panel.shape) {
      panel.shape.forEach(function (p, i) {
        var sx = (p[0] - lr[0]) * scale, sy = py - (p[1] - lr[1]) * scale;
        if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
      });
      ctx.closePath();
    } else {
      ctx.rect(0, 0, px, py);
    }
    ctx.fillStyle = panel.fill;
    ctx.fill();

    // 刀线：CUT 实线 / CREASE 虚线（含 HALFCUT 点线）
    var lwCut = Math.max(1, 2.2 * scale / 1.5);
    var lwCrease = Math.max(1, 2.0 * scale / 1.5);
    for (var i = 0; i < segments.length; i++) {
      var seg = segments[i];
      var kind = seg.kind;
      if (kind !== 'cut' && kind !== 'crease' && kind !== 'halfcut') continue;
      var pts = [];
      var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (var j = 0; j < seg.points.length; j++) {
        var lp = netToLocal(seg.points[j][0], seg.points[j][1], panel.tex);
        pts.push(lp);
        if (lp[0] < minX) minX = lp[0];
        if (lp[1] < minY) minY = lp[1];
        if (lp[0] > maxX) maxX = lp[0];
        if (lp[1] > maxY) maxY = lp[1];
      }
      // 与面板局部范围相交才绘制
      if (maxX < lr[0] - 0.5 || minX > lr[2] + 0.5 || maxY < lr[1] - 0.5 || minY > lr[3] + 0.5) continue;
      ctx.save();
      ctx.strokeStyle = kind === 'cut' ? '#1f2937' : kind === 'crease' ? '#d6364f' : '#1d4ed8';
      ctx.lineWidth = kind === 'cut' ? lwCut : kind === 'crease' ? lwCrease : Math.max(1, lwCrease * 0.7);
      ctx.setLineDash(kind === 'cut' ? [] : kind === 'crease' ? [7 * scale / 1.5, 5 * scale / 1.5] : [2 * scale / 1.5, 4 * scale / 1.5]);
      ctx.beginPath();
      for (var k = 0; k < pts.length; k++) {
        var sx2 = (pts[k][0] - lr[0]) * scale, sy2 = py - (pts[k][1] - lr[1]) * scale;
        if (k === 0) ctx.moveTo(sx2, sy2); else ctx.lineTo(sx2, sy2);
      }
      ctx.stroke();
      ctx.restore();
    }
    return canvas;
  }

  // =====================================================================
  // 渲染器 + 交互
  // =====================================================================
  var scene, camera, renderer, netRoot;
  var hinges = [];
  var segments = [];
  var texScale = 1.5;
  var foldProgress = 0;
  var foldAnim = null;
  var autoRotate = true;
  var dragging = false;
  var needsRender = true;   // 复用补帧：仅当相机/折叠/自动旋转变化时才真正 render，空闲不烧 GPU
  var lastX = 0, lastY = 0;
  var radius = 380, theta = 0.9, phi = 0.85;
  var target = new THREE.Vector3(0, 30, 10);
  var cameraTarget = new THREE.Vector3(0, 30, 10);
  var lastGeometry = null, lastMeta = null;
  var solidMode = false;
  // 缩放/取景：范围、home 视角（build 时按需刷新）、多点触摸（移动端捏合缩放）
  var MIN_R = 100, MAX_R = 6000;
  var homeRadius = 380, homeTheta = 0.9, homePhi = 0.85;
  var pointers = {};               // pointerId -> {x, y}（活动触摸点）
  var pinchStartDist = 0, pinchStartRadius = 0;
  var controlsEl = null;           // 缩放控制浮层（±/重置视角）

  function materialFor(panel, canvas) {
    if (solidMode) {
      var hash = 0;
      for (var i = 0; i < panel.id.length; i++) hash = (hash * 31 + panel.id.charCodeAt(i)) >>> 0;
      var hue = (hash % 360) / 360;
      return new THREE.MeshLambertMaterial({
        color: new THREE.Color().setHSL(hue, 0.55, 0.72),
        side: THREE.DoubleSide,
      });
    }
    var tex = new THREE.CanvasTexture(canvas);
    tex.anisotropy = 4;
    return new THREE.MeshLambertMaterial({
      map: tex,
      side: THREE.DoubleSide,
    });
  }

  function disposeObject(obj) {
    obj.traverse(function (node) {
      if (node.geometry) node.geometry.dispose();
      if (node.material) {
        if (node.material.map) node.material.map.dispose();
        node.material.dispose();
      }
    });
  }

  function build(geometry, meta) {
    var h = buildHierarchy(geometry, meta);
    hinges = h.hinges;
    segments = geometry.segments || [];

    var maxDim = h.dims.y5 + 2 * (h.dims.Hw);
    texScale = clamp(1500 / maxDim, 1.0, 3.0);

    h.panels.forEach(function (panel) {
      if (!panel.mesh) return;
      var canvas = drawPanelTexture(panel, segments, texScale);
      panel.mesh.material = materialFor(panel, canvas);
      panel.texture = panel.mesh.material.map;
      panel.mesh.userData.label = panel.id;
    });

    netRoot = new THREE.Group();
    netRoot.add(h.root);
    var box = new THREE.Box3().setFromObject(netRoot);
    var center = box.getCenter(new THREE.Vector3());
    var size = box.getSize(new THREE.Vector3());
    netRoot.position.set(-center.x, -center.y, 0);
    netRoot.updateMatrixWorld(true);

    // 相机目标 = 当前几何(展开网板)的包围盒中心，并按视野角精确 fit 距离：
    // 用包围盒半对角 / tan(较小半视角) 求得最小完整入镜距离，杜绝魔法数估算
    // 导致的「盒子超出视锥看不见」。无论宽高比/移动端竖屏(窄 aspect)，盒体
    // 恒居中且完整可见。homeRadius 随时覆盖，重置视角即回到「恰好套住」的距离。
    var wbox = new THREE.Box3().setFromObject(netRoot);
    var wc = wbox.getCenter(new THREE.Vector3());
    var wsize = wbox.getSize(new THREE.Vector3());
    frameTo(wc.x, wc.y, wc.z, wsize.x, wsize.y, wsize.z);

    scene.add(netRoot);
    applyProgress(0);
    if (camera) updateCamera();
  }

  function applyProgress(p) {
    foldProgress = p;
    for (var i = 0; i < hinges.length; i++) {
      var hn = hinges[i];
      if (!hn.axis) continue;
      var raw = (p - hn.range[0]) / (hn.range[1] - hn.range[0]);
      var e = ease(raw);
      hn.group.rotation[hn.axis] = hn.from + (hn.to - hn.from) * e;
    }
    needsRender = true;
  }

  function animateTo(target, duration) {
    if (foldAnim) cancelAnimationFrame(foldAnim);
    var start = foldProgress;
    var t0 = performance.now();
    function frame(now) {
      var amt = clamp((now - t0) / duration, 0, 1);
      applyProgress(start + (target - start) * amt);
      if (amt < 1) foldAnim = requestAnimationFrame(frame);
      else foldAnim = null;
    }
    foldAnim = requestAnimationFrame(frame);
  }

  function updateCamera() {
    camera.position.set(
      target.x + radius * Math.sin(phi) * Math.sin(theta),
      target.y + radius * Math.cos(phi),
      target.z + radius * Math.sin(phi) * Math.cos(theta)
    );
    camera.lookAt(target.x, target.y, target.z);
    needsRender = true;
  }

  // 把当前几何包围盒中心设为相机目标，并按视野角精确计算最小完整入镜距离。
  // 保留用户当前旋转角度(theta/phi)，只重算中心与半径，转屏/切换时保证盒子居中完整。
  function frameTo(cx, cy, cz, sx, sy, sz) {
    target.set(cx, cy, cz);
    cameraTarget.set(cx, cy, cz);
    var halfV = camera.fov * Math.PI / 360;                     // 垂直半视角
    var halfH = Math.atan(Math.tan(halfV) * camera.aspect);     // 水平半视角
    var half = Math.min(halfV, halfH);                          // 取更严(更小)约束
    var diag = Math.sqrt(sx * sx + sy * sy + sz * sz);          // 包围盒对角线
    radius = clamp((0.5 * diag) / Math.tan(half) * 1.08, MIN_R, MAX_R);
    homeRadius = radius;
    if (camera) updateCamera();
  }

  // 乘性缩放（滚轮/按钮/双击共用），保持随手势比例
  function zoomBy(factor) {
    radius = clamp(radius * factor, MIN_R, MAX_R);
    updateCamera();
  }
  function resetView() {
    radius = homeRadius; theta = homeTheta; phi = homePhi;
    updateCamera();
  }

  // 缩放控制浮层（+ / − / 重置视角）。host 是 #preview-3d；宿主重挂载后自动重建。
  function ensureControls(host) {
    if (controlsEl && controlsEl.isConnected) return;
    controlsEl = document.createElement('div');
    controlsEl.style.cssText =
      'position:absolute;top:10px;right:10px;display:flex;gap:6px;z-index:5;font-family:system-ui,sans-serif;';
    var btns = [
      { t: '+', ti: '放大', act: function () { zoomBy(0.7); } },
      { t: '−', ti: '缩小', act: function () { zoomBy(1.4); } },
      { t: '⌂', ti: '重置视角', act: resetView },
    ];
    btns.forEach(function (b) {
      var el = document.createElement('button');
      el.type = 'button';
      el.textContent = b.t;
      el.title = b.ti;
      el.style.cssText =
        'width:36px;height:36px;border:none;border-radius:9px;background:rgba(255,255,255,.92);' +
        'color:#0f172a;font-size:19px;font-weight:600;line-height:1;cursor:pointer;' +
        'box-shadow:0 1px 5px rgba(0,0,0,.18);';
      el.addEventListener('pointerdown', function (ev) { ev.stopPropagation(); });
      el.addEventListener('click', function (ev) { ev.stopPropagation(); b.act(); });
      controlsEl.appendChild(el);
    });
    host.appendChild(controlsEl);
  }

  function mount() {
    var host = document.getElementById('preview-3d');
    if (!host || !window.THREE) return;
    if (renderer) {
      if (!host.contains(renderer.domElement)) host.appendChild(renderer.domElement);
      ensureControls(host);
      resize();
      return;
    }
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf3f4f6);
    camera = new THREE.PerspectiveCamera(38, 1, 1, 20000);
    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.appendChild(renderer.domElement);

    scene.add(new THREE.HemisphereLight(0xffffff, 0x9aa7b8, 1.5));
    var key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(300, 420, 280);
    scene.add(key);
    var fill = new THREE.DirectionalLight(0x8fb3ff, 0.35);
    fill.position.set(-260, 120, -220);
    scene.add(fill);

    var grid = new THREE.GridHelper(700, 24, 0xc3cdd9, 0xdfe4ea);
    grid.position.y = -0.2;
    scene.add(grid);

    function onDown(e) {
      pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      if (Object.keys(pointers).length === 2) {
        var p = Object.values(pointers);
        pinchStartDist = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
        pinchStartRadius = radius;
        dragging = false;
      } else {
        dragging = true;
        lastX = e.clientX; lastY = e.clientY;
      }
      try { host.setPointerCapture(e.pointerId); } catch (err) {}
      autoRotate = false;
    }
    function onMove(e) {
      if (pointers[e.pointerId]) pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      var ids = Object.keys(pointers);
      // 双指捏合：用两指间距比例缩放（移动端手势）
      if (ids.length >= 2) {
        var p = Object.values(pointers);
        var d = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
        if (pinchStartDist > 0) {
          radius = clamp(pinchStartRadius * (pinchStartDist / d), MIN_R, MAX_R);
          updateCamera();
        }
        return;
      }
      if (!dragging) return;
      var dx = e.clientX - lastX, dy = e.clientY - lastY;
      lastX = e.clientX; lastY = e.clientY;
      theta -= dx * 0.007;
      phi = clamp(phi - dy * 0.007, 0.15, Math.PI - 0.15);
      updateCamera();
    }
    function onUp(e) {
      if (pointers[e.pointerId]) delete pointers[e.pointerId];
      var ids = Object.keys(pointers);
      if (ids.length === 1) {           // 剩单指，续接旋转
        lastX = pointers[ids[0]].x; lastY = pointers[ids[0]].y;
        dragging = true;
      } else if (ids.length === 0) {
        dragging = false;
      }
    }
    function onWheel(e) {
      e.preventDefault();
      zoomBy(Math.pow(1.0012, e.deltaY));   // 乘性缩放，随手势比例
    }
    function onDblClick() { zoomBy(0.7); }
    function onTouchMove(e) { if (e.touches.length > 1) e.preventDefault(); }
    host.addEventListener('pointerdown', onDown);
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    host.addEventListener('wheel', onWheel, { passive: false });
    host.addEventListener('dblclick', onDblClick);
    host.addEventListener('touchmove', onTouchMove, { passive: false });
    window.addEventListener('resize', resize);
    ensureControls(host);
    resize();          // 首次挂载即按容器铺满，避免 canvas 默认 300×150 致窗口过小
    updateCamera();

    function render() {
      requestAnimationFrame(render);
      if (autoRotate && !dragging) {
        theta += 0.0028;
        updateCamera();
      }
      if (needsRender) { renderer.render(scene, camera); needsRender = false; }
    }
    render();
  }

  function resize() {
    var host = document.getElementById('preview-3d');
    if (!host || !renderer) return;
    var w = Math.max(host.clientWidth, 320);
    var h = Math.max(host.clientHeight, 360);
    // updateStyle=true：让 three 同步 canvas 的 CSS width/height 为宿主的 w×h。
    // 否则移动端 devicePixelRatio>1 时，canvas 无 style 会以 attribute(=CSS×DPR) 尺寸
    // 显示，以 DPR 倍溢出宿主，导致「盒子在舞台右下角」。（桌面 DPR=1 恰好相等，故仅移动端异常）
    renderer.setSize(w, h, true);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    // 转屏/容器尺寸变化后重取景：若已有盒体，按当前包围盒重新 fit 居中，
    // 保证移动端竖屏⇄横屏切换时盒子始终完整可见、中心不跑偏。
    if (netRoot) {
      var b = new THREE.Box3().setFromObject(netRoot);
      var c = b.getCenter(new THREE.Vector3());
      var s = b.getSize(new THREE.Vector3());
      frameTo(c.x, c.y, c.z, s.x, s.y, s.z);
    } else {
      updateCamera();
    }
  }

  // =====================================================================
  // 对外 API
  // =====================================================================
  var Diecut3D = {
    mount: mount,
    update: function (geometry, meta) {
      if (!geometry || !geometry.dimensions) return;
      lastGeometry = geometry;
      lastMeta = meta;
      mount();
      if (scene && netRoot) {
        scene.remove(netRoot);
        disposeObject(netRoot);
        netRoot = null;
      }
      build(geometry, meta);
    },
    setProgress: function (p) {
      if (foldAnim) cancelAnimationFrame(foldAnim);
      foldAnim = null;
      applyProgress(p);
    },
    toggleFold: function () {
      if (netRoot) animateTo(foldProgress < 0.5 ? 1 : 0, 1200);
      return foldProgress >= 0.5;
    },
    play: function (toClose) {
      animateTo(toClose === undefined ? (foldProgress < 0.5 ? 1 : 0) : toClose, 1400);
    },
    dispose: function () {
      if (scene && netRoot) {
        scene.remove(netRoot);
        disposeObject(netRoot);
        netRoot = null;
      }
    },
    getProgress: function () { return foldProgress; },
    setCamera: function (horizDeg, vertRad, dist) {
      theta = horizDeg === undefined ? theta : (horizDeg * Math.PI / 180);
      if (vertRad !== undefined) phi = clamp(vertRad, 0.15, Math.PI - 0.15);
      if (dist !== undefined) radius = clamp(dist, MIN_R, MAX_R);
      if (camera) updateCamera();
    },
    hideGrid: function () {
      if (scene) scene.children.forEach(function (c) { if (c.isGridHelper) scene.remove(c); });
    },
    setSolid: function (on) {
      solidMode = !!on;
      if (scene && netRoot) {
        scene.remove(netRoot);
        disposeObject(netRoot);
        netRoot = null;
        if (lastGeometry) build(lastGeometry, lastMeta || {});
      }
    },
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { buildHierarchy: buildHierarchy, netToLocal: netToLocal, drawPanelTexture: drawPanelTexture, STAGES: STAGES };
  }
  if (typeof window !== 'undefined') window.Diecut3D = Diecut3D;
}());
