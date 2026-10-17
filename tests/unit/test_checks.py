# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Test failure propagation, isolation and check completeness."""

import ast
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

from scripts.check import check, require_complete_tests
from scripts.common import ROOT, GateError, Lane, isolated_environment, run, uv_command
from scripts.render import Rendered, render
from scripts.smoke import (
    IDENTITIES,
    check_fix_stability,
    check_nox_gate,
    check_tree,
    default_sessions,
    identity_sessions,
    install_scenario_collections,
    install_utils,
    lint_constraints,
    lint_project,
    lint_resources,
    module_smoke,
    newest_released_core,
    pinned_archives,
    pinned_utils_version,
    private_podman_store,
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
    assert env["ANSIBLE_REMOTE_TMP"] == str(tmp_path / "remote-tmp")
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
        pytest.fail("Checks started before rejecting the socket layout")

    monkeypatch.setattr("scripts.check.check_static", unexpected)
    # The check's runtime directory would be too deep for its sockets.
    base = tmp_path / ("x" * 104)
    base.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(base))
    output = tmp_path / "output"
    with pytest.raises(GateError, match="exceeds"):
        check(
            output,
            full=True,
            backends=["podman", "libvirt", "ee"],
            utils_source=tmp_path,
        )
    assert not output.exists()
    assert not list(base.iterdir())


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
    envdirs: set[Path] = set()

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
                "* helper-lint -> Ruff for Python helpers\n"
                "* docs -> Role documentation with DocSmith\n"
                "- molecule -> Molecule\n\n"
                "sessions marked with * are selected, sessions marked with - are skipped.\n"
            )
        if command[0] == "nox":
            nox_runs.append(command)
            assert "--no-venv" not in command
            # The environments stay in the render, reached through a short link.
            envdir = Path(command[command.index("--envdir") + 1])
            assert envdir.is_symlink()
            assert envdir.resolve() == tmp_path / "nox"
            assert envdir.parent.name.startswith("sk-")
            envdirs.add(envdir)
            env = kwargs["env"]
            assert isinstance(env, dict)
            assert env["CI"] == "true"
            if command[1:3] == ["--sessions", "build-import-check"]:
                assert "PIP_CONSTRAINT" not in env
                return ""
            assert command[1:6] == [
                "--sessions",
                "docs-check",
                "ansible-lint",
                "helper-lint",
                "docs",
            ]
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
    assert len(envdirs) == 1
    assert not envdirs.pop().parent.exists()


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


SKELETONS = (
    "role_default",
    "collection_default",
    "collection_modules",
    "collection_modules_appliance",
)
SKELETON_GROUPS = {
    "all": SKELETONS,
    "role_and_collection_default": ("role_default", "collection_default"),
    "collections": SKELETONS[1:],
    "module_skeletons": ("collection_modules", "collection_modules_appliance"),
}
# Files that must stay identical within a group outside their variant regions,
# named by their path in collection_default. Explicit paths keep a missing copy
# in the comparison; a file belongs to the largest group that shares it.
SHARED_FILES = {
    "all": (
        ".ansible-lint.j2",
        ".gitattributes.j2",
        ".gitignore.j2",
        ".rumdl.toml.j2",
        ".yamllint.j2",
        "CODE_OF_CONDUCT.md.j2",
        "LICENSES/GPL-3.0-or-later.txt",
        "REUSE.toml.j2",
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
    ),
    "role_and_collection_default": (
        ".ansible-docsmith.toml.j2",
        "extensions/molecule/README.md.j2",
        "extensions/molecule/default/inventory/hosts.yml.j2",
        "extensions/molecule/default/molecule.yml.j2",
        "extensions/molecule/default/tasks/prepare/.gitkeep",
        "extensions/molecule/default/tasks/side_effect/.gitkeep",
        "extensions/molecule/default/tasks/verify/.gitkeep",
        "extensions/molecule/ee/inventory/hosts.yml.j2",
        "extensions/molecule/ee/molecule.yml.j2",
        "extensions/molecule/ee/requirements.yml.j2",
        "extensions/molecule/ee/tasks/converge/artifact-ee.yml.j2",
        "extensions/molecule/ee/tasks/destroy/ee.yml.j2",
        "extensions/molecule/ee/tasks/prepare/all.yml.j2",
        "extensions/molecule/ee/tasks/prepare/localhost.yml.j2",
        "extensions/molecule/ee/tasks/verify/ee.yml.j2",
    ),
    "collections": (
        ".releasing.toml.j2",
        "CHANGELOG.rst.j2",
        "changelogs/changelog.yaml.j2",
        "changelogs/config.yaml.j2",
        "changelogs/fragments/00-fragment-template.txt.j2",
        "changelogs/fragments/archive/.gitkeep",
        "galaxy.yml.j2",
        "meta/runtime.yml.j2",
        "requirements-dev.txt.j2",
        "ruff.toml.j2",
        "tests/config.yml.j2",
        "tests/runtime_check.py.j2",
    ),
    "module_skeletons": (
        ".mypy.ini.j2",
        "DEVELOPMENT.md.j2",
        "antsibull-nox.toml.j2",
        "extensions/molecule/README.md.j2",
        "extensions/molecule/default/inventory/hosts.yml.j2",
        "extensions/molecule/default/molecule.yml.j2",
        "extensions/molecule/default/tasks/cleanup/fixture_nested.yml.j2",
        "extensions/molecule/default/tasks/cleanup/loss.yml.j2",
        "extensions/molecule/default/tasks/converge/fixture_nested.yml.j2",
        "extensions/molecule/default/tasks/side_effect/fixture_nested.yml.j2",
        "extensions/molecule/default/tasks/side_effect/zz_loss.yml.j2",
        "extensions/molecule/default/tasks/verify/fixture_nested.yml.j2",
        "extensions/molecule/shared/files/loss.py.j2",
        "noxfile.py.j2",
        "plugins/module_utils/_activation.py.j2",
        "plugins/module_utils/_binding.py.j2",
        "plugins/module_utils/_compat.py.j2",
        "plugins/module_utils/_engine.py.j2",
        "plugins/module_utils/_fixture_nested.py.j2",
        "plugins/module_utils/_lifecycle.py.j2",
        "plugins/modules/fixture_nested.py.j2",
        "tests/integration/targets/fixture_nested/aliases.j2",
        "tests/integration/targets/fixture_nested/meta/main.yml.j2",
        "tests/integration/targets/fixture_nested/tasks/main.yml.j2",
        "tests/unit/plugins/conftest.py.j2",
        "tests/unit/plugins/modules/test_activation.py.j2",
        "tests/unit/plugins/modules/test_contract.py.j2",
        "tests/unit/plugins/modules/test_engine.py.j2",
        "tests/unit/plugins/modules/test_fixture_nested.py.j2",
        "tests/unit/plugins/modules/test_info_failure.py.j2",
        "tests/unit/plugins/modules/test_lifecycle.py.j2",
        "tools/inventory/extract.py.j2",
    ),
}
# Static files that only acquire a template suffix in the module skeletons.
MODULE_SKELETON_TEMPLATES = frozenset(
    ("LICENSES/GPL-3.0-or-later.txt", "changelogs/fragments/archive/.gitkeep")
)
# Lines a skeleton needs differently in an otherwise shared file. Galaxy renders
# the Jinja comment lines to nothing; see DEVELOPMENT.md.
VARIANT_REGION = re.compile(
    r"^\{# skeleton-variant: (?P<name>[a-z][a-z0-9-]*) #\}\n"
    r".*?"
    r"^\{# end skeleton-variant #\}\n",
    re.DOTALL | re.MULTILINE,
)
VARIANT_MARKER = re.compile(r"^\{# (end )?skeleton-variant\b.*#\}\n", re.MULTILINE)


def shared_path(skeleton: str, name: str) -> Path:
    """Path of the shared file ``name`` (as in collection_default) in a skeleton."""
    if skeleton == "role_default" and name.startswith("extensions/molecule/"):
        name = name.removeprefix("extensions/")
    if skeleton in SKELETON_GROUPS["module_skeletons"] and (
        name in MODULE_SKELETON_TEMPLATES
    ):
        name = f"{name}.j2"
    return ROOT / skeleton / name


def shared_text(path: Path) -> str:
    """The shared part of a file: each variant region is reduced to its name."""
    text = VARIANT_REGION.sub(
        lambda region: f"[variant {region['name']}]\n",
        path.read_text(encoding="utf-8"),
    )
    assert "skeleton-variant" not in text, f"Unbalanced variant marker in {path}"
    return text


def source_yaml(path: Path) -> Any:
    """Parse a YAML template source as Galaxy renders its variant markers."""
    return yaml.safe_load(VARIANT_MARKER.sub("", path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("other", "same"),
    [
        ("a\n{# skeleton-variant: x #}\n2\n3\n{# end skeleton-variant #}\nb\n", True),
        ("a\n{# skeleton-variant: x #}\n{# end skeleton-variant #}\nb\n", True),
        ("a\n{# skeleton-variant: y #}\n1\n{# end skeleton-variant #}\nb\n", False),
        ("a\n{# skeleton-variant: x #}\n1\n{# end skeleton-variant #}\nc\n", False),
        ("a\n1\nb\n", False),
    ],
)
def test_variant_regions_alone_may_differ(
    tmp_path: Path, other: str, same: bool
) -> None:
    reference = tmp_path / "reference"
    reference.write_text(
        "a\n{# skeleton-variant: x #}\n1\n{# end skeleton-variant #}\nb\n",
        encoding="utf-8",
    )
    (tmp_path / "other").write_text(other, encoding="utf-8")
    assert (shared_text(reference) == shared_text(tmp_path / "other")) is same


@pytest.mark.parametrize(
    "text",
    [
        "{# skeleton-variant: x #}\n1\n",
        "1\n{# end skeleton-variant #}\n",
        "{# skeleton-variant: x #}\n{# skeleton-variant: y #}\n"
        "{# end skeleton-variant #}\n{# end skeleton-variant #}\n",
        "{# skeleton-variant: Bad Name #}\n{# end skeleton-variant #}\n",
    ],
)
def test_unbalanced_variant_markers_are_rejected(tmp_path: Path, text: str) -> None:
    path = tmp_path / "shared"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(AssertionError, match="Unbalanced variant marker"):
        shared_text(path)


@pytest.mark.parametrize(
    ("group", "name"),
    [
        pytest.param(group, name, id=f"{group}:{name}")
        for group, names in SHARED_FILES.items()
        for name in names
    ],
)
def test_shared_skeleton_files_are_identical(group: str, name: str) -> None:
    paths = {
        skeleton: shared_path(skeleton, name) for skeleton in SKELETON_GROUPS[group]
    }
    missing = [skeleton for skeleton, path in paths.items() if not path.is_file()]
    assert not missing, f"{name} is missing in: {', '.join(missing)}"
    reference, *others = paths
    content = shared_text(paths[reference])
    differing = [
        skeleton for skeleton in others if shared_text(paths[skeleton]) != content
    ]
    assert not differing, f"{name} differs from {reference} in: {', '.join(differing)}"


def test_shared_files_are_declared_once_per_skeleton() -> None:
    assert set(SHARED_FILES) <= set(SKELETON_GROUPS)
    seen: set[tuple[str, str]] = set()
    for group, names in SHARED_FILES.items():
        assert len(names) == len(set(names)), group
        for name in names:
            # A path may be shared twice only by disjoint groups.
            pairs = {(skeleton, name) for skeleton in SKELETON_GROUPS[group]}
            assert not seen & pairs, f"{name} is declared twice"
            seen |= pairs


def readme_closing_sections(skeleton: str) -> str:
    """The README from the licensing section to the end."""
    text = (ROOT / skeleton / "README.md.j2").read_text(encoding="utf-8")
    start = text.find("\n## Licensing, copyright")
    assert start != -1, f"{skeleton} README has no licensing section"
    return text[start:]


@pytest.mark.parametrize("skeleton", SKELETON_GROUPS["collections"])
def test_collection_release_tool_requires_sanity_gate(skeleton: str) -> None:
    path = ROOT / skeleton / "requirements-dev.txt.j2"
    requirements = [
        Requirement(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    release_tool = next(item for item in requirements if item.name == "releasing")
    assert "7.0.0" in release_tool.specifier
    assert "6.0.0" not in release_tool.specifier


def test_collection_readme_closing_sections_are_identical() -> None:
    reference, *others = SKELETON_GROUPS["collections"]
    expected = readme_closing_sections(reference)
    differing = [
        skeleton for skeleton in others if readme_closing_sections(skeleton) != expected
    ]
    assert not differing, f"README closing sections differ in: {', '.join(differing)}"


def test_template_inputs_are_not_linted_as_ansible_templates() -> None:
    config = source_yaml(ROOT / "collection_default/.ansible-lint.j2")
    assert config["kinds"] == [
        {"inventory": "extensions/molecule/**/inventory/*.yml.j2"},
    ]


@pytest.mark.parametrize("skeleton", SKELETONS)
def test_skeletons_ship_no_repository_tooling(skeleton: str) -> None:
    assert not (ROOT / skeleton / "pyproject.toml").exists()
    assert not (ROOT / skeleton / "scripts").exists()


def test_skeletons_enable_the_same_foundata_rules() -> None:
    enabled = {
        skeleton: [
            rule
            for rule in source_yaml(ROOT / skeleton / ".ansible-lint.j2")["enable_list"]
            if rule.startswith("foundata-")
        ]
        for skeleton in SKELETONS
    }
    reference = enabled["collection_default"]
    assert len(reference) == len(set(reference))
    # The verify command only rejects unknown rules; this count keeps every rule
    # that ansible-lint-foundata ships enabled.
    assert len(reference) == 17
    assert {"foundata-string-quotes", "foundata-parameter-order"} <= set(reference)
    assert all(rules == reference for rules in enabled.values()), enabled


def test_collection_default_lint_configuration() -> None:
    config = source_yaml(ROOT / "collection_default/.ansible-lint.j2")
    assert config["exclude_paths"] == [
        "tools/",
        ".cache/",
        ".git/",
        ".hg/",
        ".svn/",
        ".tox/",
        ".nox/",
        ".venv/",
        "changelogs/changelog.yaml",
        "changelogs/.plugin-cache.yaml",
    ]
    yaml_config = yaml.safe_load(
        (ROOT / "collection_default/.yamllint.j2").read_text(encoding="utf-8")
    )
    assert yaml_config["rules"]["empty-lines"] == {
        "max": 2,
        "max-start": 0,
        "max-end": 0,
    }


@pytest.mark.parametrize("pins", [0, 1, 2])
def test_module_checks_need_exactly_one_utils_pin(tmp_path: Path, pins: int) -> None:
    collections = [{"name": "community.general", "version": "13.2.0"}]
    collections += [{"name": "foundata.utils", "version": "1.0.0"}] * pins
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/requirements.yml").write_text(
        yaml.safe_dump({"collections": collections}), encoding="utf-8"
    )
    if pins == 1:
        assert pinned_utils_version(tmp_path) == "1.0.0"
    else:
        with pytest.raises(GateError, match="exactly once"):
            pinned_utils_version(tmp_path)


def test_module_identities_split_the_ansible_test_lanes() -> None:
    # Quick checks run the default sessions only.
    assert identity_sessions(0, full=False) == [[]]
    assert identity_sessions(1, full=False) == [[]]
    # The first identity runs every lane on every core.
    assert identity_sessions(0, full=True) == [
        [],
        ["ansible-test-sanity"],
        ["ansible-test-units"],
        ["ansible-test-integration"],
    ]
    # The second identity's long names reach validate-modules and ansible-doc
    # in sanity, once, on the newest released core of the matrix.
    assert identity_sessions(1, full=True) == [[], ["ansible-test-sanity-2.21"]]


@pytest.mark.parametrize("identity", [None, 0, 1])
def test_module_identities_run_alone_or_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identity: int | None
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        "scripts.smoke.check_missing_product",
        lambda _output, _profile: calls.append("missing"),
    )
    monkeypatch.setattr(
        "scripts.smoke.module_identity",
        lambda _output, index, **_options: calls.append(index),
    )
    output = tmp_path / "collection_modules"
    if identity is not None:
        # The other identity's step may have created the directory first.
        output.mkdir()
    module_smoke(output, profile="collection_modules", identity=identity)
    assert calls == {None: ["missing", 0, 1], 0: ["missing", 0], 1: [1]}[identity]
    # The check writes the profile record once both identity steps passed.
    assert (output / "profile.json").exists() == (identity is None)


def test_an_interrupted_identity_removes_its_containers_and_runtime_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime_base: Path
) -> None:
    runtimes: list[Path] = []
    removed: list[Path] = []

    def interrupted(_output: Path, index: int, *, runtime: Path, **_: object) -> None:
        runtimes.append(runtime)
        raise KeyboardInterrupt

    monkeypatch.setattr("scripts.smoke.module_identity", interrupted)
    monkeypatch.setattr("scripts.smoke.remove_render_containers", removed.append)
    output = tmp_path / "collection_modules"
    with pytest.raises(KeyboardInterrupt):
        module_smoke(output, profile="collection_modules", identity=1)
    assert removed == [output / IDENTITIES[1][1]]
    assert runtimes[0].parent == runtime_base
    assert not runtimes[0].exists()


def test_module_render_keeps_its_podman_run_state_to_itself(tmp_path: Path) -> None:
    env = isolated_environment(tmp_path)
    rendered = Rendered(
        tmp_path, tmp_path / "role", tmp_path / "collection", env, "collection_modules"
    )
    run_root = tmp_path / "runtime/p0"
    private_podman_store(rendered, run_root)
    config = tmp_path / "home/.config/containers/storage.conf"
    storage = tomllib.loads(config.read_text(encoding="utf-8"))["storage"]
    assert Path(storage["graphroot"]).is_relative_to(tmp_path / "home")
    # The run state goes to the runtime directory; Podman creates it on use.
    assert storage["runroot"] == str(run_root)
    assert not run_root.exists()
    assert "options" not in config.read_text(encoding="utf-8")
    private_podman_store(rendered, run_root, "/user/store")
    options = tomllib.loads(config.read_text(encoding="utf-8"))["storage"]["options"]
    # ansible-test finds the user's images there and pulls nothing.
    assert options == {"additionalimagestores": ["/user/store"]}


def write_pinned_archives(directory: Path) -> None:
    directory.mkdir()
    for archive in (
        "containers-podman-1.20.2.tar.gz",
        "community-crypto-3.3.0.tar.gz",
        "community-general-13.2.0.tar.gz",
        "foundata-utils-1.0.0.tar.gz",
    ):
        (directory / archive).write_bytes(b"archive")


def test_pinned_archives_follow_the_requirements(tmp_path: Path) -> None:
    downloads = tmp_path / "collections"
    write_pinned_archives(downloads)
    archives = pinned_archives(downloads)
    assert list(archives) == [
        "containers.podman",
        "community.crypto",
        "community.general",
        "foundata.utils",
    ]
    assert archives["foundata.utils"] == downloads / "foundata-utils-1.0.0.tar.gz"
    (downloads / "community-crypto-3.3.0.tar.gz").unlink()
    with pytest.raises(GateError, match=re.escape("community.crypto 3.3.0")):
        pinned_archives(downloads)


def test_downloaded_collections_replace_galaxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloads = tmp_path / "collections"
    write_pinned_archives(downloads)
    commands: list[list[str]] = []

    def record(command: list[str], **_options: object) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr("scripts.smoke.run", record)
    monkeypatch.setattr("scripts.smoke.lint_project", lambda *_: None)
    monkeypatch.setattr("scripts.smoke.check_fix_stability", lambda *_: None)
    monkeypatch.setattr("scripts.smoke.check_nox_gate", lambda _rendered: None)
    rendered = Rendered(
        tmp_path,
        tmp_path / "role",
        tmp_path / "collection",
        {"ANSIBLE_COLLECTIONS_PATH": str(tmp_path / "installed")},
    )
    install_utils(rendered, collections=downloads)
    lint_resources(rendered, downloads)
    installs = [
        command
        for command in commands
        if command[:3] == ["ansible-galaxy", "collection", "install"]
    ]
    assert installs[0][-1] == str(downloads / "foundata-utils-1.0.0.tar.gz")
    assert sorted(installs[1][4:]) == sorted(str(path) for path in downloads.iterdir())
    assert not any("requirements" in part for command in installs for part in command)


def test_the_scenario_collections_come_from_the_run_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloads = tmp_path / "collections"
    write_pinned_archives(downloads)
    scenario = tmp_path / "collection/extensions/molecule/default"
    scenario.mkdir(parents=True)
    requirements = scenario / "requirements.yml"
    requirements.write_text(
        "collections:\n"
        '  - "containers.podman"\n'
        '  - name: "community.crypto"\n'
        '  - "community.general"\n',
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def record(command: list[str], **_options: object) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr("scripts.smoke.run", record)
    rendered = Rendered(
        tmp_path,
        tmp_path / "role",
        tmp_path / "collection",
        {"ANSIBLE_COLLECTIONS_PATH": str(tmp_path / "installed")},
    )
    install_scenario_collections(rendered, downloads)
    assert commands == [
        [
            "ansible-galaxy",
            "collection",
            "install",
            "--no-deps",
            "-p",
            str(tmp_path / "installed"),
            str(downloads / "containers-podman-1.20.2.tar.gz"),
            str(downloads / "community-crypto-3.3.0.tar.gz"),
            str(downloads / "community-general-13.2.0.tar.gz"),
        ]
    ]
    requirements.write_text('collections:\n  - "ansible.posix"\n', encoding="utf-8")
    with pytest.raises(GateError, match=r"tests/requirements.yml: ansible\.posix"):
        install_scenario_collections(rendered, downloads)


def test_newest_released_core_follows_the_matrix(tmp_path: Path) -> None:
    assert newest_released_core() == "2.21"
    (tmp_path / "pyproject.toml").write_text(
        "[tool.skeletons]\n"
        "matrix = [\n"
        '  { python = "3.14", group = "core221" },\n'
        '  { python = "3.14", group = "core310" },\n'
        '  { python = "3.12", group = "core219" },\n'
        "]\n",
        encoding="utf-8",
    )
    assert newest_released_core(tmp_path) == "3.10"
    (tmp_path / "pyproject.toml").write_text(
        '[tool.skeletons]\nmatrix = [{ python = "3.14", group = "lint" }]\n',
        encoding="utf-8",
    )
    with pytest.raises(GateError, match="core series"):
        newest_released_core(tmp_path)


@pytest.mark.parametrize(
    "profile", ["collection_modules", "collection_modules_appliance"]
)
def test_pinned_utils_release_satisfies_the_skeleton_dependency(profile: str) -> None:
    galaxy = (ROOT / profile / "galaxy.yml.j2").read_text(encoding="utf-8")
    match = re.search(r'^  "foundata\.utils": "([^"]+)"$', galaxy, re.MULTILINE)
    assert match is not None
    assert pinned_utils_version() in SpecifierSet(match[1])


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


class HelperSkip(Exception):
    """Represent a skipped generated helper session without loading Nox."""


class HelperSession:
    """Execute the generated session's Ruff commands without installing tools."""

    def __init__(self) -> None:
        self.posargs: list[str] = []
        self.commands: list[tuple[str, ...]] = []

    def run(self, *command: str, silent: bool = False) -> str:
        self.commands.append(command)
        assert command[0] == "ruff"
        result = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        return result.stdout

    def skip(self, message: str) -> None:
        raise HelperSkip(message)

    def error(self, message: str) -> None:
        raise ValueError(message)


def load_helper_session(
    profile: str, *, check_only: bool
) -> Callable[[HelperSession], None]:
    tree = ast.parse((ROOT / profile / "noxfile.py.j2").read_text(encoding="utf-8"))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "helper_lint"
    ]
    assert len(functions) == 1
    function = functions[0]
    decorators = [ast.unparse(decorator) for decorator in function.decorator_list]
    assert decorators == [
        "nox.session(name='helper-lint', default=True)",
        "antsibull_nox.sessions.install_packages(packages=['ruff'])",
    ]
    function.decorator_list = []
    namespace: dict[str, Any] = {
        "Path": Path,
        "CHECK_ONLY": check_only,
        "nox": SimpleNamespace(Session=HelperSession),
    }
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "<helper-lint>", "exec"),
        namespace,
    )
    return cast(Callable[[HelperSession], None], namespace["helper_lint"])


@pytest.fixture(params=SKELETON_GROUPS["collections"])
def helper_project(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    profile = request.param
    assert isinstance(profile, str)
    monkeypatch.chdir(tmp_path)
    run(["git", "init", "--quiet"], cwd=tmp_path)
    (tmp_path / "ruff.toml").write_text(
        (ROOT / profile / "ruff.toml.j2").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return profile


def test_helper_sessions_are_identical() -> None:
    functions = []
    for profile in SKELETON_GROUPS["collections"]:
        tree = ast.parse((ROOT / profile / "noxfile.py.j2").read_text(encoding="utf-8"))
        functions.append(
            ast.dump(
                next(
                    node
                    for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "helper_lint"
                )
            )
        )
    assert len(set(functions)) == 1


def test_helper_session_covers_all_roots_without_executing_files(
    helper_project: str, tmp_path: Path
) -> None:
    paths = [
        "extensions/molecule/default/files/acceptance.py",
        "extensions/molecule/focused/files/test_browser.py",
        "roles/first/files/guard.py",
        "roles/second/files/nested/guard.py",
        "tools/inventory/extract.py",
        "scripts/maintenance.py",
        "tests/runtime_check.py",
    ]
    for name in paths:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            'raise RuntimeError("Must not execute helpers")\n', encoding="utf-8"
        )
    session = HelperSession()
    load_helper_session(helper_project, check_only=True)(session)
    assert len(session.commands) == 3
    assert set(session.commands[0][-6:]) == {
        "extensions/molecule",
        "tools",
        "scripts",
        "tests/runtime_check.py",
        "roles/first/files",
        "roles/second/files",
    }
    assert all("--fix" not in command for command in session.commands)
    assert session.commands[-1][:3] == ("ruff", "format", "--check")


@pytest.mark.parametrize("empty_directory", [False, True])
def test_empty_helper_session_never_falls_back_to_repository_root(
    helper_project: str, tmp_path: Path, empty_directory: bool
) -> None:
    (tmp_path / "unrelated.py").write_text("invalid python !\n", encoding="utf-8")
    if empty_directory:
        (tmp_path / "tools").mkdir()
    session = HelperSession()
    with pytest.raises(HelperSkip, match="No Python"):
        load_helper_session(helper_project, check_only=True)(session)
    assert len(session.commands) == int(empty_directory)


def test_helper_session_respects_exclusions_and_python_only_scope(
    helper_project: str, tmp_path: Path
) -> None:
    config = tmp_path / "ruff.toml"
    config.write_text(
        'extend-exclude = ["tools/vendor"]\n' + config.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text("tools/ignored.py\n", encoding="utf-8")
    for name in (
        "tools/ignored.py",
        "tools/vendor/invalid.py",
        "tools/.venv/invalid.py",
        "tools/template.py.j2",
        "tools/notebook.ipynb",
        "tools/script.rb",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("invalid python !\n", encoding="utf-8")
    session = HelperSession()
    with pytest.raises(HelperSkip, match="after Ruff exclusions"):
        load_helper_session(helper_project, check_only=True)(session)
    assert len(session.commands) == 1


@pytest.mark.parametrize(
    "content", ["print(missing_name)\n", "VALUE=1\n", "import sys\n"]
)
def test_helper_ci_reports_failure_without_rewriting(
    helper_project: str, tmp_path: Path, content: str
) -> None:
    config = tmp_path / "ruff.toml"
    config.write_text(
        "fix = true\nunsafe-fixes = true\n" + config.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    path = tmp_path / "tools/helper.py"
    path.parent.mkdir()
    path.write_text(content, encoding="utf-8")
    session = HelperSession()
    with pytest.raises(subprocess.CalledProcessError):
        load_helper_session(helper_project, check_only=True)(session)
    assert path.read_text(encoding="utf-8") == content
    assert all("--fix" not in command for command in session.commands)


def test_helper_local_mode_fixes_imports_and_formatting_only(
    helper_project: str, tmp_path: Path
) -> None:
    path = tmp_path / "tools/helper.py"
    path.parent.mkdir()
    path.write_text(
        'import sys\nimport os\n\nVALUE={name: True for name in ("one", "two")}\n'
        "print(sys.version, os.name, VALUE)\n",
        encoding="utf-8",
    )
    session = HelperSession()
    with pytest.raises(subprocess.CalledProcessError):
        load_helper_session(helper_project, check_only=False)(session)
    updated = path.read_text(encoding="utf-8")
    assert updated.startswith("import os\nimport sys\n")
    assert 'VALUE = {name: True for name in ("one", "two")}' in updated
    fixes = [command for command in session.commands if "--fix" in command]
    assert len(fixes) == 1
    assert fixes[0][:5] == ("ruff", "check", "--select", "I", "--fix")
    assert all("--unsafe-fixes" not in command for command in session.commands)


def test_helper_ci_rejects_arguments_that_could_override_check_mode(
    helper_project: str,
) -> None:
    session = HelperSession()
    session.posargs = ["--fix"]
    with pytest.raises(ValueError, match="accepts no arguments"):
        load_helper_session(helper_project, check_only=True)(session)
    assert not session.commands


def test_helper_local_formatting_is_stable(helper_project: str, tmp_path: Path) -> None:
    path = tmp_path / "tools/helper.py"
    path.parent.mkdir()
    path.write_text(
        "import sys\nimport os\n\nVALUE=1\nprint(sys.version, os.name, VALUE)\n",
        encoding="utf-8",
    )
    helper = load_helper_session(helper_project, check_only=False)
    first = HelperSession()
    helper(first)
    formatted = path.read_bytes()
    assert b"VALUE = 1\n" in formatted
    assert len(first.commands) == 5
    helper(HelperSession())
    assert path.read_bytes() == formatted
    load_helper_session(helper_project, check_only=True)(HelperSession())
