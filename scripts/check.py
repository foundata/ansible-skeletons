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
import xml.etree.ElementTree as ET
from pathlib import Path

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
from scripts.controller import check_socket_layout, controller_environment
from scripts.render import MODULE_PROFILES


def default_lane() -> Lane:
    """Select the documented development lane from the shared configuration."""
    config = configuration()
    return Lane(str(config["default-python"]), str(config["default-group"]))


def check_static() -> None:
    """Run formatting, typing, documentation, licensing and shell checks."""
    require_tools("uv", "shfmt", "shellcheck", "checkbashisms", "sh")
    sources = [
        "scripts",
        "tests",
        "collection_default/noxfile.py.j2",
        *[f"{profile}/noxfile.py.j2" for profile in MODULE_PROFILES],
    ]
    run(["ruff", "format", "--check", "--extension", "j2:python", *sources])
    run(["ruff", "check", "--extension", "j2:python", *sources])
    run(["mypy", "scripts", "tests"])
    run(
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
    run(["reuse", "lint"])
    for script in sorted((ROOT / "scripts").glob("*.sh")):
        run(
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
        run(
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
        run(["checkbashisms", str(script)])
        run(["sh", "-n", str(script)])


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


def required_checks(*, full: bool, backends: list[str]) -> list[str]:
    """List all stages that must succeed for a given invocation."""
    config = configuration()
    pythons = config["python-versions"] if full else [str(config["default-python"])]
    if not isinstance(pythons, list) or not all(
        isinstance(item, str) for item in pythons
    ):
        raise GateError("Invalid helper Python matrix")
    lanes = matrix() if full else [default_lane()]
    return [
        "static",
        *[f"unit-python{python}" for python in pythons],
        *[lane.name for lane in lanes],
        "generated-lint",
        *MODULE_PROFILES,
        *[f"integration-{backend}" for backend in backends],
    ]


def check(
    output: Path,
    *,
    full: bool = False,
    backends: list[str] | None = None,
    foundata_wheel: Path | None = None,
    utils_source: Path | None = None,
) -> None:
    """Run required stages and emit a complete report only after all succeed."""
    selected = backends or []
    if foundata_wheel is not None and (
        not foundata_wheel.is_file() or foundata_wheel.suffix != ".whl"
    ):
        raise GateError("--foundata-wheel must name an existing wheel file")
    packages = [str(foundata_wheel.resolve())] if foundata_wheel else []
    if utils_source is None:
        raise GateError(
            "Module profile checks need --utils-source for unpublished foundata.utils"
        )
    runtime_outputs = [
        output,
        *[output / f"integration-{backend}" for backend in selected],
    ]
    for runtime_output in runtime_outputs:
        check_socket_layout(runtime_output)
    output.mkdir(parents=True, exist_ok=False)
    checks: list[str] = []
    check_static()
    checks.append("static")
    config = configuration()
    pythons = config["python-versions"] if full else [str(config["default-python"])]
    if not isinstance(pythons, list):
        raise GateError("Invalid helper Python matrix")
    os.environ["TMPDIR"] = controller_environment(output, dict(os.environ))["TMPDIR"]
    for runtime_output in runtime_outputs:
        for python in pythons:
            run(
                [
                    "uv",
                    "run",
                    "--locked",
                    "--isolated",
                    "--python",
                    str(python),
                    "python",
                    "-m",
                    "scripts.controller",
                    "--output",
                    str(runtime_output),
                ]
            )
        if runtime_output != output:
            evidence = output / "controller-checks" / runtime_output.name
            evidence.mkdir(parents=True)
            for record in runtime_output.glob("controller-python*.json"):
                record.rename(evidence / record.name)
            # Keep the tier's exclusive output-directory creation check.
            (runtime_output / "t").rmdir()
            runtime_output.rmdir()
    for python in pythons:
        result = output / f"unit-python{python}.xml"
        run(
            [
                "uv",
                "run",
                "--locked",
                "--isolated",
                "--python",
                str(python),
                "python",
                "-m",
                "pytest",
                "-q",
                f"--junitxml={result}",
            ]
        )
        require_complete_tests(result)
        checks.append(f"unit-python{python}")
    for lane in matrix() if full else [default_lane()]:
        run(
            uv_command(
                lane,
                "python",
                "-m",
                "scripts.smoke",
                "--output",
                str(output / lane.name),
            ),
            timeout=1800,
        )
        checks.append(lane.name)
    run(
        uv_command(
            default_lane(),
            "python",
            "-m",
            "scripts.smoke",
            "--lint",
            "--output",
            str(output / "generated-lint"),
            groups=["lint"],
            packages=packages,
        ),
        timeout=1800,
    )
    checks.append("generated-lint")
    for profile in MODULE_PROFILES:
        run(
            uv_command(
                default_lane(),
                "python",
                "-m",
                "scripts.smoke",
                "--profile",
                profile,
                "--utils-source",
                str(utils_source.resolve()),
                "--output",
                str(output / profile),
                *(["--ansible-tests"] if full else []),
            ),
            timeout=21600,
        )
        checks.append(profile)
    for backend in selected:
        run(
            uv_command(
                default_lane(),
                "python",
                "-m",
                "scripts.integration",
                backend,
                "--output",
                str(output / f"integration-{backend}"),
                groups=["integration", "lint"],
                packages=packages,
            ),
            timeout=7200,
        )
        checks.append(f"integration-{backend}")
    expected = required_checks(full=full, backends=selected)
    if checks != expected:
        raise GateError("Incomplete check run")
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
    print(f"All checks passed. Results: {output}")


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
        "--foundata-wheel",
        type=Path,
        help="Override the released extension with a local wheel",
    )
    parser.add_argument(
        "--utils-source",
        type=Path,
        default=os.environ.get("SKELETONS_UTILS_SOURCE"),
        help="Local source of unpublished foundata.utils",
    )
    args = parser.parse_args()
    try:
        if args.output:
            check(
                args.output.resolve(),
                full=args.matrix,
                backends=args.integration,
                foundata_wheel=args.foundata_wheel,
                utils_source=args.utils_source,
            )
        else:
            with tempfile.TemporaryDirectory(
                prefix="ansible-skeletons-check-"
            ) as temporary:
                check(
                    Path(temporary) / "results",
                    full=args.matrix,
                    backends=args.integration,
                    foundata_wheel=args.foundata_wheel,
                    utils_source=args.utils_source,
                )
    except (GateError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
