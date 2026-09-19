# 因果消融框架 v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `revamp-ablation-v2-causal-claims` delta 交付因果主张消融框架：登记表准入、注入法矩阵、单元级判定与聚类推断、族 B 指标管线、校准门控、结论注册表——全部新增代码落在 `evals/causal_ablation/` 新包内，零 LLM 成本。

**Architecture:** 新建 `evals/causal_ablation/` 包承载全部 v2 逻辑（不改 `evals/ablation.py`，避免与在途 delta `update-ablation-driver-parity-and-report-status` 冲突）；每个模块一个职责，纯函数优先、外部依赖（LLM/NLI/judge）以注入 callable 形式留接口，使单元测试零网络。文档类产物落 `evals/ablation/preregister/`。

**Tech Stack:** Python 3.12 / pytest / dataclasses / numpy（经 `evals/stats.py` 复用 bootstrap 底座）/ ruff / mypy

## Global Constraints

- 所有新代码放 `evals/causal_ablation/`，测试放 `tests/evals/causal_ablation/`；**禁止修改** `evals/ablation.py`、`tests/scripts/ablation_pilot.py`、`evals/run.py`、`docs/evals/metrics.md`、`README.md`（这些文件由其他 delta 持有，改动任务见 GATED 段）。
- 判定方式取值域固定：`code | nli | judge`；污染类型固定 8 类：`value_error, direction_error, unit_error, period_shift, mirror_narrative, fabricated_event, stale_macro, illegal_price`。
- 校准门控阈值固定 `0.80`（对应 delta spec「LLM 判定的校准门控全覆盖」）。
- 统计推断固定：聚类重采样单元 = 标的；`B=10_000`；`seed=42`；百分位 CI。
- 逃逸率分母规则：未终裁单元不计入分子也不计入分母（单列 pending）。
- 禁止占位符；每个测试必须能在无网络、无 LLM 凭据下通过（`uv run pytest tests/evals/causal_ablation -q`）。
- 提交信息格式：`feat(evals): <描述>` / `test(evals): <描述>`。
- 每任务结束运行：`uv run ruff check evals/causal_ablation tests/evals/causal_ablation && uv run mypy evals/causal_ablation`。

---

### Task 1: 因果主张登记表（2.1）

**Files:**
- Create: `evals/causal_ablation/__init__.py`
- Create: `evals/causal_ablation/claims.py`
- Test: `tests/evals/causal_ablation/test_claims.py`

**Interfaces:**
- Consumes: 无
- Produces: `CausalClaim`（frozen dataclass: `id, family, target, claim, failure_mode, primary_metric, method, effect_expectation, suspended`）、`DEFAULT_REGISTRY: tuple[CausalClaim, ...]`、`load_registry(path: Path | None = None) -> tuple[CausalClaim, ...]`、`validate_registry(registry) -> list[str]`、`assert_admissible(target_id: str, registry=None) -> CausalClaim`、`UnregisteredTarget(ValueError)`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/causal_ablation/test_claims.py
from pathlib import Path

import pytest

from evals.causal_ablation.claims import (
    DEFAULT_REGISTRY,
    CausalClaim,
    UnregisteredTarget,
    assert_admissible,
    load_registry,
    validate_registry,
)


class TestRegistryShape:
    def test_covers_family_a_mechanisms(self):
        ids = {c.id for c in DEFAULT_REGISTRY if c.family == "A"}
        assert ids == {"A1", "A2", "A3", "A4", "A5", "A6", "A7"}

    def test_covers_family_b_layers(self):
        ids = {c.id for c in DEFAULT_REGISTRY if c.family == "B"}
        assert ids == {"B1", "B2", "B3", "B4", "B5", "B6"}

    def test_every_claim_has_metric_and_method(self):
        for c in DEFAULT_REGISTRY:
            assert c.claim and c.failure_mode and c.primary_metric
            assert c.method in {"code", "nli", "judge"}

    def test_a5_effect_expectation_is_pending_not_proven(self):
        a5 = next(c for c in DEFAULT_REGISTRY if c.id == "A5")
        assert "待" in a5.effect_expectation  # incident 027 修正：不得声称已实证


class TestValidation:
    def test_valid_registry_has_no_issues(self):
        assert validate_registry(DEFAULT_REGISTRY) == []

    def test_duplicate_id_reported(self):
        dup = (DEFAULT_REGISTRY[0], DEFAULT_REGISTRY[0])
        assert any("重复" in issue for issue in validate_registry(dup))

    def test_unknown_method_reported(self):
        bad = CausalClaim(
            id="X1", family="A", target="t", claim="c", failure_mode="f",
            primary_metric="m", method="vibes", effect_expectation="e",
        )
        assert any("method" in issue for issue in validate_registry((bad,)))


class TestAdmission:
    def test_registered_target_passes(self):
        assert assert_admissible("A3").id == "A3"

    def test_unregistered_target_rejected(self):
        with pytest.raises(UnregisteredTarget):
            assert_admissible("Z9")


class TestJsonMerge:
    def test_json_override_adds_target(self, tmp_path: Path):
        p = tmp_path / "registry.json"
        p.write_text(
            '[{"id":"B7","family":"B","target":"新层","claim":"c","failure_mode":"f",'
            '"primary_metric":"m","method":"code","effect_expectation":"e"}]',
            encoding="utf-8",
        )
        merged = load_registry(p)
        assert "B7" in {c.id for c in merged}
        assert len(merged) == len(DEFAULT_REGISTRY) + 1

    def test_missing_file_falls_back_to_default(self, tmp_path: Path):
        assert load_registry(tmp_path / "nope.json") == DEFAULT_REGISTRY
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/causal_ablation/test_claims.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.causal_ablation'`

- [ ] **Step 3: Write minimal implementation**

```python
# evals/causal_ablation/__init__.py
"""因果主张消融框架 v2（delta revamp-ablation-v2-causal-claims）。"""
```

```python
# evals/causal_ablation/claims.py
"""因果主张登记表：无登记主张的对象不得进入消融矩阵（spec causal-ablation）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

VALID_METHODS = ("code", "nli", "judge")


@dataclass(frozen=True)
class CausalClaim:
    id: str
    family: str  # "A"（反幻觉机制）| "B"（编排层）
    target: str
    claim: str
    failure_mode: str
    primary_metric: str
    method: str  # code | nli | judge
    effect_expectation: str
    suspended: str | None = None  # 悬置原因；None = 有对应实验


DEFAULT_REGISTRY: tuple[CausalClaim, ...] = (
    CausalClaim(
        id="A1", family="A", target="确定性指标注入",
        claim="没有代码算指标，LLM 会自算并算错",
        failure_mode="计算幻觉", primary_metric="计算型 claim 数值错误率",
        method="code",
        effect_expectation="大：多位数心算错误率经验值 20%+",
    ),
    CausalClaim(
        id="A2", family="A", target="语义头 + 单一词表",
        claim="没有语义头，序列方向/字段名会被误读",
        failure_mode="镜像叙事、路径幻觉", primary_metric="负索引/期次错位率",
        method="code", effect_expectation="中：incident 022 实证同族失败模式",
    ),
    CausalClaim(
        id="A3", family="A", target="claim 登记 + verify_citations",
        claim="没有校验器，编造数字无人拦截",
        failure_mode="无出处数字逃逸", primary_metric="注入污染逃逸率",
        method="code", effect_expectation="大：自然分布真幻觉≈0，需注入法测",
    ),
    CausalClaim(
        id="A4", family="A", target="单点修复回路",
        claim="没有修复回路，残余 value_mismatch 滞留正文",
        failure_mode="错误数字进入报告", primary_metric="修复前后真 FAIL 率差",
        method="code", effect_expectation="中：只覆盖 <3 处的稀疏场景",
    ),
    CausalClaim(
        id="A5", family="A", target="价位参考带 + 三价 sanity 校验",
        claim="没有 sanity 校验，Trader 可编造不可执行价位",
        failure_mode="价位幻觉", primary_metric="价位越带率 / 无出处价位率",
        method="code",
        effect_expectation="待 027-price-validate-state-keys-dropped 修复后的遥测实证（该回路曾因 state 键未声明从未生效）",
    ),
    CausalClaim(
        id="A6", family="A", target="文本 claim 回声匹配",
        claim="没有回声匹配，编造事件/新闻无法被识别",
        failure_mode="事件幻觉", primary_metric="事件型 claim 不可回声率",
        method="code", effect_expectation="未知——当前最薄防线，实验价值最高",
    ),
    CausalClaim(
        id="A7", family="A", target="宏观时效标记",
        claim="没有时效标记，旧数据会被当现值引用",
        failure_mode="时效幻觉", primary_metric="stale 引用率",
        method="code", effect_expectation="小但确定",
    ),
    CausalClaim(
        id="B1", family="B", target="辩论层",
        claim="没有辩论，单方分析师遗漏的风险点无人补",
        failure_mode="风险点遗漏", primary_metric="风险点增量率（新增且被决策吸收 / 只）",
        method="nli", effect_expectation="中：提示效应集中在大分歧标的",
    ),
    CausalClaim(
        id="B2", family="B", target="辩论层",
        claim="没有交锋，错误观点无人反驳",
        failure_mode="错误观点滞留", primary_metric="交锋修正率（rebuttal_to 锚定且被修正）",
        method="judge", effect_expectation="未知",
    ),
    CausalClaim(
        id="B3", family="B", target="决策+风控层",
        claim="没有该层，执行参数（VaR/止损/仓位）无前文出处",
        failure_mode="执行参数自构", primary_metric="风控数字出处率",
        method="code", effect_expectation="大：出处率 +10pp 量级",
    ),
    CausalClaim(
        id="B4", family="B", target="决策+风控层",
        claim="没有 sanity 校验，价位非法/不可执行",
        failure_mode="价位非法", primary_metric="sanity 打回率 / 修正触发率",
        method="code", effect_expectation="中",
    ),
    CausalClaim(
        id="B5", family="B", target="编排整体",
        claim="完整层应产出更有助于决策的报告",
        failure_mode="决策辅助价值不足", primary_metric="pairwise 盲评胜率",
        method="judge", effect_expectation="中：阈值须附成本换算依据",
    ),
    CausalClaim(
        id="B6", family="B", target="全层（事后）",
        claim="—（不作层间归因）",
        failure_mode="—", primary_metric="事后结算胜率（仅绝对质量线）",
        method="code", effect_expectation="不作裁剪裁决依据",
        suspended="track-record 历史 predictions 全为 full 变体产出，无法层间对比",
    ),
)


class UnregisteredTarget(ValueError):
    """目标未登记因果主张，不得进入消融矩阵。"""


def load_registry(path: Path | None = None) -> tuple[CausalClaim, ...]:
    """默认表 + 可选 JSON 增量（同 id 覆盖默认行）。文件不存在则返回默认表。"""
    if path is None or not path.exists():
        return DEFAULT_REGISTRY
    raw = json.loads(path.read_text(encoding="utf-8"))
    extra = tuple(
        CausalClaim(
            id=row["id"], family=row["family"], target=row["target"], claim=row["claim"],
            failure_mode=row["failure_mode"], primary_metric=row["primary_metric"],
            method=row["method"], effect_expectation=row["effect_expectation"],
            suspended=row.get("suspended"),
        )
        for row in raw
    )
    by_id = {c.id: c for c in DEFAULT_REGISTRY}
    for c in extra:
        by_id[c.id] = c
    return tuple(by_id.values())


def validate_registry(registry: tuple[CausalClaim, ...]) -> list[str]:
    """返回问题列表（空 = 合法）。"""
    issues: list[str] = []
    seen: set[str] = set()
    for c in registry:
        if c.id in seen:
            issues.append(f"重复 id: {c.id}")
        seen.add(c.id)
        if c.method not in VALID_METHODS:
            issues.append(f"{c.id}: method 非法 {c.method!r}（须为 {VALID_METHODS}）")
        if not c.primary_metric:
            issues.append(f"{c.id}: 缺主指标")
        if not c.claim:
            issues.append(f"{c.id}: 缺因果主张")
    return issues


def assert_admissible(
    target_id: str, registry: tuple[CausalClaim, ...] | None = None
) -> CausalClaim:
    """准入校验：未登记即拒绝。"""
    reg = registry if registry is not None else DEFAULT_REGISTRY
    for c in reg:
        if c.id == target_id:
            return c
    raise UnregisteredTarget(f"目标 {target_id} 未登记因果主张，不得进入消融矩阵")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/causal_ablation/test_claims.py -q`
Expected: PASS（12 passed）

- [ ] **Step 5: Commit**

```bash
git add evals/causal_ablation/__init__.py evals/causal_ablation/claims.py tests/evals/causal_ablation/test_claims.py
git commit -m "feat(evals): 因果主张登记表与准入校验（v2 消融框架 Task 1）"
```

---

### Task 2: 预登记 schema + 跑批门禁（2.2）

**Files:**
- Create: `evals/causal_ablation/preregister.py`
- Test: `tests/evals/causal_ablation/test_preregister.py`

**Interfaces:**
- Consumes: 无
- Produces: `REQUIRED_FIELDS: tuple[str, ...]`、`Preregistration(path: Path, fields: dict[str, str], raw: str)`（属性 `valid: bool`、`issues: list[str]`）、`parse_preregister(text: str) -> Preregistration`、`find_latest_preregister(dir_path: Path) -> Preregistration | None`、`assert_preregistered(dir_path: Path) -> Preregistration`、`MissingPreregistration(RuntimeError)`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/causal_ablation/test_preregister.py
from pathlib import Path

import pytest

from evals.causal_ablation.preregister import (
    MissingPreregistration,
    assert_preregistered,
    find_latest_preregister,
    parse_preregister,
)

GOOD = """# P1 预登记
- 主指标: 逃逸率
- MDE: 6pp
- 决策阈值: 拦截率 < 50% 建议降级；依据：成本-效用换算见附录 A
- 样本量依据: 20 标的 × 32 单元，不一致对子按 20% 估
- 停止规则: 不一致对子 < 10% 则收窄污染矩阵
- rubric 版本: judge-v8 / round7
"""


class TestParse:
    def test_good_document_is_valid(self):
        p = parse_preregister(GOOD)
        assert p.valid is True
        assert p.issues == []
        assert p.fields["MDE"] == "6pp"

    def test_missing_field_reported(self):
        text = GOOD.replace("- 停止规则: 不一致对子 < 10% 则收窄污染矩阵\n", "")
        p = parse_preregister(text)
        assert p.valid is False
        assert any("停止规则" in i for i in p.issues)

    def test_bare_threshold_without_rationale_rejected(self):
        text = GOOD.replace(
            "- 决策阈值: 拦截率 < 50% 建议降级；依据：成本-效用换算见附录 A",
            "- 决策阈值: 60%",
        )
        p = parse_preregister(text)
        assert p.valid is False
        assert any("换算依据" in i for i in p.issues)


class TestLookup:
    def test_latest_by_filename(self, tmp_path: Path):
        (tmp_path / "2026-09-10-x.md").write_text(GOOD, encoding="utf-8")
        (tmp_path / "2026-09-16-y.md").write_text(GOOD, encoding="utf-8")
        assert find_latest_preregister(tmp_path).path.name == "2026-09-16-y.md"

    def test_empty_dir_returns_none(self, tmp_path: Path):
        assert find_latest_preregister(tmp_path) is None

    def test_assert_raises_when_absent(self, tmp_path: Path):
        with pytest.raises(MissingPreregistration):
            assert_preregistered(tmp_path)

    def test_assert_raises_when_invalid(self, tmp_path: Path):
        (tmp_path / "2026-09-16-bad.md").write_text("# 空文档\n", encoding="utf-8")
        with pytest.raises(MissingPreregistration):
            assert_preregistered(tmp_path)

    def test_assert_passes_when_valid(self, tmp_path: Path):
        (tmp_path / "2026-09-16-good.md").write_text(GOOD, encoding="utf-8")
        assert assert_preregistered(tmp_path).valid is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/causal_ablation/test_preregister.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.causal_ablation.preregister'`

- [ ] **Step 3: Write minimal implementation**

```python
# evals/causal_ablation/preregister.py
"""预登记门禁：无有效预登记不得跑批（spec causal-ablation「预登记与阳性对照」）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REQUIRED_FIELDS: tuple[str, ...] = (
    "主指标",
    "MDE",
    "决策阈值",
    "样本量依据",
    "停止规则",
    "rubric 版本",
)
_FIELD_RE = re.compile(r"^-\s*(?P<key>[^:：]+)[:：]\s*(?P<value>.+)$")
_RATIONALE_MARKERS = ("依据", "换算", "换算式")


@dataclass
class Preregistration:
    path: Path
    fields: dict[str, str]
    raw: str

    @property
    def issues(self) -> list[str]:
        out: list[str] = []
        for field in REQUIRED_FIELDS:
            if not self.fields.get(field, "").strip():
                out.append(f"缺字段: {field}")
        threshold = self.fields.get("决策阈值", "")
        if threshold and not any(m in threshold for m in _RATIONALE_MARKERS):
            out.append("决策阈值为裸数字，缺换算依据（须含「依据/换算」说明）")
        return out

    @property
    def valid(self) -> bool:
        return not self.issues


class MissingPreregistration(RuntimeError):
    """未找到有效预登记文档，拒绝跑批。"""


def parse_preregister(text: str) -> Preregistration:
    fields: dict[str, str] = {}
    for line in (text or "").splitlines():
        m = _FIELD_RE.match(line.strip())
        if m:
            fields[m.group("key").strip()] = m.group("value").strip()
    return Preregistration(path=Path("<inline>"), fields=fields, raw=text or "")


def find_latest_preregister(dir_path: Path) -> Preregistration | None:
    if not dir_path.exists():
        return None
    candidates = sorted(dir_path.glob("*.md"))
    if not candidates:
        return None
    latest = candidates[-1]
    parsed = parse_preregister(latest.read_text(encoding="utf-8"))
    parsed.path = latest
    return parsed


def assert_preregistered(dir_path: Path) -> Preregistration:
    found = find_latest_preregister(dir_path)
    if found is None:
        raise MissingPreregistration(
            f"未找到预登记文档（{dir_path}）：无预登记不得跑批"
        )
    if not found.valid:
        raise MissingPreregistration(
            f"预登记文档无效（{found.path}）：{'; '.join(found.issues)}"
        )
    return found
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/causal_ablation/test_preregister.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: Commit**

```bash
git add evals/causal_ablation/preregister.py tests/evals/causal_ablation/test_preregister.py
git commit -m "feat(evals): 预登记 schema 与跑批门禁（v2 消融框架 Task 2）"
```

---

### Task 3: 单元级判定记录（2.3）

**Files:**
- Create: `evals/causal_ablation/units.py`
- Test: `tests/evals/causal_ablation/test_units.py`

**Interfaces:**
- Consumes: 无
- Produces: `UNIT_TYPES: tuple[str, ...]`、`UnitJudgment`（frozen dataclass: `unit_id, ticker, run, variant, unit_type, judgment, method, confidence`）、`write_units(path, units)`、`read_units(path) -> list[UnitJudgment]`、`incomplete_reasons(units) -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/causal_ablation/test_units.py
from pathlib import Path

import pytest

from evals.causal_ablation.units import (
    UnitJudgment,
    incomplete_reasons,
    read_units,
    write_units,
)


def _unit(**kw):
    base = dict(
        unit_id="u1", ticker="002412", run="r1", variant="full",
        unit_type="claim", judgment="pass", method="code", confidence=0.9,
    )
    base.update(kw)
    return UnitJudgment(**base)


class TestValidation:
    def test_valid_unit_constructs(self):
        assert _unit().unit_type == "claim"

    @pytest.mark.parametrize("method", ["code", "nli", "judge"])
    def test_allowed_methods(self, method):
        assert _unit(method=method).method == method

    def test_bad_method_rejected(self):
        with pytest.raises(ValueError, match="method"):
            _unit(method="guess")

    def test_bad_unit_type_rejected(self):
        with pytest.raises(ValueError, match="unit_type"):
            _unit(unit_type="啥")

    @pytest.mark.parametrize("conf", [-0.1, 1.5])
    def test_confidence_out_of_range_rejected(self, conf):
        with pytest.raises(ValueError, match="confidence"):
            _unit(confidence=conf)

    def test_empty_judgment_rejected(self):
        with pytest.raises(ValueError, match="judgment"):
            _unit(judgment="")


class TestRoundTrip:
    def test_jsonl_round_trip(self, tmp_path: Path):
        p = tmp_path / "units.jsonl"
        units = [_unit(unit_id="u1"), _unit(unit_id="u2", method="judge", unit_type="risk_point")]
        write_units(p, units)
        assert read_units(p) == units

    def test_read_missing_file_returns_empty(self, tmp_path: Path):
        assert read_units(tmp_path / "nope.jsonl") == []


class TestCompleteness:
    def test_complete_set_has_no_reasons(self):
        assert incomplete_reasons([_unit()]) == []

    def test_missing_field_reported(self):
        reasons = incomplete_reasons([_unit(ticker="")])
        assert any("ticker" in r for r in reasons)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/causal_ablation/test_units.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# evals/causal_ablation/units.py
"""单元级判定记录：claim / 风险点 / 执行参数 / 价位（spec causal-ablation）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

UNIT_TYPES: tuple[str, ...] = ("claim", "risk_point", "exec_param", "price_level")
VALID_METHODS: tuple[str, ...] = ("code", "nli", "judge")


@dataclass(frozen=True)
class UnitJudgment:
    unit_id: str
    ticker: str
    run: str
    variant: str
    unit_type: str
    judgment: str
    method: str
    confidence: float

    def __post_init__(self) -> None:
        if self.method not in VALID_METHODS:
            raise ValueError(f"method 非法: {self.method!r}（须为 {VALID_METHODS}）")
        if self.unit_type not in UNIT_TYPES:
            raise ValueError(f"unit_type 非法: {self.unit_type!r}（须为 {UNIT_TYPES}）")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence 越界: {self.confidence}")
        if not self.judgment:
            raise ValueError("judgment 不得为空")


def write_units(path: Path, units: list[UnitJudgment]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(asdict(u), ensure_ascii=False) for u in units]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def read_units(path: Path) -> list[UnitJudgment]:
    if not path.exists():
        return []
    out: list[UnitJudgment] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(UnitJudgment(**json.loads(line)))
    return out


def incomplete_reasons(units: list[UnitJudgment]) -> list[str]:
    reasons: list[str] = []
    for u in units:
        for field in ("unit_id", "ticker", "run", "variant", "unit_type", "judgment"):
            if not getattr(u, field):
                reasons.append(f"{u.unit_id or '<无 id>'}: 缺字段 {field}")
    return reasons
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/causal_ablation/test_units.py -q`
Expected: PASS（11 passed）

- [ ] **Step 5: Commit**

```bash
git add evals/causal_ablation/units.py tests/evals/causal_ablation/test_units.py
git commit -m "feat(evals): 单元级判定记录结构与落库（v2 消融框架 Task 3）"
```

---

### Task 4: 聚类 bootstrap 与设计效应（2.4）

**Files:**
- Create: `evals/causal_ablation/aggregate.py`
- Test: `tests/evals/causal_ablation/test_aggregate.py`

**Interfaces:**
- Consumes: `evals.stats.paired_bootstrap_ci`（既有底座，只读复用）
- Produces: `cluster_mean_diff_ci(prev_by_cluster, cur_by_cluster, *, B=10_000, seed=42, alpha=0.05) -> tuple[float, float]`、`design_effect(cluster_sizes: Sequence[int], icc: float) -> float`、`effective_n(total_units: int, deff: float) -> float`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/causal_ablation/test_aggregate.py
import math
from statistics import mean

import pytest

from evals.causal_ablation.aggregate import (
    cluster_mean_diff_ci,
    design_effect,
    effective_n,
)


class TestClusterCi:
    def test_null_cluster_contains_zero(self):
        prev = {"a": [5.0, 5.0], "b": [4.0, 4.0], "c": [4.5, 4.5]}
        cur = {"a": [5.0, 5.0], "b": [4.0, 4.0], "c": [4.5, 4.5]}
        lo, hi = cluster_mean_diff_ci(prev, cur)
        assert lo <= 0 <= hi

    def test_strong_positive_shift_excludes_zero(self):
        prev = {t: [3.0] * 5 for t in ("a", "b", "c", "d", "e", "f")}
        cur = {t: [5.0] * 5 for t in ("a", "b", "c", "d", "e", "f")}
        lo, hi = cluster_mean_diff_ci(prev, cur)
        assert lo > 0

    def test_point_estimate_is_unit_mean_diff(self):
        prev = {"a": [1.0, 3.0], "b": [2.0, 2.0]}
        cur = {"a": [3.0, 5.0], "b": [4.0, 4.0]}
        lo, hi = cluster_mean_diff_ci(prev, cur)
        assert lo <= mean([3.0, 5.0, 4.0, 4.0]) - mean([1.0, 3.0, 2.0, 2.0]) <= hi

    def test_cluster_mismatch_rejected(self):
        with pytest.raises(ValueError, match="簇"):
            cluster_mean_diff_ci({"a": [1.0]}, {"b": [1.0]})

    def test_resampling_respects_cluster_boundary(self):
        # 簇间方向相反：按单元重采样会得到窄 CI；按簇重采样必须给出覆盖 [-10, 10] 的宽 CI
        prev = {"a": [0.0] * 50, "b": [10.0] * 50}
        cur = {"a": [10.0] * 50, "b": [0.0] * 50}
        lo, hi = cluster_mean_diff_ci(prev, cur, B=2000)
        assert hi - lo > 1.0


class TestDesignEffect:
    def test_icc_zero_is_one(self):
        assert design_effect([30, 30, 30], 0.0) == pytest.approx(1.0)

    def test_formula(self):
        # 1 + (m-1)ρ，m=30, ρ=0.1 → 3.9
        assert design_effect([30] * 20, 0.1) == pytest.approx(3.9)

    def test_effective_n_20x30_icc01(self):
        n = effective_n(600, design_effect([30] * 20, 0.1))
        assert math.isclose(n, 600 / 3.9, rel_tol=1e-6)
        assert 150 < n < 160

    def test_empty_sizes_rejected(self):
        with pytest.raises(ValueError):
            design_effect([], 0.1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/causal_ablation/test_aggregate.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# evals/causal_ablation/aggregate.py
"""聚类 bootstrap 与设计效应：推断单元是标的，不是 run/单元（spec causal-ablation）。"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import mean

import numpy as np


def cluster_mean_diff_ci(
    prev_by_cluster: dict[str, list[float]],
    cur_by_cluster: dict[str, list[float]],
    *,
    B: int = 10_000,  # noqa: N803 — 与 evals.stats 同口径
    seed: int = 42,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """以簇（标的）为重采样单元的配对 bootstrap CI（mean(cur) - mean(prev)）。

    点估计是单元级均值差；CI 反映的是簇层不确定性——两个簇各 50 个单元也会得到宽 CI。
    """
    clusters = sorted(set(prev_by_cluster) & set(cur_by_cluster))
    if not clusters:
        raise ValueError("无共同簇：prev/cur 的簇集合必须一致")
    only_prev = set(prev_by_cluster) - set(cur_by_cluster)
    only_cur = set(cur_by_cluster) - set(prev_by_cluster)
    if only_prev or only_cur:
        raise ValueError(f"簇不配对: 仅 prev={sorted(only_prev)} 仅 cur={sorted(only_cur)}")

    def _mean(values: Sequence[float]) -> float:
        return mean(values) if values else 0.0

    point = _mean([v for t in clusters for v in cur_by_cluster[t]]) - _mean(
        [v for t in clusters for v in prev_by_cluster[t]]
    )
    rng = np.random.default_rng(seed)
    n = len(clusters)
    diffs = np.empty(B, dtype=float)
    for i in range(B):
        picks = rng.integers(0, n, size=n)
        cur_vals = [v for p in picks for v in cur_by_cluster[clusters[p]]]
        prev_vals = [v for p in picks for v in prev_by_cluster[clusters[p]]]
        diffs[i] = _mean(cur_vals) - _mean(prev_vals)
    lo = float(np.percentile(diffs, 100 * alpha / 2))
    hi = float(np.percentile(diffs, 100 * (1 - alpha / 2)))
    # 点估计落在 CI 之外说明重采样退化（簇数=1 等），此时把点估计并入区间保证可解释
    return (min(lo, point), max(hi, point))


def design_effect(cluster_sizes: Sequence[int], icc: float) -> float:
    """设计效应 1 + (m-1)ρ；m 取簇大小均值。"""
    if not cluster_sizes:
        raise ValueError("cluster_sizes 不得为空")
    m = mean(cluster_sizes)
    return 1.0 + (m - 1.0) * icc


def effective_n(total_units: int, deff: float) -> float:
    """有效样本量 = 总单元数 / 设计效应。"""
    if deff <= 0:
        raise ValueError(f"设计效应必须为正: {deff}")
    return total_units / deff
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/causal_ablation/test_aggregate.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: Commit**

```bash
git add evals/causal_ablation/aggregate.py tests/evals/causal_ablation/test_aggregate.py
git commit -m "feat(evals): 聚类 bootstrap 与设计效应披露（v2 消融框架 Task 4）"
```

---

### Task 4B: 结论句式纪律（evaluation「结论句式纪律」）

**Files:**
- Create: `evals/causal_ablation/conclusion.py`
- Test: `tests/evals/causal_ablation/test_conclusion.py`

**Interfaces:**
- Consumes: 无
- Produces: `LEGAL_FORMS = ("true_negative", "inconclusive")`、`Conclusion`（frozen: `form, sentence, mde, threshold`）、`conclude(ci: tuple[float, float], *, mde: float, threshold: float, unit: str = "") -> Conclusion`、`assert_sentence_legal(sentence: str, mde: float | None) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/causal_ablation/test_conclusion.py
import pytest

from evals.causal_ablation.conclusion import (
    Conclusion,
    assert_sentence_legal,
    conclude,
)


class TestConclude:
    def test_ci_below_threshold_is_true_negative(self):
        got = conclude((0.0, 1.0), mde=2.0, threshold=3.0, unit="拦截率")
        assert got.form == "true_negative"
        assert "3.0" in got.sentence and "2.0" in got.sentence

    def test_ci_crossing_zero_and_threshold_is_inconclusive(self):
        got = conclude((-0.5, 4.0), mde=2.0, threshold=3.0)
        assert got.form == "inconclusive"
        assert "分辨率不足" in got.sentence

    def test_ci_above_threshold_is_not_a_cut_candidate(self):
        got = conclude((5.0, 9.0), mde=2.0, threshold=3.0)
        assert got.form == "inconclusive"
        assert "扩至" in got.sentence

    def test_mde_always_present_in_sentence(self):
        got = conclude((0.0, 1.0), mde=1.5, threshold=2.0)
        assert "MDE" in got.sentence


class TestLegality:
    def test_bare_no_support_rejected(self):
        with pytest.raises(ValueError, match="不合格"):
            assert_sentence_legal("该层价值未获统计支持", mde=None)

    def test_sentence_with_mde_passes(self):
        assert_sentence_legal("在本实验分辨率（MDE=2.0）下……", mde=2.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/causal_ablation/test_conclusion.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# evals/causal_ablation/conclusion.py
"""结论句式纪律：只有两种合法形态，且必带 MDE（spec evaluation 结论句式纪律）。"""

from __future__ import annotations

from dataclasses import dataclass

LEGAL_FORMS: tuple[str, ...] = ("true_negative", "inconclusive")
_BARE_NO_SUPPORT = "未获统计支持"


@dataclass(frozen=True)
class Conclusion:
    form: str
    sentence: str
    mde: float
    threshold: float


def conclude(
    ci: tuple[float, float],
    *,
    mde: float,
    threshold: float,
    unit: str = "增量",
) -> Conclusion:
    """ci 整体低于阈值 → 真阴性；其余（跨 0 或高于阈值）→ 分辨率不足，须扩样。"""
    lo, hi = ci
    if hi < threshold:
        sentence = (
            f"在本实验分辨率（MDE={mde}）下，该对象{unit}低于其成本对应阈值"
            f"（{threshold}），建议降级/裁剪"
        )
        return Conclusion("true_negative", sentence, mde, threshold)
    sentence = (
        f"分辨率不足（MDE={mde}，CI=[{lo}, {hi}] 未整体低于阈值 {threshold}），需扩至 N 只标的"
    )
    return Conclusion("inconclusive", sentence, mde, threshold)


def assert_sentence_legal(sentence: str, mde: float | None) -> None:
    """裸「未获统计支持」不合格：必须附 MDE。"""
    if _BARE_NO_SUPPORT in sentence and mde is None:
        raise ValueError(f"不合格结论句（缺 MDE）：{sentence!r}")
    if mde is not None and "MDE" not in sentence:
        raise ValueError(f"不合格结论句（未写出 MDE={mde}）：{sentence!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/causal_ablation/test_conclusion.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add evals/causal_ablation/conclusion.py tests/evals/causal_ablation/test_conclusion.py
git commit -m "feat(evals): 结论句式纪律（两句式 + MDE 强制）（v2 消融框架 Task 4B）"
```

---

### Task 5: 注入矩阵基础设施（2.5）

**Files:**
- Create: `evals/causal_ablation/injection.py`
- Test: `tests/evals/causal_ablation/test_injection.py`

**Interfaces:**
- Consumes: `evals.causal_ablation.claims.assert_admissible`
- Produces: `POLLUTION_TYPES: tuple[str, ...]`、`INJECTION_POINTS: tuple[str, ...]`、`COST_CLASS: dict[str, str]`（污染类型 → `offline_replay | real_run`）、`MechanismSwitch`（frozen: `id, kind, key, off_value, on_value`）、`MECHANISM_SWITCHES: dict[str, MechanismSwitch]`、`InjectionCase`（frozen: `case_id, pollution_type, injection_point, mechanism_id, payload`）、`build_injection_cases(pollution_type, *, ticker, base_values, n=4, seed=0) -> list[InjectionCase]`、`apply_injection(snapshot: dict, case: InjectionCase) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/causal_ablation/test_injection.py
import copy

import pytest

from evals.causal_ablation.injection import (
    INJECTION_POINTS,
    MECHANISM_SWITCHES,
    POLLUTION_TYPES,
    apply_injection,
    build_injection_cases,
)


BASE = {"price": 10.0, "revenue": 1.0e9, "growth": 0.15, "macro_as_of": "2026-09-01",
        "news": ["真新闻A"], "stop_loss": 9.0, "entry": 10.0}


class TestMatrix:
    def test_all_eight_pollutions_registered(self):
        assert len(POLLUTION_TYPES) == 8
        assert set(POLLUTION_TYPES) == {
            "value_error", "direction_error", "unit_error", "period_shift",
            "mirror_narrative", "fabricated_event", "stale_macro", "illegal_price",
        }

    def test_three_injection_points(self):
        assert set(INJECTION_POINTS) == {"context", "analyst_output", "decision"}

    def test_every_pollution_has_cost_class(self):
        assert set(COST_CLASS) == set(POLLUTION_TYPES)
        assert set(COST_CLASS.values()) <= {"offline_replay", "real_run"}

    def test_verifier_side_pollutions_are_offline(self):
        for pt in ("direction_error", "unit_error", "period_shift", "fabricated_event", "stale_macro"):
            assert COST_CLASS[pt] == "offline_replay"

    def test_input_side_pollutions_are_real_run(self):
        for pt in ("value_error", "mirror_narrative", "illegal_price"):
            assert COST_CLASS[pt] == "real_run"

    def test_every_switch_maps_to_registered_mechanism(self):
        from evals.causal_ablation.claims import DEFAULT_REGISTRY

        ids = {c.id for c in DEFAULT_REGISTRY}
        assert set(MECHANISM_SWITCHES) <= ids

    def test_switch_declares_on_and_off(self):
        for sw in MECHANISM_SWITCHES.values():
            assert sw.off_value != sw.on_value


class TestDeterminism:
    def test_same_seed_same_payload(self):
        a = build_injection_cases("value_error", ticker="002412", base_values=BASE, seed=7)
        b = build_injection_cases("value_error", ticker="002412", base_values=BASE, seed=7)
        assert a == b

    def test_different_seed_differs(self):
        a = build_injection_cases("value_error", ticker="002412", base_values=BASE, seed=1)
        b = build_injection_cases("value_error", ticker="002412", base_values=BASE, seed=2)
        assert a != b

    def test_n_cases_respected(self):
        assert len(build_injection_cases("value_error", ticker="T", base_values=BASE, n=4)) == 4

    def test_unknown_pollution_rejected(self):
        with pytest.raises(ValueError, match="污染类型"):
            build_injection_cases("meltdown", ticker="T", base_values=BASE)


class TestPayloadSemantics:
    def test_value_error_changes_numeric_by_5_to_50_pct(self):
        cases = build_injection_cases("value_error", ticker="T", base_values=BASE, n=4, seed=3)
        for c in cases:
            field, polluted = next(iter(c.payload["set"].items()))
            original = float(BASE[field])
            ratio = abs(polluted - original) / abs(original)
            assert 0.05 <= ratio <= 0.50

    def test_direction_error_flips_sign(self):
        case = build_injection_cases("direction_error", ticker="T", base_values=BASE, n=1)[0]
        assert case.payload["set"]["growth"] == pytest.approx(-0.15)

    def test_unit_error_is_1e8_scale(self):
        case = build_injection_cases("unit_error", ticker="T", base_values=BASE, n=1)[0]
        assert case.payload["set"]["revenue"] == pytest.approx(1.0e9 * 1.0e8)

    def test_fabricated_event_appends_title(self):
        case = build_injection_cases("fabricated_event", ticker="T", base_values=BASE, n=1)[0]
        assert len(case.payload["news_append"]) == 1

    def test_illegal_price_breaks_long_stop_rule(self):
        case = build_injection_cases("illegal_price", ticker="T", base_values=BASE, n=1)[0]
        assert case.payload["set"]["stop_loss"] > case.payload["set"]["entry"]


class TestApply:
    def test_apply_does_not_mutate_input(self):
        snapshot = copy.deepcopy(BASE)
        case = build_injection_cases("value_error", ticker="T", base_values=BASE, n=1)[0]
        apply_injection(snapshot, case)
        assert snapshot == BASE

    def test_apply_writes_payload(self):
        case = build_injection_cases("direction_error", ticker="T", base_values=BASE, n=1)[0]
        out = apply_injection(dict(BASE), case)
        assert out["growth"] == pytest.approx(-0.15)

    def test_news_append_extends_list(self):
        case = build_injection_cases("fabricated_event", ticker="T", base_values=BASE, n=1)[0]
        out = apply_injection(dict(BASE), case)
        assert len(out["news"]) == len(BASE["news"]) + 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/causal_ablation/test_injection.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# evals/causal_ablation/injection.py
"""注入法污染矩阵：8 类污染的确定性构造 + 机制开关注册（spec causal-ablation）。"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any

POLLUTION_TYPES: tuple[str, ...] = (
    "value_error",
    "direction_error",
    "unit_error",
    "period_shift",
    "mirror_narrative",
    "fabricated_event",
    "stale_macro",
    "illegal_price",
)
INJECTION_POINTS: tuple[str, ...] = ("context", "analyst_output", "decision")


@dataclass(frozen=True)
class MechanismSwitch:
    """机制开关点：off_value/on_value 为配置项（env 或属性）的取值。"""

    id: str
    kind: str  # "env" | "callable"
    key: str
    off_value: Any
    on_value: Any


MECHANISM_SWITCHES: dict[str, MechanismSwitch] = {
    "A1": MechanismSwitch("A1", "callable", "compute_metrics_injection", False, True),
    "A2": MechanismSwitch("A2", "callable", "semantic_header", False, True),
    "A3": MechanismSwitch("A3", "callable", "verify_citations", False, True),
    "A4": MechanismSwitch("A4", "callable", "surgical_repair", False, True),
    "A5": MechanismSwitch("A5", "callable", "price_sanity_check", False, True),
    "A6": MechanismSwitch("A6", "callable", "text_echo_match", False, True),
    "A7": MechanismSwitch("A7", "callable", "macro_freshness_mark", False, True),
}

# 污染类型 → 注入点, 因果负责机制
_POLLUTION_ROUTING: dict[str, tuple[str, str]] = {
    "value_error": ("context", "A3"),
    "direction_error": ("analyst_output", "A3"),
    "unit_error": ("analyst_output", "A3"),
    "period_shift": ("analyst_output", "A2"),
    "mirror_narrative": ("context", "A2"),
    "fabricated_event": ("analyst_output", "A6"),
    "stale_macro": ("context", "A7"),
    "illegal_price": ("decision", "A5"),
}

# 成本分型：校验侧污染可对已有产物离线重放（零 LLM）；输入侧/决策层须真跑全管线
COST_CLASS: dict[str, str] = {
    "value_error": "real_run",
    "direction_error": "offline_replay",
    "unit_error": "offline_replay",
    "period_shift": "offline_replay",
    "mirror_narrative": "real_run",
    "fabricated_event": "offline_replay",
    "stale_macro": "offline_replay",
    "illegal_price": "real_run",
}


@dataclass(frozen=True)
class InjectionCase:
    case_id: str
    pollution_type: str
    injection_point: str
    mechanism_id: str
    payload: dict[str, Any] = field(default_factory=dict)


def build_injection_cases(
    pollution_type: str,
    *,
    ticker: str,
    base_values: dict[str, Any],
    n: int = 4,
    seed: int = 0,
) -> list[InjectionCase]:
    """确定性构造 n 个污染单元（同 seed 同结果，见测试 TestDeterminism）。"""
    if pollution_type not in POLLUTION_TYPES:
        raise ValueError(f"未知污染类型: {pollution_type!r}（须为 {POLLUTION_TYPES}）")
    point, mechanism = _POLLUTION_ROUTING[pollution_type]
    rng = random.Random(f"{seed}:{ticker}:{pollution_type}")
    cases: list[InjectionCase] = []
    for i in range(n):
        payload = _payload(pollution_type, base_values, rng, i)
        cases.append(
            InjectionCase(
                case_id=f"{ticker}-{pollution_type}-{i}",
                pollution_type=pollution_type,
                injection_point=point,
                mechanism_id=mechanism,
                payload=payload,
            )
        )
    return cases


def _payload(
    pollution_type: str, base_values: dict[str, Any], rng: random.Random, idx: int
) -> dict[str, Any]:
    if pollution_type == "value_error":
        field_name = "revenue" if idx % 2 == 0 else "price"
        original = float(base_values[field_name])
        factor = 1.0 + rng.choice([-1.0, 1.0]) * rng.uniform(0.05, 0.50)
        return {"set": {field_name: original * factor}, "field": field_name}
    if pollution_type == "direction_error":
        return {"set": {"growth": -float(base_values["growth"])}, "field": "growth"}
    if pollution_type == "unit_error":
        return {"set": {"revenue": float(base_values["revenue"]) * 1.0e8}, "field": "revenue"}
    if pollution_type == "period_shift":
        return {"set": {"period_label": "2026Q3", "period_value": 0.0}, "field": "period_label"}
    if pollution_type == "mirror_narrative":
        return {"reverse_sequence": True, "field": "series_direction"}
    if pollution_type == "fabricated_event":
        return {"news_append": [f"{'、'.join([]) or '某公司'}宣布重大战略合作（注入）"], "field": "news"}
    if pollution_type == "stale_macro":
        return {"set": {"macro_as_of": "2026-05-01"}, "field": "macro_as_of"}
    if pollution_type == "illegal_price":
        entry = float(base_values["entry"])
        return {"set": {"stop_loss": round(entry * 1.1, 4), "entry": entry}, "field": "stop_loss"}
    raise ValueError(f"未实现污染类型: {pollution_type}")


def apply_injection(snapshot: dict[str, Any], case: InjectionCase) -> dict[str, Any]:
    """返回污染后的新快照（深拷贝；不得修改入参——见 TestApply）。"""
    out = copy.deepcopy(snapshot)
    for key, value in case.payload.get("set", {}).items():
        out[key] = value
    if "news_append" in case.payload:
        out["news"] = list(out.get("news", [])) + list(case.payload["news_append"])
    if case.payload.get("reverse_sequence"):
        series = out.get("close_series")
        if isinstance(series, list):
            out["close_series"] = list(reversed(series))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/causal_ablation/test_injection.py -q`
Expected: PASS（15 passed）

- [ ] **Step 5: Commit**

```bash
git add evals/causal_ablation/injection.py tests/evals/causal_ablation/test_injection.py
git commit -m "feat(evals): 注入矩阵基础设施与机制开关注册（v2 消融框架 Task 5）"
```

---

### Task 6: 逃逸率统计与误报分离（2.6）

**Files:**
- Create: `evals/causal_ablation/escape.py`
- Test: `tests/evals/causal_ablation/test_escape.py`

**Interfaces:**
- Consumes: 无
- Produces: `EscapePair`（frozen: `case_id, on_state, off_state`，取值 `caught | escaped`）、`classify_state(*, polluted_present: bool, flagged: bool) -> str`、`mcnemar_table(pairs) -> dict[str, float]`（键 `b, c, discordant_ratio`）、`escape_rate(pairs, *, adjudicated: set[str]) -> dict[str, float | int]`、`split_verifier_buckets(counts: dict[str, int]) -> dict[str, int]`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/causal_ablation/test_escape.py
import pytest

from evals.causal_ablation.escape import (
    EscapePair,
    classify_state,
    escape_rate,
    mcnemar_table,
    split_verifier_buckets,
)


class TestClassify:
    def test_flagged_is_caught(self):
        assert classify_state(polluted_present=True, flagged=True) == "caught"

    def test_present_and_unflagged_is_escaped(self):
        assert classify_state(polluted_present=True, flagged=False) == "escaped"

    def test_absent_is_caught(self):
        assert classify_state(polluted_present=False, flagged=False) == "caught"


class TestMcNemar:
    def test_counts_discordant_pairs(self):
        pairs = [
            EscapePair("a", "caught", "escaped"),   # b
            EscapePair("b", "escaped", "caught"),   # c
            EscapePair("c", "caught", "caught"),    # 一致
            EscapePair("d", "escaped", "escaped"),  # 一致
        ]
        table = mcnemar_table(pairs)
        assert table["b"] == 1
        assert table["c"] == 1
        assert table["discordant_ratio"] == pytest.approx(0.5)

    def test_no_discordant_pairs_ratio_zero(self):
        table = mcnemar_table([EscapePair("a", "caught", "caught")])
        assert table["discordant_ratio"] == 0.0


class TestEscapeRate:
    def test_rate_denominator_is_adjudicated_units(self):
        pairs = [
            EscapePair("a", "caught", "escaped"),
            EscapePair("c", "caught", "caught"),
        ]
        result = escape_rate(pairs, adjudicated={"a", "c"})
        assert result["escapes"] == 1
        assert result["denominator"] == 2
        assert result["rate"] == pytest.approx(0.5)

    def test_unadjudicated_escapes_are_pending_not_counted(self):
        pairs = [
            EscapePair("a", "caught", "escaped"),
            EscapePair("b", "caught", "escaped"),
        ]
        result = escape_rate(pairs, adjudicated={"a"})
        assert result["escapes"] == 1
        assert result["pending"] == 1
        assert result["rate"] == pytest.approx(1.0)

    def test_no_adjudication_yields_none_not_zero(self):
        """未终裁时不得报 0%——「0 逃逸」与「没判定过」是两回事。"""
        result = escape_rate([EscapePair("a", "caught", "escaped")], adjudicated=set())
        assert result["escapes"] == 0
        assert result["pending"] == 1
        assert result["rate"] is None

    def test_empty_pairs_rate_none(self):
        assert escape_rate([], adjudicated=set())["rate"] is None


class TestFalsePositiveSplit:
    def test_four_buckets_total(self):
        got = split_verifier_buckets(
            {"blocked": 3, "analyst_true_fail": 1, "surgical_repaired": 2, "verifier_normalized": 4}
        )
        assert got["total"] == 10
        assert got["verifier_normalized"] == 4

    def test_unknown_bucket_rejected(self):
        with pytest.raises(ValueError, match="未知桶"):
            split_verifier_buckets({"误报": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/causal_ablation/test_escape.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# evals/causal_ablation/escape.py
"""逃逸率统计：只认真逃逸（人工终裁），校验器误报走四桶拆报（spec causal-ablation）。"""

from __future__ import annotations

from dataclasses import dataclass

VERIFIER_BUCKETS: tuple[str, ...] = (
    "blocked",
    "analyst_true_fail",
    "surgical_repaired",
    "verifier_normalized",
)


@dataclass(frozen=True)
class EscapePair:
    case_id: str
    on_state: str  # caught | escaped
    off_state: str


def classify_state(*, polluted_present: bool, flagged: bool) -> str:
    """逃逸 = 污染值出现在终态产物且未被告警；其余为 caught。"""
    return "escaped" if (polluted_present and not flagged) else "caught"


def mcnemar_table(pairs: list[EscapePair]) -> dict[str, float]:
    """不一致对子表：b = 开拦下/关逃逸，c = 反向；一致对子不携带信息。"""
    b = sum(1 for p in pairs if p.on_state == "caught" and p.off_state == "escaped")
    c = sum(1 for p in pairs if p.on_state == "escaped" and p.off_state == "caught")
    total = len(pairs)
    return {
        "b": float(b),
        "c": float(c),
        "discordant_ratio": ((b + c) / total) if total else 0.0,
    }


def escape_rate(pairs: list[EscapePair], *, adjudicated: set[str]) -> dict[str, float | int]:
    """rate 的分母为已终裁单元数；未终裁单列 pending，不进分子也不进分母。"""
    escaped = [p for p in pairs if p.off_state == "escaped"]
    adjudicated_escapes = [p for p in escaped if p.case_id in adjudicated]
    pending = [p for p in escaped if p.case_id not in adjudicated]
    denom = len(adjudicated_escapes) + len(
        [p for p in pairs if p.off_state == "caught" and p.case_id in adjudicated]
    )
    return {
        "escapes": len(adjudicated_escapes),
        "pending": len(pending),
        "denominator": denom,
        "rate": (len(adjudicated_escapes) / denom) if denom else None,
    }


def split_verifier_buckets(counts: dict[str, int]) -> dict[str, int]:
    """四桶拆报：校验器误报（verifier_normalized 等）不进逃逸率分母。"""
    unknown = set(counts) - set(VERIFIER_BUCKETS)
    if unknown:
        raise ValueError(f"未知桶: {sorted(unknown)}（合法桶 {VERIFIER_BUCKETS}）")
    out = {b: int(counts.get(b, 0)) for b in VERIFIER_BUCKETS}
    out["total"] = sum(out.values())
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/causal_ablation/test_escape.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: Commit**

```bash
git add evals/causal_ablation/escape.py tests/evals/causal_ablation/test_escape.py
git commit -m "feat(evals): 逃逸率统计与校验器误报分离（v2 消融框架 Task 6）"
```

---

### Task 7: 族 B 指标管线（2.7）

**Files:**
- Create: `evals/causal_ablation/family_b.py`
- Test: `tests/evals/causal_ablation/test_family_b.py`

**Interfaces:**
- Consumes: 无（NLI/judge 以注入 callable 形式留接口，测试用 fake）
- Produces: `GroundingParam`（frozen: `name, value, source_ref`）、`risk_point_diff(prev_points, cur_points) -> list[str]`、`absorption_rate(new_points: list[str], is_absorbed: Callable[[str], bool]) -> float`、`grounding_rate(params: Sequence[GroundingParam]) -> float`、`pairwise_assign(left_id, right_id, *, pair_key) -> tuple[str, str]`、`majority_verdict(votes: Sequence[str]) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/causal_ablation/test_family_b.py
import pytest

from evals.causal_ablation.family_b import (
    GroundingParam,
    absorption_rate,
    grounding_rate,
    majority_verdict,
    pairwise_assign,
    risk_point_diff,
)


class TestDiff:
    def test_new_points_extracted(self):
        prev = ["估值偏高", "行业景气下行"]
        cur = ["估值偏高", "行业景气下行", "应收周转恶化"]
        assert risk_point_diff(prev, cur) == ["应收周转恶化"]

    def test_whitespace_normalized(self):
        assert risk_point_diff(["A 风险"], ["A风险"]) == []

    def test_empty_prev_returns_all(self):
        assert risk_point_diff([], ["x", "y"]) == ["x", "y"]

    def test_order_stable(self):
        assert risk_point_diff([], ["b", "a"]) == ["b", "a"]


class TestAbsorption:
    def test_rate_over_new_points(self):
        assert absorption_rate(["a", "b"], lambda p: p == "a") == pytest.approx(0.5)

    def test_no_new_points_returns_none(self):
        assert absorption_rate([], lambda p: True) is None


class TestGrounding:
    def test_rate_counts_sourced_params(self):
        params = [
            GroundingParam("止损", 9.0, "trader:stop_loss"),
            GroundingParam("仓位", 0.3, None),
        ]
        assert grounding_rate(params) == pytest.approx(0.5)

    def test_empty_returns_none(self):
        assert grounding_rate([]) is None


class TestPairwise:
    def test_assignment_is_deterministic(self):
        assert pairwise_assign("full", "plus_debate", pair_key="002412#1") == pairwise_assign(
            "full", "plus_debate", pair_key="002412#1"
        )

    def test_assignment_returns_both_ids_once(self):
        got = pairwise_assign("full", "plus_debate", pair_key="600519#2")
        assert set(got) == {"full", "plus_debate"}

    def test_position_varies_across_keys(self):
        keys = [f"t#i" for i in range(30)]
        firsts = {pairwise_assign("full", "plus_debate", pair_key=k)[0] for k in keys}
        assert firsts == {"full", "plus_debate"}


class TestMajority:
    def test_majority_wins(self):
        assert majority_verdict(["A", "A", "B"]) == "A"

    def test_tie_returns_tie(self):
        assert majority_verdict(["A", "B"]) == "tie"

    def test_empty_is_tie(self):
        assert majority_verdict([]) == "tie"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/causal_ablation/test_family_b.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# evals/causal_ablation/family_b.py
"""族 B 编排层价值指标：B1 风险点增量、B3 出处率、B5 pairwise 盲评协议。

LLM/NLI 判定以 callable 注入（is_absorbed），使本模块可离线单测。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class GroundingParam:
    name: str
    value: float
    source_ref: str | None


def _norm(text: str) -> str:
    return "".join((text or "").split())


def risk_point_diff(prev_points: list[str], cur_points: list[str]) -> list[str]:
    """差集：cur 有而 prev 无的风险点（空白归一化后比对，保持 cur 顺序）。"""
    seen = {_norm(p) for p in prev_points}
    return [p for p in cur_points if _norm(p) not in seen]


def absorption_rate(
    new_points: list[str], is_absorbed: Callable[[str], bool]
) -> float | None:
    """被决策吸收的新增风险点占比；无新增点返回 None（不得记为 0）。"""
    if not new_points:
        return None
    return sum(1 for p in new_points if is_absorbed(p)) / len(new_points)


def grounding_rate(params: Sequence[GroundingParam]) -> float | None:
    """B3：有出处（source_ref 非空）的执行参数占比。"""
    if not params:
        return None
    return sum(1 for p in params if p.source_ref) / len(params)


def pairwise_assign(left_id: str, right_id: str, *, pair_key: str) -> tuple[str, str]:
    """A/B 位置随机化：同一 pair_key 结果稳定（可复现），跨 key 位置分布均匀。"""
    digest = hashlib.sha256(pair_key.encode("utf-8")).digest()
    return (right_id, left_id) if digest[0] % 2 else (left_id, right_id)


def majority_verdict(votes: Sequence[str]) -> str:
    """K 次多数决；无多数（含空输入）返回 'tie'。"""
    counts: dict[str, int] = {}
    for v in votes:
        counts[v] = counts.get(v, 0) + 1
    if not counts:
        return "tie"
    top = max(counts.values())
    winners = [k for k, v in counts.items() if v == top]
    return winners[0] if len(winners) == 1 else "tie"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/causal_ablation/test_family_b.py -q`
Expected: PASS（14 passed）

- [ ] **Step 5: Commit**

```bash
git add evals/causal_ablation/family_b.py tests/evals/causal_ablation/test_family_b.py
git commit -m "feat(evals): 族 B 指标管线（B1/B3/B5 确定性部分）（v2 消融框架 Task 7）"
```

---

### Task 8: LLM 判定校准门控（2.8）

**Files:**
- Create: `evals/causal_ablation/calibration_gate.py`
- Test: `tests/evals/causal_ablation/test_calibration_gate.py`

**Interfaces:**
- Consumes: 无
- Produces: `MIN_AGREEMENT = 0.80`、`MIN_REVIEW_FRACTION = 0.20`、`agreement_rate(judge_labels, human_labels) -> float`、`gate_dimension(method, judge_labels, human_labels, *, min_agreement=MIN_AGREEMENT) -> dict`、`CalibrationBlocked(RuntimeError)`、`assert_calibrated(method, judge_labels, human_labels) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/causal_ablation/test_calibration_gate.py
import pytest

from evals.causal_ablation.calibration_gate import (
    CalibrationBlocked,
    agreement_rate,
    assert_calibrated,
    gate_dimension,
)


class TestAgreement:
    def test_perfect_agreement(self):
        assert agreement_rate(["a", "b"], ["a", "b"]) == pytest.approx(1.0)

    def test_partial_agreement(self):
        assert agreement_rate(["a", "b", "b", "a"], ["a", "b", "a", "a"]) == pytest.approx(0.75)

    def test_length_mismatch_rejected(self):
        with pytest.raises(ValueError, match="等长"):
            agreement_rate(["a"], ["a", "b"])

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            agreement_rate([], [])


class TestGate:
    def test_code_method_not_gated(self):
        got = gate_dimension("code", [], [])
        assert got["gated"] is False
        assert got["passed"] is True

    def test_nli_below_threshold_blocked(self):
        got = gate_dimension("nli", ["a"] * 7 + ["b"] * 3, ["a"] * 10)
        assert got["gated"] is True
        assert got["passed"] is False
        assert got["agreement"] == pytest.approx(0.7)

    def test_judge_at_threshold_passes(self):
        got = gate_dimension("judge", ["a"] * 8 + ["b"] * 2, ["a"] * 10)
        assert got["passed"] is True

    def test_assert_raises_for_uncalibrated(self):
        with pytest.raises(CalibrationBlocked, match="nli"):
            assert_calibrated("nli", ["a", "b"], ["a", "a"])

    def test_assert_passes_for_calibrated(self):
        assert_calibrated("judge", ["a"] * 9 + ["b"], ["a"] * 10)

    def test_unknown_method_rejected(self):
        with pytest.raises(ValueError, match="method"):
            gate_dimension("vibes", ["a"], ["a"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/causal_ablation/test_calibration_gate.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# evals/causal_ablation/calibration_gate.py
"""校准门控全覆盖：凡 method ∈ {nli, judge} 的判定须人工一致率 ≥ 0.80（spec causal-ablation）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

MIN_AGREEMENT = 0.80
MIN_REVIEW_FRACTION = 0.20
_GATED_METHODS = ("nli", "judge")


class CalibrationBlocked(RuntimeError):
    """判定未过校准门控，不得进入消融结论。"""


def agreement_rate(judge_labels: Sequence[str], human_labels: Sequence[str]) -> float:
    if not judge_labels or not human_labels:
        raise ValueError("校准比对要求标签非空")
    if len(judge_labels) != len(human_labels):
        raise ValueError("校准比对要求两侧等长")
    same = sum(1 for j, h in zip(judge_labels, human_labels, strict=True) if j == h)
    return same / len(judge_labels)


def gate_dimension(
    method: str,
    judge_labels: Sequence[str],
    human_labels: Sequence[str],
    *,
    min_agreement: float = MIN_AGREEMENT,
) -> dict[str, Any]:
    """返回 {gated, passed, agreement, reason}；code 判定不适用校准门控。"""
    if method == "code":
        return {"gated": False, "passed": True, "agreement": None, "reason": "code 判定不适用校准门控"}
    if method not in _GATED_METHODS:
        raise ValueError(f"未知 method: {method!r}（须为 code/nli/judge 之一）")
    rate = agreement_rate(judge_labels, human_labels)
    passed = rate >= min_agreement
    reason = (
        "一致率达标"
        if passed
        else f"一致率 {rate:.2f} < {min_agreement:.2f}：该批判定作废并重校准"
    )
    return {"gated": True, "passed": passed, "agreement": rate, "reason": reason}


def assert_calibrated(
    method: str,
    judge_labels: Sequence[str],
    human_labels: Sequence[str],
    *,
    min_agreement: float = MIN_AGREEMENT,
) -> None:
    result = gate_dimension(
        method, judge_labels, human_labels, min_agreement=min_agreement
    )
    if not result["passed"]:
        raise CalibrationBlocked(f"{method} 判定未过校准门控：{result['reason']}")
```

> 注意：code 分支的 reason 文案为「code 判定不适用校准门控」，测试不校验文案。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/causal_ablation/test_calibration_gate.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: Commit**

```bash
git add evals/causal_ablation/calibration_gate.py tests/evals/causal_ablation/test_calibration_gate.py
git commit -m "feat(evals): LLM 判定校准门控全覆盖（v2 消融框架 Task 8）"
```

---

### Task 9: 结论注册表状态解析（F8 代码部分）

**Files:**
- Create: `evals/causal_ablation/report_status.py`
- Test: `tests/evals/causal_ablation/test_report_status.py`

**Interfaces:**
- Consumes: 无
- Produces: `STATUSES = ("active", "superseded-by")`、`parse_status(text) -> tuple[str, str | None]`、`assert_status_valid(text) -> None`、`status_badge(status, target=None) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/causal_ablation/test_report_status.py
import pytest

from evals.causal_ablation.report_status import (
    assert_status_valid,
    parse_status,
    status_badge,
)


class TestParse:
    def test_active(self):
        assert parse_status("# R\n\n**status**: active\n") == ("active", None)

    def test_superseded(self):
        status, target = parse_status("# R\n\n**status**: superseded-by: docs/x.md\n")
        assert status == "superseded-by"
        assert target == "docs/x.md"

    def test_missing_raises(self):
        with pytest.raises(ValueError, match="status"):
            parse_status("# R\n")

    def test_unknown_status_raises(self):
        with pytest.raises(ValueError, match="未知 status"):
            parse_status("**status**: maybe\n")


class TestValidate:
    def test_valid_passes(self):
        assert_status_valid("**status**: active\n")

    def test_superseded_requires_target(self):
        with pytest.raises(ValueError, match="目标"):
            assert_status_valid("**status**: superseded-by:\n")


class TestBadge:
    def test_active_badge(self):
        assert "active" in status_badge("active")

    def test_superseded_badge_points_to_target(self):
        assert "docs/x.md" in status_badge("superseded-by", "docs/x.md")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/causal_ablation/test_report_status.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# evals/causal_ablation/report_status.py
"""结论注册表状态解析：报告结论有生命周期（spec causal-ablation「结论注册表生命周期」）。"""

from __future__ import annotations

import re

STATUSES: tuple[str, ...] = ("active", "superseded-by")
_STATUS_RE = re.compile(r"\*\*status\*\*:\s*(?P<status>[A-Za-z-]+)\s*:?\s*(?P<target>\S*)")


def parse_status(text: str) -> tuple[str, str | None]:
    m = _STATUS_RE.search(text or "")
    if not m:
        raise ValueError("报告缺 status 头（期望 `**status**: active | superseded-by: <path>`）")
    status = m.group("status")
    if status not in STATUSES:
        raise ValueError(f"未知 status: {status!r}（须为 {STATUSES}）")
    target = m.group("target") or None
    return status, target


def assert_status_valid(text: str) -> None:
    status, target = parse_status(text)
    if status == "superseded-by" and not target:
        raise ValueError("superseded-by 必须带目标路径（取代者报告）")


def status_badge(status: str, target: str | None = None) -> str:
    if status == "active":
        return "![active](badge:active)"
    return f"![superseded](badge:superseded) → {target or '<缺目标>'}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/causal_ablation/test_report_status.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: Commit**

```bash
git add evals/causal_ablation/report_status.py tests/evals/causal_ablation/test_report_status.py
git commit -m "feat(evals): 结论注册表状态解析与徽章（v2 消融框架 Task 9）"
```

---

### Task 10: P1 预登记文档（3.4）

**Files:**
- Create: `evals/ablation/preregister/2026-09-16-p1-injection-pilot.md`
- Test: `tests/evals/causal_ablation/test_preregister_p1.py`

**Interfaces:**
- Consumes: `evals.causal_ablation.preregister.assert_preregistered`
- Produces: 无（文档产物；测试证明它能过门禁）

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/causal_ablation/test_preregister_p1.py
from pathlib import Path

from evals.causal_ablation.preregister import assert_preregistered


def test_p1_preregister_is_valid():
    doc = assert_preregistered(Path("evals/ablation/preregister"))
    assert doc.valid is True
    assert "逃逸率" in doc.fields["主指标"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/causal_ablation/test_preregister_p1.py -q`
Expected: FAIL with `MissingPreregistration`

- [ ] **Step 3: Write minimal implementation**

```markdown
<!-- evals/ablation/preregister/2026-09-16-p1-injection-pilot.md -->
# P1 注入法反幻觉消融 pilot 预登记

- 主指标: 逃逸率（污染值原样出现在终态产物且经人工终裁确认为真逃逸）
- MDE: 6pp（10 标的 × 32 污染单元 × 2 态，不一致对子按 20% 估，α=0.05 / power=0.8）
- 决策阈值: 某机制拦截率 < 50% 判为薄防线并优先加固；依据：拦截率 50% 以下意味着该机制在受控污染下不比随机拦下更好，加固成本低于保留误报成本（详见 delta design D6）
- 样本量依据: 10 标的 pilot（强度校准）→ 20 标的正式批；单元数 = 标的数 × 8 类污染 × 4 实例
- 停止规则: 不一致对子比例 < 10%（污染太易/太难）立即停跑并重校注入强度，不硬扩样本；人工终裁量上限 300 条，超限收窄污染矩阵而非放宽终裁
- rubric 版本: judge-v8 + round7 校准基线（2026-09-13 达标：MAE 0.342 / 方向一致率 97.6%）
- 成本分型: 离线重放型（A2/A3/A4/A6/A7）零 LLM；真跑型（A1/A5 输入侧联动与决策层）限量 pilot
- 阳性对照: 每批带关闭 verify_citations 的已知劣化变体；测不出显著差异则本批阴性结论作废
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/causal_ablation/test_preregister_p1.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add evals/ablation/preregister/2026-09-16-p1-injection-pilot.md tests/evals/causal_ablation/test_preregister_p1.py
git commit -m "docs(evals): P1 注入法 pilot 预登记（v2 消融框架 Task 10）"
```

---

### Task 11: 阳性对照离线验证（3.1）

**Files:**
- Create: `tests/evals/causal_ablation/test_positive_control.py`
- Test: 同文件

**Interfaces:**
- Consumes: `evals.causal_ablation.escape.escape_rate`、`evals.causal_ablation.escape.EscapePair`
- Produces: 无（验证管线灵敏度可测）

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/causal_ablation/test_positive_control.py
"""阳性对照：已知劣化变体（关 verify_citations）必须测得出差异，否则管线灵敏度未证实。"""

from evals.causal_ablation.escape import EscapePair, escape_rate, mcnemar_table


def _pairs(on_caught: int, on_escaped: int, total: int) -> list[EscapePair]:
    pairs = [EscapePair(f"c{i}", "caught", "escaped") for i in range(on_caught)]
    pairs += [EscapePair(f"e{i}", "escaped", "escaped") for i in range(on_escaped)]
    pairs += [EscapePair(f"x{i}", "caught", "caught") for i in range(total - on_caught - on_escaped)]
    return pairs


class TestPositiveControlSensitivity:
    def test_known_degradation_is_detectable(self):
        # 关 verify_citations：20 单元中 16 个由「拦下」变「逃逸」
        pairs = _pairs(on_caught=16, on_escaped=4, total=20)
        table = mcnemar_table(pairs)
        assert table["b"] == 16
        assert table["discordant_ratio"] >= 0.5

    def test_no_effect_yields_no_discordant_pairs(self):
        pairs = [EscapePair(f"c{i}", "caught", "caught") for i in range(20)]
        assert mcnemar_table(pairs)["discordant_ratio"] == 0.0

    def test_escape_rate_requires_adjudication(self):
        pairs = _pairs(on_caught=16, on_escaped=4, total=20)
        adjudicated = {p.case_id for p in pairs}
        result = escape_rate(pairs, adjudicated=adjudicated)
        assert result["rate"] == 1.0
        assert result["pending"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/causal_ablation/test_positive_control.py -q`
Expected: PASS if Task 6 is complete; FAIL with `ModuleNotFoundError` otherwise

- [ ] **Step 3: 记录结论（本任务为验证任务，无新增实现代码）**

若 Step 2 未通过，说明 Task 6 未完成——先完成 Task 6，不要在本任务里补实现。通过后在本文件下方记录：阳性对照在本实验分辨率下可检出（不一致对子比例 ≥ 50%），管线灵敏度证实；接入真实材料后若 `discordant_ratio < 0.10`，按 spec 本轮阴性结论作废。

- [ ] **Step 4: 记录结论**

在提交信息中写明：阳性对照在本实验分辨率下可检出（不一致对子比例 ≥ 50%），管线灵敏度证实；若接入真实材料后 `discordant_ratio < 0.10`，本轮阴性结论按 spec 作废。

- [ ] **Step 5: Commit**

```bash
git add tests/evals/causal_ablation/test_positive_control.py
git commit -m "test(evals): 阳性对照灵敏度验证（v2 消融框架 Task 11）"
```

---

### Task 12: 注入强度 pilot 校准脚本（3.2）

**Files:**
- Create: `evals/causal_ablation/pilot_calibration.py`
- Test: `tests/evals/causal_ablation/test_pilot_calibration.py`

**Interfaces:**
- Consumes: `evals.causal_ablation.escape.mcnemar_table`、`EscapePair`
- Produces: `calibration_verdict(pairs, *, min_ratio=0.10, max_ratio=0.90) -> dict`（键 `verdict: "ok" | "too_easy" | "too_hard"`、`ratio`、`advice`）

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/causal_ablation/test_pilot_calibration.py
import pytest

from evals.causal_ablation.escape import EscapePair
from evals.causal_ablation.pilot_calibration import calibration_verdict


def _pairs(discordant: int, total: int) -> list[EscapePair]:
    out = [EscapePair(f"d{i}", "caught", "escaped") for i in range(discordant)]
    out += [EscapePair(f"s{i}", "caught", "caught") for i in range(total - discordant)]
    return out


class TestCalibration:
    def test_healthy_ratio_is_ok(self):
        got = calibration_verdict(_pairs(4, 10))
        assert got["verdict"] == "ok"

    def test_all_caught_is_too_easy(self):
        got = calibration_verdict(_pairs(0, 10))
        assert got["verdict"] == "too_easy"
        assert "强度" in got["advice"]

    def test_all_escaped_is_too_hard(self):
        got = calibration_verdict(_pairs(10, 10))
        assert got["verdict"] == "too_hard"

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            calibration_verdict([])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/causal_ablation/test_pilot_calibration.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# evals/causal_ablation/pilot_calibration.py
"""注入强度校准：太易全拦/太难全穿都浪费样本（spec causal-ablation 风险对策）。"""

from __future__ import annotations

from evals.causal_ablation.escape import EscapePair, mcnemar_table


def calibration_verdict(
    pairs: list[EscapePair], *, min_ratio: float = 0.10, max_ratio: float = 0.90
) -> dict[str, object]:
    if not pairs:
        raise ValueError("pilot 校准要求非空配对集合")
    ratio = mcnemar_table(pairs)["discordant_ratio"]
    if ratio < min_ratio:
        verdict, advice = "too_easy", "污染太易被拦：提高注入强度或换注入点，不硬扩样本"
    elif ratio > max_ratio:
        verdict, advice = "too_hard", "污染太难被拦：降低注入强度或校验收紧，不硬扩样本"
    else:
        verdict, advice = "ok", "注入强度落在健康区间，可扩到正式批次"
    return {"verdict": verdict, "ratio": ratio, "advice": advice}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/causal_ablation/test_pilot_calibration.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add evals/causal_ablation/pilot_calibration.py tests/evals/causal_ablation/test_pilot_calibration.py
git commit -m "feat(evals): 注入强度 pilot 校准判据（v2 消融框架 Task 12）"
```

---

## 阶段收口（所有 T 任务完成后）

- [ ] **C1 全量验证**

Run: `uv run pytest -q -m "not live" && uv run ruff check && uv run mypy`
Expected: 0 failures（与基线一致）

- [ ] **C2 覆盖核对**：`uv run pytest tests/evals/causal_ablation -q --cov=evals.causal_ablation --cov-report=term-missing`，未覆盖分支须是显式跳过的错误路径并注释原因

- [ ] **C3 验证报告草稿**：`tests/validation/2026-09-16-causal-ablation-framework-validation.md`，记录：本批交付模块与测试数、阳性对照灵敏度结论、注入强度校准结论、遗留（GATED 任务状态）

---

## GATED：依赖 `update-ablation-driver-parity-and-report-status` 落库后再做

**前置检查（每个 GATED 任务开工前必须先跑）**

```bash
cd /d/WorkSpace/finance_analysis_agent
OK=1
grep -q '"diff_mean"' evals/ablation.py || OK=0
grep -q '_applicable_dims' tests/scripts/ablation_pilot.py || OK=0
grep -q 'snapshot_digest' tests/scripts/ablation_pilot.py || OK=0
git diff --quiet -- evals/ablation.py tests/scripts/ablation_pilot.py || OK=0
[ "$OK" = 1 ] && echo UNLOCKED || echo BLOCKED
```

判据含义：三个标记（`diff_mean` 重命名、驱动接回库侧 `_applicable_dims`、驱动侧 digest 核验）都已在**已提交**的 `evals/ablation.py` / `tests/scripts/ablation_pilot.py` 中，且这两个文件**无未提交改动**。

> 不要用 `git log --oneline -1 -- evals/ablation.py | grep 消融测量收口` 之类只看提交信息的判据——a86986a 的提交信息含「消融测量收口」但**不含** diff_mean 重命名，会造成假 UNLOCKED（2026-09-16 夜晚实测踩过）。

`BLOCKED` 时：跳过全部 GATED 任务，在 C3 报告中标注「等待 parity delta 合并」，不要在主工作区动手。

- [ ] **G1（tasks 1.2）驱动薄壳化**：`tests/scripts/ablation_pilot.py` 的 judge 判分与 `judge_vars` 落盘移入库侧；驱动只留续跑/记账/coverage
- [ ] **G2（tasks 1.3）citation 腿四桶拆报**接入 `aggregate_results` 路径，`citation_pass` 标量退出层增量比较
- [ ] **G3（tasks 1.4）结论注册表落地**：`docs/evals/` 全库 status 头核对 + 索引渲染（复用 Task 9 的解析器）
- [ ] **G4（tasks 3.3）`docs/evals/metrics.md` §1 口径先行变更** + 时间线一行
- [ ] **G5（tasks 1.5）3 标的通路验证**（`TESTING=1` stub，复用 resume.json 断点续跑）
- [ ] **G6（tasks 3.5）人工验证报告**：薄壳化通路 + 注入确定性复现 + 阳性对照结果，落 `tests/validation/`
