# 3D 折叠预览验证测试

验证 `static/diecut-3d.js` 的 3D 折叠模型正确性（纯 node，无需浏览器）。

## 安装

```bash
cd tests/3d
npm install   # 安装 three
```

## 运行

```bash
node verify-flat.js     # 张开图 == 原刀线图（每个面板 3D 范围必须精确等于网区域）
node verify-logic.js    # 面板纹理网区域一致性
node audit-box.js       # 折叠后各面板中心 + 法线审计（盒体组合正确性）
node verify-fold.js     # 折叠最终态：外段钩端/插舌到达盒底
node verify-sequence.js # 折叠顺序：前后壁→腰部两翼→侧壁+外段→盖翼→盖面→插舌两翼→插舌
```

全部 PASS（exit 0）即 3D 模型正确。

## 说明

- `diecut-3d.js` 用"网坐标几何"：面板 mesh 直接用刀版网坐标，张开图天然精确等于
  原始刀线图，杜绝方向/镜像/纹理错位。
- 折叠 = 正向运动学（面板以折痕为锚点嵌套在父面板的 THREE.Group，子面板继承父旋转），
  算法借鉴 [Somacharitha/dieline-fold](https://github.com/Somacharitha/dieline-fold)。
- 外段折 180° 贴侧壁内表面、钩端到达盒底；插舌垂下插入盒内前壁。
