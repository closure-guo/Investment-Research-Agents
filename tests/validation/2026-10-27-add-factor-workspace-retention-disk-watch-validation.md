# 人工验证报告: add-factor-workspace-retention-disk-watch

**日期**: 2026-10-27
**验证人**: agent（Closure 授权代行）
**关联 delta**: openspec/changes/add-factor-workspace-retention-disk-watch/
**E2E 门禁**: tests/e2e/playwright/playwright.monitor-disk.config.ts（专属端口对 8008/5181，`reuseExistingServer: false`），3/3 通过（2026-10-27 10:05 本机实跑）

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 保留窗清理：过期目录删、窗内留、失败容忍 | 单测（TestPruneWorkspace 4 例） | 48h 外删、恰等不删、OSError 进 failed | 单测绿 + distro 实跑：手工 `--prune` 删 10 个过夜过期目录、freed 5.6GB、kept 1162、failed=[] | ✅ |
| 低水位判定与停发 | 单测（TestDiskWatermark 5 例） | 恰等阈值不 low；双侧失败 fail-open | 单测绿 + distro 实跑 `--disk-check` exit=0 放行、`.disk_pause.json` 写 `paused=false`（guest 442GB/host 306GB） | ✅ |
| 看护脚本接线 | 单测（TestSupervisorRender）+ distro bash -n | 渲染产物含 --prune/--disk-check/sleep 300，bash -n 过 | Windows 跳过 bash 用例，distro 实机 `bash -n` rc=0 补验通过 | ✅ |
| 采集器 disk 读数 | 单测（TestCollectDisk 10 例）+ 生产快照 | guest/host/workspace/paused_reason 如实上报 | 重启代理守护后快照 `disk` 块出现：guest_free=440,950,489,088 与 `df` 440GB 交叉一致 | ✅ |
| 端点 disk 派生（GB/low_watermark/paused_reason） | 单测（TestDiskWatermark 14 例）+ E2E 20/21 | GB 1 位小数；阈值派生；paused_reason 透传 | E2E：横幅文案含「guest 空闲 25.3 GB」「guest 侧空闲不足」 | ✅ |
| 探活读写双探针 | 单测（文本断言） | exec 后追加 rw 探针，失败 → fs_rw_failed | 源码断言过；csc 实编译通过；生产 distro_probe.json 正常写入 | ✅ |
| 前端水位卡 + 低水位横幅 | vitest（5 例）+ E2E（20/21/22） | 卡渲染 GB；横幅低水位/停发文案；旧快照降级 | vitest 47 passed；E2E 3/3：低水位横幅可见含停发说明、正常态无横幅、旧快照两者皆无且页面不崩 | ✅ |
| 零命令面 | E2E 20 | 横幅内无 button/a | `banner.locator('button, a')` count=0 | ✅ |
| 旧快照兼容 | E2E 22 + 单测 | 无 disk 块 → 卡/横幅不渲染、端点全 None+False | 均绿 | ✅ |

## 门禁记录

- pytest（涉改面合并跑）：`tests/scripts/spike/test_factor_research_agent.py` + `test_collect_factor_status.py` + `test_launcher_scripts.py` + `tests/test_api_factor_research.py` → **190 passed, 1 skipped**（skip=bash 语法用例，Windows 无可执行 bash；distro 实机已补验）
- ruff check / ruff format --check：全过；mypy：98 errors 与干净 HEAD 完全持平（全部既有债务，新代码零新增）
- vitest：factorResearchPage.test.tsx **47 passed**；`tsc --noEmit` 0 错
- E2E（monitor-disk 专属套件）：**3 passed**（18.4s）；E2E 过程修复两处——fixture 须写采集器原始字节契约（非端点 GB 形态）、前端 webServer 须 `VITE_API_TARGET` 指向 8008（默认代理会打到生产 8000，incident 038 同族）

## 部署与生产验证（2026-10-27 10:14–10:22）

1. `factor_research_agent.py` / `collect_factor_status.py` 经 `tr -d '\r'` 部署至 distro `/root/`，import 自检通过。
2. `POST /api/factor-research/restart`（request_id=46e227db9c44）→ `result=restarted`，新看护加载新模板。
3. 代理守护为常驻进程，restart 不换模块 → 手动 kill 守护（pid 16），启动器 2 分钟节拍重拉新代码，快照 `disk` 块随即出现。
4. 低水位停发行为本次未在生产触发（guest 441GB / host 306GB 远高于阈值）——停发路径由单测真值表与 E2E 覆盖。

## 异常记录

- 无阻塞异常。备忘：代理守护进程内存模块不随 supervisor restart 更新，部署采集器/代理改动后必须重启守护（kill → 启动器自愈重拉），已记入 incident 051 教训段。

## 结论

- [x] 全部通过，可 archive
