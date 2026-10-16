# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for the independent module skeleton and its rendering."""

import ast
import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from scripts.common import ROOT, GateError
from scripts.render import example_metadata, render
from scripts.smoke import identity_sessions


@pytest.mark.parametrize(
    "profile", ["collection_modules_appliance", "collection_modules"]
)
@pytest.mark.parametrize(
    "product",
    [
        "Example Product",
        'Other "Quoted" Product',
        "Triple \"\"\" and '''",
        "Path\\name\nnext line",
        "Unicode ä😀",
    ],
)
def test_inventory_product_literal_preserves_content_and_formatting(
    profile: str, product: str
) -> None:
    source = (ROOT / profile / "tools/inventory/extract.py.j2").read_text()
    expression = (
        "{{ product | default('Example Product') | to_json(ensure_ascii=False) }}"
    )
    assert expression in source
    source = source.replace(expression, json.dumps(product, ensure_ascii=False))
    product_line = next(line for line in source.splitlines() if '"product":' in line)
    assert ast.literal_eval("{" + product_line + "}")["product"] == product
    subprocess.run(
        [
            "ruff",
            "format",
            "--check",
            "--config",
            str(ROOT / profile / "ruff.toml.j2"),
            "--stdin-filename",
            "tools/inventory/extract.py",
            "-",
        ],
        input=source,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "profile", ["collection_modules_appliance", "collection_modules"]
)
def test_release_gate_runs_unrestricted_sanity_for_every_series(profile: str) -> None:
    # The first identity runs the meta sessions, which cover every series.
    assert identity_sessions(0, full=True)[1:] == [
        ["ansible-test-sanity"],
        ["ansible-test-units"],
        ["ansible-test-integration"],
    ]
    with (ROOT / profile / "antsibull-nox.toml.j2").open("rb") as stream:
        config = tomllib.load(stream)
    sanity = config["sessions"]["ansible_test_sanity"]
    assert sanity == {
        "skip_tests": ["changelog", "no-smart-quotes"],
        "include_devel": True,
    }
    assert config["collection"] == {"min_python_version": "ansible-test-config"}


@pytest.mark.parametrize(
    "profile", ["collection_modules_appliance", "collection_modules"]
)
def test_module_skeleton_layout(profile: str) -> None:
    skeleton = ROOT / profile
    assert all(
        path.name.endswith(".j2") for path in skeleton.rglob("*") if path.is_file()
    )
    assert (skeleton / "requirements.txt.j2").is_file()
    assert (skeleton / "tests/config.yml.j2").is_file()
    assert (skeleton / "plugins/modules/fixture.py.j2").is_file()
    assert not (skeleton / "roles").exists()


def test_act_once_is_appliance_only() -> None:
    for profile in ("collection_modules", "collection_modules_appliance"):
        skeleton = ROOT / profile
        appliance = profile.endswith("_appliance")
        assert (
            skeleton / "plugins/module_utils/_act_once.py.j2"
        ).is_file() is appliance
        assert (
            skeleton / "tests/unit/plugins/modules/test_act_once.py.j2"
        ).is_file() is appliance
        base = (skeleton / "plugins/module_utils/_lifecycle.py.j2").read_text()
        for hook in ("act_once", "preflight", "activate", "effective", "activation_id"):
            assert f"def {hook}(" not in base


def test_management_guard_is_appliance_only() -> None:
    for profile in ("collection_modules", "collection_modules_appliance"):
        skeleton = ROOT / profile
        appliance = profile.endswith("_appliance")
        assert (
            skeleton / "plugins/module_utils/_management_guard.py.j2"
        ).is_file() is appliance
        assert (
            skeleton / "tests/unit/plugins/modules/test_management_guard.py.j2"
        ).is_file() is appliance
        family = (skeleton / "plugins/module_utils/_fixture_family.py.j2").read_text()
        assert ("protect_listener" in family) is appliance


def test_recovery_stage_is_appliance_only() -> None:
    paths = (
        "shared/tasks/recovery.yml.j2",
        "shared/tasks/recovery_fresh.yml.j2",
        "shared/playbooks/recovery.yml.j2",
        "shared/files/recovery_dns.py.j2",
        "default/tasks/recovery/induce.yml.j2",
        "default/tasks/recovery/absent.yml.j2",
        "default/tasks/side_effect/zz_recovery.yml.j2",
    )
    for profile in ("collection_modules", "collection_modules_appliance"):
        molecule = ROOT / profile / "extensions/molecule"
        for relative in paths:
            assert (molecule / relative).is_file() is profile.endswith("_appliance")
        assert not (molecule / "recovery/molecule.yml.j2").exists()
    scenario = ROOT / "collection_modules_appliance/extensions/molecule/default"
    stages = sorted((scenario / "tasks/side_effect").glob("*.yml.j2"))
    assert stages[-1].name == "zz_recovery.yml.j2"
    stage = stages[-1].read_text()
    assert "'console' not in __product_credentials" in stage
    assert "Recovery drill skipped:" in stage


# REUSE-IgnoreStart
@pytest.mark.parametrize("name", ["collection_modules_appliance", "collection_modules"])
def test_python_templates_carry_the_license_header(name: str) -> None:
    profile = ROOT / name
    holder = (
        "# SPDX-FileCopyrightText: {{ company | default('FIXME your organization')"
        " ~ (' <' ~ company_url ~ '>' if company_url | default('') else '') }}"
    )
    for path in profile.rglob("*.py.j2"):
        head = path.read_text().splitlines()[:20]
        if not head:
            continue  # An empty package marker stays empty.
        assert holder in head, path
        assert "# SPDX-License-Identifier: GPL-3.0-or-later" in head, path
        if path.parent == profile / "plugins/modules":
            assert "GNU General Public License v3.0+" in "\n".join(head), path


# REUSE-IgnoreEnd


@pytest.mark.parametrize(
    "scenario",
    [
        "role_default/molecule/default",
        "collection_default/extensions/molecule/default",
        "collection_modules_appliance/extensions/molecule/default",
        "collection_modules/extensions/molecule/default",
    ],
)
def test_molecule_inventory_render_name(scenario: str) -> None:
    directory = ROOT / scenario
    assert (directory / "inventory/hosts.yml.j2").is_file()
    assert not (directory / "inventory/hosts.yml.j2.j2").exists()
    assert 'hosts: "inventory/hosts.yml"' in (directory / "molecule.yml.j2").read_text()


@pytest.mark.parametrize(
    "filename,sections",
    [
        (
            "README.md.j2",
            [
                "Included content",
                "Dependencies",
                "Licensing, copyright",
                "Author information",
            ],
        ),
        ("DEVELOPMENT.md.j2", ["Prerequisites", "Checks", "Releases"]),
    ],
)
@pytest.mark.parametrize(
    "profile", ["collection_modules_appliance", "collection_modules"]
)
def test_module_skeleton_has_standard_documentation(
    filename: str, sections: list[str], profile: str
) -> None:
    source = (ROOT / profile / filename).read_text()
    for section in sections:
        assert f"## {section}<a id=" in source


@pytest.mark.parametrize(
    "profile", ["collection_modules_appliance", "collection_modules"]
)
def test_module_render_does_not_require_default_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    source = tmp_path / "source"
    for name in ("role_default", profile):
        (source / name).mkdir(parents=True)
    monkeypatch.setattr("scripts.render.run", lambda *args, **kwargs: "")
    rendered = render(tmp_path / "render", source=source, profile=profile)
    assert rendered.profile == profile


@pytest.mark.parametrize(
    "namespace,name,role",
    [
        ("test_one", "first", "example_role"),
        ("test_two", "second", "other_role"),
        ("n" * 64, "c" * 64, "r" * 64),
    ],
)
@pytest.mark.parametrize(
    "profile",
    ["collection_default", "collection_modules_appliance", "collection_modules"],
)
def test_render_selects_profile_and_preserves_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    namespace: str,
    name: str,
    role: str,
    profile: str,
) -> None:
    calls: list[list[str]] = []

    def command(argv: list[str], **kwargs: object) -> str:
        calls.append(argv)
        return ""

    monkeypatch.setattr("scripts.render.run", command)
    metadata = {
        **example_metadata(punctuation=True),
        "product": 'A "Product"',
    }
    rendered = render(
        tmp_path / "render",
        namespace=namespace,
        collection_name=name,
        role_name=role,
        profile=profile,
        metadata=metadata,
    )
    assert rendered.profile == profile
    assert calls[1][calls[1].index("--collection-skeleton") + 1] == str(ROOT / profile)
    assert calls[1][-1] == f"{namespace}.{name}"
    assert calls[0][-1] == role
    assert json.loads((rendered.work / "extra-vars.json").read_text()) == metadata


def test_invalid_profile_never_creates_output(tmp_path: Path) -> None:
    output = tmp_path / "invalid"
    with pytest.raises(GateError, match="Unknown collection profile"):
        render(output, profile="unknown")
    assert not output.exists()


@pytest.mark.parametrize(
    "profile", ["collection_modules_appliance", "collection_modules"]
)
def test_engine_contains_no_protocol_literals(profile: str) -> None:
    source = (ROOT / profile / "plugins/module_utils/_engine.py.j2").read_text()
    source = source.replace("{{ namespace }}", "example").replace(
        "{{ collection_name }}", "product"
    )
    forbidden = {
        ",",
        "0",
        "1",
        "uuid",
        "UUID",
        r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
        "id",
        "search_item",
        "get_item",
        "add_item",
        "set_item",
        "del_item",
        "saved",
        "result",
        "selected",
        "deleted",
        "rows",
        "total",
        "current",
        "rowCount",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "GET",
    }
    tree = ast.parse(source)
    docstrings = {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
    }
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
    }
    tokens = {
        token for value in literals for token in re.findall(r"[A-Za-z_]\w*", value)
    }
    assert not (tokens | literals) & forbidden
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr in {"read", "write", "search", "_request"}
        for node in ast.walk(tree)
    )
    # Product limits belong to definitions: numbers other than 0 and 1 may
    # appear only as dataclass defaults.
    defaults = {
        id(constant)
        for cls in ast.walk(tree)
        if isinstance(cls, ast.ClassDef)
        for statement in cls.body
        if isinstance(statement, ast.AnnAssign) and statement.value is not None
        for constant in ast.walk(statement.value)
    }
    numbers = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and node.value not in (0, 1)
        and id(node) not in defaults
    ]
    assert not numbers


def test_rest_fixture_has_no_activation_surface() -> None:
    root = ROOT / "collection_modules"
    assert (root / "plugins/module_utils/_activation.py.j2").is_file()
    assert not list(root.glob("**/*apply*"))
    assert (
        "only where saved and active state differ" in (root / "FIXME.md.j2").read_text()
    )
