# 验证报告: 热库迁 named volume（harden-sqlite-availability 轨道 A，issue #515）

**日期**: 2026-11-01
**执行人**: agent（ZCode 会话，用户「开」指令授权）
**关联**: PR #549（compose+脚本入库）、PR #550（脚本修复）、issue #515、incident 053

## 迁移结果

| 项 | 结果 |
|---|---|
| compose 形态 | backend data = named volume `finance-agent_data`（docker inspect 实证）；factor_research / research_report_inbox / cohort 三个 JSON 子目录保留宿主 bind；reports/scripts bind 不变 |
| 数据新鲜度 | 卷内最新会话 = 2026-10-31T09:49（切换前最后写入）；snapshot latest_date=2026-10-30；/api/factors 12 条 |
| 八库对账 | sessions/screening/execution/events/regime/research_reports/factor_library/market_snapshot 全部 quick_check 双侧 ok + 全表行数逐一致（src 尺寸 sessions 483MB / snapshot 486MB） |
| 回滚副本 | 宿主 data/ 原样保留（含迁移前全量）；回滚脚本 scripts/rollback_hot_db_to_volume.sh 就绪 |

## 过程中的四个静默失败（全部实证并修复，PR #550）

1. **pipefail 零命中**：grep 零命中（=0 个 running，期望态）返回 1 → `|| echo 0` 兜底拼出 "00" → 前置检查误中止。修法 `|| true` 吸收。
2. **MSYS 挂载空目录**：`-v /d/...` Docker Desktop 不认 → 容器内 /src 为空 → cp 的 cd 失败，但计数 echo 仍打印旧卷内容「89 项」——首跑以 10-29 旧副本起栈，端点验证手看最新会话时间才暴露。修法 `pwd -W`。
3. **对账静默跳过**：`docker run` 缺 `-i`，heredoc 经 stdin 喂 python 直接空转（exit 0 放行起栈）。
4. **MSYS 参数改写**：`python - /src /dst` 的 POSIX 形态参数被 MSYS 转成 Git 安装路径——挂载一直正常、python 找不到目录。修法 src/dst 硬编码进 heredoc。
   另：源库带 `-wal/-shm` 时 9p 只读直开报 `unable to open database file`——验证侧先把源拷进容器可写 /tmp 再开库。

**教训**：这条链上每个环节都会「静默成功」或「静默失败」，唯一可靠的锚是**端点读出业务数据的新鲜度**（最新会话时间戳）——最终以此判定首跑失败、二跑成功。

## 首跑事故记录

首跑（11-01 00:0x）以 10-29 旧副本起栈并验证「通过」（对账静默跳过 + 端点展示旧数据被误读风险）——发现后立即用修复版脚本重跑（停栈→新鲜整拷→对账→起栈），全程宿主副本无损。**当前生产数据无任何丢失**（宿主副本 + 卷内新鲜副本双份）。

## 后续观察点

- **2026-11-02（周一）15:30 日批 + 16:22 池分析批**：首个真实写入负载，观察 9p 时代的 `unable to open database file` / `disk I/O error` 是否绝迹（10-29 迁移后曾跑 15 只批零 DB 故障，本次预期一致）
- #491 重测（10 并发抢源）同窗口顺带观察
- 回滚脚本空跑演练（delta task 4 余项）待做
- 宿主直读热库的脚本清单改造（delta task 1）待做——宿主 data/ 从此只作回滚副本，任何直读都是读旧数据
