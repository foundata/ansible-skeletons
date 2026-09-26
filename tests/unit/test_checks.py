"""Test failure propagation, isolation and check completeness."""

import os
import sys
from pathlib import Path

import pytest

from scripts.check import require_complete_tests
from scripts.common import GateError, isolated_environment, run
from scripts.render import render


def test_nonzero_subprocess_status_fails_gate(tmp_path: Path) -> None:
    with pytest.raises(GateError, match="exit status 7"):
        run([sys.executable, "-c", "raise SystemExit(7)"], cwd=tmp_path)


def test_ambient_ansible_configuration_is_not_inherited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANSIBLE_CONFIG", "/unexpected/ansible.cfg")
    monkeypatch.setenv("ANSIBLE_ROLES_PATH", "/unexpected/roles")
    monkeypatch.setenv("ANSIBLE_EXTRA_VARS", "unexpected")
    env = isolated_environment(tmp_path)
    assert env["ANSIBLE_CONFIG"] == str(tmp_path / "ansible.cfg")
    assert env["ANSIBLE_ROLES_PATH"] == str(tmp_path / "roles")
    assert "ANSIBLE_EXTRA_VARS" not in env
    assert os.environ["ANSIBLE_CONFIG"] == "/unexpected/ansible.cfg"


@pytest.mark.parametrize("name", ["../role", "a.b", "a b", "--help", "MixedCase"])
def test_invalid_resource_names_are_rejected_before_rendering(
    tmp_path: Path, name: str
) -> None:
    output = tmp_path / "output"
    with pytest.raises(GateError, match="Invalid resource name"):
        render(output, role_name=name)
    assert not output.exists()


def test_render_refuses_existing_output(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        render(tmp_path)


@pytest.mark.parametrize(
    ("tests", "skipped", "failures", "errors"),
    [
        (0, 0, 0, 0),
        (3, 1, 0, 0),
        (3, 0, 1, 0),
        (3, 0, 0, 1),
    ],
)
def test_incomplete_unit_suite_is_not_a_pass(
    tmp_path: Path, tests: int, skipped: int, failures: int, errors: int
) -> None:
    path = tmp_path / "tests.xml"
    path.write_text(
        f'<testsuites><testsuite tests="{tests}" skipped="{skipped}" failures="{failures}" errors="{errors}" /></testsuites>',
        encoding="utf-8",
    )
    with pytest.raises(GateError):
        require_complete_tests(path)
