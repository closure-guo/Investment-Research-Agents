"""TDD tests for data/cache.py — SQLite 缓存 + TTL。

TTL 策略（ADR-0004）：
- 三大报表：持久化，TTL = 下个财报季（4/30 或 8/31 或 10/31）
- 行情数据：TTL = 当日收盘（当日 15:00 后过期）
- 行业归属：TTL = 30 天
- 预计算指标：TTL 同报表

接口：get(key) → data | None, set(key, data, ttl_seconds), delete(key)
"""

import time

import pandas as pd
import pytest

from finance_agent.data.cache import DataCache


@pytest.fixture
def cache(tmp_path):
    """每个测试用独立的 SQLite 文件。"""
    db_path = tmp_path / "test_cache.db"
    return DataCache(str(db_path))


class TestBasicOperations:
    def test_set_and_get_dict(self, cache):
        cache.set("test_key", {"a": 1, "b": 2})
        result = cache.get("test_key")
        assert result == {"a": 1, "b": 2}

    def test_set_and_get_dataframe(self, cache):
        df = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
        cache.set("test_df", df)
        result = cache.get("test_df")
        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == ["x", "y"]
        assert len(result) == 2

    def test_get_missing_key(self, cache):
        assert cache.get("nonexistent") is None

    def test_delete(self, cache):
        cache.set("del_me", {"val": 42})
        assert cache.get("del_me") is not None
        cache.delete("del_me")
        assert cache.get("del_me") is None

    def test_overwrite(self, cache):
        cache.set("key", {"v": 1})
        cache.set("key", {"v": 2})
        assert cache.get("key") == {"v": 2}


class TestTTLExpiration:
    def test_not_expired(self, cache):
        cache.set("fresh", {"val": 1}, ttl_seconds=3600)
        assert cache.get("fresh") == {"val": 1}

    def test_expired(self, cache):
        cache.set("stale", {"val": 1}, ttl_seconds=1)
        time.sleep(1.1)
        assert cache.get("stale") is None

    def test_no_ttl_never_expires(self, cache):
        cache.set("permanent", {"val": 1}, ttl_seconds=None)
        time.sleep(0.1)
        assert cache.get("permanent") == {"val": 1}

    def test_ttl_refreshed_on_set(self, cache):
        cache.set("key", {"v": 1}, ttl_seconds=2)
        time.sleep(1.0)
        # re-set before expiry
        cache.set("key", {"v": 2}, ttl_seconds=2)
        time.sleep(1.5)
        # should still be there (new TTL)
        assert cache.get("key") == {"v": 2}


class TestKeyPatterns:
    def test_stock_code_in_key(self, cache):
        cache.set("600519:balance_sheet", {"data": True})
        assert cache.get("600519:balance_sheet") == {"data": True}

    def test_list_all_keys(self, cache):
        cache.set("a", 1)
        cache.set("b", 2)
        keys = cache.keys()
        assert set(keys) == {"a", "b"}


class TestTTLByDate:
    def test_set_with_expire_at(self, cache):
        """set 支持 expire_at 参数（绝对时间戳）。"""
        future = time.time() + 3600
        cache.set("dated", {"v": 1}, expire_at=future)
        assert cache.get("dated") == {"v": 1}

    def test_expire_at_past(self, cache):
        past = time.time() - 1
        cache.set("old", {"v": 1}, expire_at=past)
        assert cache.get("old") is None


class TestConcurrentWriteSafety:
    """fix(concurrent-cache)：DataCache 并发写安全（2026-09-08 缓存层审计）。

    真实缺陷：共享 Connection 无锁，两个分析请求并发 fetch_data 时并发
    c.set → InterfaceError: bad parameter or other API misuse + 写入静默
    丢失（实测 400 次并发写只成功 200）。管线表现为随机一方 fetch_data
    崩溃 → 整条分析失败。
    """

    def test_concurrent_set_no_error_no_loss(self, cache):
        """两线程各写 200 key：无异常、400 全部落库。"""
        import threading

        errors: list[str] = []

        def writer(tag: str):
            try:
                for i in range(200):
                    cache.set(f"{tag}:k{i}", {"v": i}, ttl_seconds=3600)
            except Exception as e:  # noqa: BLE001 - 收集线程错误
                errors.append(f"{tag}: {type(e).__name__}: {e}")

        t1 = threading.Thread(target=writer, args=("600519",))
        t2 = threading.Thread(target=writer, args=("688072",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"并发写异常: {errors[:3]}"
        keys = cache.keys()
        n_600519 = sum(1 for k in keys if k.startswith("600519:"))
        n_688072 = sum(1 for k in keys if k.startswith("688072:"))
        assert n_600519 == 200, f"600519 写入丢失: {n_600519}/200"
        assert n_688072 == 200, f"688072 写入丢失: {n_688072}/200"

    def test_concurrent_get_set_mixed(self, cache):
        """读写并发（一管线 check_cache 读、一管线 fetch_data 写）不崩。"""
        import threading

        cache.set("seed:0", {"v": 0}, ttl_seconds=3600)
        errors: list[str] = []

        def reader():
            try:
                for i in range(200):
                    cache.get(f"seed:{i % 5}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"reader: {type(e).__name__}")

        def writer():
            try:
                for i in range(200):
                    cache.set(f"w:{i}", {"v": i}, ttl_seconds=3600)
            except Exception as e:  # noqa: BLE001
                errors.append(f"writer: {type(e).__name__}")

        t1 = threading.Thread(target=reader)
        t2 = threading.Thread(target=writer)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert not errors, f"读写并发异常: {errors[:3]}"


class TestSharedCacheSingleton:
    """fix(cache-singleton)：nodes/cache 与 nodes/fetch 共用同一 DataCache 实例。

    此前两模块各自 DataCache() 单例（两个 Connection 指向同一 cache.db），
    加倍并发冲突面且语义分裂。统一后跨模块读写经同一实例（有锁保护）。
    """

    def test_nodes_modules_share_singleton(self, monkeypatch, tmp_path):
        """三入口同指一个实例。

        #248 教训：无参 _get_cache()/get_shared_cache() 会以相对路径
        "cache.db" 在 cwd（仓库/worktree 根）落盘进程单例，毒化管线 E2E
        后端缓存。本测试 monkeypatch 到 tmp_path 作用域单例，禁止相对落盘。
        """
        import finance_agent.data.cache as cache_pkg
        from finance_agent.nodes import cache as cache_mod
        from finance_agent.nodes import fetch as fetch_mod

        scoped = cache_pkg.DataCache(str(tmp_path / "shared_cache.db"))
        # nodes/cache 是模块级 import（本地绑定），data/cache 与 nodes/fetch 是
        # 调用点延迟 import——三处都要 patch 才能不触真实进程单例
        monkeypatch.setattr(cache_pkg, "get_shared_cache", lambda: scoped)
        monkeypatch.setattr(cache_mod, "get_shared_cache", lambda: scoped)

        c1 = cache_mod._get_cache()
        c2 = fetch_mod._get_cache()
        c3 = cache_pkg.get_shared_cache()
        assert c1 is c2, "nodes/cache 与 nodes/fetch 单例分裂"
        assert c1 is c3, "未统一到 get_shared_cache"

    def test_cache_db_path_env_branch(self, monkeypatch, tmp_path):
        """CACHE_DB_PATH 于调用期读 env；未设置时默认 "cache.db" 不变。

        T1 视觉基线套件依赖该 env 分支把设置中心「缓存管理/数据监控」
        隔离到专属测试库（cache.py 注释：导入期冻结会让 E2E 注入失效）。
        默认分支先 chdir(tmp_path)，避免相对路径在 worktree 根落盘
        cache.db（#248 教训）。
        """
        import finance_agent.data.cache as cache_pkg

        monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "env_cache.db"))
        monkeypatch.setattr(cache_pkg, "_SHARED", None)
        assert cache_pkg.get_shared_cache()._db_path == str(tmp_path / "env_cache.db")

        monkeypatch.delenv("CACHE_DB_PATH")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cache_pkg, "_SHARED", None)
        assert cache_pkg.get_shared_cache()._db_path == "cache.db"


def test_stats_reports_per_type_and_totals(cache: DataCache):
    cache.set("600519:stock_quote", {"price": 100}, ttl_seconds=3600)
    cache.set("600519:kline", {"x": 1}, ttl_seconds=3600)
    cache.set("000001:stock_quote", {"price": 50})  # 永久
    s = cache.stats()
    assert s["entries"] == 3
    assert s["permanent"] == 1
    cats = {p["category"]: p["entries"] for p in s["per_type"]}
    assert cats["stock_quote"] == 2
    assert cats["kline"] == 1


def test_stats_counts_expired(cache: DataCache):
    import time

    cache.set("600519:news", {"n": 1}, expire_at=time.time() - 10)
    s = cache.stats()
    assert s["expired"] == 1


def test_clear_all_and_delete_by_code(cache: DataCache):
    cache.set("600519:news", {"n": 1})
    cache.set("000001:news", {"n": 2})
    assert cache.delete_by_code("600519") == 1
    assert cache.keys() == ["000001:news"]
    assert cache.clear_all() == 1
    assert cache.keys() == []


def test_delete_by_type_uses_key_suffix(cache: DataCache):
    cache.set("600519:kline", {"k": 1})
    cache.set("000001:stock_quote", {"q": 1})
    cache.set("benchmark_kline", {"b": 1})
    assert cache.delete_by_type("kline") == 1  # 只清 600519:kline，不动 benchmark_kline
    assert sorted(cache.keys()) == ["000001:stock_quote", "benchmark_kline"]
