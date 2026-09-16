"""人工终裁材料：逐态证据回填 + 去重案例表 + 机器预读（spec「逃逸须终裁确认」）。

背景：P1 round-2 的终裁清单只带了 `polluted_values` 与状态字，四列逐态证据
（`on/off_polluted_present` / `on/off_flagged`）交付时全空——人工无从判读，只能回
`resume.json` 手工翻。本模块把证据整理成可直接判的表：

- `worklist_rows`：单元级清单（判定口径同 `pilot_report`：`status=ok` 且关态逃逸），
  附四列证据 + 拦截原因桶（区分真拦与伪拦截）；
- `case_groups`：同一标的同一型的确定性实例**完全相同**（注入载荷与污染 claim 一致），
  折叠成「案例」级——判 50 次而不是 180 次，组内套用；
- `machine_reading`：机器**预读**（证据整理，非终裁）——把「真逃逸候选 / 无效污染 /
  覆盖缺口 / 无拦截面 / 伪拦截」按确定性规则分开，人工只做确认与推翻。

口径纪律：`human_verdict` 列 SHALL 由人填写；重生成材料 SHALL 按 `unit_id` 继承已填
裁定（不得覆盖人工终裁）。
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# 机器预读判词（evidence 整理，非终裁）
READING_COUNTERFACTUAL = "真逃逸候选（反事实型：开态拦下、关态逃逸）"
READING_SPURIOUS = "伪拦截（FAIL 与污染无关：field_ref 本就解析不出）"
READING_COVERAGE_GAP = "覆盖缺口（校验器无此面）"
READING_NO_SURFACE = "无拦截面（state 侧污染，校验链无 claim 级目标）"
READING_SELF_CERTIFIED = "无效污染（注入自证出处：假事件与假引用同源注入）"
READING_INERT = "无效污染（注入未产生有效差异：污染值与真值一致或可归一）"
READING_ESCAPE = "真逃逸候选（开态未拦且污染成立）"
READING_UNREADABLE = "待人工判读（证据形态未登记）"

# 与污染无关的 FAIL 桶：field_ref 本就解析不出，关态同样会 FAIL（不得算机制功劳）
_UNATTRIBUTED_BUCKETS: frozenset[str] = frozenset({"path_unresolvable"})
# 值级 PASS 的等价判定容差（与 citation REL_TOL 同量级：0.5%）
_INERT_REL_TOL = 0.005

WORKLIST_COLUMNS: tuple[str, ...] = (
    "unit_id",
    "ticker",
    "pollution_type",
    "mechanism_id",
    "leg",
    "on_state",
    "off_state",
    "on_polluted_present",
    "on_flagged",
    "off_polluted_present",
    "off_flagged",
    "on_flag_bucket",
    "polluted_values",
    "evidence_paths",
    "human_verdict(真逃逸?/误报?/待查)",
)

CASES_COLUMNS: tuple[str, ...] = (
    "case_key",
    "ticker",
    "pollution_type",
    "mechanism_id",
    "leg",
    "instances",
    "unit_ids",
    "polluted_field",
    "polluted_values",
    "on_state",
    "off_state",
    "on_flagged",
    "on_verdict",
    "on_flag_bucket",
    "ground_truth",
    "delta",
    "delta_note",
    "machine_reading",
    "human_verdict(真逃逸?/误报?/待查)",
)


def _leg_state(unit: dict, leg: str, key: str, default: Any = None) -> Any:
    block = unit.get(leg)
    if not isinstance(block, dict):
        return default
    return block.get(key, default)


def _polluted_verdict(unit: dict) -> dict:
    """开态下**被污染 claim** 的判定（无 claim 级目标时为空 dict）。"""
    index = (unit.get("injection") or {}).get("mutated_flat_index")
    verdicts = _leg_state(unit, "on", "verdicts", []) or []
    if index is None or not isinstance(index, int) or index >= len(verdicts):
        return {}
    verdict = verdicts[index]
    return verdict if isinstance(verdict, dict) else {}


def _polluted_claim(unit: dict) -> dict:
    index = (unit.get("injection") or {}).get("mutated_flat_index")
    claims = _leg_state(unit, "on", "claims", []) or []
    if index is None or not isinstance(index, int) or index >= len(claims):
        return {}
    claim = claims[index]
    return claim if isinstance(claim, dict) else {}


def machine_reading(unit: dict) -> str:
    """机器预读：按确定性规则给该单元一个候选读法（终裁仍由人填 `human_verdict`）。"""
    index = (unit.get("injection") or {}).get("mutated_flat_index")
    if index is None:
        return READING_NO_SURFACE
    verdict = _polluted_verdict(unit)
    bucket = str(verdict.get("bucket") or "")
    if _leg_state(unit, "on", "flagged", False):
        if bucket in _UNATTRIBUTED_BUCKETS:
            return READING_SPURIOUS
        return READING_COUNTERFACTUAL
    status = str(verdict.get("status") or "")
    if status == "UNVERIFIABLE":
        return f"{READING_COVERAGE_GAP}：{bucket or '未标桶'}"
    if status == "PASS":
        if _self_certified(unit):
            return READING_SELF_CERTIFIED
        if _inert_pass(verdict):
            return READING_INERT
        return READING_ESCAPE
    return READING_UNREADABLE


def _self_certified(unit: dict) -> bool:
    """注入是否把「出处」一并写进 state（假新闻与假引用同源注入）——污染自证。

    判定依据：`mutated_fields` 里出现快照侧路径（如 `snapshot.news_list`）——
    此时污染 claim 的"出处"是注入自己造的，回声匹配必然 PASS，单元不携带机制信息。
    """
    fields = (unit.get("injection") or {}).get("mutated_fields") or []
    return any(str(f).startswith("snapshot.") for f in fields)


def delta_note(verdict: dict) -> str:
    """`delta` 列的读法标注。

    该列按「同一单位容差比对」设计，但校验器在 FAIL 时回落到**原始候选**（不做
    百分比/单位归一）——面值是百分数、真值是小数这类形态下，delta 是跨单位原始差
    （实测：002415 面值 18.52 vs 真值 0.1852 → delta 18.71，按同一单位应为 0.37）。
    故逐行标注读法：判词看桶，`delta` 不读作幅度。
    """
    delta = verdict.get("delta")
    if not isinstance(delta, int | float):
        return "无 delta（语义类检查不做数值比对）"
    if str(verdict.get("status") or "") == "FAIL":
        return "原始差（未归一）：只示比对不通过，不读作幅度"
    return "同单位差（比对通过）"


def _inert_pass(verdict: dict) -> bool:
    """值级 PASS 是否等价于「污染未产生有效差异」（归一命中或落在容差内）。"""
    if verdict.get("unit_normalized") is not None:
        return True
    delta = verdict.get("delta")
    ground_truth = verdict.get("ground_truth")
    if not isinstance(delta, int | float):
        return False
    if not isinstance(ground_truth, int | float) or isinstance(ground_truth, bool):
        return False
    if ground_truth == 0:
        return abs(float(delta)) == 0.0
    return abs(float(delta)) <= _INERT_REL_TOL * abs(float(ground_truth))


def _worklist_row(unit: dict) -> dict:
    return {
        "unit_id": unit.get("unit_id"),
        "ticker": unit.get("ticker"),
        "pollution_type": unit.get("pollution_type"),
        "mechanism_id": unit.get("mechanism_id"),
        "leg": unit.get("leg"),
        "on_state": unit.get("on_state"),
        "off_state": unit.get("off_state"),
        "on_polluted_present": _leg_state(unit, "on", "polluted_present", False),
        "on_flagged": _leg_state(unit, "on", "flagged", False),
        "off_polluted_present": _leg_state(unit, "off", "polluted_present", False),
        "off_flagged": _leg_state(unit, "off", "flagged", False),
        "on_flag_bucket": str(_polluted_verdict(unit).get("bucket") or ""),
        "polluted_values": list((unit.get("injection") or {}).get("polluted_values") or []),
        "evidence_paths": ";".join(str(p) for p in (unit.get("evidence_paths") or [])),
    }


VERDICT_COLUMN = "human_verdict(真逃逸?/误报?/待查)"
VERDICT_CONFIRMED = "真逃逸"


def adjudicated_case_ids(path: Path, *, confirmed: str = VERDICT_CONFIRMED) -> set[str]:
    """从终裁清单读回**已确认**的 case_id 集合（`escape_rate` 的 `adjudicated` 入参）。

    - 案例级行（有 `unit_ids`）：展开组内全部实例的 case_id（判一次、组内套用）；
    - 单元级行：直接取 `unit_id` 的 case_id 段；
    - 只有填了「真逃逸」（或缺省前缀对应的判词）的行才算已终裁——误报/待查/留空都不进
      （「0 逃逸」与「没判定过」不得混为一谈，spec「逃逸须终裁确认」）。
    """
    path = Path(path)
    if not path.exists():
        return set()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return set()
    out: set[str] = set()
    for row in csv.DictReader(text.splitlines()):
        verdict = str(row.get(VERDICT_COLUMN) or "").strip()
        if not verdict.startswith(confirmed):
            continue
        raw = row.get("unit_ids")
        if raw:
            try:
                units = json.loads(raw)
            except json.JSONDecodeError:
                units = []
            out.update(str(u).split("::")[0] for u in units if str(u))
            continue
        unit_id = str(row.get("unit_id") or row.get("case_id") or "")
        if unit_id:
            out.add(unit_id.split("::")[0])
    return out


def adjudicable_units(units: Sequence[dict]) -> list[dict]:
    """终裁口径的单元全集（与 `pilot_report` 一致：`status=ok`、关态逃逸、非输入侧面）。

    清单与案例表 SHALL 用同一口径取数——两处各写一遍过滤条件会让「判 50 案例」
    与「判 180 行」对不上（案例表若含未逃逸/void 单元，实例数会虚增）。
    输入侧面型（A7 时效标记）无「拦截/逃逸」语义 → 不进清单（判读走输入侧证据块）。
    """
    from evals.causal_ablation.pilot_runner import FLAG_SURFACE_KIND

    return [
        u
        for u in units
        if u.get("status") == "ok"
        and u.get("off_state") == "escaped"
        and FLAG_SURFACE_KIND.get(str(u.get("pollution_type")), "none") != "input_side"
    ]


def worklist_rows(units: Sequence[dict]) -> list[dict]:
    """单元级终裁清单（判定口径与 `pilot_report` 一致：ok 且关态逃逸）。"""
    return [_worklist_row(u) for u in adjudicable_units(units)]


def _case_signature(unit: dict) -> str:
    """案例去重签名：注入载荷 + 被污染 claim 的可辨字段（同签名 = 同一案例的重复实例）。"""
    injection = unit.get("injection") or {}
    claim = _polluted_claim(unit)
    claim_sig = {k: claim.get(k) for k in ("field_ref", "stated_value", "direction", "period")}
    return json.dumps(
        {
            "injected": injection.get("injected_values"),
            "mutated_fields": injection.get("mutated_fields"),
            "claim": claim_sig,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def case_groups(units: Sequence[dict]) -> list[dict]:
    """把确定性重复的实例折叠为案例级记录（组内实例完全相同，判一次、组内套用）。"""
    order: list[tuple[str, str, str, str]] = []
    buckets: dict[tuple[str, str, str, str], list[dict]] = {}
    for unit in units:
        key = (
            str(unit.get("ticker")),
            str(unit.get("pollution_type")),
            str(unit.get("leg")),
            _case_signature(unit),
        )
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(unit)
    rows: list[dict] = []
    for key in order:
        group = buckets[key]
        head = group[0]
        verdict = _polluted_verdict(head)
        unit_ids = [str(u.get("unit_id")) for u in group]
        rows.append(
            {
                "case_key": min(unit_ids).split("::")[0],
                "ticker": head.get("ticker"),
                "pollution_type": head.get("pollution_type"),
                "mechanism_id": head.get("mechanism_id"),
                "leg": head.get("leg"),
                "instances": len(group),
                "unit_ids": unit_ids,
                # 被污染的字段（终裁要知道「动了哪条 claim」，光看污染值不够）
                "polluted_field": str(_polluted_claim(head).get("field_ref") or ""),
                "polluted_values": list((head.get("injection") or {}).get("polluted_values") or []),
                "on_state": head.get("on_state"),
                "off_state": head.get("off_state"),
                "on_flagged": _leg_state(head, "on", "flagged", False),
                # 无 claim 级目标（state 侧污染）时判定为空——不写 "None/-" 冒充判定
                "on_verdict": (
                    f"{verdict.get('status')}/{verdict.get('bucket') or '-'}" if verdict else ""
                ),
                "on_flag_bucket": str(verdict.get("bucket") or ""),
                "ground_truth": verdict.get("ground_truth"),
                "delta": verdict.get("delta"),
                "delta_note": delta_note(verdict),
                "machine_reading": machine_reading(head),
            }
        )
    return rows


def _cell(value: Any) -> str:
    """CSV 单元格编码：容器走 JSON（可回读），None 记空串。"""
    if value is None:
        return ""
    if isinstance(value, list | dict | tuple):
        return json.dumps(list(value), ensure_ascii=False, default=str)
    return str(value)


def _existing_verdicts(path: Path, key: str, column: str) -> dict[str, str]:
    """读回已填的人工裁定（重生成材料不得覆盖人工终裁）。"""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for row in csv.DictReader(text.splitlines()):
        verdict = str(row.get(column) or "").strip()
        if verdict:
            out[str(row.get(key) or "")] = verdict
    return out


def write_worklist_csv(path: Path, rows: Sequence[dict]) -> None:
    """写单元级清单：四列证据回填；已填的人工裁定按 `unit_id` 继承。"""
    path = Path(path)
    verdict_column = WORKLIST_COLUMNS[-1]
    inherited = _existing_verdicts(path, "unit_id", verdict_column)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(WORKLIST_COLUMNS))
        writer.writeheader()
        for row in rows:
            out = {c: _cell(row.get(c)) for c in WORKLIST_COLUMNS}
            out[verdict_column] = inherited.get(str(row.get("unit_id")), "")
            writer.writerow(out)


def write_cases_csv(path: Path, rows: Sequence[dict]) -> None:
    """写案例级清单（50 案例版）：人工裁定按 `case_key` 继承。"""
    path = Path(path)
    verdict_column = CASES_COLUMNS[-1]
    inherited = _existing_verdicts(path, "case_key", verdict_column)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CASES_COLUMNS))
        writer.writeheader()
        for row in rows:
            out = {c: _cell(row.get(c)) for c in CASES_COLUMNS}
            out[verdict_column] = inherited.get(str(row.get("case_key")), "")
            writer.writerow(out)
