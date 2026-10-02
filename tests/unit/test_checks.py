"""Test failure propagation, isolation and check completeness."""

import os
import sys
from pathlib import Path

import pytest
import yaml

from scripts.check import check, require_complete_tests
from scripts.common import ROOT, GateError, Lane, isolated_environment, run, uv_command
from scripts.render import render
from scripts.smoke import check_fix_stability, require_rule_discovery


def test_nonzero_subprocess_status_fails_gate(tmp_path: Path) -> None:
    with pytest.raises(GateError, match="exit status 7"):
        run([sys.executable, "-c", "raise SystemExit(7)"], cwd=tmp_path)


def test_explicitly_allowed_fix_status_is_not_a_failure(tmp_path: Path) -> None:
    run(
        [sys.executable, "-c", "raise SystemExit(8)"],
        cwd=tmp_path,
        allowed_exit_codes=(0, 8),
    )


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


def test_lint_wheel_is_installed_before_running_the_check() -> None:
    command = uv_command(
        Lane("3.14", "core221"),
        "python",
        "-m",
        "scripts.smoke",
        groups=["lint"],
        packages=["/tmp/rules.whl"],
    )
    assert command[-5:] == ["--with", "/tmp/rules.whl", "python", "-m", "scripts.smoke"]


def test_missing_lint_wheel_is_rejected_before_any_checks(tmp_path: Path) -> None:
    output = tmp_path / "output"
    with pytest.raises(GateError, match="existing wheel"):
        check(output, foundata_wheel=tmp_path / "missing.whl")
    assert not output.exists()


@pytest.mark.parametrize("discovered", [False, True])
def test_required_extension_cannot_silently_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, discovered: bool
) -> None:
    (tmp_path / ".ansible-lint").write_text(
        'enable_list: ["foundata-condition-list"]\n', encoding="utf-8"
    )

    def listing(*args: object, **kwargs: object) -> str:
        return (
            "- foundata-condition-list Use lists.\n" if discovered else "- yaml YAML.\n"
        )

    monkeypatch.setattr("scripts.smoke.run", listing)
    if discovered:
        require_rule_discovery(tmp_path, {})
    else:
        with pytest.raises(GateError, match="not discovered: foundata-condition-list"):
            require_rule_discovery(tmp_path, {})


def test_skeleton_lint_configurations_stay_in_sync() -> None:
    for name in (".ansible-lint", ".yamllint"):
        assert (ROOT / "role_default" / name).read_bytes() == (
            ROOT / "collection_default" / name
        ).read_bytes()
    config = yaml.safe_load((ROOT / "collection_default/.ansible-lint.j2").read_text())
    assert (
        len([rule for rule in config["enable_list"] if rule.startswith("foundata-")])
        == 7
    )
    assert config["exclude_paths"] == [
        ".cache/",
        ".git/",
        ".hg/",
        ".svn/",
        ".tox/",
        ".nox/",
        ".venv/",
        "changelogs/changelog.yaml",
    ]
    yaml_config = yaml.safe_load((ROOT / "collection_default/.yamllint.j2").read_text())
    assert yaml_config["rules"]["empty-lines"] == {
        "max": 2,
        "max-start": 0,
        "max-end": 0,
    }


@pytest.mark.parametrize("unstable", [False, True])
def test_second_fix_must_not_change_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unstable: bool
) -> None:
    calls = 0

    def fix(*args: object, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        (tmp_path / "main.yml").write_text(
            f"---\nvalue: {calls if unstable else 1}\n", encoding="utf-8"
        )
        return ""

    monkeypatch.setattr("scripts.smoke.run", fix)
    if unstable:
        with pytest.raises(GateError, match="Second autofix pass changed YAML"):
            check_fix_stability(tmp_path, {})
    else:
        check_fix_stability(tmp_path, {})
