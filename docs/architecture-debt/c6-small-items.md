# C6 · 白话档案：三个小件——规则抄 5 份 / 一段死代码 / 577 行造数函数住错地方

**状态**：待做（第 2 批，低成本清理，可与第 1 批搭车）
**来源**：2026-11-21 架构评审侦察通道 2/3
**关联**：openspec/specs/settlement-significance-stats（唯一红线）· issue #606（settle.py 点名）

## 一句话

三个不大的问题，各自独立，清理起来成本低。

## 小件①：一条规则抄了 5 份

「**结算记录不足 10 条，不显示胜率**」——这是战绩页唯一的统计红线。它在 5 个地方各写了一遍：

| 位置 | 备注 |
|---|---|
| `evals/outcome/caliber.py:17` | **真相源**（但生产代码 import 不到 evals 目录） |
| `api.py:3144` | 字面复制 |
| `api.py:3179` | 字面复制，注释自己写着「按字面同步」——自己承认是复制粘贴 |
| `outcome/track_record/report.py:33` | 又一遍 |
| `outcome/track_record/{significance.py:15, segments.py:106}` | 再两遍 |

**修法**：`outcome/track_record/readout.py: overview(来源, 版本)` 一个入口拥有这条门控，生产侧立一个常量（`outcome/ops/prereg.py:53` 已有反向引用先例，可行）。红线 5 → 1，约 −80 行。

## 小件②：一段死代码，复活即踩雷

`outcome/settle.py` 的 `evaluate_decision`（96–185 行）：生产结算早已切到 `judgment.py`，这段旧代码没人调了。但：

- 唯一还连着它的是 `evals/golden` 的判例复算——**测试面锚在死代码上**。
- 里面**写死了「只做多」**——哪天有人误启用，就是 C2 档案里说的方向反向 bug。

**修法**：删掉它；golden 复算改判 `judgment.py`；存量数据的只读查询（API 读旧列）保留。

## 小件③：577 行造数函数住在生产代码里

`api.py:882-1458` 的 `test_seed`——一个 E2E 造数据端点，函数体里重写了 8 个子系统的造数编排（战绩/选股/记忆/因子/模拟盘/市场状态/事件/快照）。**测试代码住在生产路由文件里**，占生产文件 10%。

**修法**：整体搬去 `tests/e2e` fixtures 层；api.py 保留一个薄壳或不留。

## 好处（白话）

- api.py 从 5584 行瘦身约 10%。
- 红线一处；死代码的雷排掉；测试件回测试家。

## 坑与注意事项

- 删 `evaluate_decision` 前**先核 `evals/golden/gates.py` 的用法**——复算语义要原样切到 judgment。
- `test_seed` 搬家要同步 E2E 里的调用路径（E2E seed 用它造数），别把 E2E 弄红。
- 小件①搬门控时注意：evals 侧真相源保持单一——生产立常量后，evals 反过来引用生产常量，别两边各留一份。
