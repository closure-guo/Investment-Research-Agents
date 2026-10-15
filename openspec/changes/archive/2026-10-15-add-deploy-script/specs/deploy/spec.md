# deploy delta: add-deploy-script

## ADDED Requirements

### Requirement: 安全前置检查

部署脚本 SHALL 在执行任何变更动作前完成只读前置检查：① 后端可达且存在 `running` 会话 → SHALL 中止（MUST NOT 在有运行中分析时推进/重启，仓库红线）；② 工作树存在与 incoming diff 有交集的未提交改动 → SHALL 默认中止并列出文件，仅显式 `--stash` 且冲突预判（`merge-tree`）无冲突时才自动 stash → pull → pop；③ incoming 触碰的运行时文件（`data/` 前缀）SHALL 按类处置——untracked → 挪开留证、已跟踪且已修改 → 丢弃并报告；非 `data/` 前缀的同类冲突 SHALL 中止人工裁决。

#### Scenario: running 会话中止

- **GIVEN** 后端可达且存在一条 `running` 会话
- **WHEN** 执行部署
- **THEN** 中止（退出码非 0）且输出该会话标识
- **AND** MUST NOT 执行 pull 或重启容器

#### Scenario: WIP 重叠默认中止

- **GIVEN** 本地未提交改动与 incoming diff 存在文件交集
- **WHEN** 执行部署（无 `--stash`）
- **THEN** 中止并逐文件列出交集
- **AND** 工作树零变更

#### Scenario: 运行时文件按类处置

- **GIVEN** incoming 新增 `data/execution.db` 而本地已存在同名 untracked 文件
- **WHEN** 执行部署
- **THEN** 该文件被挪至 `.deploy-bak-<ts>` 后 pull 成功
- **AND** 报告含挪开清单

### Requirement: 拉取与健康探针

脚本 SHALL 以 `git pull --ff-only` 推进（禁止产生 merge 提交）；随后探针后端 `/api/sessions` 与前端 `/` 均 200 为健康；不健康时 SHALL 重启应用容器一次并复探，仍不健康 SHALL 以非零退出码报告（MUST NOT 以「已 pull」冒充部署完成）。

#### Scenario: 探针通过

- **GIVEN** pull 成功且容器运行中
- **WHEN** 执行探针
- **THEN** 输出 from→to 提交与两项探针结果
- **AND** 退出码 0

#### Scenario: 不健康重试一次后仍失败

- **GIVEN** pull 成功但后端探针非 200
- **WHEN** 重启应用容器并复探后仍非 200
- **THEN** 退出码非 0 且报告两轮探针结果

### Requirement: 幂等与 dry-run

脚本 SHALL 幂等可重入：已为最新版本时 SHALL 跳过拉取、仅执行探针并报告（零副作用）；`--dry-run` SHALL 执行全部前置检查并输出计划，MUST NOT 产生任何变更（含 stash/挪开/pull/重启）。

#### Scenario: 已最新重复执行

- **GIVEN** 工作树已与 origin/main 一致
- **WHEN** 重复执行部署
- **THEN** 跳过拉取，探针通过，退出码 0
- **AND** 工作树与容器状态零变更

#### Scenario: dry-run 零变更

- **GIVEN** 存在 WIP 重叠等需处置的现场
- **WHEN** 以 `--dry-run` 执行
- **THEN** 输出完整检查结论与处置计划
- **AND** 工作树、stash 列表与容器状态零变更
