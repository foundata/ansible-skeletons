# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check generated Nox behavior without requiring Nox in the unit environment."""

import io
import json
import runpy
import sys
import tarfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, Mock

import pytest

from scripts.common import ROOT, GateError


@pytest.fixture
def noxfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> dict[str, object]:
    """Load the generated noxfile with nox and antsibull-nox replaced by stubs."""
    installed_packages: list[object] = []

    def decorator(
        **kwargs: object,
    ) -> Callable[[Callable[[Mock], None]], Callable[[Mock], None]]:
        if "packages" in kwargs:
            installed_packages.append(kwargs["packages"])
        if kwargs.get("name") == "runtime-check":
            assert kwargs["default"] is True
        return lambda function: function

    nox = ModuleType("nox")
    nox.session = decorator  # type: ignore[attr-defined]
    nox.Session = Mock  # type: ignore[attr-defined]
    antsibull = ModuleType("antsibull_nox")
    sessions = ModuleType("antsibull_nox.sessions")
    sessions.install_packages = decorator  # type: ignore[attr-defined]
    sessions.prepare_collections = Mock(  # type: ignore[attr-defined]
        return_value=SimpleNamespace(current_place=tmp_path / "collections")
    )
    antsibull.sessions = sessions  # type: ignore[attr-defined]
    antsibull.load_antsibull_nox_toml = Mock()  # type: ignore[attr-defined]
    antsibull.IN_CI = False  # type: ignore[attr-defined]
    for module in (nox, antsibull, sessions):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.chdir(tmp_path)
    profile = getattr(request, "param", "collection_default")
    loaded = runpy.run_path(str(ROOT / profile / "noxfile.py.j2"))
    assert ["ansible-lint-foundata"] in installed_packages
    return loaded


@pytest.fixture
def lint_session(noxfile: dict[str, object]) -> Callable[[Mock], None]:
    return cast(Callable[[Mock], None], noxfile["ansible_lint"])


@pytest.mark.parametrize(
    "noxfile",
    ["collection_default", "collection_modules", "collection_modules_appliance"],
    indirect=True,
)
def test_runtime_check_passes_prepared_dependencies_and_artifact(
    noxfile: dict[str, object], tmp_path: Path
) -> None:
    (tmp_path / "requirements.txt").touch()
    (tmp_path / "tests/unit").mkdir(parents=True)
    (tmp_path / "tests/unit/requirements.txt").touch()
    session = MagicMock()
    session.posargs = ["--artifact", "/release/collection.tar.gz"]
    check = cast(Callable[[Mock], None], noxfile["runtime_check"])
    check(session)
    assert [call.args for call in session.install.call_args_list] == [
        ("-r", "requirements.txt"),
        ("-r", "tests/unit/requirements.txt"),
    ]
    session.run.assert_called_once_with(
        "python",
        "tests/runtime_check.py",
        "--dependencies",
        str(tmp_path / "collections"),
        "--artifact",
        "/release/collection.tar.gz",
    )


@pytest.mark.parametrize(
    "noxfile", ["collection_modules", "collection_modules_appliance"], indirect=True
)
@pytest.mark.parametrize(
    "problem", [None, "exit", "json", "missing", "outside", "symlink-root"]
)
def test_import_check_requires_installed_artifact_docs(
    noxfile: dict[str, object], tmp_path: Path, problem: str | None
) -> None:
    archive = tmp_path / "example.tar.gz"
    content = json.dumps(
        {"collection_info": {"namespace": "test", "name": "example"}}
    ).encode()
    with tarfile.open(archive, "w:gz") as stream:
        info = tarfile.TarInfo("MANIFEST.json")
        info.size = len(content)
        stream.addfile(info, io.BytesIO(content))
    installed = tmp_path / "installed"
    module = installed / "ansible_collections/test/example/plugins/modules/fixture.py"
    module.parent.mkdir(parents=True)
    module.write_text("# Installed artifact\n")
    session = MagicMock()
    session.error.side_effect = GateError
    docs = {"test.example.fixture": {"doc": {"filename": str(module)}}}
    if problem == "outside":
        docs["test.example.fixture"]["doc"]["filename"] = str(tmp_path / "fixture.py")
    elif problem == "symlink-root":
        foreign = tmp_path / "foreign-modules"
        module.parent.rename(foreign)
        module.parent.symlink_to(foreign, target_is_directory=True)
        docs["test.example.fixture"]["doc"]["filename"] = str(foreign / "fixture.py")
    elif problem == "missing":
        docs = {}

    def run(*args: str, **kwargs: object) -> str:
        if args[0] != "ansible-doc":
            return ""
        if problem == "exit":
            raise GateError("ansible-doc exited nonzero")
        assert args == ("ansible-doc", "--json", "test.example.fixture")
        assert kwargs["env"] == {
            "ANSIBLE_COLLECTIONS_PATH": f"{installed}:{tmp_path / 'dependencies'}",
            "ANSIBLE_COLLECTIONS_SCAN_SYS_PATH": "false",
            "PYTHONPATH": "",
        }
        return "invalid" if problem == "json" else json.dumps(docs)

    session.run.side_effect = run
    check = cast(Callable[..., None], noxfile["_artifact_docs"])
    if problem:
        with pytest.raises(GateError):
            check(session, archive, installed, str(tmp_path / "dependencies"))
    else:
        check(session, archive, installed, str(tmp_path / "dependencies"))
        session.chdir.assert_called_once_with(installed)
        session.error.assert_not_called()


@pytest.mark.parametrize(
    "noxfile", ["collection_modules", "collection_modules_appliance"], indirect=True
)
def test_importer_output_stays_in_its_temporary_directory(
    noxfile: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    check = cast(Callable[[Mock], None], noxfile["import_check"])
    docs = Mock()
    monkeypatch.setitem(check.__globals__, "_artifact_docs", docs)
    session = MagicMock()
    session.create_tmp.return_value = ".nox/import-check/tmp"
    output = tmp_path / ".nox/import-check/tmp/import-check"

    @contextmanager
    def chdir(path: Path) -> Iterator[None]:
        with monkeypatch.context() as context:
            context.chdir(path)
            yield

    def run(*args: str, **kwargs: object) -> str:
        if args[0] == "ansible-galaxy":
            output.mkdir(parents=True, exist_ok=True)
            (output / "test-example-1.0.0.tar.gz").touch()
        else:
            assert args[:3] == ("python", "-m", "galaxy_importer.main")
            assert Path(args[3]).is_absolute() and Path(args[3]).is_file()
            assert Path.cwd() == output
            Path("importer_result.json").write_text("{}\n")
        return ""

    session.chdir.side_effect = chdir
    session.run.side_effect = run
    for _ in range(2):
        check(session)
        assert not (tmp_path / "importer_result.json").exists()
        assert (output / "importer_result.json").is_file()
        assert Path.cwd() == tmp_path
    assert docs.call_count == 2


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("managed", [False, True])
def test_optional_extension_runs_in_the_lint_environment(
    tmp_path: Path, lint_session: Callable[[Mock], None], installed: bool, managed: bool
) -> None:
    session = Mock()
    session.posargs = ["--fix"]
    session.virtualenv.is_sandboxed = managed
    session.run.return_value = f"{installed}\n"
    lint_session(session)
    calls = session.run.call_args_list
    assert calls[0].args[:2] == ("python", "-c")
    assert "find_spec('ansible_lint_foundata')" in calls[0].args[2]
    assert calls[0].kwargs["silent"] is True
    env = {"ANSIBLE_COLLECTIONS_PATH": str(tmp_path / "collections")} if managed else {}
    assert all(call.kwargs["env"] == env for call in calls)
    prepare = vars(sys.modules["antsibull_nox.sessions"])["prepare_collections"]
    assert prepare.call_count == int(managed)
    assert calls[-1].args == (
        "python",
        "-m",
        "ansiblelint",
        "--offline",
        "--strict",
        "--fix",
    )
    if installed:
        assert calls[1].args == (
            "python",
            "-m",
            "ansible_lint_foundata.cli",
            "verify",
            "--project-dir",
            ".",
        )
        session.warn.assert_not_called()
        assert len(calls) == 3
    else:
        session.warn.assert_called_once_with(
            "ansible-lint-foundata is not installed; running stock rules only."
        )
        assert len(calls) == 2


@pytest.mark.parametrize("stage", ["detection", "verification", "lint"])
def test_nox_does_not_hide_errors(
    lint_session: Callable[[Mock], None], stage: str
) -> None:
    session = Mock()
    session.posargs = []

    def run(*args: str, **kwargs: object) -> str:
        current = (
            "detection"
            if args[1] == "-c"
            else "verification"
            if args[2] == "ansible_lint_foundata.cli"
            else "lint"
        )
        if current == stage:
            raise GateError(stage)
        return "True\n"

    session.run.side_effect = run
    with pytest.raises(GateError, match=stage):
        lint_session(session)
    session.warn.assert_not_called()
    assert (
        session.run.call_count == {"detection": 1, "verification": 2, "lint": 3}[stage]
    )


def test_unexpected_detection_output_is_not_a_stock_fallback(
    lint_session: Callable[[Mock], None],
) -> None:
    session = Mock()
    session.run.return_value = "unexpected"
    session.error.side_effect = GateError("detection")
    with pytest.raises(GateError, match="detection"):
        lint_session(session)
    session.warn.assert_not_called()
    assert session.run.call_count == 1


def test_failed_collection_preparation_is_not_a_stock_fallback(
    lint_session: Callable[[Mock], None],
) -> None:
    session = Mock()
    session.virtualenv.is_sandboxed = True
    session.error.side_effect = GateError("collections")
    prepare = vars(sys.modules["antsibull_nox.sessions"])["prepare_collections"]
    prepare.return_value = None
    with pytest.raises(GateError, match="collections"):
        lint_session(session)
    session.run.assert_not_called()
    session.warn.assert_not_called()


SCENARIO = """---
platforms:
  - name: "molecule-debian13"
    type: "podman"
  - name: "molecule-debian12"
"""


@pytest.mark.parametrize(
    ("posargs", "serial", "machines", "expected"),
    [
        ([], None, False, [["test", "--scenario-name", "default"]]),
        (["list"], None, False, [["list"]]),
        (
            [],
            None,
            True,
            [
                ["test", "--scenario-name", "default", "--platform-name", name]
                for name in ("molecule-debian13", "molecule-debian12", "molecule-vm")
            ],
        ),
        ([], "false", True, [["test", "--scenario-name", "default"]]),
        (
            [],
            "true",
            False,
            [
                ["test", "--scenario-name", "default", "--platform-name", name]
                for name in ("molecule-debian13", "molecule-debian12")
            ],
        ),
    ],
)
def test_molecule_session_runs_machines_one_at_a_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    noxfile: dict[str, object],
    posargs: list[str],
    serial: str | None,
    machines: bool,
    expected: list[list[str]],
) -> None:
    scenario = tmp_path / "extensions/molecule/default/molecule.yml"
    scenario.parent.mkdir(parents=True)
    extra = '  - name: "molecule-vm"\n    type: "libvirt"\n' if machines else ""
    scenario.write_text(SCENARIO + extra, encoding="utf-8")
    monkeypatch.delenv("MOLECULE_SERIAL", raising=False)
    monkeypatch.delenv("MOLECULE_PLATFORM_NAME", raising=False)
    if serial is not None:
        monkeypatch.setenv("MOLECULE_SERIAL", serial)
    session = MagicMock()
    session.posargs = posargs
    molecule = cast(Callable[[MagicMock], None], noxfile["molecule"])
    molecule(session)
    calls = [list(call.args[1:]) for call in session.run.call_args_list]
    assert [call.args[0] for call in session.run.call_args_list] == ["molecule"] * len(
        expected
    )
    assert calls == expected
    session.chdir.assert_called_once_with("extensions")
