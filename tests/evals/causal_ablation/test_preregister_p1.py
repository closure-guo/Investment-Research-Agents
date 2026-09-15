from pathlib import Path

from evals.causal_ablation.preregister import assert_preregistered


def test_p1_preregister_is_valid():
    doc = assert_preregistered(Path("evals/ablation/preregister"))
    assert doc.valid is True
    assert "逃逸率" in doc.fields["主指标"]
