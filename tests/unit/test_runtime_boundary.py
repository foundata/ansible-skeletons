# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The runtime check must fail closed, without a source-tree fallback."""

import ast
import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scripts.common import ROOT

CHECKER = ROOT / "collection_default/tests/runtime_check.py.j2"


@pytest.fixture
def checker() -> dict[str, Any]:
    return runpy.run_path(str(CHECKER))


def test_stripping_removes_every_nonruntime_entry(
    tmp_path: Path, checker: dict[str, Any]
) -> None:
    for name in ("meta", "plugins", "roles", "playbooks", "tests", "docs", ".git"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "keep").touch()
    for name in ("galaxy.yml", "MANIFEST.json", "FILES.json", "README.md"):
        (tmp_path / name).touch()
    checker["strip_runtime"](tmp_path)
    assert {path.name for path in tmp_path.iterdir()} == checker["RUNTIME_DIRECTORIES"]
    assert all((path / "keep").is_file() for path in tmp_path.iterdir())


@pytest.mark.parametrize("link", ["root", "directory", "file", "dangling"])
def test_runtime_links_cannot_escape(
    tmp_path: Path, checker: dict[str, Any], link: str
) -> None:
    external = tmp_path / "outside"
    external.mkdir()
    (external / "sentinel").write_text("untouched")
    collection = tmp_path / "installed"
    collection.mkdir()
    if link == "root":
        collection.rmdir()
        collection.symlink_to(external, target_is_directory=True)
    elif link == "directory":
        (collection / "plugins").symlink_to(external, target_is_directory=True)
    else:
        (collection / "plugins").mkdir()
        (collection / "plugins/link").symlink_to(
            external / ("missing" if link == "dangling" else "sentinel")
        )
    with pytest.raises((ValueError, FileNotFoundError)):
        checker["strip_runtime"](collection)
    assert (external / "sentinel").read_text() == "untouched"


def test_internal_runtime_link_is_retained(
    tmp_path: Path, checker: dict[str, Any]
) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "original.py").touch()
    (plugins / "alias.py").symlink_to("original.py")
    checker["strip_runtime"](tmp_path)
    assert (plugins / "alias.py").resolve() == plugins / "original.py"


def test_runtime_environment_drops_ambient_paths(
    tmp_path: Path, checker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("ANSIBLE_LIBRARY", "ANSIBLE_CONFIG", "PYTHONPATH", "PYTEST_ADDOPTS"):
        monkeypatch.setenv(name, "/source/checkout")
    env = checker["isolated_environment"](tmp_path, tmp_path / "collections")
    assert "/source/checkout" not in env.values()
    assert "ANSIBLE_LIBRARY" not in env
    assert "PYTEST_ADDOPTS" not in env
    assert env["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] == "false"
    assert env["PYTHONPATH"] == str(tmp_path / "collections")
    assert env["PYTHONSAFEPATH"] == "1"
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["HOME"] != os.environ["HOME"]


def test_dependencies_cannot_supply_a_second_subject(
    tmp_path: Path, checker: dict[str, Any]
) -> None:
    source = tmp_path / "prepared"
    for name in ("subject", "helper"):
        path = source / "ansible_collections/test" / name
        path.mkdir(parents=True)
        (path / "marker").write_text(name)
    output = tmp_path / "isolated"
    checker["copy_dependencies"](source, output, ("test", "subject"))
    assert not (output / "ansible_collections/test/subject").exists()
    assert (output / "ansible_collections/test/helper/marker").read_text() == "helper"
    assert (source / "ansible_collections/test/subject/marker").is_file()


def test_fixture_relocation_preserves_runtime_imports_and_source(
    tmp_path: Path, checker: dict[str, Any]
) -> None:
    collection = tmp_path / "installed"
    fixtures = collection / "tests/unit"
    fixtures.mkdir(parents=True)
    source = (
        "from ansible_collections.test.subject.tests.unit.helper import fixture\n"
        "from ansible_collections.test.subject.plugins.module_utils.api import Client\n"
        "from ansible_collections.test.subject.tests_other import unrelated\n"
        "from .local import local_fixture\n"
    )
    (fixtures / "test_example.py").write_text(source)
    harness = tmp_path / "harness"
    checker["copy_test_fixtures"](collection, harness, "test.subject")
    copied = harness / "_runtime_tests/unit/test_example.py"
    imports = [
        node.module
        for node in ast.walk(ast.parse(copied.read_text()))
        if isinstance(node, ast.ImportFrom)
    ]
    assert imports == [
        "_runtime_tests.unit.helper",
        "ansible_collections.test.subject.plugins.module_utils.api",
        "ansible_collections.test.subject.tests_other",
        "local",
    ]
    assert (fixtures / "test_example.py").read_text() == source
    assert not (harness / "ansible_collections").exists()
    assert (harness / "_runtime_tests/__init__.py").is_file()


def test_fixture_relocation_accepts_an_artifact_without_tests(
    tmp_path: Path, checker: dict[str, Any]
) -> None:
    checker["copy_test_fixtures"](tmp_path, tmp_path / "harness", "test.subject")
    assert not (tmp_path / "harness").exists()


@pytest.mark.parametrize("namespace", ["../escape", "a/b", "", 7, "test"])
def test_artifact_identity_is_validated(
    tmp_path: Path, checker: dict[str, Any], namespace: object
) -> None:
    archive = tmp_path / "artifact.tar.gz"
    payload = json.dumps(
        {"collection_info": {"namespace": namespace, "name": "subject"}}
    ).encode()
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("MANIFEST.json")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    if namespace == "test":
        assert checker["collection_identity"](archive) == ("test", "subject")
    else:
        with pytest.raises(ValueError, match="identity"):
            checker["collection_identity"](archive)


def test_origin_check_rejects_an_ambient_import(
    tmp_path: Path, checker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = ModuleType("ansible_collections.test.subject.plugins.module_utils.bad")
    module.__file__ = str(tmp_path / "checkout/plugins/module_utils/bad.py")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(ValueError, match="outside"):
        checker["check_origins"](tmp_path / "installed", "test.subject")


@pytest.mark.parametrize("needs_root_file", [False, True])
def test_real_import_probe_detects_a_removed_runtime_dependency(
    tmp_path: Path, checker: dict[str, Any], needs_root_file: bool
) -> None:
    collections = tmp_path / "collections"
    subject = collections / "ansible_collections/test/subject"
    utilities = subject / "plugins/module_utils"
    utilities.mkdir(parents=True)
    content = (
        "from pathlib import Path\n"
        "VALUE = (Path(__file__).resolve().parents[2] / 'data.txt').read_text()\n"
        if needs_root_file
        else "VALUE = 42\n"
    )
    (utilities / "example.py").write_text(content)
    (subject / "data.txt").write_text("must not be available")
    checker["strip_runtime"](subject)
    runner = tmp_path / "probe.py"
    shutil.copyfile(CHECKER, runner)
    result = subprocess.run(
        [sys.executable, str(runner), "--probe", str(subject), "test.subject"],
        cwd=tmp_path,
        env=checker["isolated_environment"](tmp_path, collections),
        text=True,
        capture_output=True,
    )
    assert (result.returncode != 0) is needs_root_file, result.stderr
    if needs_root_file:
        assert "data.txt" in result.stderr
    else:
        assert "Imported 1 Python files" in result.stdout


@pytest.mark.parametrize("case", ["pass", "namespace", "skipped", "missing-file"])
def test_unit_runner_requires_executed_runtime_tests(
    tmp_path: Path, checker: dict[str, Any], case: str
) -> None:
    collections = tmp_path / "collections"
    subject = collections / "ansible_collections/test/subject"
    utilities = subject / "plugins/module_utils"
    utilities.mkdir(parents=True)
    (utilities / "example.py").write_text(
        "from pathlib import Path\n"
        "VALUE = 42\n"
        "def read():\n"
        "    return (Path(__file__).resolve().parents[2] / 'data.txt').read_text()\n"
    )
    (subject / "data.txt").write_text("not runtime content")
    harness = tmp_path / "harness"
    tests = harness / "_runtime_tests/unit"
    tests.mkdir(parents=True)
    if case == "namespace":
        code = "import ansible_collections.test.subject\ndef test_ok(): pass\n"
    else:
        code = "from ansible_collections.test.subject.plugins.module_utils import example\n"
        if case == "skipped":
            code += "import pytest\n@pytest.mark.skip(reason='fixture')\n"
        code += "def test_runtime():\n"
        code += (
            "    assert example.read()\n"
            if case == "missing-file"
            else "    assert example.VALUE == 42\n"
        )
    (tests / "test_runtime.py").write_text(code)
    checker["strip_runtime"](subject)
    runner = tmp_path / "runner.py"
    shutil.copyfile(CHECKER, runner)
    result = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--units",
            str(subject),
            "test.subject",
            str(harness),
        ],
        cwd=tmp_path,
        env=checker["isolated_environment"](tmp_path, collections),
        text=True,
        capture_output=True,
    )
    assert (result.returncode == 0) is (case == "pass"), result.stdout + result.stderr
    if case == "namespace":
        assert "Unit tests imported no code" in result.stderr
    elif case == "skipped":
        assert "unit tests executed no cases" in result.stderr
    elif case == "missing-file":
        assert "data.txt" in result.stdout
