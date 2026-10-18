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
]

# 占位符白名单：文档/示例中明确标注的伪密钥，不是真实凭证
PLACEHOLDERS = {
    "tvly-xxxxxxxxxxxx",
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
                # 模糊化输出，避免把密钥打印到 CI 日志
                masked = hit[:6] + "***" + hit[-4:]
                hits.append(f"  {rel_path}:{i} 疑似密钥: {masked}")
    return hits


def main() -> int:
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
