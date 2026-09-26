"""Validate a committed release, create its local tag, or verify publication."""

import argparse
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from scripts.check import default_lane, required_checks
from scripts.common import ROOT, GateError, digest, run, uv_command, write_json

REPOSITORY = "foundata/ansible-skeletons"
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def git(*arguments: str, root: Path = ROOT) -> str:
    """Run a bounded Git command in the selected repository."""
    return run(["git", *arguments], cwd=root, capture=True, timeout=60).strip()


def version_parts(value: str) -> tuple[int, ...]:
    """Accept stable SemVer releases without an optional tag prefix."""
    if VERSION.fullmatch(value) is None:
        raise GateError("Version must be major.minor.patch without a v prefix")
    return tuple(int(part) for part in value.split("."))


def clean_revision(root: Path = ROOT) -> str:
    """Reject staged, unstaged and untracked changes before identifying HEAD."""
    if git("status", "--porcelain", "--untracked-files=all", root=root):
        raise GateError(
            "Commit the reviewed changes first; release checks require a clean tree"
        )
    git("diff", "--check", "HEAD", root=root)
    return git("rev-parse", "--verify", "HEAD", root=root)


def check_changelog(version: str, root: Path = ROOT) -> None:
    """Validate the release section, date and links in this repository's format."""
    version_parts(version)
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    sections = list(
        re.finditer(
            r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?\s*$", text, re.MULTILINE
        )
    )
    if (
        len(sections) < 2
        or sections[0][1].lower() != "unreleased"
        or sections[1][1] != version
    ):
        raise GateError(
            "The first dated changelog section must match the intended version"
        )
    if sum(section[1] == version for section in sections) != 1:
        raise GateError("Duplicate release section in CHANGELOG.md")
    if (
        not sections[1][2]
        or date.fromisoformat(sections[1][2]) > datetime.now(UTC).date()
    ):
        raise GateError("The release needs a valid, non-future changelog date")
    unreleased = text[sections[0].end() : sections[1].start()]
    if any(
        line.strip() not in ("", "- Nothing worth mentioning right now.")
        for line in unreleased.splitlines()
    ):
        raise GateError("Move the Unreleased entries into the release section first")
    end = sections[2].start() if len(sections) > 2 else len(text)
    if not re.search(r"^\s*[-*] \S", text[sections[1].end() : end], re.MULTILINE):
        raise GateError("The release changelog section is empty")
    links = dict(re.findall(r"^\[([^\]]+)\]:\s+(\S+)\s*$", text, re.MULTILINE))
    expected = f"https://github.com/{REPOSITORY}"
    if (
        links.get("unreleased", links.get("Unreleased"))
        != f"{expected}/compare/v{version}...HEAD"
    ):
        raise GateError("Update the Unreleased comparison link to the intended tag")
    release_link = links.get(version, "")
    if release_link != f"{expected}/releases/tag/v{version}" and not (
        release_link.startswith(f"{expected}/compare/")
        and release_link.endswith(f"...v{version}")
    ):
        raise GateError("The release changelog link does not identify the intended tag")


def previous_release(version: str, root: Path = ROOT) -> str | None:
    """Find the highest reachable release and reject reused or older versions."""
    intended = version_parts(version)
    all_tags = git("tag", "--list", "v*", root=root).splitlines()
    versions = {
        tag: version_parts(tag[1:]) for tag in all_tags if VERSION.fullmatch(tag[1:])
    }
    if any(parts >= intended for parts in versions.values()):
        raise GateError(
            "The intended version must be newer than all existing release tags"
        )
    reachable = git("tag", "--merged", "HEAD", "--list", "v*", root=root).splitlines()
    candidates = [tag for tag in reachable if tag in versions]
    return max(candidates, key=lambda tag: versions[tag]) if candidates else None


def required_backends(changed: list[str], *, initial: bool = False) -> list[str]:
    """Require infrastructure tests conservatively when shared inputs change."""
    backends = {"podman"}
    if initial or any(
        path.startswith(("scripts/", "tests/")) or path in ("pyproject.toml", "uv.lock")
        for path in changed
    ):
        backends.update(("libvirt", "ee"))
    for path in changed:
        if "/molecule/shared/" in path or "/molecule/default/" in path:
            backends.add("libvirt")
        if (
            "/molecule/shared/" in path
            or "/molecule/ee/" in path
            or path.endswith("meta/execution-environment.yml.j2")
            or path == "collection_default/galaxy.yml.j2"
        ):
            backends.add("ee")
    return [backend for backend in ("podman", "libvirt", "ee") if backend in backends]


def release_inputs(
    version: str, root: Path = ROOT
) -> tuple[str, str | None, list[str]]:
    """Determine the immutable revision and all required integration backends."""
    revision = clean_revision(root)
    check_changelog(version, root)
    base = previous_release(version, root)
    changed = (
        git("diff", "--name-only", f"{base}..{revision}", root=root).splitlines()
        if base
        else []
    )
    return revision, base, required_backends(changed, initial=base is None)


def export_revision(
    revision: str, archive: Path, destination: Path, root: Path = ROOT
) -> None:
    """Export only committed content and unpack it without a developer checkout."""
    git("archive", "--format=tar", f"--output={archive}", revision, root=root)
    destination.mkdir()
    with tarfile.open(archive) as bundle:
        bundle.extractall(destination, filter="data")


def check_release(version: str, output: Path, root: Path = ROOT) -> None:
    """Validate one clean commit; retain evidence without tagging or publishing."""
    revision, base, backends = release_inputs(version, root)
    output = output.resolve()
    if output.is_relative_to(root.resolve()):
        raise GateError("Release evidence must be outside the working tree")
    output.mkdir(parents=True, exist_ok=False)
    archive = output / "source.tar"
    with tempfile.TemporaryDirectory(prefix="ansible-skeletons-release-") as temporary:
        source = Path(temporary) / "source"
        export_revision(revision, archive, source, root)
        command = [
            "uv",
            "run",
            "--locked",
            "--isolated",
            "python",
            "-m",
            "scripts.check",
            "--matrix",
            "--output",
            str(output / "checks"),
        ]
        for backend in backends:
            command.extend(["--integration", backend])
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            not in (
                "PYTHONPATH",
                "PYTHONHOME",
                "VIRTUAL_ENV",
                "UV_PROJECT",
                "UV_PROJECT_ENVIRONMENT",
                "UV_WORKING_DIRECTORY",
            )
        }
        # An archive has no Git ignore rules for REUSE to consult. Keep tool
        # environments and generated caches outside the exported source tree.
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "RUFF_NO_CACHE": "true",
                "MYPY_CACHE_DIR": str(Path(temporary) / "mypy-cache"),
                "UV_LINK_MODE": "copy",
            }
        )
        log = output / "check.log"
        print(
            f"Checking {revision}; required backends: {', '.join(backends)}\nLog: {log}",
            flush=True,
        )
        with log.open("w", encoding="utf-8") as stream:
            try:
                subprocess.run(
                    command,
                    cwd=source,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=21600,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                raise GateError(
                    f"Release checks failed; inspect {log}. No passing report was created"
                ) from exc
    if clean_revision(root) != revision:
        raise GateError("HEAD changed during release validation")
    checks_path = output / "checks/checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8"))
    if checks.get("checks") != required_checks(full=True, backends=backends):
        raise GateError("The release check report is incomplete")
    report = {
        "schema": 1,
        "status": "passed",
        "repository": REPOSITORY,
        "version": version,
        "revision": revision,
        "base": base,
        "backends": backends,
        "completed_at": datetime.now(UTC).isoformat(),
        "source_sha256": digest(archive),
        "checks_sha256": digest(checks_path),
    }
    pending = output / "report.json.tmp"
    write_json(pending, report)
    pending.replace(output / "report.json")
    print(f"Release checks passed: {output / 'report.json'}")


def validate_report(
    path: Path, root: Path = ROOT, *, tagged: bool = False
) -> dict[str, object]:
    """Reject incomplete, stale or damaged release evidence before reuse."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or raw.get("schema") != 1
        or raw.get("status") != "passed"
        or raw.get("repository") != REPOSITORY
    ):
        raise GateError("Not a passing release report for this repository")
    version, revision = raw.get("version"), raw.get("revision")
    if not isinstance(version, str) or not isinstance(revision, str):
        raise GateError("Invalid release report identity")
    version_parts(version)
    if clean_revision(root) != revision:
        raise GateError("This release report belongs to a different commit")
    check_changelog(version, root)
    if tagged:
        if (
            git("cat-file", "-t", f"refs/tags/v{version}", root=root) != "tag"
            or git("rev-parse", f"refs/tags/v{version}^{{commit}}", root=root)
            != revision
        ):
            raise GateError(
                "The annotated release tag does not identify the validated commit"
            )
        # The preceding release still determines which backends were required.
        base = raw.get("base")
        if base is not None and not isinstance(base, str):
            raise GateError("Invalid report base")
        changed = (
            git("diff", "--name-only", f"{base}..{revision}", root=root).splitlines()
            if base
            else []
        )
        backends = required_backends(changed, initial=base is None)
    else:
        _, base, backends = release_inputs(version, root)
        if raw.get("base") != base:
            raise GateError("The release base changed after validation")
    if raw.get("backends") != backends:
        raise GateError("The report omits required integration backends")
    directory = path.resolve().parent
    checks_path = directory / "checks/checks.json"
    if digest(directory / "source.tar") != raw.get("source_sha256") or digest(
        checks_path
    ) != raw.get("checks_sha256"):
        raise GateError("Release evidence was modified after validation")
    checks = json.loads(checks_path.read_text(encoding="utf-8"))
    if not isinstance(checks, dict) or checks.get("checks") != required_checks(
        full=True, backends=backends
    ):
        raise GateError("Required checks are missing from the report")
    artifacts = checks.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise GateError("No validated collection artifacts were retained")
    for name, checksum in artifacts.items():
        if not isinstance(name, str) or not isinstance(checksum, str):
            raise GateError("Invalid artifact record")
        artifact = (directory / "checks" / name).resolve()
        if (
            not artifact.is_relative_to(directory / "checks")
            or digest(artifact) != checksum
        ):
            raise GateError(f"Retained artifact is missing or changed: {name}")
    return dict(raw)


def create_tag(path: Path, root: Path = ROOT) -> None:
    """Create only a local annotated tag after checking retained release evidence."""
    report = validate_report(path, root)
    version, revision = str(report["version"]), str(report["revision"])
    # Check all remote versions, including tags absent from the local checkout.
    result = subprocess.run(
        [
            "git",
            "ls-remote",
            "--tags",
            "--refs",
            "origin",
        ],
        cwd=root,
        check=False,
        timeout=60,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise GateError(
            f"Remote release tags could not be verified: {result.stderr.strip()}"
        )
    intended = version_parts(version)
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise GateError("Unexpected remote tag listing")
        tag = fields[1].removeprefix("refs/tags/v")
        if VERSION.fullmatch(tag) and version_parts(tag) >= intended:
            raise GateError("An equal or newer remote tag already exists")
    if clean_revision(root) != revision:
        raise GateError("HEAD changed before tagging")
    git("tag", "-a", f"v{version}", revision, "-m", f"version {version}", root=root)
    print(f"Created local tag v{version}; nothing was pushed or published")


def verify_publication(path: Path, root: Path = ROOT) -> None:
    """Check the remote tag, forge release and fresh clone's consumer workflow."""
    report = validate_report(path, root, tagged=True)
    version, revision = str(report["version"]), str(report["revision"])
    remote = git("ls-remote", "origin", f"refs/tags/v{version}^{{}}", root=root)
    if remote.split()[:1] != [revision]:
        raise GateError(
            "The published annotated tag does not match the validated commit"
        )
    release = json.loads(
        run(
            ["gh", "api", f"repos/{REPOSITORY}/releases/latest"],
            cwd=root,
            capture=True,
            timeout=60,
        )
    )
    if (
        release.get("tag_name") != f"v{version}"
        or release.get("draft")
        or release.get("prerelease")
    ):
        raise GateError(
            "The forge does not report the intended stable release as latest"
        )
    with tempfile.TemporaryDirectory(
        prefix="ansible-skeletons-published-"
    ) as temporary:
        source = Path(temporary) / "source"
        run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                f"v{version}",
                f"https://github.com/{REPOSITORY}.git",
                str(source),
            ],
            timeout=120,
        )
        if git("rev-parse", "HEAD", root=source) != revision:
            raise GateError("The public clone has a different release revision")
        run(
            uv_command(
                default_lane(),
                "python",
                "-m",
                "scripts.smoke",
                "--output",
                str(Path(temporary) / "smoke"),
            ),
            cwd=source,
            timeout=1800,
        )
    print(f"Published v{version} verified")


def main() -> int:
    """Dispatch explicit validation, local tagging and read-only publication checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="Validate a committed release")
    check_parser.add_argument("version")
    check_parser.add_argument("--output", required=True, type=Path)
    for command in ("tag", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "check":
            check_release(args.version, args.output)
        elif args.command == "tag":
            create_tag(args.report)
        else:
            verify_publication(args.report)
    except (GateError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
