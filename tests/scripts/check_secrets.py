"""检测仓库中硬编码密钥（防泄露 CI 门禁）。

扫描 git 已跟踪文件（排除 .env、依赖目录、构建产物），用正则检测常见密钥模式：
  - sk- 开头的高熵 API Key（OpenAI 风格，≥20 字符）
  - oc_sk_（opencode zen/go；2026-10-18 轮换后的在用网关 key 格式）
  - pk-lf- / sk-lf-（Langfuse）
  - tvly-（Tavily；真实格式含连字符：tvly-<env>-<token>）
  - AKIA（AWS Access Key）
  - 已知泄露的真实密钥（黑名单，防止回滚复发）

用法: uv run python tests/scripts/check_secrets.py
退出码 0 = 通过；非 0 = 发现泄露（CI 将失败）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# 已知曾泄露的真实密钥（黑名单）。仅用于 CI 门禁，防止回滚复发。
# 注意：这些密钥本身不应出现在任何已跟踪文件中（除 .env，其被 gitignore 排除；
# 以及本脚本自身——黑名单定义就是这些密钥的文本）。
KNOWN_LEAKED_KEYS = [
    "sk-9Ve5ssMJuMIRhr7vUh88O8Ut6U7quO6H95DCayUc7TC7xo52TmX8YYLdpKgD3KWY",
    "sk-2e9c5078489c4a9abb8d275470a8b4b2",
]

# 常见密钥模式（高熵字符串）。字符数下限避免误报（如 stub-key-for-testing 不含长随机串）。
KEY_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    # oc_sk_<id>_<token>：opencode 网关 key；GitHub push protection 不认该提供商，
    # 本扫描器是唯一兜底（2026-10-18 轮换后启用；盲点即泄露通道）。
    re.compile(r"\boc_sk_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bpk-lf-[A-Za-z0-9]{10,}\b"),
    re.compile(r"\bsk-lf-[A-Za-z0-9]{10,}\b"),
    # 真实 Tavily key 形如 tvly-dev-xxxx-yyyy（含连字符）——旧式 [A-Za-z0-9] 会漏检。
    re.compile(r"\btvly-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # 智谱/bigmodel：<32hex>.<16+ alnum>（系统 env 持有该 key；judge 曾用 bigmodel）
    re.compile(r"\b[0-9a-f]{32}\.[A-Za-z0-9]{16,}\b"),
]

# 占位符白名单：文档/示例中明确标注的伪密钥，不是真实凭证
# （2026-10-18 历史扫描 CI 首跑实证：本文件注释里的 tvly-dev-xxxx-yyyy 被抓成「新泄露」
#  —— 当前树扫描自跳过本文件、历史扫描看 diff 不看排除表，占位符必须显式放行）
PLACEHOLDERS = {
    "tvly-xxxxxxxxxxxx",
    "tvly-dev-xxxx-yyyy",
}

# 排除路径：依赖、构建产物、虚拟环境、git 内部
EXCLUDE_SUBSTR = (
    "node_modules",
    ".venv",
    "dist",
    "build",
    ".git/",
    "sessions.db",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "test-assets/output",
    ".superpowers",
    # 黑名单定义自身含密钥文本（KNOWN_LEAKED_KEYS），跳过对自身的扫描
    "tests/scripts/check_secrets.py",
)


def tracked_files() -> list[str]:
    """返回 git 已跟踪文件列表（相对仓库根）。git 不可用时回退全部文件。"""
    try:
        git = shutil.which("git")
        if not git:
            raise FileNotFoundError("git not found")
        out = subprocess.run(  # noqa: S603 - git 路径来自 shutil.which，非用户输入
            [git, "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return [f for f in out.stdout.splitlines() if f]
    except Exception:  # noqa: BLE001
        # git 不可用（如 CI 未初始化仓库）：退化为扫描全部非排除文件
        all_files = []
        for p in REPO_ROOT.rglob("*"):
            if p.is_file() and not any(ex in p.as_posix() for ex in EXCLUDE_SUBSTR):
                all_files.append(p.relative_to(REPO_ROOT).as_posix())
        return all_files


def _mask(hit: str) -> str:
    """模糊化输出，避免把密钥打印到 CI 日志。"""
    return hit[:6] + "***" + hit[-4:]


def _is_known_or_placeholder(hit: str) -> bool:
    """黑名单全值 / 占位符：历史扫描报告但不判失败（已吊销留档 / 明确伪值）。"""
    if hit in PLACEHOLDERS:
        return True
    return any(hit == key or hit in key or key.startswith(hit) for key in KNOWN_LEAKED_KEYS)


def scan_file(rel_path: str) -> list[str]:
    """扫描单个文件，返回命中的泄露描述列表。"""
    full = REPO_ROOT / rel_path
    if not full.is_file():
        return []
    # 二进制文件跳过
    try:
        text = full.read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, OSError):
        return []

    hits: list[str] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for key in KNOWN_LEAKED_KEYS:
            if key in line:
                hits.append(f"  {rel_path}:{i} 命中已知泄露密钥黑名单")
        for pat in KEY_PATTERNS:
            for m in pat.finditer(line):
                hit = m.group(0)
                if hit in PLACEHOLDERS:
                    continue
                hits.append(f"  {rel_path}:{i} 疑似密钥: {_mask(hit)}")
    return hits


def _git(args: list[str], *, timeout: int = 300) -> str:
    """在 REPO_ROOT 跑 git（历史扫描用；REPO_ROOT 可被测试 monkeypatch）。"""
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git 不可用，无法执行历史扫描")
    out = subprocess.run(  # noqa: S603 - git 路径来自 shutil.which，非用户输入
        [git, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {out.stderr.strip()[:200]}")
    return out.stdout


def scan_history() -> tuple[list[str], list[str]]:
    """扫描**全部 git 历史**（所有分支）的密钥痕迹（``--history`` 模式）。

    与当前树扫描互补：当前树答"现在有没有"，历史扫描答"曾经有没有进过仓"——
    本仓库两次真实泄露（提交后删除、当前树干净）恰属后者，只靠当前树扫描永远
    看不见。判定口径：黑名单全值（已吊销+留档）与占位符**报告但不判失败**
    （否则门禁被历史钉死）；其余命中 = 新/未知密钥形态 → 判失败。

    返回 ``(new_hits, known_hits)``；输出一律掩码。
    """
    new_hits: list[str] = []
    known_hits: list[str] = []

    def _record(sha: str, subject: str, hit: str) -> None:
        if (sha, hit) in seen_hits:
            return
        seen_hits.add((sha, hit))
        line = f"  {sha[:12]} {subject[:60]} → {_mask(hit)}"
        (known_hits if _is_known_or_placeholder(hit) else new_hits).append(line)

    seen: set[tuple[str, str]] = set()
    seen_hits: set[tuple[str, str]] = set()
    for pat in KEY_PATTERNS:
        log = _git(["log", "--all", f"-G{pat.pattern}", "--format=%H%x09%s"])
        for entry in log.splitlines():
            if not entry.strip():
                continue
            sha, _, subject = entry.partition("\t")
            if (sha, pat.pattern) in seen:
                continue
            seen.add((sha, pat.pattern))
            diff = _git(["show", sha, "--format=", f"-G{pat.pattern}"])
            for line in diff.splitlines():
                for m in pat.finditer(line):
                    _record(sha, subject, m.group(0))
    # 黑名单全值另走字面量（形态可能不匹配任何正则，防漏）
    for key in KNOWN_LEAKED_KEYS:
        log = _git(["log", "--all", "-S", key, "--format=%H%x09%s"])
        for entry in log.splitlines():
            if not entry.strip():
                continue
            sha, _, subject = entry.partition("\t")
            known_hits.append(f"  {sha[:12]} {subject[:60]} → {_mask(key)}（黑名单全值命中）")
    return new_hits, known_hits


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--history" in args:
        new_hits, known_hits = scan_history()
        if known_hits:
            print(f"[INFO] 历史中的已知泄露（已吊销，不判失败）{len(known_hits)} 条：")
            for h in known_hits:
                print(h)
        if new_hits:
            print("[FAIL] 历史扫描发现疑似密钥曾进仓（当前树干净不代表历史干净）：")
            for h in new_hits:
                print(h)
            print(
                "\n处置顺序（GitHub 官方）：① 先吊销/轮换该密钥；② 再按需清历史"
                "（git-filter-repo --sensitive-data-removal + 联系 GitHub Support 清缓存/fork）。"
            )
            return 1
        print("[PASS] 历史扫描未发现新泄露（已知吊销项见上）")
        return 0

    files = tracked_files()
    all_hits: list[str] = []
    for rel in files:
        if any(ex in rel for ex in EXCLUDE_SUBSTR):
            continue
        all_hits.extend(scan_file(rel))

    if all_hits:
        print("[FAIL] 检测到潜在密钥泄露：")
        for h in all_hits:
            print(h)
        print("\n请将密钥改为从环境变量读取（如 process.env.LLM_API_KEY），不要硬编码在源码中。")
        return 1

    print(f"[PASS] 未检测到硬编码密钥（扫描 {len(files)} 个已跟踪文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
