from pathlib import Path

from evals.causal_ablation.preregister import assert_preregistered


def test_p1_preregister_is_valid():
    """同目录叠加后续实验（P2 族 B / A4 补测）后，门禁仍须锁到本实验的文档。"""
    doc = assert_preregistered(
        Path("evals/ablation/preregister"), name_contains="p1-injection-pilot"
    )
    assert doc.valid is True
    assert "逃逸率" in doc.fields["主指标"]


def test_p1_a4_preregister_is_valid():
    doc = assert_preregistered(Path("evals/ablation/preregister"), name_contains="p1-a4-repair")
    assert doc.valid is True
    assert "真 FAIL 率差" in doc.fields["主指标"]
