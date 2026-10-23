# Design: add-industry-report-context

## 实测前置（2026-10-22，先测后设计）

写方案前先测了口径，结论直接改变了设计：

| 项 | 实测 |
|---|---|
| 研报行业词表 | **64 个名字 / 228 行**，申万 2021 分类，带层级后缀（`IT服务Ⅱ` / `白酒Ⅱ` / `银行Ⅱ` / `证券Ⅱ` / `非白酒` / `炼化及贸易`） |
| 个股行业来源 | `stock_individual_info_em`（东财 F10），东财行业名（`电池` / `白酒` / `多元化保险` / `乘用车`） |
| 样本命中（纯等值） | 300750 电池 ✓、002594 乘用车 ✓、600519 白酒 ✗（词表是「白酒Ⅱ」）、601318 多元化保险 ✗（词表是「保险Ⅱ」）、000858 白酒 ✗ |

**纯等值匹配 ≈ 40%**（2/5）。若照此实现，白酒股、保险股会拿到「无同行业研报」的静默空态——而库里明明有 2 篇「白酒Ⅱ」、2 篇「保险Ⅱ」。这正是 `research-report-dataplane` 既有纪律禁止的「以空结果伪装无覆盖」。所以设计必须包含三层：层级后缀剥离、显式别名表、未命中显式状态。

## Approach

### 1. 归一与别名（`data/research_reports.py`）

```python
_LEVEL_SUFFIXES = ("Ⅱ", "Ⅲ", "Ⅱ", "II", "III")

def normalize_industry(name: str | None) -> str:
    """NFKC + 去空白 + 剥离末尾申万层级标识。不做模糊匹配。"""

INDUSTRY_ALIASES: dict[str, str] = {
    # 键 = 个股行业名（东财 F10），值 = 研报词表名（申万）；只收录实测确认项
    "多元化保险": "保险",
}
```

别名表是**显式数据**，不放模糊规则。加一条别名必须配一条测试；表里每个值必须出现在 `tests/fixtures/industry_report_vocab.txt`（实测词表快照）里，防手抄漂移。

### 2. 读取层（`data/research_reports_store.py`）

只读查询，不含业务判断：

```python
def list_industry_reports(industry, *, since, as_of, limit, db_path=None) -> list[dict]
    # report_type='industry' AND industry IN (...) AND since <= publish_date <= as_of
    # ORDER BY publish_date DESC LIMIT ?
```

`industry IN (...)` 的候选集由调用方给出：`{归一后的原名, 归一后的别名值}`。

### 3. 聚合层

```python
INDUSTRY_DIGEST_MAX_REPORTS = 8
INDUSTRY_DIGEST_MAX_AGE_DAYS = 90

def get_industry_digest(industry, *, as_of=None, db_path=None,
                        max_reports=None, max_age_days=None) -> dict | None
def format_industry_digest(digest) -> str
```

```python
{
  "industry": "白酒",          # 请求侧原名
  "status": "ok" | "stale" | "unmatched",
  "report_count": 2,
  "brokers": ["招商证券"],
  "latest_report_date": "2026-10-20",
  "age_days": 1,
  "documents": [{report_key, title, org, analyst, publish_date, depth_class}],
}
```

- `ok`：窗口内有数据。
- `stale`：窗口内 0 篇、库内有该行业历史 → 只报最新日期与距今天数，不列标题。
- `unmatched`：归一与别名都没命中 → 消费端渲染「研报库无对应行业口径」，与「无研报」区分。
- 三种状态都是 dict；只有「库里根本没有 industry 数据」才返回 None。

### 4. 注入层

- `nodes/fetch.py`：既有研报块之后 `result["industry_report_digest"] = get_industry_digest(industry_info.get("industry"))`，同一 `try/except` fail-open。
- `state.py`：`industry_report_digest: dict | None`。
- `_build_fundamental_context`：插在「券商研报」段之后、一致预期段之前；段头 `同行业研报（state 键 industry_report_digest；仅元数据，未读取正文）:`；段尾防锚定 + 断更/未覆盖说明。
- `citation.py`：`documents[].title` 并入回声源，写法对齐 `strategy_report_digest` 分支。

### 5. 提示词

`prompts/fundamental_analyst.md` 数据清单增一行「同行业研报（标题/机构/日期；仅元数据无正文）」。改后 `uv run python scripts/deploy_prompts.py` 发布。

## Alternatives Considered

- **纯等值匹配**：被实测否决（≈40% 命中，且失败态会静默伪装成「无研报」）。
- **模糊/子串匹配**（「电池」命中「电池化学品」）：否决。会把不同行业串味，且失败不可解释。
- **语义检索/embedding**：否决。行业研报只有元数据、没有正文，embedding 无对象；全库全文仅 1 篇。
- **复用 `get_strategy_digest` 通道**：否决。那条通道吃 `report_extractions`（投喂 PDF 抽取），行业研报没有抽取行；混在一起会抹掉「有无正文」这个关键差异。
- **注入宏观分析师**：否决。行业观点必须与标的行业绑定，且宏观上下文已很长。
- **下载 PDF 解析正文**：本次不做，属全文供给路线的独立决策。

## Risks

- **别名表腐化**：词表会随东财接口变动。对策：词表快照 fixture + 测试保证别名值存在；新增别名走 code review。
- **层级剥离过度**：`Ⅱ` 剥离后「银行」可能与「银行Ⅱ」以外的名字碰撞（当前词表无碰撞）。对策：`industry IN (...)` 多候选，命中多条时按 `report_count` 正常合并，不做取舍。
- **标题诱导幻觉**：只有标题没有结论。对策：段头段尾双重披露「仅元数据、未读取正文」，prompt 里写明不得据标题推断结论。
- **上下文膨胀**：硬上限 8 条 + 标题截断 ≤ 80 字，单段 ≤ 约 600 字。
- **回声源过宽**：标题进回声源可让含标题词的文本 claim 直接 PASS(echo)，与策略观点聚合口径一致，接受。
