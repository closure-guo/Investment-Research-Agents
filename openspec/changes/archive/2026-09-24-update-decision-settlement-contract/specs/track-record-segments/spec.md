# Delta for track-record-segments

## MODIFIED Requirements

### Requirement: 四维切片指标

系统 SHALL 支持按行业/市值桶/市场环境/持有期桶切片输出 {样本数, 胜率, 平均超额}；市场环境按基准 250 日均线判定牛熊。**切片内的胜率与平均超额 SHALL 仅统计 long/short 方向且状态为 `resolved_win` / `resolved_loss` / `resolved_neutral` 三态的观点**（与 `metrics.md` §1.8① 头条口径一致，`evals/outcome/caliber.py` 为口径锚）；`status='avoidance'` 的 neutral 终态行 SHALL NOT 进入二者的分子分母，但 SHALL 计入该分段的**样本数**（与分桶分母）。
(Previously: 切片胜率/平均超额人口未限定方向——neutral 观点按 short 符号误判后混入本应只含 long/short 的读数。)

#### Scenario: 切片查询

- **WHEN** 请求切片指标 API 并指定维度
- **THEN** 返回该维度各分段的样本数/胜率/平均超额，n<10 分段按显著性规则标注

#### Scenario: 回避终态不进切片胜率与超额

- **GIVEN** 某分段同时含 long/short 三态观点与 `status='avoidance'`（`avoidance_status` 非空）的 neutral 终态观点
- **WHEN** 计算该分段的胜率与平均超额
- **THEN** `avoidance` 行 SHALL NOT 进入两者的分子分母
- **AND** `avoidance` 行 SHALL 计入该分段的样本数
