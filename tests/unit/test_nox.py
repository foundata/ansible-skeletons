"""Check generated Nox behavior without requiring Nox in the unit environment."""

import runpy
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from scripts.common import ROOT, GateError


@pytest.fixture
def lint_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[Mock], None]:
    def decorator(
        **kwargs: object,
    ) -> Callable[[Callable[[Mock], None]], Callable[[Mock], None]]:
        if "packages" in kwargs:
            assert kwargs["packages"] == ["ansible-lint-foundata"]
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
    for module in (nox, antsibull, sessions):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.chdir(tmp_path)
    loaded = runpy.run_path(str(ROOT / "collection_default/noxfile.py.j2"))
    return cast(Callable[[Mock], None], loaded["ansible_lint"])


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
