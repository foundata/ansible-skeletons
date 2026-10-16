# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check step scheduling, per-step logs and per-step temporary directories."""

import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts import check as check_module
from scripts.check import (
    Context,
    Runner,
    Step,
    check_output_layout,
    completed_checks,
    default_lane,
    identity_step,
    plan,
    required_checks,
)
from scripts.common import GateError
from scripts.render import MODULE_PROFILES


@pytest.fixture
def output(tmp_path: Path) -> Path:
    (tmp_path / "t").mkdir(mode=0o700)
    return tmp_path


def recorder(log: list[str], name: str) -> Callable[[Context], None]:
    def action(_context: Context) -> None:
        log.append(name)

    return action


def test_one_job_keeps_the_plan_order(output: Path) -> None:
    ran: list[str] = []
    steps = [
        Step("a", recorder(ran, "a")),
        Step("b", recorder(ran, "b"), long=True),
        Step("c", recorder(ran, "c"), ("a",)),
    ]
    status = Runner(output, steps, jobs=1).execute()
    assert ran == ["a", "b", "c"]
    assert status == {"a": "passed", "b": "passed", "c": "passed"}


def test_independent_steps_run_at_the_same_time(output: Path) -> None:
    # Both steps must be inside the barrier together, or it times out.
    barrier = threading.Barrier(2, timeout=10)

    def meet(_context: Context) -> None:
        barrier.wait()

    steps = [Step("a", meet), Step("b", meet)]
    assert set(Runner(output, steps, jobs=2).execute().values()) == {"passed"}


def test_long_steps_start_first_with_several_jobs(output: Path) -> None:
    started: list[str] = []
    steps = [
        Step("short", recorder(started, "short")),
        Step("long", recorder(started, "long"), long=True),
    ]
    Runner(output, steps, jobs=2).execute()
    assert started[0] == "long"


def test_a_failure_starts_nothing_new_but_lets_running_steps_finish(
    output: Path,
) -> None:
    failed = threading.Event()
    runner: Runner

    def fail(context: Context) -> None:
        print("diagnostics", file=context.log)
        failed.set()
        raise GateError("broken")

    def slow(_context: Context) -> None:
        assert failed.wait(10)
        # Let the runner record the failure before this step completes.
        deadline = time.monotonic() + 10
        while runner.status.get("fail") != "failed" and time.monotonic() < deadline:
            time.sleep(0.01)

    steps = [
        Step("slow", slow, long=True),
        Step("fail", fail),
        Step("later", recorder([], "later")),
    ]
    runner = Runner(output, steps, jobs=2)
    status = runner.execute()
    assert status == {"slow": "passed", "fail": "failed", "later": "not run"}


def test_dependents_of_a_failed_step_never_start(output: Path) -> None:
    def fail(_context: Context) -> None:
        raise GateError("broken")

    steps = [Step("a", fail), Step("b", recorder([], "b"), ("a",))]
    assert Runner(output, steps, jobs=4).execute() == {"a": "failed", "b": "not run"}


def test_serial_steps_never_overlap(output: Path) -> None:
    lock = threading.Lock()
    active: list[int] = [0, 0]

    def tier(_context: Context) -> None:
        with lock:
            active[0] += 1
            active[1] = max(active)
        time.sleep(0.05)
        with lock:
            active[0] -= 1

    steps = [Step(name, tier, serial=True) for name in ("podman", "libvirt", "ee")]
    steps.append(Step("quick", recorder([], "quick")))
    assert set(Runner(output, steps, jobs=4).execute().values()) == {"passed"}
    assert active[1] == 1


def test_steps_have_private_logs_and_temporary_directories(output: Path) -> None:
    seen: dict[str, str] = {}

    def note(name: str) -> Callable[[Context], None]:
        def action(context: Context) -> None:
            seen[name] = context.env["TMPDIR"]
            print(f"output of {name}", file=context.log)

        return action

    steps = [Step(name, note(name)) for name in ("a", "b", "c")]
    Runner(output, steps, jobs=3).execute()
    assert len(set(seen.values())) == 3
    for name, temporary in seen.items():
        path = Path(temporary)
        assert path.parent == output / "t"
        assert len(path.name) == 1
        assert not path.stat().st_mode & 0o077
        log = output / "logs" / f"{name}.log"
        assert log.read_text(encoding="utf-8") == f"output of {name}\n"


def test_a_failed_step_prints_its_log_tail_and_path(
    output: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(context: Context) -> None:
        for number in range(60):
            print(f"line {number}", file=context.log)
        raise GateError("broken")

    def crash(_context: Context) -> None:
        raise RuntimeError("unexpected")

    Runner(output, [Step("a", fail), Step("b", crash)], jobs=1).execute()
    printed = capsys.readouterr().out
    assert f"FAILED a after 0 s; log: {output / 'logs/a.log'}" in printed
    assert "    line 59\n" in printed
    assert "    line 19\n" not in printed
    assert "    error: broken" in printed
    # Steps after a failure do not start.
    assert "start  b" not in printed
    Runner(output, [Step("b", crash)], jobs=1).execute()
    log = (output / "logs/b.log").read_text(encoding="utf-8")
    assert "Traceback" in log
    assert "error: unexpected" in log


def test_checks_follow_the_plan_whatever_finishes_first() -> None:
    steps = [
        Step("static", recorder([], "static"), check="static"),
        Step("prepare", recorder([], "prepare")),
        Step("one-identity1", recorder([], "x"), check="one"),
        Step("one-identity2", recorder([], "x"), check="one"),
        Step("two", recorder([], "x"), check="two"),
    ]
    passed = {step.name: "passed" for step in reversed(steps)}
    assert completed_checks(steps, passed) == ["static", "one", "two"]
    assert completed_checks(steps, {**passed, "one-identity2": "failed"}) == [
        "static",
        "two",
    ]


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("backends", [[], ["podman", "libvirt", "ee"]])
def test_a_complete_plan_completes_the_required_checks(
    full: bool, backends: list[str]
) -> None:
    steps = plan(full=full, backends=backends, packages=[], utils_source=None)
    passed = {step.name: "passed" for step in steps}
    assert completed_checks(steps, passed) == required_checks(
        full=full, backends=backends
    )


def test_each_module_identity_is_its_own_step() -> None:
    steps = {
        step.name: step
        for step in plan(full=True, backends=[], packages=[], utils_source=None)
    }
    for profile in MODULE_PROFILES:
        identities = [steps[identity_step(profile, index)] for index in (0, 1)]
        assert [step.check for step in identities] == [profile, profile]
        assert all(step.long and step.after == ("controller",) for step in identities)


def test_infrastructure_tiers_wait_for_the_quick_checks_one_at_a_time() -> None:
    steps = plan(full=False, backends=["podman", "ee"], packages=[], utils_source=None)
    tiers = [step for step in steps if step.name.startswith("integration-")]
    assert [step.name for step in tiers] == ["integration-podman", "integration-ee"]
    for tier in tiers:
        assert tier.serial
        assert set(tier.after) == {"static", "unit-python3.14", default_lane().name}


def test_invalid_jobs_are_rejected_before_any_output(tmp_path: Path) -> None:
    with pytest.raises(GateError, match="at least 1"):
        check_module.check(tmp_path / "output", jobs=0)
    assert not (tmp_path / "output").exists()


def test_temporary_output_fits_the_socket_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    used: list[Path] = []

    def record(output: Path, **_options: object) -> None:
        check_output_layout(output, ["podman", "libvirt", "ee"])
        used.append(output)

    monkeypatch.setattr(tempfile, "tempdir", "/tmp")
    monkeypatch.setattr(check_module, "check", record)
    monkeypatch.setattr(sys, "argv", ["check"])
    assert check_module.main() == 0
    assert len(used) == 1
    assert used[0].parent.name.startswith("sk-")
