# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for stopping commands with everything they started."""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from io import StringIO
from pathlib import Path

import pytest

from scripts import check as check_module
from scripts.common import (
    CLEANUP_GRACE,
    VM_RECORD,
    GateError,
    processes,
    remove_recorded_vms,
    run,
    run_process,
    stop_processes,
    write_json,
)
from scripts.integration import leave, vm_name

# A shell that starts a long grandchild, reports its PID and waits for it.
TREE = 'sleep 300 & echo $! > "$1"; wait'


def alive(pid: int) -> bool:
    return pid in processes()


def wait_for(condition: Callable[[], bool], seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "The condition did not hold in time"
        time.sleep(0.05)


def read_pid(path: Path) -> int:
    wait_for(lambda: path.is_file() and bool(path.read_text().strip()))
    return int(path.read_text())


@pytest.fixture
def neighbor() -> Iterator[subprocess.Popen[bytes]]:
    """A process of the same user and parent that nothing may touch."""
    process = subprocess.Popen(["sleep", "300"])
    yield process
    process.kill()
    process.wait()


def test_a_timeout_stops_the_grandchild_and_spares_a_neighbor(
    tmp_path: Path, neighbor: subprocess.Popen[bytes]
) -> None:
    marker = tmp_path / "pid"
    with pytest.raises(GateError, match="exceeded its 1s timeout"):
        run(["sh", "-c", TREE, "tree", str(marker)], timeout=1, grace=5)
    assert not alive(read_pid(marker))
    assert neighbor.poll() is None


def test_a_process_ignoring_sigterm_is_killed_after_the_grace(tmp_path: Path) -> None:
    marker = tmp_path / "pid"
    command = ["sh", "-c", f"trap '' TERM; {TREE}", "tree", str(marker)]
    began = time.monotonic()
    with pytest.raises(GateError, match="timeout"):
        run(command, timeout=1, grace=1)
    assert not alive(read_pid(marker))
    assert time.monotonic() - began >= 2


def test_a_cleanup_finishes_within_the_grace(tmp_path: Path) -> None:
    """A stopped tier destroys its targets through commands it starts then."""
    started, done = tmp_path / "started", tmp_path / "done"
    script = (
        f"trap 'sleep 1; touch {done}; exit 0' TERM; touch {started}; sleep 300 & wait"
    )
    with pytest.raises(GateError, match="timeout"):
        run(["sh", "-c", script], timeout=2, grace=10)
    assert started.is_file() and done.is_file()


def test_the_output_of_a_stopped_command_stays_for_the_diagnosis() -> None:
    log = StringIO()
    with pytest.raises(GateError, match="timeout"):
        run(["sh", "-c", "echo before; sleep 300"], timeout=1, capture=True, log=log)
    assert "before" in log.getvalue()


def test_stopping_a_finished_process_changes_nothing() -> None:
    process = subprocess.Popen(["true"])
    process.wait()
    stop_processes(process.pid, 0)


def test_stopping_this_process_spares_it(tmp_path: Path) -> None:
    script = (
        "import os, subprocess, sys, time\n"
        "from scripts.common import stop_processes\n"
        "child = subprocess.Popen(['sleep', '300'])\n"
        "print(child.pid, flush=True)\n"
        "stop_processes(os.getpid(), 5)\n"
        "child.wait()\n"
        "print('survived', flush=True)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    child, verdict = result.stdout.split()
    assert verdict == "survived" and not alive(int(child))


def test_run_process_reports_a_failing_status() -> None:
    assert run_process(["sh", "-c", "echo x; exit 3"], capture=True) == (3, "x\n")


def test_ctrl_c_still_reaches_commands_through_the_terminals_group(
    tmp_path: Path,
) -> None:
    """A command stays in its caller's process group, unlike a new session."""
    marker = tmp_path / "pid"
    # A shell's background job ignores SIGINT; a foreground one does not.
    foreground = 'sh -c \'echo $$ > "$1"; exec sleep 300\' inner "$1"; true'
    script = (
        "from scripts.common import run\n"
        f"run(['sh', '-c', {foreground!r}, 'tree', {str(marker)!r}], timeout=120)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        start_new_session=True,
        stderr=subprocess.DEVNULL,
    )
    grandchild = read_pid(marker)
    assert os.getpgid(grandchild) == process.pid
    os.killpg(process.pid, signal.SIGINT)
    process.wait(timeout=30)
    wait_for(lambda: not alive(grandchild))


def test_a_terminated_check_stops_its_steps_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped: list[tuple[int, float]] = []

    def record(root: int, grace: float) -> None:
        stopped.append((root, grace))

    monkeypatch.setattr(check_module, "stop_processes", record)
    with pytest.raises(KeyboardInterrupt):
        check_module.stop_on_signal(signal.SIGTERM, None)
    assert stopped == [(os.getpid(), CLEANUP_GRACE)]


def test_a_stopped_tier_still_destroys_its_targets(tmp_path: Path) -> None:
    """The SIGTERM of a stop runs the tier's finally block, not a sudden death."""
    started, destroyed = tmp_path / "started", tmp_path / "destroyed"
    script = (
        "import signal, sys\n"
        "from pathlib import Path\n"
        "from scripts.common import run\n"
        "from scripts.integration import leave\n"
        "signal.signal(signal.SIGTERM, leave)\n"
        "try:\n"
        f"    run(['sh', '-c', 'touch {started}; sleep 300'], timeout=120)\n"
        "finally:\n"
        f"    run(['sh', '-c', 'sleep 1; touch {destroyed}'], timeout=30)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", script])
    wait_for(started.is_file)
    stop_processes(process.pid, 30)
    assert process.wait(timeout=10) == 143
    assert destroyed.is_file()


def test_leaving_on_a_signal_is_an_exit_with_its_status() -> None:
    with pytest.raises(SystemExit) as stopped:
        leave(signal.SIGTERM, None)
    assert stopped.value.code == 143


@pytest.fixture
def virsh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand in for virsh, which fails for a domain that no longer exists."""
    tools = tmp_path / "tools"
    tools.mkdir()
    calls = tmp_path / "virsh-calls"
    script = tools / "virsh"
    script.write_text(f'#!/bin/sh\necho "$@" >> {calls}\nexit "${{VIRSH_EXIT:-0}}"\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}:{os.environ['PATH']}")
    return calls


def test_a_killed_tiers_vms_are_removed_by_their_recorded_names(
    tmp_path: Path, virsh: Path
) -> None:
    name = vm_name("0123456789ab")
    write_json(tmp_path / VM_RECORD, [name])
    remove_recorded_vms(tmp_path)
    assert virsh.read_text().splitlines() == [
        f"--connect qemu:///session destroy {name}",
        f"--connect qemu:///session undefine {name}",
    ]


def test_a_vm_the_tier_removed_itself_is_no_failure(
    tmp_path: Path, virsh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIRSH_EXIT", "1")
    write_json(tmp_path / VM_RECORD, [vm_name("0123456789ab")])
    remove_recorded_vms(tmp_path)
    assert len(virsh.read_text().splitlines()) == 2


@pytest.mark.parametrize(
    "recorded",
    [
        ["work-vm"],
        ["skeleton-gate-0123456789ab-debian", "work-vm"],
        ["skeleton-gate-0123456789ab-debian; reboot"],
        "skeleton-gate-0123456789ab-debian",
    ],
)
def test_no_other_vm_of_the_session_can_be_named(
    tmp_path: Path, virsh: Path, recorded: object
) -> None:
    write_json(tmp_path / VM_RECORD, recorded)
    with pytest.raises(GateError, match="Refusing the VM names"):
        remove_recorded_vms(tmp_path)
    assert not virsh.exists()


def test_an_output_without_a_record_needs_no_virsh(tmp_path: Path) -> None:
    remove_recorded_vms(tmp_path)
