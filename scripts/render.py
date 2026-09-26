"""Render both skeletons into a new directory using ansible-galaxy."""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.common import ROOT, GateError, isolated_environment, run, write_json


@dataclass(frozen=True)
class Rendered:
    """Locate generated resources and the isolated Ansible environment."""

    work: Path
    role: Path
    collection: Path
    env: dict[str, str]


def render(
    output: Path,
    *,
    source: Path = ROOT,
    namespace: str = "skeleton_test",
    collection_name: str = "example_app",
    role_name: str = "example_role",
    metadata: dict[str, object] | None = None,
) -> Rendered:
    """Render real Galaxy resources, refusing to merge with existing output.

    Names must be valid identifiers. Extra variables are serialized as JSON so
    punctuation in metadata cannot change how the CLI parses arguments.
    """
    for name in (namespace, collection_name, role_name):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise GateError(f"Invalid resource name: {name!r}")
    source, output = source.resolve(), output.resolve()
    for skeleton in ("role_default", "collection_default"):
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
            str(source / "collection_default"),
            "--init-path",
            str(collections),
            *extras,
            f"{namespace}.{collection_name}",
        ],
        cwd=output,
        env=env,
    )
    return Rendered(
        output, roles / role_name, collections / namespace / collection_name, env
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
    }


def main() -> int:
    """Render examples for inspection, keeping all output in the requested path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--defaults", action="store_true")
    args = parser.parse_args()
    try:
        rendered = render(
            args.output,
            source=args.source,
            metadata=None if args.defaults else example_metadata(),
        )
    except (GateError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Role: {rendered.role}\nCollection: {rendered.collection}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
