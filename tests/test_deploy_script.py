"""add-deploy-script：scripts/deploy.py 的纯函数单测 + tmp git 仓库实证。

纪律（对齐 tests/test_prompt_history.py 先例）：
- 网络（GET /api/sessions 门禁、两侧探针）与 docker 重启一律注入 fake，测试不真连；
- git 行为用真实 git 在 tmp 仓库实证（work + origin.git 裸远端，基线 HEAD==origin/main）。
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from scripts.deploy import (
    check_running_sessions,
    classify_wip_overlap,
    deploy,
    needs_image_rebuild,
    plan_reload_services,
    plan_runtime_file_actions,
    predict_stash_merge,
    probe_endpoints,
)

# ── git 帮助与仓库搭建 ──


def _run(args: Sequence[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - 固定 git 参数,无外部输入
        ["git", *args],  # noqa: S607 - git 走 PATH 解析,无外部输入
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
    )


def _git(args: Sequence[str], cwd: Path) -> str:
    return _run(args, cwd).stdout.strip()


def _readme(**changes: str) -> str:
    """10 行 README；changes 形如 line1="X"（行号从 1 起），用于构造不同/相同区块改动。"""
    lines = {i: f"line{i}" for i in range(1, 11)}
    for key, value in changes.items():
        lines[int(key.removeprefix("line"))] = value
    return "\n".join(lines[i] for i in range(1, 11)) + "\n"


class Sandbox:
    """work（模拟主检出）+ origin.git（裸远端）：work 的 HEAD 基线 == origin/main。"""

    def __init__(self, root: Path, work: Path, origin: Path) -> None:
        self.root = root
        self.work = work
        self.origin = origin
        self._other: Path | None = None

    # ── 状态 ──

    def head(self) -> str:
        return _git(["rev-parse", "HEAD"], self.work)

    def status(self) -> str:
        return _git(["status", "--porcelain", "-uall"], self.work)

    def stash_list(self) -> str:
        return _git(["stash", "list"], self.work)

    def snapshot(self) -> tuple[str, str, str]:
        """零变更断言的现场快照：HEAD + 工作树 + stash 列表。"""
        return (self.head(), self.status(), self.stash_list())

    # ── 操作 ──

    def write(self, rel: str, content: str) -> None:
        path = self.work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self, msg: str) -> str:
        _git(["add", "-A", "-f"], self.work)
        _git(["commit", "-qm", msg], self.work)
        return self.head()

    def upstream(self) -> None:
        """把 work 的当前 HEAD 推到 origin 并刷新 origin/main（恢复 HEAD==origin/main 基线）。"""
        _git(["push", "-q", "origin", "main"], self.work)
        _git(["fetch", "-q", "origin"], self.work)

    def fetch(self) -> None:
        _git(["fetch", "-q", "origin"], self.work)

    def incoming(self, changes: dict[str, str], msg: str = "incoming") -> str:
        """在另一 clone 提交并推送（origin/main 前进，work 尚未 fetch）；返回新 tip。"""
        other = self._other
        if other is None:
            other = self.root / "other"
            _git(["clone", "-q", str(self.origin), str(other)], self.root)
            for key, value in (
                ("user.email", "t@t"),
                ("user.name", "t"),
                ("core.autocrlf", "false"),
            ):
                _git(["config", key, value], other)
            self._other = other
        _git(["fetch", "-q", "origin"], other)
        _git(["reset", "-q", "--hard", "origin/main"], other)
        for rel, content in changes.items():
            path = other / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        _git(["add", "-A", "-f"], other)
        _git(["commit", "-qm", msg], other)
        _git(["push", "-q", "origin", "main"], other)
        return _git(["rev-parse", "HEAD"], other)


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    origin = tmp_path / "origin.git"
    _run(["init", "-q", "--bare", str(origin)], tmp_path)
    _run(["symbolic-ref", "HEAD", "refs/heads/main"], origin)
    work = tmp_path / "work"
    work.mkdir()
    _run(["init", "-q"], work)
    _run(["symbolic-ref", "HEAD", "refs/heads/main"], work)
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("core.autocrlf", "false")):
        _git(["config", key, value], work)
    sb = Sandbox(tmp_path, work, origin)
    sb.write(".gitignore", "data/\n")
    sb.write("README.md", _readme())
    sb.write("src/app.py", "v1\n")
    _git(["add", "-A"], work)
    _git(["commit", "-qm", "v1"], work)
    _git(["remote", "add", "origin", str(origin)], work)
    _git(["push", "-qu", "origin", "main"], work)
    return sb


# ── 注入的 fake：网络与 docker（不真连） ──

_MISSING = object()


class _Seq:
    """探针序列取值：越界后粘滞末位（= 服务持续同一状态）。"""

    def __init__(self, values: Sequence[int | None]) -> None:
        self._values = list(values)
        self._i = 0

    def __call__(self) -> int | None:
        value = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return value

    @property
    def calls(self) -> int:
        return self._i


class FakeStack:
    """注入的 fake：sessions 门禁 + 两侧探针 + docker 重启。"""

    def __init__(
        self,
        *,
        sessions: object = _MISSING,
        backend: Sequence[int | None] = (200,),
        frontend: Sequence[int | None] = (200,),
        restart_rc: int = 0,
    ) -> None:
        self.sessions = {"sessions": []} if sessions is _MISSING else sessions
        self.backend = _Seq(backend)
        self.frontend = _Seq(frontend)
        self._restart_rc = restart_rc
        self.sessions_calls = 0
        self.restart_calls = 0
        self.restart_repo: Path | None = None
        self.restart_services: list[tuple[str, ...]] = []

    def fetch_sessions(self) -> object:
        self.sessions_calls += 1
        return self.sessions

    def probe_backend(self) -> int | None:
        return self.backend()

    def probe_frontend(self) -> int | None:
        return self.frontend()

    def restart(self, repo: Path, services: Sequence[str] = ("backend", "frontend")) -> int:
        self.restart_calls += 1
        self.restart_repo = repo
        self.restart_services.append(tuple(services))
        return self._restart_rc

    def deploy_kwargs(
        self, out: list[str], *, dry_run: bool = False, allow_stash: bool = False
    ) -> dict:
        return {
            "dry_run": dry_run,
            "allow_stash": allow_stash,
            "fetch_sessions": self.fetch_sessions,
            "backend_probe": self.probe_backend,
            "frontend_probe": self.probe_frontend,
            "restart": self.restart,
            "post_restart_wait_s": 0.0,
            "now_stamp": lambda: "TESTTS",
            "out": out.append,
        }


# ── 纯函数 ──


class TestClassifyWipOverlap:
    def test_disjoint_is_empty(self):
        assert classify_wip_overlap(["src/a.py"], ["src/b.py"]) == ()

    def test_empty_inputs_is_empty(self):
        assert classify_wip_overlap([], ["src/b.py"]) == ()
        assert classify_wip_overlap(["src/a.py"], []) == ()

    def test_overlap_listed_sorted(self):
        assert classify_wip_overlap(["b.py", "a.py", "c.py"], ["c.py", "a.py"]) == ("a.py", "c.py")


class TestPlanRuntimeFileActions:
    def test_untracked_runtime_collision_moved_aside(self):
        plan = plan_runtime_file_actions(
            incoming=["data/execution.db"], untracked=["data/execution.db"], modified=[]
        )
        assert plan.move_aside == ("data/execution.db",)
        assert plan.discard == ()
        assert plan.blocked == ()

    def test_tracked_modified_runtime_discarded(self):
        plan = plan_runtime_file_actions(
            incoming=["data/execution.db"], untracked=[], modified=["data/execution.db"]
        )
        assert plan.discard == ("data/execution.db",)
        assert plan.move_aside == ()
        assert plan.blocked == ()

    def test_tracked_modified_non_runtime_left_to_wip_check(self):
        # 已跟踪+已修改的非 data/ 文件属 ③ WIP 交集（默认中止、--stash 预判），不在 ④ 计划中
        plan = plan_runtime_file_actions(
            incoming=["src/app.py"], untracked=[], modified=["src/app.py"]
        )
        assert (plan.move_aside, plan.discard, plan.blocked) == ((), (), ())

    def test_untracked_non_runtime_blocks(self):
        plan = plan_runtime_file_actions(
            incoming=["src/new.py"], untracked=["src/new.py"], modified=[]
        )
        assert plan.blocked == ("src/new.py",)

    def test_local_state_not_touched_by_incoming_ignored(self):
        plan = plan_runtime_file_actions(
            incoming=["src/incoming.py"],
            untracked=["data/runtime.db"],
            modified=["src/local.py"],
        )
        assert (plan.move_aside, plan.discard, plan.blocked) == ((), (), ())


class TestCheckRunningSessions:
    def test_reachable_with_running(self):
        got = check_running_sessions(
            {
                "sessions": [
                    {"session_id": "sess-1", "status": "running"},
                    {"session_id": "sess-2", "status": "completed"},
                ]
            }
        )
        assert got.reachable is True
        assert got.running == ("sess-1",)

    def test_reachable_without_running(self):
        got = check_running_sessions(
            {"sessions": [{"session_id": "sess-2", "status": "completed"}]}
        )
        assert got.reachable is True
        assert got.running == ()

    def test_unreachable_payload_none(self):
        got = check_running_sessions(None)
        assert got.reachable is False
        assert got.running == ()


class TestProbeEndpoints:
    def test_both_200_healthy(self):
        got = probe_endpoints(lambda: 200, lambda: 200)
        assert got.healthy is True
        assert got.backend == 200
        assert got.frontend == 200

    @pytest.mark.parametrize(
        ("backend", "frontend"),
        [(500, 200), (200, 503), (None, 200), (200, None), (None, None)],
    )
    def test_any_bad_unhealthy(self, backend, frontend):
        got = probe_endpoints(lambda: backend, lambda: frontend)
        assert got.healthy is False
        assert got.backend == backend
        assert got.frontend == frontend


# ── --stash 预判（真实 git） ──


class TestPredictStashMerge:
    def test_clean_when_edits_in_different_regions(self, sandbox):
        sandbox.incoming({"README.md": _readme(line10="INCOMING-line10")})
        sandbox.fetch()
        sandbox.write("README.md", _readme(line1="LOCAL-line1"))
        assert predict_stash_merge(sandbox.work, "origin/main").clean is True

    def test_conflict_when_same_region(self, sandbox):
        sandbox.incoming({"README.md": _readme(line2="INCOMING-2")})
        sandbox.fetch()
        sandbox.write("README.md", _readme(line2="LOCAL-2"))
        prediction = predict_stash_merge(sandbox.work, "origin/main")
        assert prediction.clean is False
        assert "README.md" in prediction.detail


# ── dry-run 零变更 ──


class TestDryRun:
    def test_dry_run_reports_plan_and_changes_nothing(self, sandbox):
        sandbox.incoming(
            {"README.md": _readme(line10="INCOMING-line10"), "data/execution.db": "INCOMING-DB\n"}
        )
        sandbox.write("README.md", _readme(line1="LOCAL-line1"))
        sandbox.write("data/execution.db", "LOCAL-DB\n")  # untracked 且被 .gitignore 忽略

        before = sandbox.snapshot()
        stack = FakeStack()
        out: list[str] = []
        code = deploy(sandbox.work, **stack.deploy_kwargs(out, dry_run=True, allow_stash=True))

        assert code == 0
        assert sandbox.snapshot() == before
        assert stack.backend.calls == 0 and stack.frontend.calls == 0
        assert stack.restart_calls == 0
        text = "\n".join(out)
        assert "dry-run" in text
        assert "git pull --ff-only" in text
        assert "data/execution.db" in text  # 挪开计划
        assert (sandbox.work / "data" / "execution.db").read_text(encoding="utf-8") == "LOCAL-DB\n"


# ── running 门禁 ──


class TestRunningSessionGate:
    def test_running_session_aborts_before_any_change(self, sandbox):
        sandbox.incoming({"src/app.py": "v2\n"})  # 有可拉取内容：门禁失效就会 pull
        before = sandbox.snapshot()
        stack = FakeStack(
            sessions={
                "sessions": [
                    {"session_id": "sess-7", "status": "running", "stock_code": "600519"},
                ]
            }
        )
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 1
        assert "sess-7" in "\n".join(out)
        assert sandbox.snapshot() == before
        assert stack.backend.calls == 0 and stack.restart_calls == 0

    def test_unreachable_backend_continues_with_note(self, sandbox):
        stack = FakeStack(sessions=None)
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0
        assert "不可达" in "\n".join(out)


# ── 幂等（已最新只探针） ──


class TestIdempotent:
    def test_already_latest_skips_pull_and_probes(self, sandbox):
        before = sandbox.snapshot()
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0
        text = "\n".join(out)
        assert "已是最新" in text and "跳过拉取" in text
        assert stack.sessions_calls == 1
        assert stack.backend.calls == 1 and stack.frontend.calls == 1
        assert stack.restart_calls == 0
        assert sandbox.snapshot() == before


# ── 拉取路径（真实 git） ──


class TestPullPaths:
    def test_no_overlap_pulls_directly(self, sandbox):
        sha = sandbox.incoming({"src/new.py": "incoming\n"})
        sandbox.write("src/app.py", "local edit\n")  # 与 incoming 无交集
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0
        assert sandbox.head() == sha
        assert (sandbox.work / "src" / "new.py").read_text(encoding="utf-8") == "incoming\n"
        assert (sandbox.work / "src" / "app.py").read_text(encoding="utf-8") == "local edit\n"

    def test_wip_overlap_refused_without_stash(self, sandbox):
        sandbox.incoming({"README.md": _readme(line10="INCOMING-line10")})
        sandbox.write("README.md", _readme(line1="LOCAL-line1"))
        before = sandbox.snapshot()
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 1
        assert "README.md" in "\n".join(out)
        assert sandbox.snapshot() == before
        assert stack.backend.calls == 0 and stack.restart_calls == 0

    def test_wip_overlap_stash_roundtrip_when_prediction_clean(self, sandbox):
        sha = sandbox.incoming({"README.md": _readme(line10="INCOMING-line10")})
        sandbox.write("README.md", _readme(line1="LOCAL-line1"))
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out, allow_stash=True)) == 0
        assert sandbox.head() == sha
        text = (sandbox.work / "README.md").read_text(encoding="utf-8")
        assert "LOCAL-line1" in text and "INCOMING-line10" in text
        assert sandbox.stash_list() == ""

    def test_wip_overlap_stash_conflict_still_refused(self, sandbox):
        sandbox.incoming({"README.md": _readme(line2="INCOMING-2")})
        sandbox.write("README.md", _readme(line2="LOCAL-2"))
        before = sandbox.snapshot()
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out, allow_stash=True)) == 1
        assert "冲突" in "\n".join(out)
        assert sandbox.snapshot() == before


# ── 运行时文件处置（真实 git） ──


class TestRuntimeFilePaths:
    def test_tracked_runtime_file_discarded_then_pulled(self, sandbox):
        sandbox.write("data/execution.db", '{"v": 1}\n')
        sandbox.commit("track runtime file")
        sandbox.upstream()
        sha = sandbox.incoming({"data/execution.db": '{"v": 2}\n'})
        sandbox.write("data/execution.db", '{"v": "local"}\n')
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0
        assert sandbox.head() == sha
        content = (sandbox.work / "data" / "execution.db").read_text(encoding="utf-8")
        assert content == '{"v": 2}\n'
        assert "丢弃" in "\n".join(out)

    def test_untracked_incoming_data_file_moved_aside(self, sandbox):
        sha = sandbox.incoming({"data/execution.db": "INCOMING-DB\n"})
        data_dir = sandbox.work / "data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "execution.db").write_text("LOCAL-DB\n", encoding="utf-8")

        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0

        assert sandbox.head() == sha
        assert "deploy-bak-TESTTS" in "\n".join(out)  # 报告含挪开清单
        assert (data_dir / "execution.db").read_text(encoding="utf-8") == "INCOMING-DB\n"
        backups = sorted(p.name for p in data_dir.glob("execution.db.deploy-bak-*"))
        assert backups == ["execution.db.deploy-bak-TESTTS"]
        assert (data_dir / backups[0]).read_text(encoding="utf-8") == "LOCAL-DB\n"

    def test_untracked_non_ascii_data_file_moved_aside(self, sandbox):
        """非 ASCII 路径：git 默认 C 转义加引号，脚本须按原样路径处置（core.quotepath=false）。"""
        sha = sandbox.incoming({"data/运行.db": "INCOMING-DB\n"})
        data_dir = sandbox.work / "data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "运行.db").write_text("LOCAL-DB\n", encoding="utf-8")

        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0

        assert sandbox.head() == sha
        assert "deploy-bak-TESTTS" in "\n".join(out)
        assert (data_dir / "运行.db").read_text(encoding="utf-8") == "INCOMING-DB\n"
        backups = sorted(p.name for p in data_dir.glob("运行.db.deploy-bak-*"))
        assert backups == ["运行.db.deploy-bak-TESTTS"]
        assert (data_dir / backups[0]).read_text(encoding="utf-8") == "LOCAL-DB\n"

    def test_non_runtime_untracked_collision_aborts(self, sandbox):
        sandbox.incoming({"src/new.py": "incoming\n"})
        sandbox.write("src/new.py", "local draft\n")
        before = sandbox.snapshot()
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 1
        assert "src/new.py" in "\n".join(out)
        assert sandbox.snapshot() == before


# ── 探针与重启（注入 fake） ──


class TestProbeRetry:
    def test_unhealthy_then_restart_recovers(self, sandbox):
        stack = FakeStack(backend=(503, 200))
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0
        assert stack.restart_calls == 1
        assert stack.restart_repo == sandbox.work
        text = "\n".join(out)
        assert "第 1 轮" in text and "第 2 轮" in text
        assert "503" in text

    def test_still_unhealthy_exits_2_with_both_rounds(self, sandbox):
        stack = FakeStack(backend=(503,), frontend=(200,))
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 2
        assert stack.restart_calls == 1
        text = "\n".join(out)
        assert "第 1 轮" in text and "第 2 轮" in text
        assert "部署失败" in text


# ── 新代码加载（issue #343：healthy-but-stale 静默半完成） ──


class TestPlanReloadServices:
    """纯函数：incoming 文件清单 → pull 后需重启的服务。"""

    def test_src_touches_backend(self):
        assert plan_reload_services(["src/finance_agent/api.py"]) == ("backend",)

    def test_frontend_touches_frontend(self):
        assert plan_reload_services(["frontend/src/Charts.tsx"]) == ("frontend",)

    def test_both(self):
        assert plan_reload_services(["src/a.py", "frontend/b.ts"]) == ("backend", "frontend")

    def test_docs_and_scripts_need_no_restart(self):
        assert plan_reload_services(["docs/x.md", "scripts/deploy.py", "tests/y.py"]) == ()

    def test_empty_incoming(self):
        assert plan_reload_services([]) == ()


class TestNeedsImageRebuild:
    """纯函数：restart 加载不到的镜像类路径（evals/ 为镜像 COPY，非挂载）。"""

    def test_evals_requires_rebuild(self):
        assert needs_image_rebuild(["evals/hallucination/x.py"]) is True

    def test_dependency_files_require_rebuild(self):
        assert needs_image_rebuild(["requirements.txt"]) is True
        assert needs_image_rebuild(["Dockerfile"]) is True
        assert needs_image_rebuild(["docker-compose.override.yml"]) is True

    def test_src_alone_no_rebuild_hint(self):
        assert needs_image_rebuild(["src/finance_agent/api.py"]) is False


class TestReloadOnNewCode:
    """pull 推进且 incoming 含后端/前端源码 → 无条件重启对应服务后才探针。

    红线：探针只判「栈是否健康」，不判「新码是否加载」——旧码照常服务 200 时
    MUST NOT 判定「✓ 部署完成」（issue #343 实证：#342 部署 21 分钟里容器跑旧码）。
    """

    def test_src_incoming_restarts_backend_before_probe(self, sandbox):
        sha = sandbox.incoming({"src/new.py": "incoming\n"})
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0
        assert sandbox.head() == sha
        assert stack.restart_calls == 1
        assert stack.restart_services == [("backend",)]
        assert "新代码加载" in "\n".join(out)

    def test_frontend_incoming_restarts_frontend(self, sandbox):
        sandbox.incoming({"frontend/src/x.ts": "x\n"})
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0
        assert stack.restart_services == [("frontend",)]

    def test_both_sources_restart_both_services(self, sandbox):
        sandbox.incoming({"src/new.py": "a\n", "frontend/src/y.ts": "b\n"})
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0
        assert stack.restart_services == [("backend", "frontend")]

    def test_docs_only_incoming_no_restart(self, sandbox):
        sandbox.incoming({"docs/x.md": "doc\n"})
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0
        assert stack.restart_calls == 0

    def test_reload_restart_failure_never_claims_success(self, sandbox):
        """新码加载重启失败 → 退出码 2 且不进探针（旧码 200 也不得冒充部署完成）。"""
        sandbox.incoming({"src/new.py": "incoming\n"})
        stack = FakeStack(restart_rc=1)
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 2
        text = "\n".join(out)
        assert "✓ 部署完成" not in text
        assert "新代码加载重启失败" in text
        assert stack.backend.calls == 0

    def test_reload_then_unhealthy_restarts_again_for_health(self, sandbox):
        """新码重启后探针不健康 → 既有健康恢复路径仍生效（第二次 restart 双服务）。"""
        sandbox.incoming({"src/new.py": "incoming\n"})
        stack = FakeStack(backend=(503, 200))
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0
        assert stack.restart_services == [("backend",), ("backend", "frontend")]

    def test_already_latest_zero_side_effects(self, sandbox):
        """幂等场景不受影响：已最新 → 零重启（spec「已最新重复执行」）。"""
        before = sandbox.snapshot()
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0
        assert stack.restart_calls == 0
        assert sandbox.snapshot() == before

    def test_image_class_incoming_logs_rebuild_hint(self, sandbox):
        """evals/（镜像 COPY，非挂载）变更：restart 加载不到 → 显式提示需 rebuild。"""
        sandbox.incoming({"evals/hallucination/x.py": "x\n"})
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out)) == 0
        assert stack.restart_calls == 0  # evals/ 不触发 restart（restart 也加载不到）
        assert "up -d --build" in "\n".join(out)

    def test_dry_run_prints_reload_plan(self, sandbox):
        sandbox.incoming({"src/new.py": "incoming\n"})
        before = sandbox.snapshot()
        stack = FakeStack()
        out: list[str] = []
        assert deploy(sandbox.work, **stack.deploy_kwargs(out, dry_run=True)) == 0
        text = "\n".join(out)
        assert "新代码加载" in text and "backend" in text
        assert stack.restart_calls == 0
        assert sandbox.snapshot() == before


# ── CLI 直跑（脚本模式回归，对齐 tests/test_prompt_history.py 先例） ──


class TestScriptModeCli:
    def test_help_runs_in_script_mode(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(  # noqa: S603 - 固定脚本路径,无外部输入
            ["uv", "run", "python", "scripts/deploy.py", "--help"],  # noqa: S607
            cwd=root,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, result.stderr[-300:]
        assert "--dry-run" in result.stdout
        assert "--stash" in result.stdout
        assert "--repo" in result.stdout


def test_data_prefix_non_db_file_not_in_runtime_plan():
    """白名单收窄（实施期裁决）：data/ 下非库文件（如 config.json）不进 ④ 白名单。

    误丢弃不可再生文件（如 data/cohort/*.json 一类）代价高于部署失败；此类文件
    按普通 WIP 处理——untracked 同名 → blocked 中止，已跟踪+已修改 → 归 ③。
    """
    plan = plan_runtime_file_actions(
        incoming=["data/config.json"],
        untracked=["data/config.json"],
        modified=["data/config.json"],
    )
    assert plan.move_aside == ()
    assert plan.discard == ()
    assert plan.blocked == ("data/config.json",)
