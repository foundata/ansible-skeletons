"""Check real Galaxy rendering, collection packaging and role execution."""

import argparse
import importlib.metadata
import json
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

import yaml

from scripts.common import ROOT, GateError, run, write_json
from scripts.render import Rendered, example_metadata, render


def check_tree(source: Path, destination: Path) -> None:
    """Verify every skeleton file survives one rendering pass and data parses."""
    for original in source.rglob("*"):
        if not original.is_file():
            continue
        relative = original.relative_to(source)
        target = destination / str(relative).removesuffix(".j2")
        if not target.is_file():
            raise GateError(f"Rendering omitted {relative}")
        if target.suffix in (".yml", ".yaml", ".example"):
            try:
                list(yaml.safe_load_all(target.read_text(encoding="utf-8")))
            except yaml.YAMLError as exc:
                raise GateError(f"Invalid generated YAML: {target}: {exc}") from exc
        elif target.suffix == ".toml":
            with target.open("rb") as stream:
                tomllib.load(stream)
    for runtime_template in (
        "shared/templates/cloud_init_user_data.yml.j2",
        "shared/templates/libvirt_domain.xml.j2",
    ):
        molecule = destination / (
            "extensions/molecule"
            if (destination / "galaxy.yml").exists()
            else "molecule"
        )
        if "{{" not in (molecule / runtime_template).read_text(encoding="utf-8"):
            raise GateError(
                f"Runtime template was consumed during initialization: {runtime_template}"
            )


def check_metadata(rendered: Rendered, metadata: dict[str, object]) -> None:
    """Assert explicit metadata retains its value, including punctuation."""
    galaxy = yaml.safe_load(
        (rendered.collection / "galaxy.yml").read_text(encoding="utf-8")
    )
    role = yaml.safe_load(
        (rendered.role / "meta/main.yml").read_text(encoding="utf-8")
    )["galaxy_info"]
    collection_role = yaml.safe_load(
        (rendered.collection / "roles/run/meta/main.yml").read_text(encoding="utf-8")
    )["galaxy_info"]
    for key in ("authors", "description", "repository", "version"):
        if galaxy[key] != metadata[key]:
            raise GateError(f"Collection metadata changed {key}")
    for key in ("author", "company", "description", "min_ansible_version"):
        if role[key] != metadata[key]:
            raise GateError(f"Role metadata changed {key}")
    if collection_role["company"] != metadata["company"]:
        raise GateError("Collection role metadata changed company")
    for project in (rendered.role, rendered.collection):
        with (project / "REUSE.toml").open("rb") as stream:
            reuse = tomllib.load(stream)
        if reuse["SPDX-PackageSupplier"] != metadata["company"]:
            raise GateError("REUSE metadata changed company")


def build_collection(rendered: Rendered) -> Path:
    """Build, inspect and install a collection without consulting Galaxy."""
    excluded = (
        ".ansible/cache",
        ".github/test.yml",
        ".vscode/settings.json",
        "AGENTS.md",
        "stale.tar.gz",
    )
    for relative in excluded:
        path = rendered.collection / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("must not ship\n", encoding="utf-8")
    artifacts = rendered.work / "artifacts"
    artifacts.mkdir()
    run(
        ["ansible-galaxy", "collection", "build", "--output-path", str(artifacts)],
        cwd=rendered.collection,
        env=rendered.env,
    )
    archives = list(artifacts.glob("*.tar.gz"))
    if len(archives) != 1:
        raise GateError("Expected exactly one generated collection archive")
    archive = archives[0]
    with tarfile.open(archive) as bundle:
        names = {member.name.removeprefix("./") for member in bundle.getmembers()}
        for required in (
            "MANIFEST.json",
            "FILES.json",
            "roles/run/tasks/main.yml",
            "meta/runtime.yml",
            "README.md",
            "LICENSES/GPL-3.0-or-later.txt",
        ):
            if required not in names:
                raise GateError(f"Collection artifact omitted {required}")
        for name in names:
            if name in (
                *excluded,
                ".rumdl.toml",
                "CODE_OF_CONDUCT.md",
            ) or name.startswith("extensions/molecule/"):
                raise GateError(
                    f"Collection artifact includes repository-only file: {name}"
                )
        stream = bundle.extractfile("MANIFEST.json")
        if stream is None:
            raise GateError("Collection manifest is missing")
        with stream:
            manifest = json.load(stream)["collection_info"]
        if manifest["name"] != rendered.collection.name:
            raise GateError("Collection manifest has the wrong name")
    run(
        [
            "ansible-galaxy",
            "collection",
            "install",
            "--no-deps",
            "--collections-path",
            rendered.env["ANSIBLE_COLLECTIONS_PATH"],
            str(archive),
        ],
        cwd=rendered.work,
        env=rendered.env,
    )
    return archive


def exercise_role(rendered: Rendered, *, collection: bool) -> None:
    """Run real role tasks against disposable files and a fake package backend."""
    name = (
        f"{rendered.collection.parent.name}.{rendered.collection.name}.run"
        if collection
        else rendered.role.name
    )
    prefix = f"run_{rendered.collection.name}" if collection else rendered.role.name
    role_path = (
        Path(rendered.env["ANSIBLE_COLLECTIONS_PATH"])
        / "ansible_collections"
        / rendered.collection.parent.name
        / rendered.collection.name
        / "roles/run"
        if collection
        else rendered.role
    )
    variables = rendered.work / f"{prefix}-smoke.json"
    write_json(
        variables,
        {
            "gate_role": name,
            "gate_prefix": prefix,
            "gate_expected_role_path": str(role_path),
            "gate_scratch": str(rendered.work / f"scratch-{prefix}"),
            "ansible_python_interpreter": sys.executable,
            "ansible_package_use": "ansible.legacy.gate_package",
            f"__{prefix}_packages_install": ["gate-example"],
            f"__{prefix}_packages_uninstall": ["gate-example"],
            f"{prefix}_service_state": "unmanaged",
            f"__{prefix}_paths_removal": [
                "{{ gate_scratch }}/remove-one",
                "{{ gate_scratch }}/remove-two",
            ],
        },
    )
    outcomes = rendered.work / f"{prefix}-outcomes.jsonl"
    env = {
        **rendered.env,
        "ANSIBLE_LIBRARY": str(ROOT / "tests/fixtures/library"),
        "ANSIBLE_CALLBACK_PLUGINS": str(ROOT / "tests/fixtures/callback_plugins"),
        "ANSIBLE_CALLBACKS_ENABLED": "gate_results",
        "SKELETONS_RESULTS": str(outcomes),
    }
    # A generic fixture supplies the dynamically named role variables in its
    # include through a JSON variable file, preserving the caller's loop item.
    playbook = rendered.work / f"{prefix}-smoke.yml"
    text = (ROOT / "tests/fixtures/smoke.yml").read_text(encoding="utf-8")
    playbook.write_text(text.replace("GATE_PREFIX", prefix), encoding="utf-8")
    command = [
        "ansible-playbook",
        "--inventory",
        "localhost,",
        "--connection",
        "local",
        "--extra-vars",
        f"@{variables}",
        str(playbook),
    ]
    run([*command, "--syntax-check"], cwd=rendered.work, env=env)
    run(command, cwd=rendered.work, env=env)
    results = [
        json.loads(line) for line in outcomes.read_text(encoding="utf-8").splitlines()
    ]
    packages = [row for row in results if row.get("package_state") is not None]
    if [row["package_state"] for row in packages] != ["present", "present", "absent"]:
        raise GateError(
            "Package tasks did not exercise present, repeated present and absent"
        )
    if any(not row["task_path"].startswith(f"{role_path}/") for row in packages):
        raise GateError("Package tasks ran from an unexpected role installation")
    removals = [
        row
        for row in results
        if row.get("action") == "ansible.builtin.file"
        and row["task_path"].startswith(f"{role_path}/")
    ]
    if [row["changed"] for row in removals] != [True, False]:
        raise GateError(
            "Repeated role execution did not preserve file-removal idempotence"
        )
    invalid = rendered.work / f"{prefix}-invalid.json"
    write_json(invalid, {f"{prefix}_state": "invalid-state"})
    result = subprocess.run(
        [*command, "--extra-vars", f"@{invalid}"],
        cwd=rendered.work,
        env=env,
        check=False,
        timeout=120,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if (
        result.returncode == 0
        or "invalid-state" not in result.stdout
        or "argument" not in result.stdout.lower()
    ):
        raise GateError(
            f"{name}: invalid role arguments were not rejected as expected\n{result.stdout}\n{result.stderr}"
        )


def smoke(output: Path, *, lint: bool = False) -> None:
    """Run a complete rendering and consumer smoke test in the current lane."""
    output.mkdir(parents=True, exist_ok=False)
    for case, metadata in (
        ("defaults", None),
        ("explicit", example_metadata()),
        ("punctuation", example_metadata(punctuation=True)),
    ):
        rendered = render(output / case, metadata=metadata)
        check_tree(ROOT / "role_default", rendered.role)
        check_tree(ROOT / "collection_default", rendered.collection)
        if metadata is not None:
            check_metadata(rendered, metadata)
        if case != "explicit":
            continue
        if lint:
            lint_resources(rendered)
        build_collection(rendered)
        exercise_role(rendered, collection=False)
        exercise_role(rendered, collection=True)
    write_json(
        output / "environment.json",
        {
            "python": sys.version,
            "ansible-core": importlib.metadata.version("ansible-core"),
            "PyYAML": importlib.metadata.version("PyYAML"),
        },
    )


def lint_resources(rendered: Rendered) -> None:
    """Lint generated resources with explicit, narrowly scoped exclusions."""
    config = rendered.work / "ansible-lint.yml"
    config.write_text(
        "profile: production\nstrict: true\nskip_list:\n  - yaml[comments]\n",
        encoding="utf-8",
    )
    for project, molecule in (
        (rendered.role, "molecule"),
        (rendered.collection, "extensions/molecule"),
    ):
        files = sorted(
            str(path)
            for path in project.rglob("*.yml")
            if not path.is_relative_to(project / molecule)
        )
        run(
            [
                "ansible-lint",
                "--config-file",
                str(config),
                "--project-dir",
                str(project),
                *files,
            ],
            cwd=project,
            env=rendered.env,
        )
        run(["reuse", "lint"], cwd=project, env=rendered.env)
    run(["antsibull-changelog", "lint"], cwd=rendered.collection, env=rendered.env)
    run(
        ["antsibull-changelog", "lint-changelog-yaml", "changelogs/changelog.yaml"],
        cwd=rendered.collection,
        env=rendered.env,
    )


def main() -> int:
    """Run one lane; the outer check command owns matrix selection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--lint", action="store_true")
    args = parser.parse_args()
    try:
        smoke(args.output.resolve(), lint=args.lint)
    except (GateError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
