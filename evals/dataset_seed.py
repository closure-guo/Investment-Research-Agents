"""Dataset 建库(spec Requirement「评估 Dataset 与覆盖矩阵」;harden-evaluation-dataset-sampling)。

幂等:以 (input.query, input.mode) 为去重键,已存在 item 跳过不覆盖。
分池:items 的 metadata.pool 区分 baseline(固定可比,跨实验配对对比对象)与
rotating(轮换候选池,每轮按标的分组随机抽样,seed 可复现,防固定标的过拟合)。
rotating 建于独立 dataset 名 a-share-analysis-v1-rot-<seed>,不污染 baseline。
langfuse 未配置时返回 error,不抛异常(seed 仅建库;实验一律经 run_experiment 执行)。
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)

DATASET_NAME = "a-share-analysis-v1"
_POOLS = ("baseline", "rotating")
_ITEMS_PATH = Path(__file__).parent / "dataset_items.json"


def load_items(path: Path | None = None, pool: str | None = None) -> list[dict]:
    """读取 dataset items(seed 建库用的 source of truth)。

    pool 给定(baseline/rotating)时只返回该池条目;None 返回全部。
    """
    items: list[dict] = json.loads((path or _ITEMS_PATH).read_text(encoding="utf-8"))
    if pool is not None:
        items = [it for it in items if (it.get("metadata") or {}).get("pool") == pool]
    return items


def _ticker_key(item: dict) -> str:
    """标的分组键:deep 用 stock_code,quick 用 ticker,兜底 query。"""
    inp = item.get("input") or {}
    return str(inp.get("stock_code") or inp.get("ticker") or inp.get("query") or "?")


def rotating_subset(items: list[dict], sample: int, seed_s: int) -> list[dict]:
    """从 rotating 池按标的分组随机抽样 sample 个标的,每组条目(deep+quick)一并取出。

    seed 可复现(同 seed 同组合);sample ≥ 池内标的总数时返回全部。
    """
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(_ticker_key(it), []).append(it)
    # 非加密用途：要求「同 seed 同组合」的可复现抽样，恰需标准伪随机源
    chosen = random.Random(seed_s).sample(sorted(groups), min(sample, len(groups)))  # noqa: S311
    out: list[dict] = []
    for key in chosen:
        out.extend(groups[key])
    return out


def seed(
    client=None,
    pool: str = "baseline",
    rotating_sample: int = 0,
    rotating_seed: int | None = None,
) -> dict:
    """幂等建库。pool=rotating 时按 rotating_sample 抽样标的,建于独立 dataset。

    返回 {created, skipped, error, dataset}。
    """
    if pool not in _POOLS:
        return {"created": 0, "skipped": 0, "error": f"未知 pool: {pool}", "dataset": None}
    if client is None:
        from finance_agent.langfuse_tracing import get_langfuse

        client = get_langfuse()
    if client is None:
        return {"created": 0, "skipped": 0, "error": "langfuse 未配置,跳过 seed", "dataset": None}
    items = load_items(pool=pool)
    if pool == "rotating" and rotating_sample > 0:
        items = rotating_subset(items, rotating_sample, rotating_seed or 0)
    dataset_name = DATASET_NAME if pool == "baseline" else f"{DATASET_NAME}-rot-{rotating_seed}"
    existing: set = set()
    try:
        dataset = client.get_dataset(dataset_name)
        existing = {(it.input.get("query"), it.input.get("mode")) for it in dataset.items}
    except Exception as e:
        # 过宽兜底:网络抖动/认证过期等异常也会落到此分支。
        # 记 warning 以便排查被吞掉的异常,再尝试 create_dataset。
        logger.warning("get_dataset(%s) 失败,尝试 create_dataset: %s", dataset_name, e)
        client.create_dataset(
            name=dataset_name,
            description=(
                "A 股分析评估 Dataset v1(覆盖矩阵:deep 典型/边界、quick、意图澄清)"
                if pool == "baseline"
                else f"rotating 轮换池子集(seed={rotating_seed})"
            ),
            metadata={"version": "v1", "pool": pool},
        )
        # create_dataset 返回原始 Dataset API 类型(无 .items);勿迭代其返回值。
        # #56:create 是按名 upsert——首查失败可能是网络瞬断,dataset 实际已存在
        # 且已有 items,existing 保持空集继续会整批重复插入。create 成功后必须
        # 重取 get_dataset 补 existing;重取仍失败则按空集继续(显式 warning)。
        try:
            dataset = client.get_dataset(dataset_name)
            existing = {(it.input.get("query"), it.input.get("mode")) for it in dataset.items}
        except Exception as refetch_err:
            logger.warning(
                "create 后重取 %s 失败,按空集继续(存在重复插入风险): %s",
                dataset_name,
                refetch_err,
            )
    created = skipped = 0
    for item in items:
        key = (item["input"]["query"], item["input"]["mode"])
        if key in existing:
            skipped += 1
            continue
        client.create_dataset_item(
            dataset_name=dataset_name,
            input=item["input"],
            expected_output=item.get("expected_output"),
            metadata=item.get("metadata"),
        )
        created += 1
    client.flush()
    return {"created": created, "skipped": skipped, "error": None, "dataset": dataset_name}


if __name__ == "__main__":
    # 与 api.py 一致：CLI 入口加载 .env（否则 shell 无 LANGFUSE/LLM key，误判「未配置」）
    import argparse

    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(
        description="评估 Dataset 建库(baseline 固定池 / rotating 轮换池)"
    )
    parser.add_argument("--pool", choices=_POOLS, default="baseline")
    parser.add_argument("--rotating-sample", type=int, default=0, help="rotating 抽取标的数")
    parser.add_argument("--rotating-seed", type=int, default=None, help="抽样种子(同 seed 可复现)")
    args = parser.parse_args()
    print(
        seed(
            pool=args.pool,
            rotating_sample=args.rotating_sample,
            rotating_seed=args.rotating_seed,
        )
    )
