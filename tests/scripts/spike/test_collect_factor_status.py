"""因子研发状态采集器 TDD（delta add-factor-research-monitor T1）。

契约（openspec delta factor-research-monitor「状态快照采集契约」）：

- 周期快照含 collected_at / collected_at_epoch / supervisor / current_session /
  sessions / totals 全部顶层字段；写入原子（tmp + rename）；
- 未知字段如实置空（null / 空数组），MUST NOT 编造进度数字；
- 只读观测：采集器不修改研发进程状态；
- 增量解析：跨周期累积本会话因子名（去重保序）、会话切换重置。

运行方式：``uv run pytest tests/scripts/spike/test_collect_factor_status.py -q``
（tests/scripts 不在默认套件内，同 test_qlib_translate.py 先例）。
"""

from __future__ import annotations

import json
import os

import collect_factor_status as cfs

# ── 真实日志形状 fixture（ANSI 转义 + \r 进度条 + factor_name 提示转储）──

SESSION_LOG = (
    "\x1b[95m\x1b[1mRole:\x1b[0m\x1b[96muser\x1b[0m\n"
    "factor_name: ret_5d_reversal\n"
    "factor_name: max_ret_20d\n"
    "factor_name: ret_5d_reversal\n"
    "\rWorkflow Progress:  20%|##| 1/5 [07:40<30:41, 460.27s/step, loop_index=3, step_index=1, step_name=coding]\r"
    "Workflow Progress: 100%|##| 5/5 [09:55<00:00, 119.13s/step, loop_index=4, step_index=0, step_name=direct_exp_gen]\n"
    "plain tail line\n"
)

# 因子块（harvest 桥输入面：name/description/formulation 三元组；真实日志形态）
FACTOR_BLOCK = (
    "Content: --------------Factor information:---------------\n"
    "factor_name: max_ret_20d\n"
    "factor_description: [Lottery/MAX Effect] Twenty-day maximum daily close-to-close return.\n"
    "factor_formulation: \\max_{i=0,\\ldots,19}\\left(\\frac{C_{t-i}}{C_{t-i-1}} - 1\\right)\n"
    "variables: {'C_{t-i}': 'Adjusted close price on day t-i.'}\n"
)

SESSION_LOG_WITH_BLOCK = SESSION_LOG + FACTOR_BLOCK


class TestStripAnsi:
    def test_removes_escape_sequences(self):
        assert cfs.strip_ansi("\x1b[95mRole:\x1b[0m ok") == "Role: ok"

    def test_keeps_plain_text(self):
        assert cfs.strip_ansi("no escapes here") == "no escapes here"


class TestParseProgress:
    def test_last_marker_wins(self):
        parsed = cfs.parse_progress(SESSION_LOG)
        assert parsed is not None
        assert parsed["loop_index"] == 4
        assert parsed["step_index"] == 0
        assert parsed["step_name"] == "direct_exp_gen"

    def test_tqdm_numbers_from_same_line(self):
        parsed = cfs.parse_progress(SESSION_LOG)
        assert parsed is not None
        assert parsed["steps_total"] == 5
        assert parsed["step_elapsed_s"] == 9 * 60 + 55
        assert parsed["step_eta_s"] == 0

    def test_no_marker_returns_none(self):
        assert cfs.parse_progress("just some log line\n") is None

    def test_hour_scale_tqdm(self):
        text = "Workflow Progress: 40%|##| 2/5 [1:07:40<0:30:41, 460.27s/step, loop_index=3, step_index=2, step_name=running]\n"
        parsed = cfs.parse_progress(text)
        assert parsed is not None
        assert parsed["step_elapsed_s"] == 3600 + 7 * 60 + 40
        assert parsed["step_eta_s"] == 30 * 60 + 41


class TestExtractFactorNames:
    def test_ordered_unique(self):
        assert cfs.extract_factor_names(SESSION_LOG) == ["ret_5d_reversal", "max_ret_20d"]

    def test_ignores_malformed(self):
        assert cfs.extract_factor_names("factor_name: 有中文名\nfactor_name:\n") == []

    def test_stops_at_non_word_char(self):
        assert cfs.extract_factor_names("factor_name: vol_ratio_20d。\n") == ["vol_ratio_20d"]


class TestExtractFactorBlocks:
    """harvest 桥输入面：因子块（name/description/formulation）抽取。"""

    def test_block_metadata_extracted(self):
        blocks = cfs.extract_factor_blocks(SESSION_LOG_WITH_BLOCK)
        assert blocks == [
            {
                "name": "max_ret_20d",
                "description": "[Lottery/MAX Effect] Twenty-day maximum daily close-to-close return.",
                "formulation": "\\max_{i=0,\\ldots,19}\\left(\\frac{C_{t-i}}{C_{t-i-1}} - 1\\right)",
            }
        ]

    def test_incomplete_block_skipped(self):
        """缺 description/formulation 的半块不产出（MUST NOT 以残缺元数据冒充）。"""
        partial = (
            "Content: --------------Factor information:---------------\n"
            "factor_name: foo\n"
            "variables: {}\n"
        )
        assert cfs.extract_factor_blocks(partial) == []

    def test_duplicate_blocks_deduped_in_order(self):
        text = FACTOR_BLOCK + FACTOR_BLOCK
        blocks = cfs.extract_factor_blocks(text)
        assert [b["name"] for b in blocks] == ["max_ret_20d"]


class TestParseSupervisorLog:
    def test_last_start_wins(self):
        text = (
            "[supervisor] started at 2026-10-16T01:00:00+08:00 pid 111\n"
            "[supervisor] session ended after 60s (log: /root/rdagent-work/factor_research_logs/session-20261016-010000.log) at 2026-10-16T01:01:00+08:00\n"
            "[supervisor] started at 2026-10-17T11:36:00+08:00 pid 7752\n"
        )
        parsed = cfs.parse_supervisor_log(text)
        assert parsed["started_at"] == "2026-10-17T11:36:00+08:00"
        assert parsed["pid"] == 7752
        assert parsed["sessions"] == [
            {
                "log_file": "session-20261016-010000.log",
                "duration_s": 60,
                "ended_at": "2026-10-16T01:01:00+08:00",
            }
        ]

    def test_empty_log(self):
        parsed = cfs.parse_supervisor_log("")
        assert parsed == {"started_at": None, "pid": None, "sessions": []}


class TestSessionStartedAt:
    def test_filename_to_iso(self):
        assert cfs.session_started_at("session-20261017-113600.log") == "2026-10-17T11:36:00+08:00"

    def test_invalid_returns_none(self):
        assert cfs.session_started_at("not-a-session.log") is None


def _make_workdir(tmp_path, *, pid=None, supervisor_log="", session_logs=(), workspace_dirs=()):
    """构造 fixture 工作目录（与 distro /root/rdagent-work 同布局）。"""
    wd = tmp_path / "rdagent-work"
    logdir = wd / "factor_research_logs"
    logdir.mkdir(parents=True)
    if pid is not None:
        (wd / "factor_research.pid").write_text(str(pid), encoding="utf-8")
    if supervisor_log:
        (logdir / "supervisor.log").write_text(supervisor_log, encoding="utf-8")
    for name, content in session_logs:
        (logdir / name).write_text(content, encoding="utf-8")
    ws = wd / "git_ignore_folder" / "RD-Agent_workspace"
    for dirname, has_factor in workspace_dirs:
        d = ws / dirname
        d.mkdir(parents=True)
        if has_factor:
            (d / "factor.py").write_text("# factor\n", encoding="utf-8")
    return wd


class TestCollectOnce:
    def _fixture(self, tmp_path, *, pid=None, session_logs=()):
        supervisor_log = (
            "[supervisor] started at 2026-10-17T11:36:00+08:00 pid 7752\n"
            "[supervisor] session ended after 300s (log: /root/rdagent-work/factor_research_logs/session-20261017-110000.log) at 2026-10-17T11:35:00+08:00\n"
        )
        return _make_workdir(
            tmp_path,
            pid=os.getpid() if pid is None else pid,
            supervisor_log=supervisor_log,
            session_logs=session_logs
            or (
                ("session-20261017-110000.log", "old session\n"),
                ("session-20261017-113600.log", SESSION_LOG),
            ),
            workspace_dirs=(("aaa", True), ("bbb", False)),
        )

    def test_full_snapshot_shape(self, tmp_path):
        wd = self._fixture(tmp_path)
        out = tmp_path / "status.json"
        state = wd / "factor_research_logs" / ".collector_state.json"
        snap = cfs.collect_once(
            wd, out, state, now_epoch=1_800_000_000.0, session_alive=lambda: True
        )

        # 原子写：目标文件即合法 JSON，无 tmp 残留
        on_disk = json.loads(out.read_text(encoding="utf-8"))
        assert on_disk == snap
        assert list(out.parent.glob("*.tmp")) == []

        assert snap["collected_at_epoch"] == 1_800_000_000
        assert snap["collected_at"].startswith("20")

        assert snap["supervisor"]["running"] is True
        assert snap["supervisor"]["pid"] == os.getpid()
        assert snap["supervisor"]["started_at"] == "2026-10-17T11:36:00+08:00"

        cur = snap["current_session"]
        assert cur["log_file"] == "session-20261017-113600.log"
        assert cur["started_at"] == "2026-10-17T11:36:00+08:00"
        assert cur["alive"] is True
        assert cur["loop_index"] == 4
        assert cur["step_index"] == 0
        assert cur["step_name"] == "direct_exp_gen"
        assert cur["steps_total"] == 5
        assert cur["step_elapsed_s"] == 595
        assert cur["factors"] == ["ret_5d_reversal", "max_ret_20d"]
        # 日志尾已清理 ANSI 且不含空行
        assert any("Workflow Progress" in line for line in cur["log_tail"])
        assert all("\x1b" not in line for line in cur["log_tail"])
        assert all(line.strip() for line in cur["log_tail"])

        # 会话历史：新→旧；旧会话带结束事件
        assert [s["log_file"] for s in snap["sessions"]] == [
            "session-20261017-113600.log",
            "session-20261017-110000.log",
        ]
        old = snap["sessions"][1]
        assert old["ended_at"] == "2026-10-17T11:35:00+08:00"
        assert old["duration_s"] == 300

        assert snap["totals"] == {"sessions": 2, "candidates": 1, "workspace_tasks": 2}

    def test_incremental_factors_accumulate(self, tmp_path):
        wd = self._fixture(tmp_path)
        out = tmp_path / "status.json"
        state = wd / "factor_research_logs" / ".collector_state.json"
        snap1 = cfs.collect_once(
            wd, out, state, now_epoch=1_800_000_000.0, session_alive=lambda: True
        )
        assert snap1["current_session"]["factors"] == ["ret_5d_reversal", "max_ret_20d"]

        # 追加新因子行（同会话）→ 第二周期应累积而非重扫丢失
        log = wd / "factor_research_logs" / "session-20261017-113600.log"
        with log.open("a", encoding="utf-8") as fh:
            fh.write("factor_name: park_vol_10d\n")
        snap2 = cfs.collect_once(
            wd, out, state, now_epoch=1_800_000_030.0, session_alive=lambda: True
        )
        assert snap2["current_session"]["factors"] == [
            "ret_5d_reversal",
            "max_ret_20d",
            "park_vol_10d",
        ]

    def test_new_session_resets_factors(self, tmp_path):
        wd = self._fixture(tmp_path)
        out = tmp_path / "status.json"
        state = wd / "factor_research_logs" / ".collector_state.json"
        cfs.collect_once(wd, out, state, now_epoch=1_800_000_000.0, session_alive=lambda: True)

        (wd / "factor_research_logs" / "session-20261017-120000.log").write_text(
            "factor_name: vol_ratio_20d\n", encoding="utf-8"
        )
        snap = cfs.collect_once(
            wd, out, state, now_epoch=1_800_000_100.0, session_alive=lambda: True
        )
        assert snap["current_session"]["log_file"] == "session-20261017-120000.log"
        assert snap["current_session"]["factors"] == ["vol_ratio_20d"]

    def test_unknown_fields_stay_null(self, tmp_path):
        wd = _make_workdir(
            tmp_path,
            pid=os.getpid(),
            supervisor_log="[supervisor] started at 2026-10-17T11:36:00+08:00 pid 1\n",
            session_logs=(
                ("session-20261017-113600.log", "session just started, no markers yet\n"),
            ),
        )
        out = tmp_path / "status.json"
        state = wd / "factor_research_logs" / ".collector_state.json"
        snap = cfs.collect_once(
            wd, out, state, now_epoch=1_800_000_000.0, session_alive=lambda: True
        )
        cur = snap["current_session"]
        assert cur["loop_index"] is None
        assert cur["step_index"] is None
        assert cur["step_name"] is None
        assert cur["factors"] == []
        assert cur["log_tail"] == ["session just started, no markers yet"]

    def test_dead_supervisor_reported_stopped(self, tmp_path):
        wd = self._fixture(tmp_path, pid=999_999_999)
        out = tmp_path / "status.json"
        state = wd / "factor_research_logs" / ".collector_state.json"
        snap = cfs.collect_once(
            wd, out, state, now_epoch=1_800_000_000.0, session_alive=lambda: True
        )
        assert snap["supervisor"]["running"] is False
        assert snap["current_session"]["alive"] is False

    def test_no_sessions_at_all(self, tmp_path):
        wd = _make_workdir(tmp_path, pid=os.getpid(), supervisor_log="")
        out = tmp_path / "status.json"
        state = wd / "factor_research_logs" / ".collector_state.json"
        snap = cfs.collect_once(
            wd, out, state, now_epoch=1_800_000_000.0, session_alive=lambda: True
        )
        assert snap["current_session"] is None
        assert snap["sessions"] == []
        assert snap["totals"]["sessions"] == 0


class TestCandidatesFile:
    """候选文件（harvest 桥）：因子块元数据随采集落盘，全局去重、跨会话不丢。"""

    def _fixture(self, tmp_path, session_log):
        return _make_workdir(
            tmp_path,
            pid=os.getpid(),
            supervisor_log="[supervisor] started at 2026-10-17T11:36:00+08:00 pid 1\n",
            session_logs=(("session-20261017-113600.log", session_log),),
            workspace_dirs=(("aaa", True),),
        )

    def test_candidates_written_with_metadata(self, tmp_path):
        wd = self._fixture(tmp_path, SESSION_LOG_WITH_BLOCK)
        out = tmp_path / "status.json"
        cand = tmp_path / "candidates.json"
        state = wd / "factor_research_logs" / ".collector_state.json"
        cfs.collect_once(
            wd,
            out,
            state,
            candidates_out=cand,
            now_epoch=1_800_000_000.0,
            session_alive=lambda: True,
        )
        data = json.loads(cand.read_text(encoding="utf-8"))
        assert [c["name"] for c in data["candidates"]] == ["max_ret_20d"]
        entry = data["candidates"][0]
        assert entry["formulation"].startswith("\\max_")
        assert entry["session"] == "session-20261017-113600.log"
        assert entry["first_seen_at"].startswith("20")

    def test_dedupe_across_cycles_and_sessions(self, tmp_path):
        wd = self._fixture(tmp_path, SESSION_LOG_WITH_BLOCK)
        out = tmp_path / "status.json"
        cand = tmp_path / "candidates.json"
        state = wd / "factor_research_logs" / ".collector_state.json"
        cfs.collect_once(
            wd,
            out,
            state,
            candidates_out=cand,
            now_epoch=1_800_000_000.0,
            session_alive=lambda: True,
        )
        # 第二轮：同块重复 + 新会话追加一个新因子块 → 只追加新名字
        new_block = (
            "Content: --------------Factor information:---------------\n"
            "factor_name: hl_range_1d\n"
            "factor_description: [Range] High-low range scaled by close.\n"
            "factor_formulation: \\frac{H_t - L_t}{C_t}\n"
            "variables: {}\n"
        )
        (wd / "factor_research_logs" / "session-20261017-120000.log").write_text(
            SESSION_LOG_WITH_BLOCK + new_block, encoding="utf-8"
        )
        cfs.collect_once(
            wd,
            out,
            state,
            candidates_out=cand,
            now_epoch=1_800_000_060.0,
            session_alive=lambda: True,
        )
        data = json.loads(cand.read_text(encoding="utf-8"))
        assert [c["name"] for c in data["candidates"]] == ["max_ret_20d", "hl_range_1d"]

    def test_candidates_out_defaults_next_to_status(self, tmp_path):
        """缺省候选文件与状态文件同目录（daemon 无需额外配置）。"""
        wd = self._fixture(tmp_path, SESSION_LOG_WITH_BLOCK)
        out = tmp_path / "status.json"
        state = wd / "factor_research_logs" / ".collector_state.json"
        cfs.collect_once(wd, out, state, now_epoch=1_800_000_000.0, session_alive=lambda: True)
        assert (tmp_path / "candidates.json").is_file()
