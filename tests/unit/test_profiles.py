"""Regression tests for the independent module skeleton and its rendering."""

import ast
import json
import re
from pathlib import Path

import pytest

from scripts.common import ROOT, GateError
from scripts.render import example_metadata, render


@pytest.mark.parametrize("profile", ["collection_modules_grid", "collection_modules"])
def test_module_skeleton_layout(profile: str) -> None:
    skeleton = ROOT / profile
    assert all(
        path.name.endswith(".j2") for path in skeleton.rglob("*") if path.is_file()
    )
    assert (skeleton / "requirements.txt.j2").is_file()
    assert (skeleton / "tests/config.yml.j2").is_file()
    assert (skeleton / "plugins/modules/fixture.py.j2").is_file()
    assert not (skeleton / "roles").exists()


@pytest.mark.parametrize("name", ["collection_modules_grid", "collection_modules"])
def test_python_templates_use_collection_license_metadata(name: str) -> None:
    profile = ROOT / name
    for path in profile.rglob("*.py.j2"):
        source = path.read_text()
        assert "SPDX-FileCopyrightText" not in source, path
        assert "SPDX-License-Identifier" not in source, path
        if path.parent == profile / "plugins/modules":
            assert "GNU General Public License v3.0+" in "\n".join(
                source.splitlines()[:20]
            ), path


@pytest.mark.parametrize(
    "scenario",
    [
        "role_default/molecule/default",
        "collection_default/extensions/molecule/default",
        "collection_modules_grid/extensions/molecule/default",
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
@pytest.mark.parametrize("profile", ["collection_modules_grid", "collection_modules"])
def test_module_skeleton_has_standard_documentation(
    filename: str, sections: list[str], profile: str
) -> None:
    source = (ROOT / profile / filename).read_text()
    for section in sections:
        assert f"## {section}<a id=" in source


@pytest.mark.parametrize("profile", ["collection_modules_grid", "collection_modules"])
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
    "namespace,name", [("test_one", "first"), ("test_two", "second")]
)
@pytest.mark.parametrize("profile", ["collection_modules_grid", "collection_modules"])
def test_render_selects_profile_and_preserves_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    namespace: str,
    name: str,
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
        profile=profile,
        metadata=metadata,
    )
    assert rendered.profile == profile
    assert calls[1][calls[1].index("--collection-skeleton") + 1] == str(ROOT / profile)
    assert calls[1][-1] == f"{namespace}.{name}"
    assert json.loads((rendered.work / "extra-vars.json").read_text()) == metadata


def test_invalid_profile_never_creates_output(tmp_path: Path) -> None:
    output = tmp_path / "invalid"
    with pytest.raises(GateError, match="Unknown collection profile"):
        render(output, profile="unknown")
    assert not output.exists()


@pytest.mark.parametrize("profile", ["collection_modules_grid", "collection_modules"])
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


def test_rest_fixture_has_no_activation_surface() -> None:
    root = ROOT / "collection_modules"
    assert (root / "plugins/module_utils/_activation.py.j2").is_file()
    assert not list(root.glob("**/*apply*"))
    assert (
        "only where saved and active state differ" in (root / "FIXME.md.j2").read_text()
    )
