# Tasks: update-outcome-band-volatility-scaled

> 2026-11-28 owner 拍板进入实施。勾选基于本机真实验证（pytest / vitest / Playwright）。

- [x] 中性带改波动率自适应：`max(1.5%, 0.5 × 20日波动)`（判定时按归属日前 20 日窗口计算，落库 `neutral_band_used`；不足 21 点回落 2%）
- [x] 连续超额统计族：平均超额、中位、超额胜率、超额 IR（均值/标准差 + t）、去重标的数（judgment/model/api 三层）
- [x] 双轨展示：track-record API 与主账本页同时输出旧带（胜率）与新口径（连续超额）读数（2 周评估期）
- [x] 存量 resolved 分类不动、不回填；新读数从切点起算（`band.settled_with_band` 披露）
- [x] 同窗随机基准（2026-11-28 增补）：`baseline` 块 + 战绩页读数条 + 不可用如实披露
- [x] metrics.md §1.24 提案小节状态从「提案未拍板」更新为拍板结果 + 切点登记
- [x] 后端测试：judgment 带宽/回落/存量不重判、连续统计、基准模块（纯函数 + I/O + 降级）、overview 端点
- [x] 前端测试：读数条渲染（有数据/基准不可用两态）
- [x] E2E（track-record 专属套件）：连续超额 + 去重标的 + 带宽披露 + 基准降级披露；全套 23 例绿
- [ ] 生产部署后观察 2 周：旧带 vs 新口径并排读数，由 owner 评估主读数切换
