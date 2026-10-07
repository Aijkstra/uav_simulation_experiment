# UAV 未来状态监督决策实验平台

本仓库提供固定材料的无人机监督决策实验程序。参试者根据当前与未来风场信息，在立即放行、延迟起飞和改道飞行之间选择预计空中飞行时间最短的方案。程序包括理解检查、4 个练习、18 个正式试次、3 个 block 的评价、结束问卷和 CSV 导出。

## 环境与安装

需要 Python 3.11+ 和 Node.js 20+。固定试次材料位于 `data/UAV_Trial_Design.xlsx`。

```bash
python3.11 -m venv .venv-macos
./.venv-macos/bin/python -m pip install -e '.[dev]'
cd frontend
npm ci
npm run build
cd ..
./start.sh
```

Windows 可按相同步骤创建 `.venv`，安装 Python 依赖并构建前端，然后运行 `start.ps1`。服务启动后访问 `http://127.0.0.1:8000`。

## 检查

```bash
./.venv-macos/bin/python -m pytest
./.venv-macos/bin/python -m uav_sim.audit
cd frontend && npm test && npm run build
```

`frontend/public/assets` 已包含运行所需底图。`tools/build_basemap.py` 仅用于从另行取得的 OpenStreetMap PBF 重新生成底图，不影响现有底图的使用。
