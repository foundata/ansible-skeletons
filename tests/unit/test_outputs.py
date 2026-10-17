# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A passed check keeps its evidence; --remove deletes only a proven output."""

import os
import shutil
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from scripts import check as check_module
from scripts.check import evidence, prune, remove_output
from scripts.common import ROOT, GateError, digest, write_json

ARCHIVES = (
    "collection_modules/module_fixture/artifacts/fixture-module_fixture-1.0.0.tar.gz",
    "generated-lint/explicit/artifacts/skeleton_test-example_app-1.0.0.tar.gz",
)
EVIDENCE = {
    "run.json",
    "records/static.json",
    "logs/static.log",
    "unit-python3.14.xml",
    "checks.json",
    *ARCHIVES,
}


def files(root: Path) -> set[str]:
    """List every file and link below a directory, relative to it."""
    return {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_symlink() or not path.is_dir()
    }


def unavailable(paths: Sequence[Path]) -> None:
    pytest.fail(f"Podman's user namespace was asked to remove {paths}")


def namespace(calls: list[list[Path]]) -> object:
    """Stand in for Podman's user namespace, whose root may remove anything."""

    def remove(paths: Sequence[Path]) -> None:
        calls.append(list(paths))
        for path in paths:
            for directory in (path, *path.rglob("*")):
                if directory.is_dir() and not directory.is_symlink():
                    directory.chmod(0o700)
            shutil.rmtree(path)

    return remove


def write(path: Path, text: str = "data\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def passed(tmp_path: Path) -> Iterator[Path]:
    """A passed run's output: its evidence and what the steps left besides."""
    output = tmp_path / "output"
    write_json(
        write(output / "run.json"),
        {"source": "tree", "full": True, "backends": [], "changed": False},
    )
    write(output / "records/static.json")
    write(output / "logs/static.log")
    write(output / "unit-python3.14.xml", "<testsuites/>\n")
    for name in ARCHIVES:
        write(output / name, name)
    write_json(
        output / "checks.json",
        {"checks": ["static"], "artifacts": dict.fromkeys(ARCHIVES, "x")},
    )
    # What the steps left besides.
    write(output / "t/0/pymp-leftover/state")
    write(output / "galaxy/collections/containers-podman-1.20.2.tar.gz")
    write(output / "controller-python3.14.8.json")
    write(output / "collection_modules/profile.json")
    write(output / "collection_modules/module_fixture/home/.config/storage.conf")
    write(output / "collection_modules/module_fixture/artifacts/notes.txt")
    write(output / "generated-lint/explicit/nox/docs-check-3-14/pyvenv.cfg")
    (output / "generated-lint/explicit/nox/python").symlink_to("/missing/python")
    # A link out of the output must go without its target.
    (output / "outside").symlink_to(tmp_path, target_is_directory=True)
    locked = write(output / "generated-lint/explicit/uv-cache/archive/file").parent
    locked.chmod(0o500)
    yield output
    # pytest removes what a test left behind.
    if locked.exists():
        locked.chmod(0o700)


def test_a_passed_run_keeps_its_evidence_byte_for_byte(passed: Path) -> None:
    before = {name: digest(passed / name) for name in EVIDENCE}
    assert {str(path) for path in evidence(passed)} == {
        "run.json",
        "records",
        "logs",
        "checks.json",
        "unit-python3.14.xml",
        *ARCHIVES,
    }
    prune(passed, unavailable)
    assert files(passed) == EVIDENCE
    assert {name: digest(passed / name) for name in EVIDENCE} == before
    assert passed.parent.is_dir()


def test_a_run_without_a_report_keeps_its_records_and_logs(passed: Path) -> None:
    # A changed-only run writes no report and keeps no archive.
    (passed / "checks.json").unlink()
    prune(passed, unavailable)
    assert files(passed) == EVIDENCE - {"checks.json", *ARCHIVES}


def test_containers_files_go_together_as_namespace_root(
    passed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = passed / "collection_modules/module_fixture/home/.local/share/storage"
    write(store / "overlay/upper/etc/passwd")
    (store / "overlay/upper/etc").chmod(0o500)
    # Files a container wrote belong to a subordinate user, not to this one.
    monkeypatch.setattr(os, "getuid", lambda: os.geteuid() + 100000)
    calls: list[list[Path]] = []
    prune(passed, namespace(calls))  # type: ignore[arg-type]
    monkeypatch.undo()
    assert len(calls) == 1
    assert passed / "collection_modules/module_fixture/home" in calls[0]
    assert files(passed) == EVIDENCE


def test_a_report_listing_an_archive_outside_the_output_prunes_nothing(
    passed: Path,
) -> None:
    write_json(
        passed / "checks.json", {"checks": [], "artifacts": {"../elsewhere.tar.gz": ""}}
    )
    with pytest.raises(GateError, match="outside"):
        prune(passed, unavailable)
    assert (passed / "galaxy").is_dir()


def gate(evidence: Path) -> Path:
    """Release evidence: the gate's record and its check's output in checks/."""
    write_json(
        write(evidence / "gate.json"),
        {
            "version": "1.1.0",
            "revision": "0" * 40,
            "base": None,
            "backends": ["podman"],
            "source": "tree",
            "source_sha256": "x",
        },
    )
    write(evidence / "source.tar")
    write_json(
        write(evidence / "checks/run.json"),
        {
            "source": "tree",
            "full": True,
            "backends": ["podman"],
            "changed": False,
            "pruned": True,
        },
    )
    return evidence


def test_remove_deletes_an_output_with_a_locked_directory(passed: Path) -> None:
    remove_output(passed, unavailable)
    assert not passed.exists()
    assert passed.parent.is_dir()


def test_remove_deletes_release_evidence(tmp_path: Path) -> None:
    evidence = gate(tmp_path / "evidence")
    remove_output(evidence, unavailable)
    assert not evidence.exists()


def test_remove_hands_containers_files_to_the_namespace(
    passed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "getuid", lambda: os.geteuid() + 100000)
    calls: list[list[Path]] = []
    remove_output(passed, namespace(calls))  # type: ignore[arg-type]
    monkeypatch.undo()
    assert calls == [[passed]]
    assert not passed.exists()


@pytest.mark.parametrize(
    "case",
    [
        "relative",
        "no run.json",
        "foreign run.json",
        "linked run.json",
        "symlink",
        "parent reference",
        "gate without checks",
        "checkout",
        "checkout ancestor",
        "home",
        "home ancestor",
    ],
)
def test_remove_refuses_anything_but_a_proven_output(
    passed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    target = passed
    if case == "relative":
        target = Path(os.path.relpath(passed))
    elif case == "no run.json":
        (passed / "run.json").unlink()
    elif case == "foreign run.json":
        write_json(passed / "run.json", {"source": "tree", "owner": "someone"})
    elif case == "linked run.json":
        (passed / "run.json").rename(tmp_path / "run.json")
        (passed / "run.json").symlink_to(tmp_path / "run.json")
    elif case == "symlink":
        target = tmp_path / "link"
        target.symlink_to(passed, target_is_directory=True)
    elif case == "parent reference":
        target = passed / ".." / passed.name
    elif case == "gate without checks":
        target = gate(tmp_path / "evidence")
        (target / "checks/run.json").unlink()
    elif case == "checkout":
        target = ROOT
    elif case == "checkout ancestor":
        target = ROOT.parent
    elif case == "home":
        monkeypatch.setenv("HOME", str(passed))
    elif case == "home ancestor":
        monkeypatch.setenv("HOME", str(passed / "logs"))
    with pytest.raises(GateError):
        remove_output(target, unavailable)
    assert target.exists() or case == "relative"
    assert (passed / "logs/static.log").is_file()


def test_the_command_line_removes_one_output(
    passed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["check", "--remove", str(passed / "logs")])
    assert check_module.main() == 1
    assert "no run.json" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["check", "--remove", str(passed)])
    assert check_module.main() == 0
    assert not passed.exists()
