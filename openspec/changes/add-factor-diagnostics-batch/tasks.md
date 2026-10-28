# Tasks: add-factor-diagnostics-batch

## 1. 存储层（library.py）

- [x] 1.1 `factor_diagnostics` 表进 `init_library` schema（幂等建表）+ `set_factor_diagnostics` / `get_factor_diagnostics` 读写函数（JSON 序列化，单测：roundtrip / 无记录返回 None / reset 清空）

## 2. 日批模块（factors/diagnostics.py）

- [x] 2.1 `run_factor_diagnostics`：增量待算集合（窗口 IC ≥ 30 且读数缺失或 `as_of` 落后）→ 按需构建一次准入面板 → 逐因子求值 + check_monotonicity + compute_style_exposure → 落库；逐因子失败隔离；无待算因子零面板构建（单测：首跑补算 / 已新鲜跳过零面板 / 失败隔离 / 幂等重跑 / 新准入因子当日补算）

## 3. API 只读改造（api.py）

- [x] 3.1 `_factor_detail_diagnostics` 改纯读库 + 附 `diagnostics_as_of`；删除请求内面板/DSL 现算路径与相关 import（单测：详情零面板构建回归测试——monkeypatch `_panel_from_snapshot` 为 raise，断言详情各分支正常返回；有落库读数 → 返回读数；无 → null 两态）

## 4. 作业注册（outcome/ops/jobs.py）

- [x] 4.1 注册 `factor_diagnostics_daily`：JOB_IDS（按 17:15 触发序插位）、label、fn、schedule 四处 + 注册表断言测试（单测：作业可解析、schedule 正确、run 幂等不抛错）

## 5. 收口

- [ ] 5.1 全量后端测试 + ruff + mypy 通过；详情接口延迟人工抽查（本地起服实测毫秒级，部署后复核）
- [ ] 5.2 部署（--build，红线检查）+ ops 手动补跑存量回填 + `/factor-research` 实页展开详情验证（合并后执行，用户主导）
