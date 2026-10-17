# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check step scheduling, per-step logs and per-step temporary directories."""

import io
import json
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts import check as check_module
from scripts.check import (
    Context,
    Runner,
    Step,
    changed_only,
    changed_paths,
    completed_checks,
    default_lane,
    identity_step,
    plan,
    remove_earlier,
    required_checks,
)
from scripts.common import GateError, matrix, source_digest
from scripts.render import MODULE_PROFILES


@pytest.fixture
def output(tmp_path: Path) -> Path:
    (tmp_path / "t").mkdir(mode=0o700)
    (tmp_path / "runtime").mkdir(mode=0o700)
    return tmp_path


def runner(output: Path, steps: list[Step], **options: Any) -> Runner:
    """Run steps in a new runtime directory, as every check does."""
    runtime = Path(tempfile.mkdtemp(dir=output / "runtime"))
    return Runner(output, steps, runtime=runtime, **options)


@pytest.fixture
def unbudgeted(monkeypatch: pytest.MonkeyPatch) -> None:
    # The tests' runtime directory may be too deep for the socket budget, which
    # is not under test here.
    monkeypatch.setattr(check_module, "check_socket_budget", lambda *_: None)


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
    status = runner(output, steps, jobs=1).execute()
    assert ran == ["a", "b", "c"]
    assert status == {"a": "passed", "b": "passed", "c": "passed"}


def test_independent_steps_run_at_the_same_time(output: Path) -> None:
    # Both steps must be inside the barrier together, or it times out.
    barrier = threading.Barrier(2, timeout=10)

    def meet(_context: Context) -> None:
        barrier.wait()

    steps = [Step("a", meet), Step("b", meet)]
    assert set(runner(output, steps, jobs=2).execute().values()) == {"passed"}


def test_long_steps_start_first_with_several_jobs(output: Path) -> None:
    steps = [
        Step("a", recorder([], "a")),
        Step("b", recorder([], "b"), long=True),
        Step("c", recorder([], "c")),
        Step("d", recorder([], "d"), long=True),
    ]
    assert [step.name for step in runner(output, steps, jobs=2).order()] == [
        "b",
        "d",
        "a",
        "c",
    ]
    assert [step.name for step in runner(output, steps, jobs=1).order()] == [
        "a",
        "b",
        "c",
        "d",
    ]


@pytest.mark.parametrize("jobs", [1, 2])
def test_a_failure_stops_one_job_and_spares_independent_parallel_steps(
    output: Path, jobs: int
) -> None:
    def fail(context: Context) -> None:
        print("diagnostics", file=context.log)
        raise GateError("broken")

    steps = [
        Step("fail", fail),
        Step("later", recorder([], "later")),
        Step("dependent", recorder([], "dependent"), ("fail",)),
    ]
    status = runner(output, steps, jobs=jobs).execute()
    assert status == {
        "fail": "failed",
        "later": "not run" if jobs == 1 else "passed",
        "dependent": "not run",
    }


def test_dependents_of_a_failed_step_never_start(output: Path) -> None:
    def fail(_context: Context) -> None:
        raise GateError("broken")

    steps = [Step("a", fail), Step("b", recorder([], "b"), ("a",))]
    assert runner(output, steps, jobs=4).execute() == {"a": "failed", "b": "not run"}


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
    assert set(runner(output, steps, jobs=4).execute().values()) == {"passed"}
    assert active[1] == 1


def test_steps_have_private_logs_and_temporary_directories(output: Path) -> None:
    seen: dict[str, str] = {}

    def note(name: str) -> Callable[[Context], None]:
        def action(context: Context) -> None:
            seen[name] = context.env["TMPDIR"]
            print(f"output of {name}", file=context.log)

        return action

    steps = [Step(name, note(name)) for name in ("a", "b", "c")]
    runner(output, steps, jobs=3).execute()
    assert len(set(seen.values())) == 3
    for name, temporary in seen.items():
        # TMPDIR is a short link in the step's runtime directory to a private
        # directory in the output.
        link = Path(temporary)
        assert link.is_symlink()
        assert link.parent.parent.parent == output / "runtime"
        assert len(link.parent.name) == 1
        path = link.resolve()
        assert path.parent == output / "t"
        assert len(path.name) == 1
        assert not path.stat().st_mode & 0o077
        assert not link.parent.stat().st_mode & 0o077
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

    runner(output, [Step("a", fail), Step("b", crash)], jobs=1).execute()
    printed = capsys.readouterr().out
    assert f"FAILED a after 0 s; log: {output / 'logs/a.log'}" in printed
    assert "    line 59\n" in printed
    assert "    line 19\n" not in printed
    assert "    error: broken" in printed
    # Steps after a failure do not start.
    assert "start  b" not in printed
    runner(output, [Step("b", crash)], jobs=1).execute()
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
        assert all(
            step.long and step.after == ("controller", "galaxy") for step in identities
        )


def test_infrastructure_tiers_wait_for_the_quick_checks_one_at_a_time() -> None:
    steps = plan(full=False, backends=["podman", "ee"], packages=[], utils_source=None)
    tiers = [step for step in steps if step.name.startswith("integration-")]
    assert [step.name for step in tiers] == ["integration-podman", "integration-ee"]
    for tier in tiers:
        assert tier.serial
        assert set(tier.after) == {
            "static",
            "galaxy",
            "unit-python3.14",
            default_lane().name,
        }


@pytest.mark.parametrize("full", [False, True])
def test_only_steps_that_install_collections_wait_for_the_download(full: bool) -> None:
    steps = plan(full=full, backends=["podman"], packages=[], utils_source=None)
    waiting = {step.name for step in steps if "galaxy" in step.after}
    assert waiting == {
        "generated-lint",
        "integration-podman",
        *[
            identity_step(profile, index)
            for profile in MODULE_PROFILES
            for index in (0, 1)
        ],
    }


def test_invalid_jobs_are_rejected_before_any_output(tmp_path: Path) -> None:
    with pytest.raises(GateError, match="at least 1"):
        check_module.check(tmp_path / "output", jobs=0)
    assert not (tmp_path / "output").exists()


def test_a_check_without_output_removes_its_temporary_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    used: list[Path] = []

    def record(output: Path, **_options: object) -> None:
        # Tools leave directories without write permission.
        (output / "locked").mkdir(parents=True)
        (output / "locked/file").write_text("kept by nobody\n", encoding="utf-8")
        (output / "locked").chmod(0o500)
        used.append(output)

    monkeypatch.setattr(check_module, "SCRATCH", tmp_path)
    monkeypatch.setattr(check_module, "check", record)
    monkeypatch.setattr(sys, "argv", ["check"])
    assert check_module.main() == 0
    assert len(used) == 1
    assert used[0].parent.parent == tmp_path
    assert not list(tmp_path.iterdir())


def test_a_check_without_output_works_on_disk() -> None:
    # The output holds container storage and several gigabytes; /tmp is often
    # a tmpfs.
    assert check_module.SCRATCH == Path("/var/tmp")


def test_passed_records_of_the_same_sources_are_reused(output: Path) -> None:
    ran: list[str] = []

    def fail_once(context: Context) -> None:
        ran.append("b")
        (context.output / "b-output").mkdir()
        if ran.count("b") == 1:
            raise GateError("interrupted")

    steps = [
        Step("a", recorder(ran, "a"), inputs="one"),
        Step("b", fail_once, owns=("b-output",)),
        Step("c", recorder(ran, "c"), ("b",)),
    ]
    first = runner(output, steps, jobs=1, source="tree").execute()
    assert first == {"a": "passed", "b": "failed", "c": "not run"}
    # The rerun replaces what the failed attempt left; "a" is not run again.
    second = runner(output, steps, jobs=1, source="tree").execute()
    assert second == {"a": "passed", "b": "passed", "c": "passed"}
    assert ran == ["a", "b", "b", "c"]
    record = json.loads((output / "records/b.json").read_text(encoding="utf-8"))
    assert {key: record[key] for key in ("step", "status", "source")} == {
        "step": "b",
        "status": "passed",
        "source": "tree",
    }


@pytest.mark.parametrize(("source", "inputs"), [("other", "one"), ("tree", "two")])
def test_records_of_other_sources_or_inputs_are_not_reused(
    output: Path, source: str, inputs: str
) -> None:
    ran: list[str] = []
    runner(
        output, [Step("a", recorder(ran, "a"), inputs="one")], jobs=1, source="tree"
    ).execute()
    step = Step("a", recorder(ran, "a"), inputs=inputs)
    assert runner(output, [step], jobs=1, source=source).execute() == {"a": "passed"}
    assert ran == ["a", "a"]


def plan_of(steps: list[Step]) -> Callable[..., list[Step]]:
    def fake(**_options: object) -> list[Step]:
        return steps

    return fake


@pytest.mark.usefixtures("unbudgeted")
def test_resume_reruns_only_what_did_not_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []

    def flaky(_context: Context) -> None:
        ran.append("flaky")
        if ran.count("flaky") == 1:
            raise GateError("network")

    steps = [
        Step("static", recorder(ran, "static"), check="static"),
        Step("flaky", flaky, check="flaky"),
    ]
    monkeypatch.setattr(check_module, "plan", plan_of(steps))
    monkeypatch.setattr(check_module, "source_digest", lambda: "tree")
    monkeypatch.setattr(
        check_module, "required_checks", lambda **_options: ["static", "flaky"]
    )
    output = tmp_path / "o"
    with pytest.raises(GateError, match="Failed: flaky"):
        check_module.check(output, jobs=1)
    assert not (output / "checks.json").exists()
    check_module.check(output, jobs=1, resume=True)
    assert ran == ["static", "flaky", "flaky"]
    checks = json.loads((output / "checks.json").read_text(encoding="utf-8"))
    assert checks["checks"] == ["static", "flaky"]


@pytest.mark.usefixtures("unbudgeted")
def test_resume_refuses_changed_sources_and_missing_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_context: Context) -> None:
        raise GateError("broken")

    monkeypatch.setattr(check_module, "plan", plan_of([Step("a", fail, check="a")]))
    monkeypatch.setattr(check_module, "source_digest", lambda: "before")
    output = tmp_path / "o"
    with pytest.raises(GateError, match="Failed: a"):
        check_module.check(output, jobs=1)
    monkeypatch.setattr(check_module, "source_digest", lambda: "after")
    with pytest.raises(GateError, match="sources differ"):
        check_module.check(output, jobs=1, resume=True)
    with pytest.raises(GateError, match="No check run to resume"):
        check_module.check(tmp_path / "missing", jobs=1, resume=True)


@pytest.mark.usefixtures("unbudgeted")
def test_sources_changed_during_a_run_cannot_be_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digests = iter(["before", "after"])
    monkeypatch.setattr(check_module, "source_digest", lambda: next(digests))
    monkeypatch.setattr(
        check_module, "plan", plan_of([Step("a", recorder([], "a"), check="a")])
    )
    monkeypatch.setattr(check_module, "required_checks", lambda **_options: ["a"])
    output = tmp_path / "o"
    check_module.check(output, jobs=1)
    assert json.loads((output / "run.json").read_text())["source"] is None


def test_source_digest_follows_content_mode_and_untracked_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    (root / "tracked.txt").write_text("one\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    digests = [source_digest(root)]
    (root / "ignored.txt").write_text("cache\n", encoding="utf-8")
    assert source_digest(root) == digests[0]
    (root / "tracked.txt").write_text("two\n", encoding="utf-8")
    digests.append(source_digest(root))
    (root / "tracked.txt").chmod(0o755)
    digests.append(source_digest(root))
    (root / "new.txt").write_text("untracked\n", encoding="utf-8")
    digests.append(source_digest(root))
    (root / "tracked.txt").unlink()
    digests.append(source_digest(root))
    assert len(set(digests)) == len(digests)


def test_exported_sources_use_the_supplied_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SKELETONS_SOURCE_DIGEST", raising=False)
    with pytest.raises(GateError, match="outside a Git checkout"):
        source_digest(tmp_path)
    monkeypatch.setenv("SKELETONS_SOURCE_DIGEST", "a" * 64)
    assert source_digest(tmp_path) == "a" * 64


@pytest.mark.parametrize(
    ("paths", "checks"),
    [
        ([], set()),
        (["role_default/tasks/main.yml.j2"], {"lanes", "generated-lint"}),
        (["collection_default/galaxy.yml.j2"], {"lanes", "generated-lint"}),
        (["collection_modules/noxfile.py.j2"], {"collection_modules"}),
        (
            ["collection_modules_appliance/FIXME.md.j2"],
            {"collection_modules_appliance"},
        ),
        (
            ["collection_modules/galaxy.yml.j2", "role_default/README.md.j2"],
            {"lanes", "generated-lint", "collection_modules"},
        ),
        (["scripts/smoke.py"], {"all"}),
        (["tests/unit/test_runner.py"], {"all"}),
        (["uv.lock"], {"all"}),
        (["README.md", "role_default/meta/main.yml.j2"], {"all"}),
    ],
)
@pytest.mark.parametrize("full", [False, True])
def test_changed_sources_select_their_checks(
    paths: list[str], checks: set[str], full: bool
) -> None:
    steps = plan(full=full, backends=[], packages=[], utils_source=None)
    lanes = {lane.name for lane in (matrix() if full else [default_lane()])}
    every = set(required_checks(full=full, backends=[]))
    always = {"static", *[check for check in every if check.startswith("unit-")]}
    expected = (
        every
        if "all" in checks
        else always | (lanes if "lanes" in checks else set()) | (checks - {"lanes"})
    )
    kept = changed_only(steps, paths)
    assert {step.check for step in kept if step.check} == expected
    # Preparation runs only for the steps that wait for it, in the plan's order.
    assert [step.name for step in kept] == [step.name for step in steps if step in kept]
    assert ("galaxy" in [step.name for step in kept]) == any(
        "galaxy" in step.after for step in kept
    )


def test_changed_paths_cover_index_working_tree_and_untracked_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    for command in (
        ["init", "--quiet"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        subprocess.run(["git", *command], cwd=root, check=True)
    for name in ("staged.txt", "edited.txt", "deleted.txt", "same.txt"):
        (root / name).write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "base"], cwd=root, check=True)
    (root / "staged.txt").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "add", "staged.txt"], cwd=root, check=True)
    (root / "edited.txt").write_text("two\n", encoding="utf-8")
    (root / "deleted.txt").unlink()
    (root / "new.txt").write_text("new\n", encoding="utf-8")
    assert changed_paths(root) == [
        "deleted.txt",
        "edited.txt",
        "new.txt",
        "staged.txt",
    ]
    with pytest.raises(GateError, match="Git checkout"):
        changed_paths(tmp_path)


@pytest.mark.usefixtures("unbudgeted")
def test_a_changed_only_run_writes_no_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ran: list[str] = []
    steps = [
        Step("static", recorder(ran, "static"), check="static"),
        Step("lint", recorder(ran, "lint"), check="generated-lint"),
        Step("module", recorder(ran, "module"), check="collection_modules"),
    ]
    monkeypatch.setattr(check_module, "plan", plan_of(steps))
    monkeypatch.setattr(check_module, "source_digest", lambda: "tree")
    monkeypatch.setattr(
        check_module, "changed_paths", lambda: ["collection_modules/galaxy.yml.j2"]
    )
    output = tmp_path / "o"
    check_module.check(output, jobs=1, changed=True)
    assert ran == ["static", "module"]
    assert not (output / "checks.json").exists()
    printed = capsys.readouterr().out
    assert "Skipped, sources unchanged: " in printed
    assert "generated-lint" in printed.split("Skipped, sources unchanged: ")[1]
    assert "Run the full quick check before committing." in printed


@pytest.mark.parametrize("options", [{"resume": True}, {"backends": ["podman"]}])
def test_changed_only_runs_neither_resume_nor_infrastructure(
    tmp_path: Path, options: dict[str, object]
) -> None:
    with pytest.raises(GateError, match="--changed"):
        check_module.check(tmp_path / "o", changed=True, **options)  # type: ignore[arg-type]
    assert not (tmp_path / "o").exists()


def fake_podman(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> list[tuple[list[str], dict[str, str]]]:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(command: list[str], **options: Any) -> str:
        calls.append((command, dict(options.get("env") or {})))
        return f"{store}\n" if command[:2] == ["podman", "info"] else ""

    monkeypatch.setattr("scripts.smoke.run", run)
    return calls


def render_with_store(output: Path, run_root: Path) -> Path:
    render = output / "collection_modules/module_fixture"
    config = render / "home/.config/containers/storage.conf"
    config.parent.mkdir(parents=True)
    config.write_text(f'[storage]\nrunroot = "{run_root}"\n', encoding="utf-8")
    return render


def test_a_rerun_removes_containers_from_the_render_store(
    output: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = output / "runtime/p0"
    run_root.mkdir()
    render = render_with_store(output, run_root)
    calls = fake_podman(monkeypatch, render / "home/.local/share/containers/storage")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/user/config")
    monkeypatch.setenv("CONTAINERS_STORAGE_CONF", "/user/storage.conf")
    remove_earlier(render, output.resolve(), io.StringIO())
    assert [command[:3] for command, _env in calls] == [
        ["podman", "info", "--format"],
        ["podman", "rm", "--all"],
    ]
    env = calls[1][1]
    assert env["HOME"] == str(render / "home")
    assert "XDG_CONFIG_HOME" not in env
    assert "CONTAINERS_STORAGE_CONF" not in env
    assert not render.exists()


def test_a_rerun_leaves_a_store_alone_whose_run_root_is_gone(
    output: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Podman would create the run root again, in a runtime directory that the
    # earlier run removed together with its containers.
    run_root = output / "runtime/sk-gone/0/p0"
    render = render_with_store(output, run_root)
    calls = fake_podman(monkeypatch, render / "home/.local/share/containers/storage")
    remove_earlier(render, output.resolve(), io.StringIO())
    assert calls == []
    assert not render.exists()
    assert not (output / "runtime/sk-gone").exists()


def test_a_rerun_never_removes_containers_from_another_store(
    output: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = output / "runtime/p0"
    run_root.mkdir()
    render = render_with_store(output, run_root)
    calls = fake_podman(monkeypatch, Path("/home/user/.local/share/containers/storage"))
    with pytest.raises(GateError, match="own store"):
        remove_earlier(render, output.resolve(), io.StringIO())
    assert [command[:2] for command, _env in calls] == [["podman", "info"]]
    assert render.exists()


def test_an_interrupted_check_says_how_to_continue(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupt(_output: Path, **_options: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(check_module, "check", interrupt)
    monkeypatch.setattr(sys, "argv", ["check", "--output", "/unused"])
    assert check_module.main() == 130
    assert "--resume" in capsys.readouterr().err
