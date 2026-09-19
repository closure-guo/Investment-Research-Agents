"""阳性对照：已知劣化变体（关 verify_citations）必须测得出差异，否则管线灵敏度未证实。"""

from evals.causal_ablation.escape import EscapePair, escape_rate, mcnemar_table


def _pairs(on_caught: int, on_escaped: int, total: int) -> list[EscapePair]:
    pairs = [EscapePair(f"c{i}", "caught", "escaped") for i in range(on_caught)]
    pairs += [EscapePair(f"e{i}", "escaped", "escaped") for i in range(on_escaped)]
    pairs += [
        EscapePair(f"x{i}", "caught", "caught") for i in range(total - on_caught - on_escaped)
    ]
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
