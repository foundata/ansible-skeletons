"""Render both skeletons into a new directory using ansible-galaxy."""

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.common import ROOT, GateError, isolated_environment, run, write_json

MODULE_PROFILES = ("collection_modules_grid", "collection_modules")
PRODUCTS = {
    "collection_modules_grid": "Example Grid",
    "collection_modules": "Example REST Product",
}
"""Product display names of the example renders of each module profile."""


def skeleton_revision() -> str:
    """Revision a render records: SKELETONS_REVISION, git describe or a marker."""
    return os.environ.get("SKELETONS_REVISION") or (
        run(["git", "describe", "--tags", "--always"], capture=True).strip()
        if (ROOT / ".git").exists()
        else "source archive"
    )


@dataclass(frozen=True)
class Rendered:
    """Locate generated resources and the isolated Ansible environment."""

    work: Path
    role: Path
    collection: Path
    env: dict[str, str]
    profile: str = "collection_default"


def render(
    output: Path,
    *,
    source: Path = ROOT,
    namespace: str = "skeleton_test",
    collection_name: str = "example_app",
    role_name: str = "example_role",
    metadata: dict[str, object] | None = None,
    profile: str = "collection_default",
) -> Rendered:
    """Render real Galaxy resources, refusing to merge with existing output.

    Names must be valid identifiers. Extra variables are serialized as JSON so
    punctuation in metadata cannot change how the CLI parses arguments.
    """
    for name in (namespace, collection_name, role_name):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise GateError(f"Invalid resource name: {name!r}")
    source, output = source.resolve(), output.resolve()
    if profile not in ("collection_default", *MODULE_PROFILES):
        raise GateError(f"Unknown collection profile: {profile}")
    for skeleton in ("role_default", profile):
        if not (source / skeleton).is_dir():
            raise GateError(f"Missing skeleton directory: {source / skeleton}")
    output.mkdir(parents=True, exist_ok=False)
    env = isolated_environment(output)
    roles = output / "roles"
    collections = output / "source-collections"
    roles.mkdir()
    collections.mkdir()
    extras: list[str] = []
    if metadata is not None:
        variables = output / "extra-vars.json"
        write_json(variables, metadata)
        extras = ["--extra-vars", f"@{variables}"]
    run(
        [
            "ansible-galaxy",
            "role",
            "init",
            "--role-skeleton",
            str(source / "role_default"),
            "--init-path",
            str(roles),
            *extras,
            role_name,
        ],
        cwd=output,
        env=env,
    )
    run(
        [
            "ansible-galaxy",
            "collection",
            "init",
            "--collection-skeleton",
            str(source / profile),
            "--init-path",
            str(collections),
            *extras,
            f"{namespace}.{collection_name}",
        ],
        cwd=output,
        env=env,
    )
    return Rendered(
        output,
        roles / role_name,
        collections / namespace / collection_name,
        env,
        profile,
    )


def example_metadata(*, punctuation: bool = False) -> dict[str, object]:
    """Return explicit metadata used in smoke tests and interactive rendering."""
    # A Galaxy-compatible author also serves as the standalone role namespace
    # during production linting. The punctuation case checks human names.
    author = 'A "Quoted" Author' if punctuation else "skeleton_test"
    company = 'Example "Company" \\ Labs' if punctuation else "Example Company"
    return {
        "author": author,
        "authors": [author, "Second Author"],
        "company": company,
        "description": 'Manage "example": service.'
        if punctuation
        else "Manage an example service.",
        "repository": "https://example.com/repository",
        "repository_url": "https://example.com/repository",
        "issues": "https://example.com/repository/issues",
        "issue_tracker_url": "https://example.com/repository/issues",
        "homepage": "https://example.com",
        "homepage_url": "https://example.com",
        "min_ansible_version": "2.19.0",
        "version": "1.0.0",
        "product": "Example Grid",
    }


def main() -> int:
    """Render examples for inspection, keeping all output in the requested path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--defaults", action="store_true")
    parser.add_argument(
        "--profile",
        choices=["collection_default", *MODULE_PROFILES],
        default="collection_default",
    )
    args = parser.parse_args()
    metadata = None if args.defaults else example_metadata()
    if metadata is not None and args.profile in PRODUCTS:
        metadata["product"] = PRODUCTS[args.profile]
        metadata["skeleton_revision"] = skeleton_revision()
    try:
        rendered = render(
            args.output,
            source=args.source,
            metadata=metadata,
            profile=args.profile,
        )
    except (GateError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Role: {rendered.role}\nCollection: {rendered.collection}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
