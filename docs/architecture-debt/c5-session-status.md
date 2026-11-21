# C5 · 白话档案：一盏灯，四个房间装了四个开关

**状态**：待做（第 4 批，交互类变更走完整管线）
**来源**：2026-11-21 架构评审侦察通道 3
**关联**：openspec/specs/session-streaming/spec.md:155-163（终态语义契约）· interrupted 三源判别史

## 一句话

一个分析任务的「进行中 / 完成 / 失败 / 被打断」这套状态，规则写在 **4 个地方**。一盏灯，四个房间都有开关——谁按了、按对了没有，没人统一管。

## 现在长什么样

| 开关位置 | 它管什么 |
|---|---|
| `api.py`（11 处调用） | 手写状态流转：clarifying→running→completed，每处一遍 |
| `pipeline_runner.py:386/532/637` | 又 3 处流转 |
| `stream_registry.py:439` | 自己读 `status=="interrupted"` 判断——和状态库有隐藏耦合 |
| 前端 `streamStore/index.ts:402/447/464` | 状态→界面阶段的映射，外加本地收口 interrupted |

另外：

- 两个启动流程（`screening_analyze` 与 `events_watchlist_analyze`）**30 行代码几乎逐行一样**。
- 「终态落库兜底」没有完整 owner：写入在 `session_store.py:542`，补写在 `scripts/repair_terminal_pending.py`——两头分家。

## 已经造成的麻烦

- 「任务断了、状态却显示不对」的怪事家族（interrupted 三源判别就是为它发明的）。
- 改一处漏三处：规范里终态语义是一段契约，实现里拆成 4 份。

## 打算怎么修

把开关装回灯旁边：新建 `sessions/lifecycle.py`

- 接口五个动作：`begin_analysis`（开始）/ `mark_running` / `mark_complete` / `mark_failed` / `mark_interrupted`。
- 它整段拥有：状态流转 + session_created 事件 + 终态 CAS（防并发覆盖）+ 终态落库兜底。
- api.py 那 11 处手写、两个 30 行重复启动流程全部收编。
- **前端照旧读同一套事件流，零接口变化**——用户看不见任何改动。

## 好处（白话）

- 状态规则 4 处 → 1 处，改一次就是全改好。
- 生命周期用假的存储就能测完整状态机，不用拉起整个 FastAPI。
- 以后查「为什么状态不对」，只有一个地方可查。

## 牵涉文件

`api.py`、`pipeline_runner.py`、`stream_registry.py`、`session_store.py`、`frontend/src/stores/streamStore`。

## 坑与注意事项

- **这是交互类变更**（涉及会话状态流转）——按仓库红线必须走完整管线：OpenSpec delta + E2E 门禁 + 人工验证报告落 `tests/validation/`。
- 只动后端编排，**不动 SSE 语义本身**（不新增/删除事件类型），降低 E2E 风险面。
- 前端 streamStore 的映射这批**不动**（它消费事件流，事件流不变），留给后续批次。
