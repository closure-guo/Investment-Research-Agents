#!/usr/bin/env python3
"""incident 049 补丁：修 `factor_runner.py` 的并行 IC 计算内存爆炸。

## 病

```python
.concat_feature.groupby("datetime").parallel_apply(
    lambda x: self.calculate_information_coefficient(x, SOTA_feature.shape[1], new_feature.shape[1])
)
```

lambda 里**按名字引用** `SOTA_feature` / `new_feature` 两个 DataFrame → 它们成为闭包自由变量；
pandarallel 用 `dill.dumps(user_defined_function)`（core.py:265）把整个闭包**按值**序列化进
**每一个** worker。于是 worker 数 × 完整特征矩阵拷贝：实测每 worker 匿名内存 ~5.4GB，
8+ 个 worker 直接把 WSL VM 的 18GB 撑爆 → 内核 OOM 杀掉部分 worker →
master 等一个永不到来的结果、其余 worker 等 futex → **pandarallel 无超时，会话永久死锁**
（2026-10-22 两次实例：05:08 先撞 /dev/shm tmpfs 上限崩会话，07:40 / 12:19 撞 VM 内存上限死锁）。

同一 lambda 还捕获了绑定方法 `self.calculate_information_coefficient` → 连带 pickle 整个
runner 实例；该方法是纯函数，改为模块级函数即可去掉这层捕获。

## 修

1. 形状先算成 int，lambda 只闭包两个整数；
2. IC 计算提为模块级 `_ic_of_columns`（方法与它共用同一实现，保持对外 API 不变）；
3. 加一行 IC dedup 尺寸日志（形状 / GB / 列数），供后续同类问题取证；
4. `pandarallel.initialize` 显式给 `nb_workers`，可用 `RDAGENT_PANDARALLEL_WORKERS` 调
   （默认 8；原先按物理核数自动取，VM 里核多而内存固定，核数不是合适的默认）。

## 用法（在 distro 内，root）

```bash
python3 /root/patch-ic-closure.py [目标文件路径]
python3 -m py_compile <目标文件路径>
```

幂等：已打过（文件内含 `[incident-049]` 标记）则跳过；首次执行前备份为
`<目标文件>.bak-ic-closure-patch`。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

DEFAULT_TARGET = (
    "/opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/qlib/developer/factor_runner.py"
)
MARKER = "[incident-049]"

OLD_INIT = "pandarallel.initialize(verbose=1)"
NEW_INIT = 'pandarallel.initialize(verbose=1, nb_workers=int(os.environ.get("RDAGENT_PANDARALLEL_WORKERS", "8")))'

OLD_LAMBDA = (
    "                lambda x: self.calculate_information_coefficient("
    "x, SOTA_feature.shape[1], new_feature.shape[1])\n"
)

OLD_BLOCK = (
    "        concat_feature = pd.concat([SOTA_feature, new_feature], axis=1)\n        IC_max = (\n"
)

NEW_BLOCK = (
    "        concat_feature = pd.concat([SOTA_feature, new_feature], axis=1)\n"
    "        # " + MARKER + " 形状先算成 int：lambda 内直接引用 SOTA_feature/new_feature 会把两个\n"
    "        # DataFrame 收进闭包，dill 随每个 worker 各复制一份（实测每 worker ~5.4GB 匿名内存）。\n"
    "        _sota_cols, _new_cols = SOTA_feature.shape[1], new_feature.shape[1]\n"
    "        logger.info(\n"
    '            f"' + MARKER + ' IC dedup: concat_feature={concat_feature.shape} "\n'
    '            f"{concat_feature.memory_usage(deep=False).sum() / 1e9:.2f}GB, "\n'
    '            f"sota_cols={_sota_cols}, new_cols={_new_cols}"\n'
    "        )\n"
    "        IC_max = (\n"
)

NEW_LAMBDA = "                lambda x: _ic_of_columns(x, _sota_cols, _new_cols)\n"

HELPER = (
    "def _ic_of_columns(concat_feature: pd.DataFrame, sota_size: int, new_size: int) -> pd.Series:\n"
    '    """' + MARKER + ' 模块级实现：不捕获 runner 实例，worker 侧只收 chunk 与两个整数。"""\n'
    "    res = pd.Series(index=range(sota_size * new_size))\n"
    "    for col1 in range(sota_size):\n"
    "        for col2 in range(sota_size, sota_size + new_size):\n"
    "            res.loc[col1 * new_size + col2 - sota_size] = concat_feature.iloc[:, col1].corr(\n"
    "                concat_feature.iloc[:, col2]\n"
    "            )\n"
    "    return res\n"
    "\n"
    "\n"
)

ANCHOR_CLASS = "class QlibFactorRunner(CachedRunner[QlibFactorExperiment]):"


def patch(path: Path) -> int:
    if not path.is_file():
        print(f"ERROR: 目标不存在: {path}")
        return 2

    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print(f"SKIP: 已打过补丁（含 {MARKER} 标记）: {path}")
        return 0

    for name, needle in (
        ("OLD_INIT", OLD_INIT),
        ("OLD_BLOCK", OLD_BLOCK),
        ("OLD_LAMBDA", OLD_LAMBDA),
        ("ANCHOR_CLASS", ANCHOR_CLASS),
    ):
        if text.count(needle) != 1:
            print(f"ERROR: 锚点 {name} 命中 {text.count(needle)} 次（期望 1）——版本不符，拒绝改")
            return 3

    backup = path.with_name(path.name + ".bak-ic-closure-patch")
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"BACKUP: {backup}")

    # 1) import os
    if "\nimport os\n" not in text:
        text = text.replace(
            "from pathlib import Path\n", "import os\nfrom pathlib import Path\n", 1
        )

    # 2) initialize 显式 nb_workers
    text = text.replace(OLD_INIT, NEW_INIT, 1)

    # 3) 模块级 IC 实现（放在 class 之前）
    text = text.replace(ANCHOR_CLASS, HELPER + ANCHOR_CLASS, 1)

    # 4) 形状外提 + 尺寸日志
    text = text.replace(OLD_BLOCK, NEW_BLOCK, 1)

    # 5) lambda 只闭包两个整数
    text = text.replace(OLD_LAMBDA, NEW_LAMBDA, 1)

    path.write_text(text, encoding="utf-8")
    print(f"PATCHED: {path}")
    return 0


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(DEFAULT_TARGET)
    rc = patch(target)
    if rc == 0:
        print("---- 结果自检 ----")
        body = target.read_text(encoding="utf-8")
        for probe in (MARKER, NEW_INIT, NEW_LAMBDA, "def _ic_of_columns"):
            print(f"  {'OK ' if probe in body else 'MISS'} {probe[:60]}")
        print(
            f"  {'OK ' if 'SOTA_feature.shape[1], new_feature.shape[1])\n' not in body else 'MISS'} "
            "旧 lambda（捕获 DataFrame）已消失"
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
