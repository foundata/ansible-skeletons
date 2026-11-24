# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check generated Nox behavior without requiring Nox in the unit environment."""

import hashlib
import io
import json
import os
import runpy
import signal
import sys
import tarfile
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import FunctionType, ModuleType, SimpleNamespace
from typing import Any, cast
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

    class Session:
        """The part of nox.Session the noxfile changes."""

        def __init__(self, tmpdir: Path) -> None:
            self.env: dict[str, str] = {}
            self.tmpdir = tmpdir

        def create_tmp(self) -> str:
            self.tmpdir.mkdir(parents=True, exist_ok=True)
            self.env["TMPDIR"] = str(self.tmpdir)
            return str(self.tmpdir)

    nox = ModuleType("nox")
    nox.options = SimpleNamespace()  # type: ignore[attr-defined]
    nox.session = decorator  # type: ignore[attr-defined]
    nox.Session = Session  # type: ignore[attr-defined]
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
    # Loading shortens a long TMPDIR and creates a directory below /var/tmp
    # without one; a short TMPDIR stays as it is.
    monkeypatch.setenv("TMPDIR", "/tmp")
    # The handler a noxfile installs stays out of the test process.
    monkeypatch.setattr("signal.signal", Mock())
    profile = getattr(request, "param", "collection_default")
    loaded = runpy.run_path(str(ROOT / profile / "noxfile.py.j2"))
    assert ["ansible-lint-foundata>=1.2.0"] in installed_packages
    return loaded


def test_the_locked_nox_still_sets_tmpdir_where_the_noxfile_expects(
    tmp_path: Path,
) -> None:
    # The rendered noxfile wraps nox.Session.create_tmp and relies on it to
    # create <envdir>/tmp, set the session's TMPDIR to it and return it. A nox
    # upgrade that changes this fails here; the check's TMPDIR probe covers the
    # nox a collection resolves at run time.
    from nox.sessions import Session as RealSession

    runner = SimpleNamespace(envdir=str(tmp_path / "env"), venv=SimpleNamespace(env={}))
    session = RealSession(runner)  # type: ignore[arg-type]
    tmpdir = session.create_tmp()
    assert Path(tmpdir) == tmp_path / "env/tmp" and Path(tmpdir).is_dir()
    assert session.env["TMPDIR"] == str(tmp_path / "env/tmp")


@pytest.mark.parametrize(
    "noxfile",
    [
        "collection_default",
        "collection_modules_rest",
        "collection_modules_appliance",
        "collection_modules_cli",
    ],
    indirect=True,
)
def test_a_deep_temporary_directory_gets_a_short_path(
    noxfile: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "run"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    cleanups: list[object] = []
    monkeypatch.setattr(
        "atexit.register", lambda *arguments: cleanups.append(arguments)
    )
    short = Path("/s")
    short_path = cast(Callable[[str], str], noxfile["_short_path"])
    assert short_path(str(short)) == str(short)
    deep = tmp_path / ("d" * 70) / "tmp"
    deep.mkdir(parents=True)
    session = cast(SimpleNamespace, noxfile["nox"]).Session(deep)
    assert session.create_tmp() == str(deep)
    link = Path(session.env["TMPDIR"])
    assert link.parent.parent == runtime and link.resolve() == deep.resolve()
    assert len(cleanups) == 1
    link.parent.chmod(0o500)
    try:
        assert short_path(str(deep)) == str(deep)
    finally:
        link.parent.chmod(0o700)


@pytest.mark.parametrize(
    "noxfile",
    [
        "collection_default",
        "collection_modules_rest",
        "collection_modules_appliance",
        "collection_modules_cli",
    ],
    indirect=True,
)
def test_sessions_without_tmpdir_share_a_private_directory_below_var_tmp(
    noxfile: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = cast(FunctionType, noxfile["_default_tmpdir"])
    assert default.__globals__["SCRATCH"] == Path("/var/tmp")
    monkeypatch.setitem(default.__globals__, "SCRATCH", tmp_path)
    expected = tmp_path / f"nox-{os.getuid()}"
    for _invocation in range(2):
        monkeypatch.delenv("TMPDIR")
        default()
        assert os.environ["TMPDIR"] == str(expected)
        assert expected.lstat().st_mode & 0o777 == 0o700
        (expected / "kept").touch()
    assert [path.name for path in tmp_path.iterdir()] == [expected.name]
    given = str(tmp_path / ("given-" * 20))
    monkeypatch.setenv("TMPDIR", given)
    default()
    assert os.environ["TMPDIR"] == given


@pytest.mark.parametrize(
    "noxfile",
    [
        "collection_default",
        "collection_modules_rest",
        "collection_modules_appliance",
        "collection_modules_cli",
    ],
    indirect=True,
)
@pytest.mark.parametrize("problem", ["symlink", "shared", "file", "foreign", "missing"])
def test_a_default_tmpdir_that_is_not_private_is_not_used(
    noxfile: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    problem: str,
) -> None:
    default = cast(FunctionType, noxfile["_default_tmpdir"])
    scratch = tmp_path / ("missing" if problem == "missing" else "scratch")
    uid = os.getuid() + (1 if problem == "foreign" else 0)
    directory = scratch / f"nox-{uid}"
    if problem != "missing":
        scratch.mkdir()
    if problem == "symlink":
        (tmp_path / "elsewhere").mkdir(mode=0o700)
        directory.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    elif problem == "shared":
        directory.mkdir()
        directory.chmod(0o755)
    elif problem == "file":
        directory.touch()
    elif problem == "foreign":
        # Owned by the real user, not by the user the noxfile believes it runs as.
        directory.mkdir(mode=0o700)
        monkeypatch.setattr("os.getuid", lambda: uid)
    monkeypatch.setitem(default.__globals__, "SCRATCH", scratch)
    monkeypatch.delenv("TMPDIR")
    default()
    assert "TMPDIR" not in os.environ
    assert f"{directory} is not a private directory" in capsys.readouterr().err


def short_invocation(noxfile: dict[str, object]) -> dict[str, Any]:
    """Give each simulated invocation its own loaded helper state."""
    original = cast(FunctionType, noxfile["_short_path"])
    namespace = {**original.__globals__, "_SHORT": []}
    assert "_sweep" not in namespace
    for name in ("_cleanup_short", "_short_path"):
        function = cast(FunctionType, noxfile[name])
        namespace[name] = FunctionType(function.__code__, namespace)
    return namespace


@pytest.mark.parametrize(
    "noxfile",
    [
        "collection_default",
        "collection_modules_rest",
        "collection_modules_appliance",
        "collection_modules_cli",
    ],
    indirect=True,
)
@pytest.mark.parametrize("pid_name", ["99999999", str(os.getpid()), "1", "00001"])
def test_pid_and_same_uid_do_not_confer_ownership(
    noxfile: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pid_name: str,
) -> None:
    parent = tmp_path / "runtime"
    parent.mkdir()
    unrelated = parent / f"nox.{pid_name}"
    unrelated.mkdir()
    (unrelated / "keep").write_text("unrelated")
    symlink = parent / "nox.88888888"
    symlink.symlink_to(unrelated, target_is_directory=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(parent))
    target = tmp_path / ("long-target-" * 8)
    target.mkdir()
    monkeypatch.setattr("atexit.register", lambda *arguments: None)
    namespace = short_invocation(noxfile)
    link = Path(namespace["_short_path"](str(target)))
    owned = link.parent
    assert owned != unrelated and owned.stat().st_mode & 0o777 == 0o700
    owner = json.loads((owned / "owner.json").read_text())
    assert owner["path"] == str(owned) and owner["pid"] == os.getpid()
    assert (owner["device"], owner["inode"]) == (
        owned.stat().st_dev,
        owned.stat().st_ino,
    )
    assert link.resolve() == target
    namespace["_cleanup_short"]()
    assert not owned.exists()
    assert (unrelated / "keep").read_text() == "unrelated"
    assert symlink.is_symlink() and target.is_dir()


@pytest.mark.parametrize(
    "noxfile",
    [
        "collection_default",
        "collection_modules_rest",
        "collection_modules_appliance",
        "collection_modules_cli",
    ],
    indirect=True,
)
@pytest.mark.parametrize("replacement", ["symlink", "directory"])
def test_replaced_owned_path_is_left_alone(
    noxfile: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    target = tmp_path / ("long-target-" * 8)
    target.mkdir()
    monkeypatch.setattr("atexit.register", lambda *arguments: None)
    namespace = short_invocation(noxfile)
    owned = Path(namespace["_short_path"](str(target))).parent
    retained = owned.with_name(owned.name + "-retained")
    owned.rename(retained)
    if replacement == "symlink":
        owned.symlink_to(target, target_is_directory=True)
    else:
        owned.mkdir()
        (owned / "keep").touch()
    namespace["_cleanup_short"]()
    assert owned.exists() and retained.exists() and target.exists()


@pytest.mark.parametrize(
    "noxfile",
    [
        "collection_default",
        "collection_modules_rest",
        "collection_modules_appliance",
        "collection_modules_cli",
    ],
    indirect=True,
)
def test_a_terminated_nox_still_runs_its_cleanup(
    noxfile: dict[str, object],
) -> None:
    leave = cast(Callable[[int, object], None], noxfile["_leave"])
    cast(Mock, signal.signal).assert_called_with(signal.SIGTERM, leave)
    with pytest.raises(SystemExit) as stopped:
        leave(signal.SIGTERM, None)
    assert stopped.value.code == 143


def test_concurrent_invocations_with_same_pid_own_distinct_paths(
    noxfile: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    target = tmp_path / ("long-target-" * 8)
    target.mkdir()
    monkeypatch.setattr("atexit.register", lambda *arguments: None)
    first, second = short_invocation(noxfile), short_invocation(noxfile)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(namespace["_short_path"], str(target))
            for namespace in (first, second)
        ]
        links = [Path(future.result()) for future in futures]
    assert links[0].parent != links[1].parent
    first["_cleanup_short"]()
    assert not links[0].parent.exists() and links[1].resolve() == target
    second["_cleanup_short"]()
    assert not links[1].parent.exists() and target.exists()


@pytest.fixture
def lint_session(noxfile: dict[str, object]) -> Callable[[Mock], None]:
    return cast(Callable[[Mock], None], noxfile["ansible_lint"])


@pytest.mark.parametrize(
    "noxfile",
    [
        "collection_default",
        "collection_modules_rest",
        "collection_modules_appliance",
        "collection_modules_cli",
    ],
    indirect=True,
)
def test_sessions_prefer_uv_and_fall_back_to_virtualenv(
    noxfile: dict[str, object],
) -> None:
    nox = cast(SimpleNamespace, noxfile["nox"])
    assert nox.options.default_venv_backend == "uv|virtualenv"


@pytest.mark.parametrize(
    "noxfile",
    [
        "collection_default",
        "collection_modules_rest",
        "collection_modules_appliance",
        "collection_modules_cli",
    ],
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
    "noxfile",
    [
        "collection_modules_rest",
        "collection_modules_appliance",
        "collection_modules_cli",
    ],
    indirect=True,
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
    "noxfile",
    [
        "collection_modules_rest",
        "collection_modules_appliance",
        "collection_modules_cli",
    ],
    indirect=True,
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
provisioner:
  playbooks:
    create: "../shared/playbooks/create-dispatch.yml"
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
    scenario.write_text(
        SCENARIO.replace("provisioner:", extra + "provisioner:"), encoding="utf-8"
    )
    monkeypatch.delenv("MOLECULE_SERIAL", raising=False)
    monkeypatch.delenv("MOLECULE_PLATFORM_NAME", raising=False)
    monkeypatch.delenv("MOLECULE_WORKSPACE", raising=False)
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


@pytest.mark.parametrize(
    ("posargs", "expected"),
    [
        ([], "default"),
        (["converge"], "default"),
        (["-vvv", "test", "--destroy=never"], "default"),
        (["test", "-s", "ee"], "ee"),
        (["test", "--scenario-name", "ee"], "ee"),
        (["test", "--scenario-name=ee"], "ee"),
        (["test", "-see"], "ee"),
        (["test", "-s", "ee", "--scenario-name=ee"], "ee"),
        (["converge", "--", "-s", "other"], "default"),
        (["test", "--all"], None),
        (["test", "-s", "default", "-s", "ee"], None),
        (["test", "-s"], None),
    ],
)
def test_molecule_arguments_name_one_scenario(
    noxfile: dict[str, object], posargs: list[str], expected: str | None
) -> None:
    scenario = cast(Callable[[list[str]], str | None], noxfile["_scenario"])
    assert scenario(posargs) == expected


@pytest.fixture
def molecule_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, noxfile: dict[str, object]
) -> Callable[[list[str]], tuple[MagicMock, dict[str, str]]]:
    """Run the molecule session and return it with the environment it passed."""
    scenario = tmp_path / "extensions/molecule/default/molecule.yml"
    scenario.parent.mkdir(parents=True)
    scenario.write_text(SCENARIO, encoding="utf-8")
    ee = tmp_path / "extensions/molecule/ee/molecule.yml"
    ee.parent.mkdir(parents=True)
    ee.write_text(
        'driver:\n  options:\n    managed: false\nplatforms:\n  - name: "localhost"\n',
        encoding="utf-8",
    )
    for name in (
        "MOLECULE_WORKSPACE",
        "MOLECULE_EPHEMERAL_DIRECTORY",
        "MOLECULE_SERIAL",
        "MOLECULE_PLATFORM_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANSIBLE_HOME", str(tmp_path / "ansible-home"))

    def run(posargs: list[str]) -> tuple[MagicMock, dict[str, str]]:
        session = MagicMock()
        session.posargs = posargs
        session.error.side_effect = GateError("session error")
        cast(Callable[[MagicMock], None], noxfile["molecule"])(session)
        environments = [call.kwargs["env"] for call in session.run.call_args_list]
        assert environments
        assert all(env == environments[0] for env in environments)
        return session, environments[0]

    return run


def test_molecule_state_and_names_follow_the_directory(
    tmp_path: Path,
    molecule_run: Callable[[list[str]], tuple[MagicMock, dict[str, str]]],
) -> None:
    project = str((tmp_path / "extensions").resolve())
    key = hashlib.sha256(project.encode("utf-8")).hexdigest()[:8]
    state = tmp_path / "ansible-home/tmp" / f"molecule.{key}"
    session, env = molecule_run([])
    assert env["MOLECULE_WORKSPACE"] == key
    assert env["MOLECULE_EPHEMERAL_DIRECTORY"] == str(state / "default")
    logged = " ".join(str(call.args[0]) for call in session.log.call_args_list)
    assert f"molecule-debian13-{key}, molecule-debian12-{key}" in logged
    assert str(state / "default") in logged
    # A later call of the debug flow finds the same state and names.
    _session, later = molecule_run(["converge"])
    assert later["MOLECULE_WORKSPACE"] == key
    assert later["MOLECULE_EPHEMERAL_DIRECTORY"] == str(state / "default")
    session, ee = molecule_run(["test", "--scenario-name", "ee"])
    assert ee["MOLECULE_EPHEMERAL_DIRECTORY"] == str(state / "ee")
    logged = " ".join(str(call.args[0]) for call in session.log.call_args_list)
    assert "Instance names" not in logged
    # A scenario with a create playbook of its own names its instances itself.
    own = tmp_path / "extensions/molecule/lab/molecule.yml"
    own.parent.mkdir(parents=True)
    own.write_text(
        'platforms:\n  - name: "product-api"\n'
        'provisioner:\n  playbooks:\n    create: "create.yml"\n',
        encoding="utf-8",
    )
    session, _lab = molecule_run(["test", "--scenario-name", "lab"])
    logged = " ".join(str(call.args[0]) for call in session.log.call_args_list)
    assert "Instance names" not in logged


@pytest.mark.parametrize(
    ("posargs", "environment", "expected"),
    [
        ([], None, None),
        (["converge"], None, None),
        (["test", "--platform-name", "molecule-debian12"], None, "molecule-debian12"),
        (["test", "--platform-name=molecule-debian12"], None, "molecule-debian12"),
        (["test", "-p", "molecule-debian12"], None, "molecule-debian12"),
        (["test", "-pmolecule-debian12"], None, "molecule-debian12"),
        (["test", "-p", "a", "--platform-name=b"], None, "b"),
        ([], "molecule-debian13", "molecule-debian13"),
        (["test", "-p", "molecule-debian12"], "molecule-debian13", "molecule-debian12"),
        ([], "", None),
        (["test", "--", "--platform-name", "other"], None, None),
        (["test", "-s", "ee", "--scenario-name=ee"], None, None),
    ],
)
def test_molecule_arguments_or_environment_name_one_platform(
    noxfile: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    posargs: list[str],
    environment: str | None,
    expected: str | None,
) -> None:
    monkeypatch.delenv("MOLECULE_PLATFORM_NAME", raising=False)
    if environment is not None:
        monkeypatch.setenv("MOLECULE_PLATFORM_NAME", environment)
    platform = cast(Callable[[list[str]], str | None], noxfile["_platform"])
    assert platform(posargs) == expected


@pytest.mark.parametrize(
    "noxfile",
    [
        "collection_default",
        "collection_modules_rest",
        "collection_modules_appliance",
        "collection_modules_cli",
    ],
    indirect=True,
)
@pytest.mark.parametrize(
    ("posargs", "environment", "expected"),
    [
        ([], None, ["molecule-debian13", "molecule-debian12"]),
        (["test", "--platform-name", "molecule-debian12"], None, ["molecule-debian12"]),
        (["test", "--platform-name=molecule-debian13"], None, ["molecule-debian13"]),
        (["converge"], "molecule-debian12", ["molecule-debian12"]),
        (["test", "--platform-name", "unknown"], None, []),
    ],
)
def test_molecule_instance_names_follow_the_platform_name(
    monkeypatch: pytest.MonkeyPatch,
    molecule_run: Callable[[list[str]], tuple[MagicMock, dict[str, str]]],
    posargs: list[str],
    environment: str | None,
    expected: list[str],
) -> None:
    if environment is not None:
        monkeypatch.setenv("MOLECULE_PLATFORM_NAME", environment)
    session, env = molecule_run(posargs)
    key = env["MOLECULE_WORKSPACE"]
    logged = [
        str(call.args[0])
        for call in session.log.call_args_list
        if str(call.args[0]).startswith("Instance names")
    ]
    names = ", ".join(f"{name}-{key}" for name in expected)
    assert logged == ([f"Instance names: {names}"] if expected else [])


def test_molecule_key_and_state_can_be_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    molecule_run: Callable[[list[str]], tuple[MagicMock, dict[str, str]]],
) -> None:
    monkeypatch.setenv("MOLECULE_WORKSPACE", "ci-42")
    _session, env = molecule_run(["converge"])
    assert env["MOLECULE_WORKSPACE"] == "ci-42"
    expected = tmp_path / "ansible-home/tmp/molecule.ci-42/default"
    assert env["MOLECULE_EPHEMERAL_DIRECTORY"] == str(expected)
    monkeypatch.setenv("MOLECULE_EPHEMERAL_DIRECTORY", str(tmp_path / "state"))
    _session, env = molecule_run(["converge"])
    assert env["MOLECULE_EPHEMERAL_DIRECTORY"] == str(tmp_path / "state")


@pytest.mark.parametrize(
    ("key", "posargs"),
    [
        ("Upper", []),
        ("-dash", []),
        ("a" * 33, []),
        ("has space", []),
        (None, ["test", "--all"]),
        (None, ["test", "-s", "default", "-s", "ee"]),
    ],
)
def test_molecule_refuses_what_cannot_keep_runs_apart(
    monkeypatch: pytest.MonkeyPatch,
    molecule_run: Callable[[list[str]], tuple[MagicMock, dict[str, str]]],
    key: str | None,
    posargs: list[str],
) -> None:
    if key is not None:
        monkeypatch.setenv("MOLECULE_WORKSPACE", key)
    with pytest.raises(GateError, match="session error"):
        molecule_run(posargs)
