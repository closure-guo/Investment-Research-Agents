"""投递时延采集器（issue #328 复现/观测用，临时运行）。

对给定端口的 `/api/v1/ops/stream-backlog` 周期轮询，把每轮各会话的
`delivery_latency_ms` 快照追加到 JSONL，并跟踪跨轮次的最大值（registry
样本为滚动窗口，轮询取 max 以不漏峰）。

用法::

    uv run python tests/scripts/collect_stream_latency.py \
        --ports 8001,8002,8003 --interval 2 --duration 900 --out /tmp/latency.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path


def _snapshot(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(  # noqa: S310 - 固定本机回环地址
            f"http://127.0.0.1:{port}/api/v1/ops/stream-backlog", timeout=5
        ) as resp:
            if int(resp.status) != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 采集器尽力而为
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ports", default="8001,8002,8003")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--duration", type=float, default=900.0)
    parser.add_argument("--out", default="stream_latency.jsonl")
    args = parser.parse_args()

    ports = [int(p) for p in args.ports.split(",") if p.strip()]
    out = Path(args.out)
    deadline = time.monotonic() + args.duration
    peak: dict[str, float] = {}
    with out.open("w", encoding="utf-8") as fh:
        while time.monotonic() < deadline:
            ts = time.time()
            for port in ports:
                payload = _snapshot(port)
                if not payload:
                    continue
                for sid, stats in payload.get("sessions", {}).items():
                    lat = stats.get("delivery_latency_ms") or {}
                    fh.write(
                        json.dumps(
                            {"ts": ts, "port": port, "session": sid, "stats": lat},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    fh.flush()
                    mx = lat.get("max")
                    if isinstance(mx, (int, float)):
                        key = f"{port}:{sid}"
                        peak[key] = max(peak.get(key, 0.0), float(mx))
            time.sleep(args.interval)
    print("peak delivery latency (ms):")
    for key, value in sorted(peak.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {key}: {value:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
