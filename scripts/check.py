# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run local quality checks, optionally expanding to the release matrix."""

import argparse
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TextIO

from scripts.common import (
    ROOT,
    GateError,
    Lane,
    configuration,
    digest,
    matrix,
    require_tools,
    run,
    uv_command,
    write_json,
)
from scripts.controller import (
    NOX_LAYOUT,
    SSH_CONTROL_LAYOUT,
    STEP_SLOTS,
    check_socket_layout,
    controller_environment,
)
from scripts.render import MODULE_PROFILES
from scripts.smoke import IDENTITIES


def default_lane() -> Lane:
    """Select the documented development lane from the shared configuration."""
    config = configuration()
    return Lane(str(config["default-python"]), str(config["default-group"]))


def default_jobs() -> int:
    """Run half as many steps at a time as there are CPUs, at least one."""
    return max(1, (os.cpu_count() or 2) // 2)


def check_static(log: TextIO | None = None, env: dict[str, str] | None = None) -> None:
    """Run formatting, typing, documentation, licensing and shell checks."""
    require_tools("uv", "shfmt", "shellcheck", "checkbashisms", "sh")
    sources = [
        "scripts",
        "tests",
        "collection_default/noxfile.py.j2",
        *[f"{profile}/noxfile.py.j2" for profile in MODULE_PROFILES],
        *[
            f"{profile}/tests/runtime_check.py.j2"
            for profile in ("collection_default", *MODULE_PROFILES)
        ],
    ]
    checked = partial(run, log=log, env=env)
    checked(["ruff", "format", "--check", "--extension", "j2:python", *sources])
    checked(["ruff", "check", "--extension", "j2:python", *sources])
    checked(["mypy", "scripts", "tests"])
    checked(
        [
            "rumdl",
            "check",
            "--config",
            ".rumdl.toml",
            "--deny-config-warnings",
            "--no-cache",
            "--exclude",
            "tests/fixtures",
            ".",
        ]
    )
    checked(["reuse", "lint"])
    for script in sorted((ROOT / "scripts").glob("*.sh")):
        checked(
            [
                "shfmt",
                "--language-dialect",
                "posix",
                "--indent",
                "2",
                "--case-indent",
                "--binary-next-line",
                "--simplify",
                "--diff",
                str(script),
            ]
        )
        checked(
            [
                "shellcheck",
                "--shell=sh",
                "--severity=style",
                "--exclude=SC2292",
                "--exclude=SC3040",
                "--exclude=SC3043",
                "--enable=all",
                str(script),
            ]
        )
        checked(["checkbashisms", str(script)])
        checked(["sh", "-n", str(script)])


def require_complete_tests(path: Path) -> None:
    """Reject empty, failed or skipped required test suites."""
    root = ET.parse(path).getroot()
    suites = list(root.iter("testsuite"))
    if not suites or sum(int(suite.get("tests", "0")) for suite in suites) == 0:
        raise GateError("The required unit suite executed no tests")
    if any(
        int(suite.get(key, "0"))
        for suite in suites
        for key in ("failures", "errors", "skipped")
    ):
        raise GateError("Required unit tests failed or were skipped")


def python_versions(*, full: bool) -> list[str]:
    """Return the controller Pythons of the helper checks."""
    config = configuration()
    pythons = config["python-versions"] if full else [str(config["default-python"])]
    if not isinstance(pythons, list) or not all(
        isinstance(item, str) for item in pythons
    ):
        raise GateError("Invalid helper Python matrix")
    return pythons


def required_checks(*, full: bool, backends: list[str]) -> list[str]:
    """List all stages that must succeed for a given invocation."""
    lanes = matrix() if full else [default_lane()]
    return [
        "static",
        *[f"unit-python{python}" for python in python_versions(full=full)],
        *[lane.name for lane in lanes],
        "generated-lint",
        *MODULE_PROFILES,
        *[f"integration-{backend}" for backend in backends],
    ]


def check_output_layout(output: Path, backends: list[str]) -> None:
    """Budget every socket a check binds below its output, before any work."""
    check_socket_layout(output, NOX_LAYOUT)
    for backend in backends:
        check_socket_layout(output / f"integration-{backend}", SSH_CONTROL_LAYOUT)


@dataclass(frozen=True)
class Context:
    """What a running step uses: the run's output, its own log and environment."""

    output: Path
    log: TextIO
    env: dict[str, str]


@dataclass(frozen=True)
class Step:
    """One unit of a check run, with its own log and temporary directory."""

    name: str
    action: Callable[[Context], None]
    # Steps that must pass before this one starts.
    after: tuple[str, ...] = ()
    # The required check this step completes, together with any other steps
    # that name the same check.
    check: str | None = None
    # Infrastructure tiers share Podman and libvirt state: one at a time.
    serial: bool = False
    # Long steps start first when several steps run at a time.
    long: bool = False


def identity_step(profile: str, index: int) -> str:
    """Name the step of one module identity."""
    return f"{profile}-identity{index + 1}"


def check_controllers(
    context: Context, runtime_output: Path, *, pythons: list[str]
) -> None:
    """Start a controller in every admitted Python below one runtime output."""
    for python in pythons:
        run(
            [
                "uv",
                "run",
                "--locked",
                "--isolated",
                "--python",
                python,
                "python",
                "-m",
                "scripts.controller",
                "--output",
                str(runtime_output),
            ],
            env=context.env,
            log=context.log,
        )


def unit_tests(context: Context, *, python: str) -> None:
    """Run the repository's regression tests in one controller Python."""
    result = context.output / f"unit-python{python}.xml"
    # The other Pythons' runs proceed at the same time; none writes a cache.
    run(
        [
            "uv",
            "run",
            "--locked",
            "--isolated",
            "--python",
            python,
            "python",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={result}",
        ],
        env=context.env,
        log=context.log,
    )
    require_complete_tests(result)


def lane_smoke(context: Context, *, lane: Lane) -> None:
    """Render, build and exercise the role and collection in one lane."""
    output = context.output / lane.name
    run(
        uv_command(lane, "python", "-m", "scripts.smoke", "--output", str(output)),
        timeout=1800,
        env=context.env,
        log=context.log,
    )


def generated_lint(context: Context, *, packages: list[str]) -> None:
    """Lint the renders with the foundata rules and run their default sessions."""
    run(
        uv_command(
            default_lane(),
            "python",
            "-m",
            "scripts.smoke",
            "--lint",
            "--output",
            str(context.output / "generated-lint"),
            groups=["lint"],
            packages=packages,
        ),
        timeout=1800,
        env=context.env,
        log=context.log,
    )


def module_identity(
    context: Context,
    *,
    profile: str,
    index: int,
    full: bool,
    utils_source: Path | None,
) -> None:
    """Check one identity of a module skeleton."""
    run(
        uv_command(
            default_lane(),
            "python",
            "-m",
            "scripts.smoke",
            "--profile",
            profile,
            "--identity",
            str(index),
            *(["--utils-source", str(utils_source.resolve())] if utils_source else []),
            "--output",
            str(context.output / profile),
            *(["--ansible-tests"] if full else []),
        ),
        timeout=21600,
        env=context.env,
        log=context.log,
    )


def integration_tier(
    context: Context, *, backend: str, packages: list[str], pythons: list[str]
) -> None:
    """Run one infrastructure tier of the generated Molecule scenarios."""
    output = context.output / f"integration-{backend}"
    check_controllers(context, output, pythons=pythons)
    evidence = context.output / "controller-checks" / output.name
    evidence.mkdir(parents=True)
    for record in output.glob("controller-python*.json"):
        record.rename(evidence / record.name)
    # Keep the tier's exclusive output-directory creation check.
    (output / "t").rmdir()
    output.rmdir()
    run(
        uv_command(
            default_lane(),
            "python",
            "-m",
            "scripts.integration",
            backend,
            "--output",
            str(output),
            groups=["integration", "lint"],
            packages=packages,
        ),
        timeout=7200,
        env=context.env,
        log=context.log,
    )


def plan(
    *,
    full: bool,
    backends: list[str],
    packages: list[str],
    utils_source: Path | None,
) -> list[Step]:
    """List the steps of a run in the order a single job runs them."""
    pythons = python_versions(full=full)
    units = [f"unit-python{python}" for python in pythons]
    steps = [
        Step(
            "static",
            lambda context: check_static(context.log, context.env),
            check="static",
        ),
        Step(
            "controller",
            lambda context: check_controllers(context, context.output, pythons=pythons),
        ),
    ]
    for python, name in zip(pythons, units, strict=True):
        steps.append(
            Step(name, partial(unit_tests, python=python), ("controller",), name)
        )
    for lane in matrix() if full else [default_lane()]:
        steps.append(
            Step(lane.name, partial(lane_smoke, lane=lane), ("controller",), lane.name)
        )
    steps.append(
        Step(
            "generated-lint",
            partial(generated_lint, packages=packages),
            ("controller",),
            "generated-lint",
            long=True,
        )
    )
    for profile in MODULE_PROFILES:
        for index in range(len(IDENTITIES)):
            steps.append(
                Step(
                    identity_step(profile, index),
                    partial(
                        module_identity,
                        profile=profile,
                        index=index,
                        full=full,
                        utils_source=utils_source,
                    ),
                    ("controller",),
                    profile,
                    long=True,
                )
            )
    # Infrastructure tiers wait for the checks that would fail them anyway.
    for backend in backends:
        name = f"integration-{backend}"
        steps.append(
            Step(
                name,
                partial(
                    integration_tier,
                    backend=backend,
                    packages=packages,
                    pythons=pythons,
                ),
                ("static", *units, default_lane().name),
                name,
                serial=True,
            )
        )
    known: set[str] = set()
    for step in steps:
        if step.name in known or not known.issuperset(step.after):
            raise GateError(f"Invalid check plan at {step.name}")
        known.add(step.name)
    if len(steps) > len(STEP_SLOTS):
        raise GateError("The check plan has more steps than temporary directories")
    return steps


def duration(seconds: float) -> str:
    """Format a step's wall time for the progress lines."""
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


class Runner:
    """Run steps in dependency order, a limited number at a time."""

    def __init__(self, output: Path, steps: Sequence[Step], *, jobs: int) -> None:
        self.output = output
        self.steps = list(steps)
        self.jobs = jobs
        self.status: dict[str, str] = {}
        self.lock = threading.Lock()

    def say(self, message: str) -> None:
        """Print one progress line; steps finish in any order."""
        with self.lock:
            print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)

    def run_step(self, step: Step) -> bool:
        """Run one step with its own log and TMPDIR; report its outcome."""
        log_path = self.output / "logs" / f"{step.name}.log"
        temporary = self.output / "t" / STEP_SLOTS[self.steps.index(step)]
        temporary.mkdir(mode=0o700, exist_ok=True)
        env = {**os.environ, "TMPDIR": str(temporary)}
        self.say(f"start  {step.name}")
        began = time.monotonic()
        with log_path.open("a", encoding="utf-8") as log:
            try:
                step.action(Context(self.output, log, env))
                passed = True
            # Any exception fails this step alone; the run reports it.
            except Exception as exc:
                if not isinstance(exc, GateError | OSError | ValueError):
                    traceback.print_exc(file=log)
                print(f"error: {exc}", file=log, flush=True)
                passed = False
        took = duration(time.monotonic() - began)
        if passed:
            self.say(f"passed {step.name} in {took}")
        else:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(f"    {line}" for line in lines[-40:])
            self.say(f"FAILED {step.name} after {took}; log: {log_path}\n{tail}")
        return passed

    def runnable(self, step: Step, running: Sequence[Step]) -> bool:
        """Whether a waiting step may start now."""
        if any(self.status.get(name) != "passed" for name in step.after):
            return False
        return not (step.serial and any(other.serial for other in running))

    def execute(self) -> dict[str, str]:
        """Run every step that can run; return each step's status."""
        (self.output / "logs").mkdir(exist_ok=True)
        # One job keeps the plan's order. More jobs start long steps first.
        order = (
            self.steps
            if self.jobs == 1
            else sorted(self.steps, key=lambda step: not step.long)
        )
        waiting = [step for step in order if step.name not in self.status]
        running: dict[Future[bool], Step] = {}
        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            while True:
                # After a failure no step starts; running steps still finish.
                if "failed" not in self.status.values():
                    for step in list(waiting):
                        if len(running) >= self.jobs:
                            break
                        if self.runnable(step, list(running.values())):
                            waiting.remove(step)
                            running[pool.submit(self.run_step, step)] = step
                if not running:
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    step = running.pop(future)
                    self.status[step.name] = "passed" if future.result() else "failed"
        return {step.name: self.status.get(step.name, "not run") for step in self.steps}


def completed_checks(steps: Sequence[Step], status: dict[str, str]) -> list[str]:
    """List the checks whose steps all passed, in the plan's order."""
    checks: list[str] = []
    for step in steps:
        if step.check is None or step.check in checks:
            continue
        if all(
            status[other.name] == "passed"
            for other in steps
            if other.check == step.check
        ):
            checks.append(step.check)
    return checks


def check(
    output: Path,
    *,
    full: bool = False,
    backends: list[str] | None = None,
    foundata_wheel: Path | None = None,
    utils_source: Path | None = None,
    jobs: int | None = None,
) -> None:
    """Run required stages and emit a complete report only after all succeed."""
    selected = backends or []
    if foundata_wheel is not None and (
        not foundata_wheel.is_file() or foundata_wheel.suffix != ".whl"
    ):
        raise GateError("--foundata-wheel must name an existing wheel file")
    jobs = default_jobs() if jobs is None else jobs
    if jobs < 1:
        raise GateError("--jobs must be at least 1")
    packages = [str(foundata_wheel.resolve())] if foundata_wheel else []
    check_output_layout(output, selected)
    steps = plan(
        full=full, backends=selected, packages=packages, utils_source=utils_source
    )
    output.mkdir(parents=True, exist_ok=False)
    controller_environment(output, {})
    began = time.monotonic()
    print(
        f"Running {len(steps)} steps, up to {jobs} at a time; "
        f"logs in {output / 'logs'}",
        flush=True,
    )
    status = Runner(output, steps, jobs=jobs).execute()
    failed = [name for name, outcome in status.items() if outcome == "failed"]
    if failed:
        raise GateError(f"Failed: {', '.join(failed)}; logs in {output / 'logs'}")
    checks = completed_checks(steps, status)
    if checks != required_checks(full=full, backends=selected):
        raise GateError("Incomplete check run")
    for profile in MODULE_PROFILES:
        write_json(
            output / profile / "profile.json", {"profile": profile, "full": full}
        )
    artifacts = {
        str(path.relative_to(output)): digest(path)
        for path in output.rglob("artifacts/*.tar.gz")
    }
    write_json(
        output / "checks.json",
        {
            "checks": checks,
            "artifacts": artifacts,
            "foundata-wheel": (
                {"name": foundata_wheel.name, "sha256": digest(foundata_wheel)}
                if foundata_wheel
                else None
            ),
            "environments": {
                path.parent.name: json.loads(path.read_text(encoding="utf-8"))
                for path in output.glob("*/environment.json")
            },
            "python": platform.python_version(),
            "tools": {
                name: importlib.metadata.version(name)
                for name in ("ruff", "mypy", "pytest", "reuse", "rumdl")
            },
        },
    )
    print(
        f"All checks passed in {duration(time.monotonic() - began)}. Results: {output}"
    )


def main() -> int:
    """Run checks in retained output or automatically cleaned temporary output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--matrix", action="store_true", help="Run every supported combination"
    )
    parser.add_argument(
        "--integration",
        action="append",
        choices=["podman", "libvirt", "ee"],
        default=[],
    )
    parser.add_argument("--output", type=Path, help="Retain results in a new directory")
    parser.add_argument(
        "--jobs",
        type=int,
        default=default_jobs(),
        help="Steps to run at a time (default: %(default)s); 1 runs them in order",
    )
    parser.add_argument(
        "--foundata-wheel",
        type=Path,
        help="Override the released extension with a local wheel",
    )
    parser.add_argument(
        "--utils-source",
        type=Path,
        default=os.environ.get("SKELETONS_UTILS_SOURCE"),
        help="Local foundata.utils source to test instead of the pinned release",
    )
    args = parser.parse_args()
    run_check = partial(
        check,
        full=args.matrix,
        backends=args.integration,
        foundata_wheel=args.foundata_wheel,
        utils_source=args.utils_source,
        jobs=args.jobs,
    )
    try:
        if args.output:
            run_check(args.output.resolve())
        else:
            # A short name keeps the temporary output within the socket budget.
            with tempfile.TemporaryDirectory(prefix="sk-") as temporary:
                run_check(Path(temporary).resolve() / "o")
    except (GateError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
