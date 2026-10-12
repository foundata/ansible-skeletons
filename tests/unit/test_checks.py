# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Test failure propagation, isolation and check completeness."""

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

from scripts.check import check, require_complete_tests
from scripts.common import ROOT, GateError, Lane, isolated_environment, run, uv_command
from scripts.render import Rendered, render
from scripts.smoke import (
    check_fix_stability,
    check_nox_gate,
    check_tree,
    default_sessions,
    lint_constraints,
    lint_project,
)


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
    assert Path(env["HOME"]) == tmp_path / "home"
    assert Path(env["HOME"]).is_dir()
    assert Path(env["XDG_CACHE_HOME"]).is_dir()
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


def test_socket_layout_is_rejected_before_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected() -> None:
        pytest.fail("Checks started before rejecting the output layout")

    monkeypatch.setattr("scripts.check.check_static", unexpected)
    output = tmp_path / ("x" * 104)
    with pytest.raises(GateError, match="shorter"):
        check(
            output,
            full=True,
            backends=["podman", "libvirt", "ee"],
            utils_source=tmp_path,
        )
    assert not output.exists()


@pytest.mark.parametrize("wheel", [False, True])
def test_nox_constraints_follow_effective_overlay_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wheel: bool
) -> None:
    for version in ("1.0.0", "1.1.0"):
        site = tmp_path / version
        metadata = site / f"gate_fixture-{version}.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: Gate_Fixture\nVersion: {version}\n",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(site))
    url = "file:///tmp/gate_fixture-1.1.0-py3-none-any.whl"
    if wheel:
        (metadata / "direct_url.json").write_text(
            json.dumps({"url": url, "archive_info": {}}), encoding="utf-8"
        )
    constraints = lint_constraints().splitlines()
    assert [line for line in constraints if line.startswith("gate-fixture")] == [
        f"gate-fixture @ {url}" if wheel else "gate-fixture==1.1.0"
    ]


def test_nox_gate_isolates_stock_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered = Rendered(
        tmp_path, tmp_path / "role", tmp_path / "collection", {"PATH": "/outer/bin"}
    )
    nox_runs = []
    config_lints = []

    def run(command: list[str], **kwargs: object) -> str:
        if command[0] == "antsibull-nox":
            config_lints.append(command)
            return ""
        if command[:2] == ["nox", "--list"]:
            return (
                "Sessions defined in noxfile.py:\n\n"
                "* docs-check -> Run 'antsibull-docs lint-collection-docs'\n"
                "* build-import-check -> Run build and import checkers\n"
                "* ansible-lint -> ansible-lint with the foundata rules\n"
                "* docs -> Role documentation with DocSmith\n"
                "- molecule -> Molecule\n\n"
                "sessions marked with * are selected, sessions marked with - are skipped.\n"
            )
        if command[0] == "nox":
            nox_runs.append(command)
            assert "--no-venv" not in command
            env = kwargs["env"]
            assert isinstance(env, dict)
            assert env["CI"] == "true"
            if command[1:3] == ["--sessions", "build-import-check"]:
                assert "PIP_CONSTRAINT" not in env
                return ""
            assert command[1:5] == ["--sessions", "docs-check", "ansible-lint", "docs"]
            assert env["PIP_CONSTRAINT"] == str(tmp_path / "lint-constraints.txt")
            return "Verified discovery and execution of 17 foundata rules.\n"
        if command[:3] == [str(tmp_path / "stock-lint/bin/python"), "-m", "nox"]:
            nox_runs.append(command)
            assert "--no-venv" in command
            env = kwargs["env"]
            assert isinstance(env, dict)
            assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / "stock-lint/bin")
            assert env["VIRTUAL_ENV"] == str(tmp_path / "stock-lint")
            return "ansible-lint-foundata is not installed; running stock rules only.\n"
        return ""

    monkeypatch.setattr("scripts.smoke.run", run)
    monkeypatch.setattr("scripts.smoke.lint_constraints", lambda: "")
    check_nox_gate(rendered)
    assert len(nox_runs) == 3
    assert config_lints == [["antsibull-nox", "lint-config"]]


@pytest.mark.parametrize("failure", [None, "verification", "lint"])
def test_repository_lint_requires_successful_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    calls = []
    env = {"ANSIBLE_CONFIG": "/expected/ansible.cfg"}

    def run(command: list[str], **kwargs: object) -> str:
        calls.append(command)
        assert command[:2] == [sys.executable, "-m"]
        assert kwargs == {"cwd": tmp_path, "env": env}
        stage = "verification" if command[2] == "ansible_lint_foundata.cli" else "lint"
        if stage == failure:
            raise GateError(stage)
        return ""

    monkeypatch.setattr("scripts.smoke.run", run)
    if failure:
        with pytest.raises(GateError, match=failure):
            lint_project(tmp_path, env)
    else:
        lint_project(tmp_path, env)
    assert calls[0][2:] == [
        "ansible_lint_foundata.cli",
        "verify",
        "--project-dir",
        str(tmp_path),
    ]
    assert len(calls) == (1 if failure == "verification" else 2)


@pytest.mark.parametrize("name", ["scripts", "pyproject.toml"])
def test_generated_projects_have_no_repository_tooling(
    tmp_path: Path, name: str
) -> None:
    path = tmp_path / name
    if name == "scripts":
        path.mkdir()
    else:
        path.touch()
    with pytest.raises(GateError, match="repository tooling"):
        check_tree(ROOT / "collection_default", tmp_path)


@pytest.mark.parametrize("profile", ["collection_modules_grid", "collection_modules"])
def test_skeleton_lint_configurations_stay_in_sync(profile: str) -> None:
    for name in (
        ".ansible-docsmith.toml.j2",
        ".ansible-lint.j2",
        ".yamllint.j2",
    ):
        assert (ROOT / "role_default" / name).read_bytes() == (
            ROOT / "collection_default" / name
        ).read_bytes()
    # Explicit paths keep missing or changed shared files in the comparison.
    for name in (
        ".gitattributes.j2",
        ".gitignore.j2",
        ".releasing.toml.j2",
        ".rumdl.toml.j2",
        ".yamllint.j2",
        "CHANGELOG.rst.j2",
        "CODE_OF_CONDUCT.md.j2",
        "REUSE.toml.j2",
        "changelogs/changelog.yaml.j2",
        "changelogs/config.yaml.j2",
        "changelogs/fragments/00-fragment-template.txt.j2",
        "extensions/molecule/default/requirements.yml.j2",
        "extensions/molecule/shared/playbooks/cleanup.yml.j2",
        "extensions/molecule/shared/playbooks/converge.yml.j2",
        "extensions/molecule/shared/playbooks/create-dispatch.yml.j2",
        "extensions/molecule/shared/playbooks/create-libvirt.yml.j2",
        "extensions/molecule/shared/playbooks/create-podman.yml.j2",
        "extensions/molecule/shared/playbooks/create.yml.j2",
        "extensions/molecule/shared/playbooks/destroy-dispatch.yml.j2",
        "extensions/molecule/shared/playbooks/destroy-libvirt.yml.j2",
        "extensions/molecule/shared/playbooks/destroy-podman.yml.j2",
        "extensions/molecule/shared/playbooks/destroy.yml.j2",
        "extensions/molecule/shared/playbooks/prepare.yml.j2",
        "extensions/molecule/shared/playbooks/side_effect.yml.j2",
        "extensions/molecule/shared/playbooks/verify.yml.j2",
        "extensions/molecule/shared/tasks/state_update.yml.j2",
        "extensions/molecule/shared/templates/cloud_init_user_data.yml.j2.j2",
        "extensions/molecule/shared/templates/libvirt_domain.xml.j2.j2",
        "requirements.txt.j2",
        "tests/config.yml.j2",
    ):
        assert (ROOT / "collection_default" / name).read_bytes() == (
            ROOT / profile / name
        ).read_bytes(), name
    # These static files only acquire a template suffix in the module skeleton.
    for name in (
        "LICENSES/GPL-3.0-or-later.txt",
        "changelogs/fragments/archive/.gitkeep",
    ):
        assert (ROOT / "collection_default" / name).read_bytes() == (
            ROOT / profile / f"{name}.j2"
        ).read_bytes(), name
    for name in (
        ".ansible-lint.j2",
        ".mypy.ini.j2",
        "ruff.toml.j2",
        "antsibull-nox.toml.j2",
        "noxfile.py.j2",
        "galaxy.yml.j2",
        "plugins/module_utils/_engine.py.j2",
        "plugins/module_utils/_activation.py.j2",
        "plugins/module_utils/_compat.py.j2",
        "tests/unit/plugins/conftest.py.j2",
        "tests/unit/plugins/modules/test_contract.py.j2",
        "tests/unit/plugins/modules/test_engine.py.j2",
        "tests/unit/plugins/modules/test_activation.py.j2",
        "tests/unit/plugins/modules/test_info_failure.py.j2",
        "docs/architecture.md.j2",
        "tools/inventory/extract.py.j2",
    ):
        assert (ROOT / "collection_modules_grid" / name).read_bytes() == (
            ROOT / "collection_modules" / name
        ).read_bytes(), name
    for skeleton in ("role_default", "collection_default"):
        assert not (ROOT / skeleton / "pyproject.toml").exists()
        assert not (ROOT / skeleton / "scripts").exists()
    config = yaml.safe_load((ROOT / "collection_default/.ansible-lint.j2").read_text())
    assert (
        len([rule for rule in config["enable_list"] if rule.startswith("foundata-")])
        == 17
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
    assert "foundata-string-quotes" in config["enable_list"]
    assert "foundata-parameter-order" in config["enable_list"]
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


def test_default_sessions_come_from_the_starred_listing() -> None:
    listing = (
        "Sessions defined in noxfile.py:\n\n"
        "* license-check -> Run license checker: reuse\n"
        "- ansible-test-sanity-2.19 -> Run sanity tests\n"
        "* changelog -> Changelog fragments\n\n"
        "sessions marked with * are selected, sessions marked with - are skipped.\n"
    )
    assert default_sessions(listing) == ["license-check", "changelog"]
