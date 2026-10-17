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
    CONTROLLER_LAYOUT,
    NOX_LAYOUT,
    PODMAN_LAYOUT,
    SOCKET_LIMIT,
    SSH_CONTROL_LAYOUT,
    check_runtime_layout,
    runtime_directory,
    temporary_environment,
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


Store = tuple[Path, Path, dict[str, str]]


@pytest.fixture
def private_store(runtime_base: Path) -> Iterator[Store]:
    with tempfile.TemporaryDirectory(prefix="p-") as directory:
        # A check's TMPDIR is a link; the output paths Podman reports are not.
        root = Path(directory).resolve()
        runtime = runtime_base / "sk-store"
        runtime.mkdir(mode=0o700)
        env = temporary_environment(root / "t", runtime / "i", {})
        yield root, runtime, podman_environment(root, runtime, env)


def info(root: Path, runtime: Path) -> tuple[dict[str, object], dict[str, str]]:
    data: dict[str, object] = {
        "store": {
            "configFile": str(root / "podman/storage.conf"),
            "graphRoot": str(root / "podman/graph"),
            "runRoot": str(runtime / "r/run"),
            "volumePath": str(root / "podman/graph/volumes"),
            "imageCopyTmpDir": str(root / "podman/tmp"),
        },
        "host": {
            "serviceIsRemote": False,
            "remoteSocket": {"path": str(runtime / "r/podman/podman.sock")},
            "ociRuntime": {"version": f"crun version 1\nrundir: {runtime}/r/crun"},
        },
    }
    debug = {
        "graph root": str(root / "podman/graph"),
        "run root": str(runtime / "r/run"),
        "static dir": str(root / "podman/graph/libpod"),
        "tmp dir": str(runtime / "r/t"),
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
        "temporary",
        "locks",
        "remote",
        "symlink",
    ],
)
def test_reset_checks_every_effective_path(
    private_store: Store,
    monkeypatch: pytest.MonkeyPatch,
    escape: str | None,
) -> None:
    root, runtime, env = private_store
    data, debug = info(root, runtime)
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
            "oci-duplicate-rundir": (
                f"rundir: {runtime}/r/crun\nrundir: {runtime}/r/crun"
            ),
            "oci-other-runtime": "runc version 1\nspec: 1.0.0",
        }
        host["ociRuntime"] = {"version": versions[escape]}
    elif escape == "missing":
        debug.pop("tmp dir")
    elif escape == "temporary":
        env["TMPDIR"] = "/run/shared"
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
            reset_podman(root, runtime, env)
        assert not any("reset" in call for call in calls)
    else:
        reset_podman(root, runtime, env)
        assert ["info" in call for call in calls] == [True, False]
        assert calls[-1][-3:] == ["system", "reset", "--force"]


def test_private_environment_refuses_overlong_runtime_paths(tmp_path: Path) -> None:
    # No Podman invocation occurs for a runtime directory too deep for its socket.
    runtime = tmp_path / ("x" * 104)
    with pytest.raises(GateError, match="exceeds"):
        podman_environment(tmp_path, runtime, {})
    assert not (tmp_path / "podman").exists()


def test_private_environment_drops_ambient_engine_overrides(runtime_base: Path) -> None:
    inherited = {
        "CONTAINERS_CONF_OVERRIDE": "/shared/override.conf",
        "CONTAINERS_CONF_MODULES": "shared",
        "CONTAINER_HOST": "ssh://shared",
        "CONTAINER_CONNECTION": "shared",
        "CONTAINERS_STORAGE_CONF": "/shared/storage.conf",
        "CONTAINERS_CONF": "/shared/containers.conf",
        "XDG_CONFIG_HOME": "/shared/config",
    }
    with tempfile.TemporaryDirectory(prefix="p-") as directory:
        root = Path(directory).resolve()
        runtime = runtime_base / "sk-store"
        runtime.mkdir(mode=0o700)
        env = podman_environment(
            root,
            runtime,
            {**inherited, "XDG_RUNTIME_DIR": "/run/shared", "TMPDIR": "/shared/tmp"},
        )
        for key in list(inherited)[:4]:
            assert key not in env
        for key in list(inherited)[4:]:
            assert Path(env[key]).is_relative_to(root)
        # Run state goes to the runtime directory; images stay in the output.
        assert Path(env["XDG_RUNTIME_DIR"]).is_relative_to(runtime)
        assert not Path(env["XDG_RUNTIME_DIR"]).stat().st_mode & 0o077
        storage = Path(env["CONTAINERS_STORAGE_CONF"]).read_text(encoding="utf-8")
        assert f'runroot = "{runtime}/r/run"' in storage
        assert f'graphroot = "{root}/podman/graph"' in storage
        # TMPDIR stays the caller's; the path check refuses a shared one.
        assert env["TMPDIR"] == "/shared/tmp"
    assert inherited["CONTAINER_HOST"] == "ssh://shared"


@pytest.mark.parametrize(
    "layout", [CONTROLLER_LAYOUT, NOX_LAYOUT, PODMAN_LAYOUT, SSH_CONTROL_LAYOUT]
)
def test_runtime_budget_counts_each_socket_path(layout: str) -> None:
    # The layout check is pure path arithmetic, so a fixed root keeps the
    # lengths independent of the test's own directories. Two separators join
    # root, name and layout; the limit itself is excluded.
    root = Path("/run/user/1000")
    # The controller's listeners are budgeted with every other layout.
    longest = max(len(layout), len(CONTROLLER_LAYOUT))
    limit = SOCKET_LIMIT - len(os.fsencode(root)) - longest - 3
    check_runtime_layout(root / ("x" * limit), layout)
    with pytest.raises(GateError, match="exceeds"):
        check_runtime_layout(root / ("x" * (limit + 1)), layout)
    with pytest.raises(GateError, match="exceeds"):
        check_runtime_layout(root / ("x" * SOCKET_LIMIT))


def test_temporary_environment_refuses_a_shared_or_linked_directory(
    tmp_path: Path,
) -> None:
    (tmp_path / "linked").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(GateError, match="private"):
        temporary_environment(tmp_path / "linked", tmp_path / "i", {})
    (tmp_path / "shared").mkdir(mode=0o755)
    with pytest.raises(GateError, match="private"):
        temporary_environment(tmp_path / "shared", tmp_path / "i", {})
    assert not (tmp_path / "i").exists()


def test_a_tool_on_its_own_removes_its_runtime_directory(runtime_base: Path) -> None:
    with runtime_directory() as runtime:
        assert runtime.parent == runtime_base
        assert runtime.name.startswith("sk-")
        assert not runtime.stat().st_mode & 0o077
        (runtime / "s").mkdir()
    assert not runtime.exists()


def test_a_tool_under_a_check_keeps_the_step_runtime_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    step = tmp_path / "step"
    monkeypatch.setenv("SKELETONS_RUNTIME_DIR", str(step))
    with runtime_directory() as runtime:
        assert runtime == step
    assert step.is_dir()


def test_real_controller_binds_through_the_short_link() -> None:
    with (
        tempfile.TemporaryDirectory(prefix="p-") as directory,
        tempfile.TemporaryDirectory(prefix="o-") as deep,
    ):
        root = Path(directory)
        # The temporary directory in the output may be deeper than the limit.
        temporary = Path(deep) / ("x" * 60) / ("y" * 60)
        run(
            [sys.executable, "-m", "scripts.controller", "--output", str(root)],
            cwd=ROOT,
            env=temporary_environment(temporary, root / "i", dict(os.environ)),
        )
        records = list(root.glob("controller-python*.json"))
        assert len(records) == 1
        record = json.loads(records[0].read_text())
        assert record["bytes"] < SOCKET_LIMIT
        assert Path(record["socket"]).is_relative_to(root / "i")
        assert not Path(record["socket"]).exists()


def test_real_controller_refuses_a_temporary_directory_too_deep_for_sockets() -> None:
    # Python 3.13 and later bind below /tmp instead; 3.12 cannot bind at all.
    with tempfile.TemporaryDirectory(prefix="o-") as deep:
        temporary = Path(deep) / ("x" * 60) / ("y" * 60)
        temporary.mkdir(mode=0o700, parents=True)
        with pytest.raises(GateError, match="exit status 1"):
            run(
                [sys.executable, "-m", "scripts.controller", "--output", deep],
                cwd=ROOT,
                env={**os.environ, "TMPDIR": str(temporary)},
            )
        assert not list(Path(deep).glob("controller-python*.json"))
