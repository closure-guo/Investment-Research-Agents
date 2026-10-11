"""Dataset:schema 合规、覆盖矩阵、幂等 seed、rotating 轮换抽样。"""

import json
from unittest.mock import MagicMock, patch

from evals.dataset_seed import (
    DATASET_NAME,
    load_items,
    rotating_subset,
    seed,
)


class TestDatasetItems:
    def test_baseline_schema_and_coverage_matrix(self):
        items = load_items(pool="baseline")
        assert 15 <= len(items) <= 20, len(items)
        categories = [it["metadata"]["category"] for it in items]
        for cat, lo, hi in [
            ("deep_typical", 5, 7),
            ("deep_edge", 2, 4),
            ("quick", 4, 6),
            ("clarify", 1, 2),
        ]:
            assert lo <= categories.count(cat) <= hi, f"{cat}: {categories.count(cat)}"
        for it in items:
            assert it["input"]["query"] and it["input"]["mode"]
            assert it["metadata"]["category"] and it["metadata"]["source"]
            assert it["metadata"]["pool"] == "baseline"

    def test_baseline_scoring_share_and_skipped_cap(self):
        """出分条目 ≥80%；follow_up + clarify（首版跳过）合计 ≤3。"""
        items = load_items(pool="baseline")
        skipped_cats = {"follow_up", "clarify"}
        skipped = sum(1 for it in items if it["metadata"]["category"] in skipped_cats)
        assert skipped <= 3
        scoring = len(items) - skipped
        assert scoring / len(items) >= 0.80, f"出分占比 {scoring}/{len(items)}"

    def test_baseline_has_ambiguity_edge_sample(self):
        """deep 边界含无代码歧义样本（分析平安）。"""
        edge = [
            it for it in load_items(pool="baseline") if it["metadata"]["category"] == "deep_edge"
        ]
        ambiguous = [
            it
            for it in edge
            if "平安" in it["input"]["query"] and "601318" not in it["input"]["query"]
        ]
        assert ambiguous, "缺歧义边界样本"
        assert ambiguous[0]["expected_output"].get("ticker") == "601318"

    def test_rotating_pool_paired_by_ticker(self):
        """rotating 池每标的 deep+quick 成对，且不入 baseline 池。"""
        rot = load_items(pool="rotating")
        base = {it["input"]["query"] for it in load_items(pool="baseline")}
        assert rot, "rotating 池为空"
        codes = {}
        for it in rot:
            code = it["input"].get("stock_code") or it["input"].get("ticker")
            codes.setdefault(code, set()).add(it["input"]["mode"])
        assert all({"deep", "quick"} <= modes for modes in codes.values())
        assert not ({it["input"]["query"] for it in rot} & base), "rotating 与 baseline 冲突"

    def test_expected_has_no_time_sensitive_numbers(self):
        # spec「expected 不含时效数值」:不允许出现金额/百分比形态
        import re

        for it in load_items(pool="baseline") + load_items(pool="rotating"):
            text = json.dumps(it["expected_output"], ensure_ascii=False)
            assert not re.search(r"\d+(\.\d+)?\s*(亿|万|%)", text), text


class TestRotatingSubset:
    def test_seed_reproducible_and_paired_by_ticker(self):
        rot = load_items(pool="rotating")
        a = rotating_subset(rot, 3, seed_s=42)
        b = rotating_subset(rot, 3, seed_s=42)
        assert [it["input"]["query"] for it in a] == [it["input"]["query"] for it in b]
        codes = {it["input"].get("stock_code") or it["input"].get("ticker") for it in a}
        assert len(codes) == 3
        for code in codes:
            picked = [
                it
                for it in a
                if (it["input"].get("stock_code") or it["input"].get("ticker")) == code
            ]
            assert {it["input"]["mode"] for it in picked} == {"deep", "quick"}

    def test_cap_at_pool_size(self):
        rot = load_items(pool="rotating")
        n_tickers = len({it["input"].get("stock_code") or it["input"].get("ticker") for it in rot})
        out = rotating_subset(rot, 999, seed_s=1)
        assert (
            len({it["input"].get("stock_code") or it["input"].get("ticker") for it in out})
            == n_tickers
        )


class TestSeed:
    def _client(self, existing_keys=()):
        client = MagicMock()
        ds = MagicMock()
        ds.items = [MagicMock(input={"query": q, "mode": m}) for (q, m) in existing_keys]
        client.get_dataset.return_value = ds
        return client

    def test_creates_baseline_only_on_empty(self):
        client = self._client()
        result = seed(client=client)
        assert result["created"] == len(load_items(pool="baseline"))
        assert result["skipped"] == 0
        assert result["dataset"] == DATASET_NAME

    def test_idempotent_on_rerun(self):
        # spec「幂等建库」:全部已存在 → 0 created,不重复
        keys = [(it["input"]["query"], it["input"]["mode"]) for it in load_items(pool="baseline")]
        client = self._client(existing_keys=keys)
        result = seed(client=client)
        assert result["created"] == 0
        assert result["skipped"] == len(load_items(pool="baseline"))
        client.create_dataset_item.assert_not_called()

    def test_rotating_uses_independent_dataset_name(self):
        client = self._client()
        result = seed(client=client, pool="rotating", rotating_sample=2, rotating_seed=7)
        assert result["dataset"] == f"{DATASET_NAME}-rot-7"
        assert result["created"] == 4  # 2 标的 × deep+quick
        names = [c.kwargs["dataset_name"] for c in client.create_dataset_item.call_args_list]
        assert names and all(n == f"{DATASET_NAME}-rot-7" for n in names)

    def test_creates_dataset_when_missing(self):
        client = MagicMock()
        client.get_dataset.side_effect = Exception("not found")
        ds = MagicMock()
        ds.items = []
        client.create_dataset.return_value = ds
        result = seed(client=client)
        client.create_dataset.assert_called_once()
        assert client.create_dataset.call_args.kwargs["name"] == DATASET_NAME
        assert result["created"] == len(load_items(pool="baseline"))

    def test_no_client_returns_error(self):
        # 显式隔离 get_langfuse:CI/nightly 注入 LANGFUSE_* 凭据时会返回真实 client,
        # 导致 client=None 隐式取值联网 seed,测试漂移。强制返回 None 走 error 分支。
        with patch("finance_agent.langfuse_tracing.get_langfuse", return_value=None):
            result = seed(client=None)
        assert result["created"] == 0
        assert result["error"] is not None

    def test_transient_get_failure_refetches_after_create(self):
        """#56:get_dataset 瞬断 → create(按名 upsert,dataset 可能已存在且有
        items)→ 必须重取 get_dataset 补 existing,防整批重复插入。"""

        keys = [(it["input"]["query"], it["input"]["mode"]) for it in load_items(pool="baseline")]
        client = MagicMock()
        ds_full = MagicMock()
        ds_full.items = [MagicMock(input={"query": q, "mode": m}) for (q, m) in keys]
        # 首查瞬断;create 成功后重取返回「已全量存在」
        client.get_dataset.side_effect = [ConnectionError("blip"), ds_full]
        result = seed(client=client)
        client.create_dataset.assert_called_once()
        client.create_dataset_item.assert_not_called()
        assert result["created"] == 0
        assert result["skipped"] == len(keys)
