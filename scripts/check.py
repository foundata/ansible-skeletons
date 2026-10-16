# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run local quality checks, optionally expanding to the release matrix."""

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TextIO

from scripts.common import (
    ROOT,
    GateError,
    Lane,
    configuration,
    digest,
    isolated_environment,
    matrix,
    require_tools,
    run,
    source_digest,
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

# Source paths, and the checks a change below them needs besides the static
# checks and the repository tests. "lanes" are the role and collection lanes.
# A change anywhere else, in scripts/, tests/, pyproject.toml, uv.lock or a
# shared file, needs every check.
CHANGED_SCOPES = {
    "role_default/": ("lanes", "generated-lint"),
    "collection_default/": ("lanes", "generated-lint"),
    "collection_modules/": ("collection_modules",),
    "collection_modules_appliance/": ("collection_modules_appliance",),
}


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
    # Output paths, as globs below the run's output, that a rerun replaces.
    owns: tuple[str, ...] = ()
    # Options besides the sources that decide this step's result. A record
    # with other inputs is not reused.
    inputs: str = ""


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


def download_collections(context: Context) -> None:
    """Download the pinned Galaxy collections once for every step of the run."""
    work = context.output / "galaxy"
    env = isolated_environment(work)
    # Ansible keeps to its isolated configuration; uv keeps its own cache.
    for name in ("HOME", "XDG_CACHE_HOME"):
        if name in context.env:
            env[name] = context.env[name]
        else:
            env.pop(name, None)
    env["TMPDIR"] = context.env["TMPDIR"]
    run(
        uv_command(
            default_lane(),
            "ansible-galaxy",
            "collection",
            "download",
            "--no-deps",
            "--requirements-file",
            str(ROOT / "tests/requirements.yml"),
            "--download-path",
            str(collections_path(context.output)),
        ),
        env=env,
        log=context.log,
    )


def collections_path(output: Path) -> Path:
    """Return where a run keeps its downloads of the pinned Galaxy collections."""
    return output / "galaxy/collections"


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
            "--collections",
            str(collections_path(context.output)),
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
            "--collections",
            str(collections_path(context.output)),
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
            "--collections",
            str(collections_path(context.output)),
            "--output",
            str(output),
            groups=["integration", "lint"],
            packages=packages,
        ),
        timeout=7200,
        env=context.env,
        log=context.log,
    )


def utils_identity(source: Path | None) -> str | None:
    """Identify a local foundata.utils source; one outside Git is never reused."""
    if source is None:
        return None
    if (source / ".git").exists():
        return source_digest(source.resolve())
    return f"unversioned {time.time_ns()}"


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
    wheels = json.dumps([digest(Path(package)) for package in packages])
    steps = [
        Step(
            "static",
            lambda context: check_static(context.log, context.env),
            check="static",
        ),
        Step(
            "controller",
            lambda context: check_controllers(context, context.output, pythons=pythons),
            owns=("controller-python*.json",),
            inputs=json.dumps(pythons),
        ),
        Step("galaxy", download_collections, owns=("galaxy",)),
    ]
    for python, name in zip(pythons, units, strict=True):
        steps.append(
            Step(
                name,
                partial(unit_tests, python=python),
                ("controller",),
                name,
                owns=(f"{name}.xml",),
            )
        )
    for lane in matrix() if full else [default_lane()]:
        steps.append(
            Step(
                lane.name,
                partial(lane_smoke, lane=lane),
                ("controller",),
                lane.name,
                owns=(lane.name,),
            )
        )
    steps.append(
        Step(
            "generated-lint",
            partial(generated_lint, packages=packages),
            ("controller", "galaxy"),
            "generated-lint",
            long=True,
            owns=("generated-lint",),
            inputs=wheels,
        )
    )
    utils = json.dumps({"full": full, "utils-source": utils_identity(utils_source)})
    for profile in MODULE_PROFILES:
        for index, (_namespace, name) in enumerate(IDENTITIES):
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
                    ("controller", "galaxy"),
                    profile,
                    long=True,
                    owns=(
                        f"{profile}/{name}",
                        *([f"{profile}/missing-metadata"] if index == 0 else []),
                    ),
                    inputs=utils,
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
                ("static", "galaxy", *units, default_lane().name),
                name,
                serial=True,
                owns=(name, f"controller-checks/{name}"),
                inputs=wheels,
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


def changed_paths(root: Path = ROOT) -> list[str]:
    """List the paths that differ from HEAD in the index or the working tree."""
    if not (root / ".git").exists():
        raise GateError("--changed needs a Git checkout")
    names: set[str] = set()
    for command in (
        ["git", "diff", "--name-only", "--no-renames", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        listing = subprocess.run(
            command, cwd=root, check=True, capture_output=True, text=True, timeout=60
        )
        names.update(listing.stdout.splitlines())
    return sorted(names)


def changed_only(steps: list[Step], paths: list[str]) -> list[Step]:
    """Keep the steps that changes below these paths need, in the plan's order.

    The static checks and the repository tests always run.
    """
    lanes = {lane.name for lane in matrix()}
    needed = {"static", *[step.name for step in steps if step.name.startswith("unit-")]}
    for path in paths:
        scope = next(
            (
                checks
                for prefix, checks in CHANGED_SCOPES.items()
                if path.startswith(prefix)
            ),
            None,
        )
        if scope is None:
            return steps
        for check in scope:
            needed.update(lanes if check == "lanes" else {check})
    kept = [step for step in steps if step.check is None or step.check in needed]
    # Preparation steps run only for the steps that wait for them.
    waited = {name for step in kept for name in step.after}
    return [step for step in kept if step.check is not None or step.name in waited]


def duration(seconds: float) -> str:
    """Format a step's wall time for the progress lines."""
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def remove_earlier(path: Path, output: Path, log: TextIO) -> None:
    """Remove what an earlier attempt of a step left below the run's output."""
    if not path.exists() and not path.is_symlink():
        return
    resolved = path.resolve()
    if path.is_symlink() or resolved == output or not resolved.is_relative_to(output):
        raise GateError(f"Refusing to remove {path} before a rerun")
    print(f"Removing the earlier attempt's {path}", file=log, flush=True)
    if path.is_file():
        path.unlink()
        return
    try:
        shutil.rmtree(path)
    except PermissionError:
        # Podman stores below a render hold files of subordinate users.
        run(["podman", "unshare", "rm", "-rf", "--", str(path)], log=log)


class Runner:
    """Run steps in dependency order, a limited number at a time.

    Every finished step leaves a record bound to the digest of the sources it
    checked. A passed record with the same sources and inputs is reused.
    """

    def __init__(
        self, output: Path, steps: Sequence[Step], *, jobs: int, source: str = ""
    ) -> None:
        self.output = output
        self.steps = list(steps)
        self.jobs = jobs
        self.source = source
        self.status: dict[str, str] = {}
        self.lock = threading.Lock()

    def record(self, step: Step) -> Path:
        """Return the path of a step's record."""
        return self.output / "records" / f"{step.name}.json"

    def reusable(self, step: Step) -> dict[str, object] | None:
        """Return a step's passed record for these sources and inputs, if any."""
        try:
            record = json.loads(self.record(step).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(record, dict):
            return None
        if (record.get("status"), record.get("source"), record.get("inputs")) != (
            "passed",
            self.source,
            step.inputs,
        ):
            return None
        return record

    def say(self, message: str) -> None:
        """Print one progress line; steps finish in any order."""
        with self.lock:
            print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)

    def run_step(self, step: Step) -> bool:
        """Run one step with its own log and TMPDIR; record its outcome."""
        log_path = self.output / "logs" / f"{step.name}.log"
        temporary = self.output / "t" / STEP_SLOTS[self.steps.index(step)]
        env = {**os.environ, "TMPDIR": str(temporary)}
        self.record(step).unlink(missing_ok=True)
        self.say(f"start  {step.name}")
        began = time.monotonic()
        with log_path.open("a", encoding="utf-8") as log:
            try:
                for pattern in step.owns:
                    for path in sorted(self.output.glob(pattern)):
                        remove_earlier(path, self.output, log)
                remove_earlier(temporary, self.output, log)
                temporary.mkdir(mode=0o700)
                step.action(Context(self.output, log, env))
                passed = True
            # Any exception fails this step alone; the run reports it.
            except Exception as exc:
                if not isinstance(exc, GateError | OSError | ValueError):
                    traceback.print_exc(file=log)
                print(f"error: {exc}", file=log, flush=True)
                passed = False
        seconds = time.monotonic() - began
        write_json(
            self.record(step),
            {
                "step": step.name,
                "status": "passed" if passed else "failed",
                "source": self.source,
                "inputs": step.inputs,
                "seconds": round(seconds, 1),
                "finished": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        )
        took = duration(seconds)
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

    def order(self) -> list[Step]:
        """One job keeps the plan's order. More jobs start long steps first."""
        if self.jobs == 1:
            return list(self.steps)
        return sorted(self.steps, key=lambda step: not step.long)

    def execute(self) -> dict[str, str]:
        """Run every step that can run; return each step's status."""
        (self.output / "logs").mkdir(exist_ok=True)
        (self.output / "records").mkdir(exist_ok=True)
        for step in self.steps:
            record = self.reusable(step)
            if record is not None:
                self.status[step.name] = "passed"
                seconds = record.get("seconds")
                took = duration(seconds) if isinstance(seconds, int | float) else "?"
                self.say(
                    f"reused {step.name}, passed in {took} at {record.get('finished')}"
                )
        waiting = [step for step in self.order() if step.name not in self.status]
        running: dict[Future[bool], Step] = {}
        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            while True:
                # One job stops at the first failure. Several jobs start every
                # step whose prerequisites passed, so a resume reruns little.
                if self.jobs > 1 or "failed" not in self.status.values():
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
    resume: bool = False,
    changed: bool = False,
) -> None:
    """Run required stages and emit a complete report only after all succeed.

    With ``resume``, the existing output's passed records of the same sources
    are reused and every other step runs. With ``changed``, only the steps
    that the sources changed since HEAD need run, and no report is written.
    """
    if changed and (resume or backends):
        raise GateError("--changed combines with neither --resume nor --integration")
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
    if changed:
        paths = changed_paths()
        kept = changed_only(steps, paths)
        skipped = [
            check
            for check in required_checks(full=full, backends=selected)
            if check not in {step.check for step in kept}
        ]
        print(
            f"{len(paths)} paths differ from HEAD. Skipped, sources unchanged: "
            f"{', '.join(skipped) or 'nothing'}",
            flush=True,
        )
        steps = kept
    source = source_digest()
    if resume:
        try:
            earlier = json.loads((output / "run.json").read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise GateError(f"No check run to resume in {output}") from exc
        if earlier.get("source") != source:
            raise GateError(
                "The sources differ from the run being resumed; "
                "check them into a new output"
            )
        (output / "checks.json").unlink(missing_ok=True)
    else:
        output.mkdir(parents=True, exist_ok=False)
    controller_environment(output, {})
    write_json(
        output / "run.json",
        {"source": source, "full": full, "backends": selected, "changed": changed},
    )
    began = time.monotonic()
    print(
        f"Running {len(steps)} steps, up to {jobs} at a time; "
        f"logs in {output / 'logs'}",
        flush=True,
    )
    try:
        status = Runner(output, steps, jobs=jobs, source=source).execute()
    finally:
        if source_digest() != source:
            # Records no longer say which sources their steps checked.
            write_json(
                output / "run.json",
                {
                    "source": None,
                    "full": full,
                    "backends": selected,
                    "changed": changed,
                },
            )
            print(
                "warning: the sources changed during the run; it cannot be resumed",
                file=sys.stderr,
            )
    failed = [name for name, outcome in status.items() if outcome == "failed"]
    if failed:
        raise GateError(f"Failed: {', '.join(failed)}; logs in {output / 'logs'}")
    checks = completed_checks(steps, status)
    if changed:
        # A partial run never writes a passing report.
        print(
            f"Changed-only checks passed in {duration(time.monotonic() - began)}. "
            "Run the full quick check before committing."
        )
        return
    if checks != required_checks(full=full, backends=selected):
        raise GateError("Incomplete check run")
    for profile in MODULE_PROFILES:
        if (output / profile).is_dir():
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
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument(
        "--output", type=Path, help="Retain results in a new directory"
    )
    destination.add_argument(
        "--resume",
        type=Path,
        metavar="OUTPUT",
        help="Rerun the failed and missing steps of a retained output",
    )
    parser.add_argument(
        "--changed",
        action="store_true",
        help="Check only what the changes since HEAD affect; writes no report",
    )
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
        changed=args.changed,
    )
    try:
        if args.resume:
            run_check(args.resume.resolve(), resume=True)
        elif args.output:
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
