"""Δ3 Task 2: cohort universe 登记文件 + 分层抽样(全程 stub client,不联网)。

Delta add-forward-paper-trading-cohort;Requirement「Cohort 标的池登记与版本化」。

分层口径（Δ3T2 审查 I1 修订）：**市值配额优先 + 桶内行业去重**——
沪深300 的 cninfo 行业中类近乎逐股唯一，若按 (industry, bucket) 分层会退化为
「行业优先」、市值档零约束；故改为先在 `market_cap_bucket` 间按池内占比分配名额，
再于桶内「行业去重优先 + rng 抖动」抽满名额。`industry` 保留作审计维度。
"""

import json

import pytest

from finance_agent.outcome.cohort.universe import (
    POOL_KEY,
    Constituent,
    Universe,
    build_universe,
    load_universe,
    pool_hash,
    pool_ref,
    write_universe,
)

# ── stub client：池由 (ticker, industry, market_cap) 三元组给出；cap=None=市值缺失 ──


class StubClient:
    """记录调用次数的 stub；每 ticker 只应被询行业/询价一次(缓存断言)。"""

    def __init__(self, pool: list[tuple[str, str | None, float | None]]):
        self.pool = list(pool)
        self.industry_calls: list[str] = []
        self.quote_calls: list[str] = []
        self.sources_seen: dict[str, set[str]] = {
            "constituents": {"stub"},
            "industry": {"stub"},
            "market_cap": {"stub"},
        }
        self._industry = {t: i for t, i, _ in self.pool}
        self._cap = {t: c for t, _, c in self.pool}

    def fetch_index_constituents(self, index_code: str = "000300") -> list[dict[str, str]]:
        return [{"ticker": t, "name": f"名称{t}"} for t, _, _ in self.pool]

    def fetch_industry(self, stock_code: str) -> dict:
        self.industry_calls.append(stock_code)
        industry = self._industry[stock_code]
        return {} if industry is None else {"industry": industry}

    def fetch_stock_quote(self, stock_code: str) -> dict:
        self.quote_calls.append(stock_code)
        return {"market_cap": self._cap[stock_code]}


# 9 只 × 三档各 3 只（市值 1/10/100 量级 → 分位切点 ~7.67 / ~41.3）；
# n=4 时三档占比均等 → 名额 {large:2, mid:1, small:1}（最大余数法，平局按 BUCKETS 顺序）
_THREE_EACH = [
    ("000001", "行业S1", 1.0),
    ("000002", "行业S2", 2.0),
    ("000003", "行业S3", 3.0),
    ("000011", "行业M1", 10.0),
    ("000012", "行业M2", 11.0),
    ("000013", "行业M3", 12.0),
    ("000021", "行业L1", 100.0),
    ("000022", "行业L2", 101.0),
    ("000023", "行业L3", 102.0),
]

# 桶内同行业（供「去重耗尽后回填」用例）：大市值档三只同属一个行业
_SAME_INDUSTRY_LARGE = [
    ("000001", "行业S1", 1.0),
    ("000011", "行业M1", 10.0),
    ("000021", "单一行业", 100.0),
    ("000022", "单一行业", 101.0),
    ("000023", "单一行业", 102.0),
]

# 基线池（M1 精确集合锁定）：三档各 4 只，档内含同行业对，用于观测行业去重
_BASELINE_POOL = [
    ("000001", "银行", 10.0),
    ("000002", "白酒", 11.0),
    ("000003", "银行", 12.0),
    ("000004", "医药", 13.0),
    ("600001", "汽车", 20.0),
    ("600002", "汽车", 21.0),
    ("600003", "地产", 22.0),
    ("600004", "券商", 23.0),
    ("300001", "电子", 30.0),
    ("300002", "电子", 31.0),
    ("300003", "煤炭", 32.0),
    ("300004", "钢铁", 33.0),
]


def _valid_payload() -> dict:
    return {
        "version": "v1",
        "effective_date": "2026-09-23",
        "seed": 42,
        "strata": {"by": "market_cap(quota)+industry(dedup)", "n": 2, "quota": {"large": 2}},
        "constituents": [
            {
                "ticker": "600519",
                "name": "贵州茅台",
                "industry": "白酒",
                "market_cap_bucket": "large",
                "market_cap": 15641.52,
            },
            {
                "ticker": "000001",
                "name": "平安银行",
                "industry": "银行",
                "market_cap_bucket": "mid",
            },
        ],
    }


class TestLoadUniverse:
    def test_parses_valid_file(self, tmp_path):
        path = tmp_path / "universe-v1.json"
        path.write_text(json.dumps(_valid_payload(), ensure_ascii=False), encoding="utf-8")

        u = load_universe(path)

        assert isinstance(u, Universe)
        assert u.version == "v1" and u.effective_date == "2026-09-23" and u.seed == 42
        assert u.strata["by"] == "market_cap(quota)+industry(dedup)"
        assert u.strata["quota"] == {"large": 2}
        assert len(u.constituents) == 2
        assert u.constituents[0] == Constituent(
            ticker="600519",
            name="贵州茅台",
            industry="白酒",
            market_cap_bucket="large",
            market_cap=15641.52,
        )
        # market_cap 为可选审计字段（缺省 None）
        assert u.constituents[1].market_cap is None

    def test_exposes_pool_audit_meta(self, tmp_path):
        """I2：池来源/切点/规模落入登记文件并可由 load_universe 读出。"""
        payload = _valid_payload()
        payload.update(
            {
                "pool_size": 298,
                "cut_points": {"low": 266.67, "high": 666.67},
                "excluded": {"missing_market_cap": 2},
                "unknown_industry": 11,
                "sources": {"constituents": ["csindex"], "industry": ["cninfo"]},
            }
        )
        path = tmp_path / "universe-v1.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        u = load_universe(path)

        assert u.meta["pool_size"] == 298
        assert u.meta["cut_points"] == {"low": 266.67, "high": 666.67}
        assert u.meta["excluded"] == {"missing_market_cap": 2}
        assert u.meta["unknown_industry"] == 11
        assert u.meta["sources"]["industry"] == ["cninfo"]

    def test_meta_absent_is_tolerated(self, tmp_path):
        """审计字段为可选（向后兼容既有登记文件）。"""
        path = tmp_path / "universe-v1.json"
        path.write_text(json.dumps(_valid_payload(), ensure_ascii=False), encoding="utf-8")
        assert load_universe(path).meta == {}

    @pytest.mark.parametrize(
        "missing", ["version", "effective_date", "seed", "strata", "constituents"]
    )
    def test_missing_required_key_raises_with_field_name(self, tmp_path, missing):
        payload = _valid_payload()
        payload.pop(missing)
        path = tmp_path / "universe-v1.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(ValueError, match=missing):
            load_universe(path)

    def test_missing_constituent_field_raises_with_field_name(self, tmp_path):
        payload = _valid_payload()
        payload["constituents"][0].pop("market_cap_bucket")
        path = tmp_path / "universe-v1.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(ValueError, match="market_cap_bucket"):
            load_universe(path)


class TestBuildUniverse:
    def test_returns_registry_shape(self):
        data = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))

        assert data["version"] == "v1"
        assert data["seed"] == 42
        assert data["strata"]["by"] == "market_cap(quota)+industry(dedup)"
        assert data["strata"]["n"] == 4
        assert data["effective_date"]  # 非空 ISO 日期
        assert len(data["constituents"]) == 4

    def test_same_seed_reproduces_same_constituents(self):
        first = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))
        second = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))

        assert first["constituents"] == second["constituents"]

    def test_constituents_are_subset_without_duplicates(self):
        stub = StubClient(_THREE_EACH)
        data = build_universe("v1", n=4, seed=42, client=stub)

        tickers = [c["ticker"] for c in data["constituents"]]
        assert len(set(tickers)) == 4  # 无放回
        assert set(tickers) <= {t for t, _, _ in stub.pool}

    def test_strata_dimensions_populated(self):
        data = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))

        for c in data["constituents"]:
            assert c["industry"]  # 行业非空
            assert c["market_cap_bucket"] in {"large", "mid", "small"}
            assert c["ticker"] and c["name"]
            assert isinstance(c["market_cap"], (int, float))

    # ── I1：市值配额优先 + 桶内行业去重 ──

    def test_quota_follows_bucket_proportions_with_tiebreak(self):
        data = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))

        assert data["strata"]["quota"] == {"large": 2, "mid": 1, "small": 1}
        got: dict[str, int] = {"large": 0, "mid": 0, "small": 0}
        for c in data["constituents"]:
            got[c["market_cap_bucket"]] += 1
        assert got == data["strata"]["quota"]

    def test_quota_ratio_scales_with_pool(self):
        """占比不等时按占比分配名额（此处小档占 8/10 → 名额倾斜）。"""
        pool = [(f"00000{i}", f"行业S{i}", 1.0) for i in range(1, 9)]  # 同值 → 全落小档
        pool += [("000101", "行业A", 100.0), ("000102", "行业B", 200.0)]  # 大档
        data = build_universe("v1", n=4, seed=42, client=StubClient(pool))

        assert data["strata"]["quota"] == {"large": 1, "mid": 0, "small": 3}
        got: dict[str, int] = {"large": 0, "mid": 0, "small": 0}
        for c in data["constituents"]:
            got[c["market_cap_bucket"]] += 1
        assert got == {"large": 1, "mid": 0, "small": 3}

    def test_within_bucket_prefers_distinct_industries(self):
        data = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))

        large = [c for c in data["constituents"] if c["market_cap_bucket"] == "large"]
        assert len(large) == 2
        assert len({c["industry"] for c in large}) == 2  # 桶内行业去重优先

    def test_within_bucket_fills_from_repeat_industries_when_dedup_exhausted(self):
        """桶内只有一个行业时仍须抽满名额（去重耗尽 → 回填，不得少抽）。"""
        data = build_universe("v1", n=4, seed=42, client=StubClient(_SAME_INDUSTRY_LARGE))

        large = [c for c in data["constituents"] if c["market_cap_bucket"] == "large"]
        assert len(large) == 2  # quota 未因去重而缩水
        assert {c["industry"] for c in large} == {"单一行业"}

    # ── I2/I3：池审计字段与缺值剔除 ──

    def test_registry_carries_pool_audit_fields(self):
        data = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))

        assert data["pool_size"] == 9
        assert data["cut_points"]["low"] < data["cut_points"]["high"]
        assert data["excluded"] == {"missing_market_cap": 0}
        assert data["unknown_industry"] == 0
        assert set(data["sources"]) == {"constituents", "industry", "market_cap"}
        assert data["sources"]["constituents"] == ["stub"]

    def test_bucket_is_consistent_with_recorded_cut_points(self):
        """审计恒等式：每只成分的档位可由记录的 market_cap + cut_points 复算。"""
        data = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))
        low, high = data["cut_points"]["low"], data["cut_points"]["high"]

        for c in data["constituents"]:
            cap = c["market_cap"]
            expected = "small" if cap <= low else ("mid" if cap <= high else "large")
            assert c["market_cap_bucket"] == expected

    def test_missing_market_cap_is_excluded_not_bucketed_small(self):
        """I3：缺市值 → 剔除出池（绝不置 0 / 判 small），并计数披露。"""
        pool = [*_THREE_EACH[:8], ("000999", "行业X", None)]
        data = build_universe("v1", n=8, seed=42, client=StubClient(pool))

        assert data["pool_size"] == 8  # 缺值者不入池
        assert data["excluded"] == {"missing_market_cap": 1}
        assert "000999" not in {c["ticker"] for c in data["constituents"]}

    def test_unknown_industry_is_kept_and_disclosed(self):
        pool = [*_THREE_EACH[:8], ("000999", None, 50.0)]
        data = build_universe("v1", n=4, seed=42, client=StubClient(pool))

        assert data["unknown_industry"] == 1
        assert data["pool_size"] == 9  # 行业未知不入排除清单，仅披露

    def test_enrichment_is_cached_per_ticker(self):
        stub = StubClient(_THREE_EACH)
        build_universe("v1", n=4, seed=42, client=stub)

        assert len(stub.industry_calls) == 9  # 每 ticker 只取一次
        assert len(stub.quote_calls) == 9
        assert len(set(stub.industry_calls)) == 9

    def test_n_greater_than_pool_raises(self):
        with pytest.raises(ValueError, match="n"):
            build_universe("v1", n=10, seed=42, client=StubClient(_THREE_EACH))

    def test_n_greater_than_pool_after_exclusion_raises(self):
        """剔除缺值后池变小，须按剔除后的池判 n。"""
        pool = [("000001", "行业S1", 1.0), ("000002", "行业S2", 2.0), ("000003", "行业S3", None)]
        with pytest.raises(ValueError, match="n"):
            build_universe("v1", n=3, seed=42, client=StubClient(pool))

    # ── M1：输出顺序（ticker 升序） ──

    def test_output_sorted_by_ticker_on_scrambled_pool(self):
        scrambled = list(reversed(_THREE_EACH))
        data = build_universe("v1", n=4, seed=42, client=StubClient(scrambled))

        tickers = [c["ticker"] for c in data["constituents"]]
        assert tickers == sorted(tickers)

    def test_output_sorted_by_ticker_is_input_order_independent(self):
        forward = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))
        backward = build_universe(
            "v1", n=4, seed=42, client=StubClient(list(reversed(_THREE_EACH)))
        )

        assert forward["constituents"] == backward["constituents"]

    def test_exact_constituent_set_baseline(self):
        """M1 基线锁定：固定池 + 固定 seed 的精确成分集合（口径改动须显式更新本用例）。

        池内可见行业去重的效果：小档 {银行,银行,白酒,医药} 名额 2 → 取「银行+医药」
        （避开同行业第二只）；中档 {汽车,汽车,地产,券商} → 取「地产+券商」。
        """
        data = build_universe("v1", n=6, seed=42, client=StubClient(_BASELINE_POOL))

        assert [
            (c["ticker"], c["industry"], c["market_cap_bucket"]) for c in data["constituents"]
        ] == [
            ("000001", "银行", "small"),
            ("000004", "医药", "small"),
            ("300003", "煤炭", "large"),
            ("300004", "钢铁", "large"),
            ("600003", "地产", "mid"),
            ("600004", "券商", "mid"),
        ]
        assert data["strata"]["quota"] == {"large": 2, "mid": 2, "small": 2}

    def test_different_seed_yields_different_sample(self):
        """rng 确实参与选股（否则「同 seed 复现」是空断言）。"""
        default = build_universe("v1", n=6, seed=42, client=StubClient(_BASELINE_POOL))
        other = build_universe("v1", n=6, seed=7, client=StubClient(_BASELINE_POOL))

        assert [c["ticker"] for c in default["constituents"]] != [
            c["ticker"] for c in other["constituents"]
        ]


class TestWriteUniverse:
    def test_writes_json_roundtrip(self, tmp_path):
        path = tmp_path / "universe-v1.json"
        data = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))

        write_universe(data, path)

        assert load_universe(path).version == "v1"
        assert len(load_universe(path).constituents) == 4

    def test_refuses_overwrite_within_version_lifecycle(self, tmp_path):
        path = tmp_path / "universe-v1.json"
        data = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))
        write_universe(data, path)

        with pytest.raises(FileExistsError):
            write_universe(data, path)
        # 原文件未被改写
        assert load_universe(path).constituents == tuple(
            Constituent(**c) for c in data["constituents"]
        )

    def test_force_rejects_different_content_same_version(self, tmp_path):
        """force 不是「换池后门」：核心字段不同（seed 42→7 → 成分清单变）→ 拒绝。

        spec Req1 Scenario 2「版本生效期内标的池 SHALL NOT 变更」——force 仅限
        同内容重生成。
        """
        path = tmp_path / "universe-v1.json"
        write_universe(build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH)), path)
        other = build_universe("v1", n=4, seed=7, client=StubClient(_THREE_EACH))

        with pytest.raises(ValueError, match="新 version"):
            write_universe(other, path, force=True)
        assert load_universe(path).seed == 42  # 原文件未被改写

    def test_force_allows_same_content_regeneration(self, tmp_path):
        """同内容 + force → 允许覆盖（离线重建：同 seed 同池复现同一成分清单）。"""
        path = tmp_path / "universe-v1.json"
        first = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))
        write_universe(first, path)

        again = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))
        # 披露字段与生效日跨轮可变，但不在比对集内 → 仍视为「同内容」
        again["effective_date"] = "2026-12-31"
        again["sources"] = {k: ["snapshot"] for k in ("constituents", "industry", "market_cap")}

        write_universe(again, path, force=True)

        assert load_universe(path).effective_date == "2026-12-31"
        assert load_universe(path).constituents == tuple(
            Constituent(**c) for c in first["constituents"]
        )

    def test_rejects_filename_version_mismatch_even_with_force(self, tmp_path):
        """M2：文件名版本与内容 version 不一致 → 拒绝（force 亦然）。"""
        path = tmp_path / "universe-v1.json"
        v2 = build_universe("v2", n=4, seed=42, client=StubClient(_THREE_EACH))

        with pytest.raises(ValueError, match="v1"):
            write_universe(v2, path, force=True)
        assert not path.exists()

    def test_rejects_version_mismatch_with_existing_file(self, tmp_path):
        """M2：既有文件 version 与写入 version 不一致 → 拒绝（换池须另起版本号）。"""
        path = tmp_path / "registry.json"  # 非约定文件名 → 只比对既有内容
        path.write_text(json.dumps(_valid_payload(), ensure_ascii=False), encoding="utf-8")
        v2 = build_universe("v2", n=4, seed=42, client=StubClient(_THREE_EACH))

        with pytest.raises(ValueError, match="v1"):
            write_universe(v2, path, force=True)
        assert load_universe(path).version == "v1"  # 原文件未被改写


class TestPoolSnapshot:
    """池快照持久化 + 离线重建：闭合同 seed 复现（Δ3T2 收口）。"""

    def test_registry_records_pool_ref_and_hash(self):
        data = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))

        assert data["pool_ref"] == "universe-v1.pool.json"
        assert pool_ref("v2") == "universe-v2.pool.json"
        assert data["pool_hash"] == pool_hash(data[POOL_KEY])

    def test_snapshot_is_json_serializable_and_keeps_missing_cap_rows(self):
        """快照须含**全部**富化行（缺值行留在快照里），否则离线重建复现不了 excluded。"""
        pool_in = [*_THREE_EACH, ("000999", "行业X", None)]
        data = build_universe("v1", n=4, seed=42, client=StubClient(pool_in))

        rows = json.loads(json.dumps(data[POOL_KEY], ensure_ascii=False))
        assert len(rows) == 10
        assert [r["ticker"] for r in rows] == sorted(r["ticker"] for r in rows)
        assert {r["ticker"] for r in rows if r["market_cap"] is None} == {"000999"}

    def test_snapshot_rows_have_canonical_keys_only(self):
        """快照行形状 = {ticker,name,industry,market_cap}；抽样标注不得回写（审查指定）。"""
        data = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))

        for row in data[POOL_KEY]:
            assert set(row) == {"ticker", "name", "industry", "market_cap"}

    def test_rebuild_from_pool_snapshot_is_identical(self):
        """核心：池快照 + 同 seed 重建 → 与登记文件逐字一致（唯一差异为披露留痕）。

        重建不变式覆盖 constituents / cut_points / pool_size / excluded / strata；
        `sources` 与 `unknown_industry` 为**环境相关披露字段**——上游信源瞬时失败
        指纹跨轮不同、不可由快照反演，故允许二者不同（Δ3T2 审查 Important）。
        """
        original = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))

        rebuilt = build_universe("v1", n=4, seed=42, pool=original[POOL_KEY])

        assert rebuilt["constituents"] == original["constituents"]  # 逐字
        differing = {k for k in original if original[k] != rebuilt.get(k)}
        assert differing <= {"sources", "unknown_industry"}, differing
        assert rebuilt["sources"] == dict.fromkeys(
            ("constituents", "industry", "market_cap"), ["snapshot"]
        )

    def test_rebuild_tolerates_env_dependent_disclosure_drift(self):
        """披露字段跨轮不可复现：登记文件记录的 `unknown_industry` 指纹（cninfo 瞬时
        失败）与快照可反演值可能不同，但**不得**动摇核心重建不变式。

        构造：登记文件记录的披露值被置为快照无法反演的旧轮指纹（模拟落盘快照重建
        与既有登记文件跨轮比对），断言差异只落在环境相关披露字段上。
        """
        original = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))
        original["unknown_industry"] = 99  # 上一轮信源瞬时失败指纹（快照不可反演）

        rebuilt = build_universe("v1", n=4, seed=42, pool=original[POOL_KEY])

        for key in ("constituents", "cut_points", "pool_size", "excluded", "strata"):
            assert rebuilt[key] == original[key], f"核心不变式 {key} 被披露字段漂移牵连"
        differing = {k for k in original if original[k] != rebuilt.get(k)}
        assert differing <= {"sources", "unknown_industry"}, differing

    def test_rebuild_reproduces_excluded_and_pool_size(self):
        pool_in = [*_THREE_EACH, ("000999", "行业X", None)]
        original = build_universe("v1", n=4, seed=42, client=StubClient(pool_in))

        rebuilt = build_universe(
            "v1", n=4, seed=42, pool=json.loads(json.dumps(original[POOL_KEY]))
        )

        assert rebuilt["excluded"] == {"missing_market_cap": 1} == original["excluded"]
        assert rebuilt["pool_size"] == original["pool_size"]
        assert rebuilt["pool_hash"] == original["pool_hash"]

    def test_rebuild_after_sidecar_file_roundtrip_is_identical(self, tmp_path):
        """脚本路径等价：快照落盘 → 读回 → 重建，成分清单逐字一致。"""
        original = build_universe("v1", n=6, seed=42, client=StubClient(_BASELINE_POOL))
        sidecar = tmp_path / pool_ref("v1")
        sidecar.write_text(
            json.dumps(original[POOL_KEY], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        rebuilt = build_universe(
            "v1", n=6, seed=42, pool=json.loads(sidecar.read_text(encoding="utf-8"))
        )

        assert rebuilt["constituents"] == original["constituents"]

    def test_pool_param_skips_enrichment(self):
        """传入 pool 时零联网：client 的富化方法不得被调用。"""
        stub = StubClient(_THREE_EACH)
        pool = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))[POOL_KEY]

        data = build_universe("v1", n=4, seed=42, client=stub, pool=pool)

        assert stub.industry_calls == [] and stub.quote_calls == []
        assert len(data["constituents"]) == 4

    def test_pool_hash_is_row_order_insensitive(self):
        pool = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))[POOL_KEY]

        assert pool_hash(pool) == pool_hash(list(reversed(pool)))

    def test_pool_hash_changes_when_any_row_changes(self):
        pool = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))[POOL_KEY]
        baseline = pool_hash(pool)
        assert len(baseline) == 12 and baseline == baseline.lower()

        for field, value in (
            ("market_cap", pool[0]["market_cap"] + 1.0),
            ("industry", "改过的行业"),
            ("name", "改过的名字"),
            ("ticker", "999999"),
        ):
            edited = [dict(r) for r in pool]
            edited[0][field] = value
            assert pool_hash(edited) != baseline, f"{field} 变化未反映在 pool_hash"

        assert pool_hash(pool[:-1]) != baseline  # 少一行亦变

    def test_rebuilt_registry_writes_without_sidecar_rows(self, tmp_path):
        """重建结果写盘：登记文件不含整池（瞬态键被剔除），pool_ref/pool_hash 保留。"""
        original = build_universe("v1", n=4, seed=42, client=StubClient(_THREE_EACH))
        path = tmp_path / "universe-v1.json"

        write_universe(original, path)

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert POOL_KEY not in raw
        assert raw["pool_ref"] == "universe-v1.pool.json"
        assert raw["pool_hash"] == original["pool_hash"]
        assert load_universe(path).meta["pool_hash"] == original["pool_hash"]
