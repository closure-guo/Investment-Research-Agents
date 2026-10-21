# Tasks: harden-sqlite-availability

- [ ] 列出宿主机直读热库的脚本清单（`grep -rn "data/sessions.db\|data/screening.db\|data/execution.db" scripts/ src/`），逐项改为容器内通道或环境变量
- [ ] compose 新增 `hot_db_data` 卷与热库路径 env；写迁移脚本（checkpoint → integrity → copy → verify）
- [ ] 停后端执行迁移，验证 `/api/health`、`/api/sessions`、`/api/v1/track-record/overview`、screening/execution 读路径
- [ ] 写回滚脚本并空跑演练（复制回 bind mount → 还原配置 → 起后端）
- [ ] `/api/health` 增加 DB 探针（含超时），补单测：全 ok / 单库不可用 / 探针超时
- [ ] `integrity_check` 增加热库页级校验，补单测：全 ok / 单库损坏 → job failed
- [ ] `pool_analysis_runs` 终态回写，补单测：completed / failed / interrupted / 无匹配行
- [ ] E2E 或集成验证：库不可用时健康端点返回 503 且给出库名；恢复后回到 200
- [ ] 人工验证报告落 `tests/validation/`（含迁移前后数据行数对照）
- [ ] `openspec validate harden-sqlite-availability --strict` 通过
