"""Exercise release refusals using real disposable Git repositories."""

import json
from pathlib import Path

import pytest

from scripts.check import required_checks
from scripts.common import GateError, digest, write_json
from scripts.release import (
    REPOSITORY,
    check_changelog,
    check_release,
    clean_revision,
    create_tag,
    export_revision,
    git,
    release_inputs,
    required_backends,
    validate_report,
)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    git("init", "--initial-branch=main", root=root)
    git("config", "user.name", "Release Test", root=root)
    git("config", "user.email", "release@example.com", root=root)
    git("config", "commit.gpgsign", "false", root=root)
    git("config", "tag.gpgsign", "false", root=root)
    (root / "content.txt").write_text("original\n", encoding="utf-8")
    git("add", ".", root=root)
    git("commit", "-m", "test: initial content", root=root)
    git("tag", "-a", "v1.0.0", "-m", "version 1.0.0", root=root)
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n- Nothing worth mentioning right now.\n\n"
        "## [1.1.0] - 2026-01-01\n\n"
        "### Added\n\n- Release checks.\n\n"
        f"[unreleased]: https://github.com/{REPOSITORY}/compare/v1.1.0...HEAD\n"
        f"[1.1.0]: https://github.com/{REPOSITORY}/releases/tag/v1.1.0\n",
        encoding="utf-8",
    )
    git("add", ".", root=root)
    git("commit", "-m", "release: prepare 1.1.0", root=root)
    remote = tmp_path / "remote.git"
    git("init", "--bare", str(remote), root=root)
    git("remote", "add", "origin", str(remote), root=root)
    return root


@pytest.fixture
def report(repository: Path, tmp_path: Path) -> Path:
    revision, base, backends = release_inputs("1.1.0", repository)
    output = tmp_path / "evidence"
    output.mkdir()
    source = output / "source.tar"
    export_revision(revision, source, output / "export", repository)
    checks = output / "checks"
    checks.mkdir()
    artifact = checks / "collection.tar.gz"
    artifact.write_bytes(b"retained test artifact")
    checks_file = checks / "checks.json"
    write_json(
        checks_file,
        {
            "checks": required_checks(full=True, backends=backends),
            "artifacts": {artifact.name: digest(artifact)},
        },
    )
    path = output / "report.json"
    write_json(
        path,
        {
            "schema": 1,
            "repository": REPOSITORY,
            "status": "passed",
            "version": "1.1.0",
            "revision": revision,
            "base": base,
            "backends": backends,
            "source_sha256": digest(source),
            "checks_sha256": digest(checks_file),
        },
    )
    return path


@pytest.mark.parametrize("staged", [False, True])
def test_dirty_tree_cannot_be_released(repository: Path, staged: bool) -> None:
    (repository / "content.txt").write_text("changed\n", encoding="utf-8")
    if staged:
        git("add", "content.txt", root=repository)
    with pytest.raises(GateError, match="clean tree"):
        clean_revision(repository)


def test_untracked_file_prevents_release(repository: Path) -> None:
    (repository / "new.txt").touch()
    with pytest.raises(GateError, match="clean tree"):
        clean_revision(repository)


@pytest.mark.parametrize(
    ("original", "replacement", "message"),
    [
        ("## [1.1.0]", "## [1.0.1]", "must match"),
        ("2026-01-01", "9999-01-01", "non-future"),
        (
            "- Nothing worth mentioning right now.",
            "- Unreleased fix.",
            "Move the Unreleased",
        ),
        (
            "- Nothing worth mentioning right now.",
            "- Nothing worth mentioning right now.\n\n- Unreleased fix.",
            "Move the Unreleased",
        ),
        ("- Release checks.", "", "section is empty"),
        ("v1.1.0...HEAD", "v1.0.0...HEAD", "comparison link"),
        ("/releases/tag/v1.1.0", "/releases/tag/v1.0.0", "changelog link"),
    ],
)
def test_invalid_changelog_prevents_release(
    repository: Path, original: str, replacement: str, message: str
) -> None:
    changelog = repository / "CHANGELOG.md"
    changelog.write_text(
        changelog.read_text(encoding="utf-8").replace(original, replacement),
        encoding="utf-8",
    )
    with pytest.raises(GateError, match=message):
        check_changelog("1.1.0", repository)


def test_changed_source_archive_prevents_tag(repository: Path, report: Path) -> None:
    (report.parent / "source.tar").write_bytes(b"changed source archive")
    with pytest.raises(GateError, match="evidence was modified"):
        create_tag(report, repository)


def test_report_for_previous_commit_cannot_authorize_tag(
    repository: Path, report: Path
) -> None:
    git("commit", "--allow-empty", "-m", "test: change revision", root=repository)
    with pytest.raises(GateError, match="different commit"):
        create_tag(report, repository)
    assert not git("tag", "--list", "v1.1.0", root=repository)


def test_report_with_missing_checks_is_rejected(repository: Path, report: Path) -> None:
    checks_file = report.parent / "checks/checks.json"
    checks = json.loads(checks_file.read_text(encoding="utf-8"))
    checks["checks"].pop()
    write_json(checks_file, checks)
    raw = json.loads(report.read_text(encoding="utf-8"))
    raw["checks_sha256"] = digest(checks_file)
    write_json(report, raw)
    with pytest.raises(GateError, match="checks are missing"):
        validate_report(report, repository)


def test_changed_artifact_prevents_tag(repository: Path, report: Path) -> None:
    (report.parent / "checks/collection.tar.gz").write_bytes(b"different bytes")
    with pytest.raises(GateError, match="artifact is missing or changed"):
        create_tag(report, repository)


def test_failed_report_cannot_authorize_tag(repository: Path, report: Path) -> None:
    raw = json.loads(report.read_text(encoding="utf-8"))
    raw["status"] = "failed"
    write_json(report, raw)
    with pytest.raises(GateError, match="passing release report"):
        create_tag(report, repository)


def test_valid_report_creates_annotated_local_tag(
    repository: Path, report: Path
) -> None:
    create_tag(report, repository)
    assert git("cat-file", "-t", "v1.1.0", root=repository) == "tag"
    assert git("rev-parse", "v1.1.0^{commit}", root=repository) == clean_revision(
        repository
    )
    assert not git("ls-remote", "--tags", "origin", root=repository)
    with pytest.raises(GateError, match="existing release tags"):
        create_tag(report, repository)


@pytest.mark.parametrize("tag", ["v1.1.0", "v1.2.0", "v2.0.0"])
def test_equal_or_newer_remote_tag_prevents_release(
    repository: Path, report: Path, tag: str
) -> None:
    git("tag", "-a", tag, "-m", f"version {tag}", root=repository)
    git("push", "origin", f"refs/tags/{tag}", root=repository)
    git("tag", "-d", tag, root=repository)
    with pytest.raises(GateError, match="remote tag"):
        create_tag(report, repository)


def test_network_failure_is_not_treated_as_absent_remote_tag(
    repository: Path, report: Path
) -> None:
    git("remote", "set-url", "origin", str(repository / "missing.git"), root=repository)
    with pytest.raises(GateError, match="tags could not be verified"):
        create_tag(report, repository)


def test_older_remote_tag_allows_release(repository: Path, report: Path) -> None:
    git("push", "origin", "refs/tags/v1.0.0", root=repository)
    create_tag(report, repository)
    assert git("cat-file", "-t", "v1.1.0", root=repository) == "tag"
    assert not git("ls-remote", "origin", "refs/tags/v1.1.0", root=repository)


def test_export_ignores_untracked_developer_files(
    repository: Path, tmp_path: Path
) -> None:
    revision = clean_revision(repository)
    (repository / "untracked.txt").write_text("must not ship", encoding="utf-8")
    export = tmp_path / "export"
    export_revision(revision, tmp_path / "source.tar", export, repository)
    assert (export / "content.txt").is_file()
    assert not (export / "untracked.txt").exists()


def test_subprocess_failure_leaves_no_passing_report(
    repository: Path, tmp_path: Path
) -> None:
    # This miniature repository has no scripts.check module. A real failing
    # child process proves that export success cannot become gate success.
    output = tmp_path / "failed-check"
    with pytest.raises(GateError, match="Release checks failed"):
        check_release("1.1.0", output, repository)
    assert (output / "check.log").is_file()
    assert not (output / "report.json").exists()


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["DEVELOPMENT.md"], ["podman"]),
        (["role_default/tasks/main.yml.j2"], ["podman"]),
        (
            ["role_default/molecule/shared/playbooks/converge.yml.j2"],
            ["podman", "libvirt", "ee"],
        ),
        (
            ["collection_default/extensions/molecule/default/molecule.yml.j2"],
            ["podman", "libvirt"],
        ),
        (["role_default/molecule/ee/tasks/verify/ee.yml.j2"], ["podman", "ee"]),
        (["scripts/check.py"], ["podman", "libvirt", "ee"]),
    ],
)
def test_changes_require_affected_backend_tests(
    paths: list[str], expected: list[str]
) -> None:
    assert required_backends(paths) == expected


def test_first_release_requires_all_backends() -> None:
    assert required_backends([], initial=True) == ["podman", "libvirt", "ee"]
