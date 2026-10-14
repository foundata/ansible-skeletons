# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Destructive Podman cleanup requires proof of private runtime paths."""

import json
import os
import shlex
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts.common import ROOT, GateError, run
from scripts.controller import (
    NOX_LAYOUT,
    SOCKET_LIMIT,
    SSH_CONTROL_LAYOUT,
    check_socket_layout,
    controller_environment,
)
from scripts.integration import molecule_command, podman_environment, reset_podman


@pytest.mark.parametrize(
    "readme",
    [
        "role_default/molecule/README.md.j2",
        "collection_default/extensions/molecule/README.md.j2",
    ],
)
def test_documented_ee_command_matches_execution(readme: str) -> None:
    commands = [
        shlex.split(line)
        for line in (ROOT / readme).read_text().splitlines()
        if line.startswith("molecule test ") and '"ee"' in line
    ]
    assert commands == [molecule_command("test", "ee")]


@pytest.fixture
def private_store() -> Iterator[tuple[Path, dict[str, str]]]:
    # Keep Unix sockets below the platform limit, including in deep checkouts.
    with tempfile.TemporaryDirectory(prefix="p-") as directory:
        root = Path(directory)
        yield root, podman_environment(root, {})


def info(root: Path) -> tuple[dict[str, object], dict[str, str]]:
    data: dict[str, object] = {
        "store": {
            "configFile": str(root / "podman/storage.conf"),
            "graphRoot": str(root / "podman/graph"),
            "runRoot": str(root / "podman/run"),
            "volumePath": str(root / "podman/graph/volumes"),
            "imageCopyTmpDir": str(root / "podman/tmp"),
        },
        "host": {
            "serviceIsRemote": False,
            "remoteSocket": {"path": str(root / "r/podman/podman.sock")},
            "ociRuntime": {"version": f"crun version 1\nrundir: {root}/r/crun"},
        },
    }
    debug = {
        "graph root": str(root / "podman/graph"),
        "run root": str(root / "podman/run"),
        "static dir": str(root / "podman/graph/libpod"),
        "tmp dir": str(root / "r/t"),
        "volume path": str(root / "podman/graph/volumes"),
    }
    return data, debug


@pytest.mark.parametrize(
    "escape",
    [
        None,
        "configFile",
        "graphRoot",
        "runRoot",
        "volumePath",
        "imageCopyTmpDir",
        "socket",
        "oci",
        "oci-missing-version",
        "oci-empty-version",
        "oci-missing-rundir",
        "oci-empty-rundir",
        "oci-relative-rundir",
        "oci-duplicate-rundir",
        "oci-other-runtime",
        "graph root",
        "run root",
        "static dir",
        "tmp dir",
        "volume path",
        "missing",
        "locks",
        "remote",
        "symlink",
    ],
)
def test_reset_checks_every_effective_path(
    private_store: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    escape: str | None,
) -> None:
    root, env = private_store
    data, debug = info(root)
    store = data["store"]
    host = data["host"]
    assert isinstance(store, dict) and isinstance(host, dict)
    if escape in store:
        store[escape] = "/run/shared"
    elif escape in debug:
        debug[escape] = "/run/shared"
    elif escape == "socket":
        host["remoteSocket"] = {"path": "/run/shared/podman.sock"}
    elif escape == "oci":
        host["ociRuntime"] = {"version": "crun version 1\nrundir: /run/shared"}
    elif escape == "oci-missing-version":
        host["ociRuntime"] = {}
    elif escape and escape.startswith("oci-"):
        versions = {
            "oci-empty-version": "",
            "oci-missing-rundir": "crun version 1\nspec: 1.0.0",
            "oci-empty-rundir": "crun version 1\nrundir:",
            "oci-relative-rundir": "crun version 1\nrundir: relative",
            "oci-duplicate-rundir": f"rundir: {root}/r/crun\nrundir: {root}/r/crun",
            "oci-other-runtime": "runc version 1\nspec: 1.0.0",
        }
        host["ociRuntime"] = {"version": versions[escape]}
    elif escape == "missing":
        debug.pop("tmp dir")
    elif escape == "locks":
        config = Path(env["CONTAINERS_CONF"])
        config.write_text(config.read_text().replace('"file"', '"shm"'))
    elif escape == "remote":
        host["serviceIsRemote"] = True
    elif escape == "symlink":
        (root / "link").symlink_to(root.parent, target_is_directory=True)
        store["runRoot"] = str(root / "link/shared")
    calls = []

    def run(command: list[str], **kwargs: object) -> str:
        calls.append(command)
        return (
            "\n".join(
                f"level=debug msg={json.dumps(f'Using {key} {value}')}"
                for key, value in debug.items()
            )
            + "\n"
            + json.dumps(data, indent=2)
        )

    monkeypatch.setattr("scripts.integration.run", run)
    if escape:
        with pytest.raises(GateError):
            reset_podman(root, env)
        assert not any("reset" in call for call in calls)
    else:
        reset_podman(root, env)
        assert ["info" in call for call in calls] == [True, False]
        assert calls[-1][-3:] == ["system", "reset", "--force"]


def test_private_environment_refuses_overlong_runtime_paths(tmp_path: Path) -> None:
    # No Podman invocation occurs for an overlong output directory.
    with pytest.raises(GateError, match="shorter"):
        root = tmp_path / ("x" * 104)
        root.mkdir()
        podman_environment(root, {})


def test_private_environment_drops_ambient_engine_overrides() -> None:
    inherited = {
        "CONTAINERS_CONF_OVERRIDE": "/shared/override.conf",
        "CONTAINERS_CONF_MODULES": "shared",
        "CONTAINER_HOST": "ssh://shared",
        "CONTAINER_CONNECTION": "shared",
        "CONTAINERS_STORAGE_CONF": "/shared/storage.conf",
        "CONTAINERS_CONF": "/shared/containers.conf",
        "XDG_RUNTIME_DIR": "/run/shared",
        "XDG_CONFIG_HOME": "/shared/config",
        "TMPDIR": "/shared/tmp",
    }
    with tempfile.TemporaryDirectory(prefix="p-") as directory:
        root = Path(directory)
        env = podman_environment(root, inherited)
        for key in list(inherited)[:4]:
            assert key not in env
        for key in list(inherited)[4:]:
            assert Path(env[key]).is_relative_to(root)
        assert not Path(env["XDG_RUNTIME_DIR"]).stat().st_mode & 0o077
    assert inherited["CONTAINER_HOST"] == "ssh://shared"


def test_controller_budget_is_stricter_than_podman(
    private_store: tuple[Path, dict[str, str]],
) -> None:
    root, _ = private_store
    path = root / ("x" * (SOCKET_LIMIT - len(os.fsencode(root)) - 29))
    assert len(os.fsencode(path / "r/podman/podman.sock")) < SOCKET_LIMIT
    with pytest.raises(GateError, match="shorter"):
        check_socket_layout(path)


@pytest.mark.parametrize("layout", [SSH_CONTROL_LAYOUT, NOX_LAYOUT])
def test_tool_socket_budgets_are_stricter_than_controller(layout: str) -> None:
    # Molecule's SSH control sockets and the generated docs-check workers bind
    # below the output too; the preflight must budget them before any work.
    # The layout check is pure path arithmetic, so a fixed root keeps the
    # lengths independent of the test's own temporary directory.
    root = Path("/gate-budget")
    # Two separators join root, name and layout; the limit itself is excluded.
    limit = SOCKET_LIMIT - len(os.fsencode(root)) - len(layout) - 3
    check_socket_layout(root / ("x" * limit), layout)
    check_socket_layout(root / ("x" * (limit + 1)))
    with pytest.raises(GateError, match="shorter"):
        check_socket_layout(root / ("x" * (limit + 1)), layout)


def test_controller_refuses_temporary_symlink_escape(
    private_store: tuple[Path, dict[str, str]],
) -> None:
    root, _ = private_store
    (root / "t").rmdir()
    (root / "t").symlink_to(root.parent, target_is_directory=True)
    with pytest.raises(GateError, match="outside"):
        controller_environment(root, {})


def test_real_controller_startup_stays_private(
    private_store: tuple[Path, dict[str, str]],
) -> None:
    root, _ = private_store
    run(
        [sys.executable, "-m", "scripts.controller", "--output", str(root)],
        cwd=ROOT,
        env=controller_environment(root, dict(os.environ)),
    )
    records = list(root.glob("controller-python*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["bytes"] < SOCKET_LIMIT
    assert Path(record["socket"]).is_relative_to(root / "t")
    assert not Path(record["socket"]).exists()
