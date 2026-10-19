# Proposal: add-factor-style-exposure

## Why

2026-10-19 复盘暴露一个披露盲区：因子库只有「与池内因子的信息重复」检查（闸④冗余），
**没有对标准风格（动量/反转/量比/波动/规模）的暴露披露**。实测（本日）：

| 已入库因子 | 主导风格 | |ρ| |
|---|---|---|
| `volratio20` | 量比（换手代理） | **1.00**（名次完全相同——是 builtin 代理的换皮） |
| `range1` | 波动率 | **0.85**（+规模 0.69） |
| `RANGE_EXPANSION_20D` | 量比 | 0.52 |
| `volcv20` | 全部 ≤0.17 | 独立 |

**设计立场（先测量后定案）**：风格（价值/低波/动量/规模）是**合法收益源**——判定
「与某风格同源」不等于「不合格」，因此**不做风格闸**（拒绝「低波因子是低波」是错的）；
正确的处置是**披露**：让每个因子说清「我其实是什么」，重复信息继续由闸④池内冗余拦截
（已入库代表存在时）。本披露同时为「风格代表 vs 独立 alpha」的组合复核提供判读输入。

## What Changes

- **MODIFIED「因子库只读查询」（factor-library）**：详情响应 SHALL 增加 `style_exposure`
  风格暴露披露块（与 builtin 风格代理篮子的 IC 序列相关 + 主导风格 + |ρ| ≥ 0.7 标注）；
  不可计算（面板不可得/共同样本不足/常量序列）SHALL 返回 `null`/逐风格 `null`，
  MUST NOT 用 0 或虚构值冒充；披露 SHALL NOT 影响任何准入裁决（纯只读）。
- **实现面**：`factors/style_exposure.py`（诊断模块，与 builtin 注册包同一真源）、
  `api.py` 详情端点接线、前端因子库页展开块（主导风格 + 逐风格读数 + 重合标注）、
  `scripts/factor_style_exposure.py`（批诊断运维入口）。

## Capabilities

- **Modified Capabilities**: `factor-library`（因子库只读查询，+风格暴露披露）

## Impact

- 代码：`factors/style_exposure.py`（新增）、`api.py`（详情载荷）、前端 `FactorLibraryPage`
- **重要副产修复**：`tests/test_api_factors.py` 增加 `SNAPSHOT_DB_PATH` 隔离——
  改造前详情端点在测试中会触碰**生产快照库**（本地实测 173 秒/12 用例，且与并发回填
  的跨挂载 WAL 冲突直接相关），隔离后 3.3 秒且零生产写入。
- 边界：**不设闸**（见 Why 的立场）；风格代理为价量六字段内的代理（无股本/市值原始
  数据，口径披露见 `builtin.py`）；已入库因子 1/3 的「风格代表」标注为**披露事实**，
  是否调整（保留/退役）留 owner 裁决。
