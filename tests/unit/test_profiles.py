"""Regression tests for the independent module skeleton and its rendering."""

import json
from pathlib import Path

import pytest

from scripts.common import ROOT, GateError
from scripts.render import example_metadata, render


def test_module_skeleton_layout() -> None:
    skeleton = ROOT / "collection_modules_grid"
    assert all(
        path.name.endswith(".j2") for path in skeleton.rglob("*") if path.is_file()
    )
    assert (skeleton / "requirements.txt.j2").is_file()
    assert (skeleton / "tests/config.yml.j2").is_file()
    assert (skeleton / "plugins/modules/fixture.py.j2").is_file()
    assert not (skeleton / "roles").exists()


def test_python_templates_use_collection_license_metadata() -> None:
    profile = ROOT / "collection_modules_grid"
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
def test_module_skeleton_has_standard_documentation(
    filename: str, sections: list[str]
) -> None:
    source = (ROOT / "collection_modules_grid" / filename).read_text()
    for section in sections:
        assert f"## {section}<a id=" in source


def test_module_render_does_not_require_default_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    for name in ("role_default", "collection_modules_grid"):
        (source / name).mkdir(parents=True)
    monkeypatch.setattr("scripts.render.run", lambda *args, **kwargs: "")
    rendered = render(
        tmp_path / "render", source=source, profile="collection_modules_grid"
    )
    assert rendered.profile == "collection_modules_grid"


@pytest.mark.parametrize(
    "namespace,name", [("test_one", "first"), ("test_two", "second")]
)
def test_render_selects_profile_and_preserves_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, namespace: str, name: str
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
        profile="collection_modules_grid",
        metadata=metadata,
    )
    assert rendered.profile == "collection_modules_grid"
    assert calls[1][calls[1].index("--collection-skeleton") + 1] == str(
        ROOT / "collection_modules_grid"
    )
    assert calls[1][-1] == f"{namespace}.{name}"
    assert json.loads((rendered.work / "extra-vars.json").read_text()) == metadata


def test_invalid_profile_never_creates_output(tmp_path: Path) -> None:
    output = tmp_path / "invalid"
    with pytest.raises(GateError, match="Unknown collection profile"):
        render(output, profile="unknown")
    assert not output.exists()
