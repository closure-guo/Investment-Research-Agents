# Tasks: optimize-factor-supply-funnel

> 程序级跟踪清单：每项验收判据随行；带行为契约的项实施时补 specs/ 或另立 change-id。

## A. 已在飞（只跟踪，不新增代码）

- [ ] A1 部署 #509/#510：后端镜像 `--build` + distro site-pages 补丁，生产容器验证契约段真实出现（部署门挂 add-feedback-quota-fairness / feed-dsl-contract-into-proposal-prompt 各自 tasks.md 第 1 项）
- [ ] A2 判效读数归档（门：2026-10-31）：untranslatable 占比 / 进闸门量 / 入库数三方同屏；`stage=translator`（复杂度桶 618 条）占比应显著下降——若转译率降而入库仍为 0，即证实需 B 段跟上

## B. 行为变更（实施时补 specs/ delta 或另立 change-id）

- [ ] B1 中间表示改造：LLM 直出本系统 DSL，或为 Qlib 映射层加横截面算子桥（`rank`/`zscore` 本系统已有，Qlib `Rank(x,N)` 是滚动秩 → 映射 `ts_rank`，769 条横截面型死在中间语言）——验收：stage=llm 且 reason 含横截面的拒译占比显著下降；`qlib_translate` 既有映射回归全绿；不引入近似转译
- [x] B2 风格缺口定向生成：active 池风格画像（缺口清单）+ per-family 拒收计数回喂 RD-Agent——**本 delta 自带 spec**（specs/factor-proposal）；验收：载荷含缺口块且画像与库内构成一致、不可得时整块省略、既有各块零回退、披露不越权（2026-10-29 实现：真实库导出核对 pool=外部×10、拒收 191 行分布逐桶一致；渲染抽查通过；pytest factors 545 passed / ruff / mypy 绿；**distro 补丁重部署随 A1 10-31 窗口**）
- [ ] B3 批次去同族化：收割投批按族分散或家族代表制——验收：单批内同构族候选数有上限且可配置；闸③ FDR 拒收出现时批内同族构成可审计
- [ ] B4 拒绝池复评通道：贴线区（|ICIR| ∈ [0.25, 0.30)，占被拒 27%）随面板加深自动重评并落事件——验收：零 LLM、纯统计重评；与 add-rejected-factor-pool 后续 delta 合并考虑，可拆独立 change

## C. 政策拍板（先决策后实施，裁决记录进 design.md）

- [ ] C1 epsilon 默认值裁决：定约定值（如 1e-8，写进转译契约并说明语义影响）或维持拒绝猜（64 条维持损耗）
- [ ] C2 多 horizon（T+5/T+20）立项裁决：闸②现只评 T+1；代价 = 多重检验 ×3、FDR 收紧、须与产品 T+20 持有语义对齐——放最后单独论证

## D. 等待项（零投入，自然解锁）

- [ ] D1 准入面板 500→750 日：快照库 ≥750 个交易日后启用（估 2027 年初）；届时 ICIR 估计 SE 0.063→约 0.052，与 B4 复评通道联动
- [ ] D2 年轻因子 IC 满窗观察：因子 9 等 2026-11 中下旬 ic_points 满 60（每日 +1），无需动作，观察即可

## 收口

- [ ] 收口指标进 `docs/evals/metrics.md` 时间线：转译率 / 条件准入率（12/563）/ 入库率（12/2115）三方同屏，且以 active 池风格构成与准入后 ICIR 持续性收口（防 Goodhart）
- [ ] 交互类判定：本 delta 无前端/SSE/会话变更，不触发 E2E 门禁；各子任务落地时按其自身 delta 判定
