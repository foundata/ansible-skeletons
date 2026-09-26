"""Exercise generated Molecule infrastructure with active role assertions."""

import argparse
import json
import os
import shutil
import socket
import sys
import uuid
from pathlib import Path

import yaml

from scripts.common import ROOT, GateError, require_tools, run, write_json
from scripts.render import Rendered, example_metadata, render


def mapping(value: object) -> dict[str, object]:
    """Validate a YAML mapping before configuring an integration fixture."""
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise GateError("Expected a mapping in the generated Molecule configuration")
    return dict(value)


def write_yaml(path: Path, value: object) -> None:
    """Write data-only YAML into a generated test project."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def fixture_tasks(project: Path, molecule: Path, *, collection: bool) -> None:
    """Add active converge and verify tasks without rewriting shipped role logic."""
    name = f"{project.parent.name}.{project.name}.run" if collection else project.name
    prefix = f"run_{project.name}" if collection else project.name
    include = {
        "name": "Gate | Execute the generated role",
        "ansible.builtin.include_role": {"name": name, "public": True},
        "vars": {f"{prefix}_state": "present", f"{prefix}_service_state": "unmanaged"},
    }
    assertion = {
        "name": "Gate | Confirm that role initialization ran",
        "ansible.builtin.assert": {
            "that": [
                "ansible_facts['distribution'] is ansible.builtin.defined",
                f"__{prefix}_platform_filenames_most_specific_first | ansible.builtin.length > 0",
            ]
        },
    }
    write_yaml(molecule / "default/tasks/converge/gate.yml", [include, assertion])
    uninstall = {
        **include,
        "vars": {f"{prefix}_state": "absent", f"{prefix}_service_state": "unmanaged"},
    }
    # Facts do not persist between Molecule's separate playbook processes, so
    # verify invokes the role itself before asserting its observable results.
    write_yaml(molecule / "default/tasks/verify/gate.yml", [uninstall, assertion])


def configure_scenario(
    project: Path, molecule: Path, backend: str, identity: str
) -> None:
    """Select small, uniquely named platforms in the generated configuration."""
    config_path = molecule / "default/molecule.yml"
    config = mapping(yaml.safe_load(config_path.read_text(encoding="utf-8")))
    if backend == "libvirt":
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        config["platforms"] = [
            {
                "name": f"skeleton-gate-{identity}-debian",
                "type": "libvirt",
                "image": os.environ.get(
                    "SKELETONS_VM_IMAGE",
                    "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2",
                ),
                "ssh_port": port,
                "memory": 2048,
                "vcpus": 2,
            }
        ]
    else:
        platforms = config.get("platforms")
        if not isinstance(platforms, list):
            raise GateError("The generated scenario has no platforms")
        selected = []
        for raw in platforms:
            platform = mapping(raw)
            if platform.get("name") in ("molecule-debian13", "molecule-almalinux9"):
                platform["name"] = f"skeleton-gate-{identity}-{platform['name']}"
                selected.append(platform)
        if len(selected) != 2:
            raise GateError(
                "The required Debian and AlmaLinux test platforms are missing"
            )
        config["platforms"] = selected
    # Dependencies are installed from the gate's lock below, not from the
    # deliberately unpinned requirements offered to generated projects.
    config["dependency"] = {"name": "galaxy", "enabled": False}
    write_yaml(config_path, config)
    ee_path = molecule / "ee/molecule.yml"
    ee = mapping(yaml.safe_load(ee_path.read_text(encoding="utf-8")))
    ee["dependency"] = {"name": "galaxy", "enabled": False}
    write_yaml(ee_path, ee)
    fixture_tasks(project, molecule, collection=(project / "galaxy.yml").is_file())


def podman_environment(output: Path, env: dict[str, str]) -> dict[str, str]:
    """Confine image builds and template cleanup to a disposable Podman store."""
    storage = output / "podman"
    storage.mkdir()
    config = storage / "storage.conf"
    config.write_text(
        "[storage]\n"
        'driver = "overlay"\n'
        f"runroot = {json.dumps(str(storage / 'run'))}\n"
        f"graphroot = {json.dumps(str(storage / 'graph'))}\n"
        f"rootless_storage_path = {json.dumps(str(storage / 'graph'))}\n",
        encoding="utf-8",
    )
    return {**env, "CONTAINERS_STORAGE_CONF": str(config)}


def lint_molecule(molecule: Path, rendered: Rendered) -> None:
    """Lint infrastructure playbooks in the directory their relative includes use."""
    config = rendered.work / "molecule-lint.yml"
    write_yaml(
        config,
        {
            "profile": "production",
            "strict": True,
            "skip_list": ["yaml[comments]", "var-naming[no-role-prefix]"],
        },
    )
    run(
        [
            "ansible-lint",
            "--config-file",
            str(config),
            str(molecule / "shared/playbooks"),
        ],
        cwd=molecule / "shared/playbooks",
        env=rendered.env,
    )


def integration(backend: str, output: Path) -> None:
    """Run both generated projects, preserving failures and always destroying targets."""
    require_tools("molecule", "ansible-galaxy", "ansible-lint")
    if backend in ("podman", "ee"):
        require_tools("podman")
    if backend == "ee":
        require_tools("ansible-builder")
    if backend == "libvirt":
        require_tools("virsh", "qemu-img", "ssh")
        run(["virsh", "--connect", "qemu:///session", "list"], timeout=30)
        if not os.access("/dev/kvm", os.R_OK | os.W_OK):
            raise GateError("libvirt integration requires access to /dev/kvm")
    output.mkdir(parents=True, exist_ok=False)
    rendered = render(output / "rendered", metadata=example_metadata())
    env = rendered.env
    if backend in ("podman", "ee"):
        env = podman_environment(output, env)
        info = mapping(
            json.loads(
                run(
                    ["podman", "info", "--format", "json"],
                    env=env,
                    timeout=60,
                    capture=True,
                )
            )
        )
        store = mapping(info.get("store"))
        if store.get("graphRoot") != str(output / "podman/graph"):
            raise GateError("Podman did not select the disposable storage directory")
    env["MOLECULE_GLOB"] = "{molecule,extensions/molecule}/*/molecule.yml"
    env["MOLECULE_VM_CACHE_DIR"] = str(output / "vm-images")
    run(
        [
            "ansible-galaxy",
            "collection",
            "install",
            "--no-deps",
            "--requirements-file",
            str(ROOT / "tests/requirements.yml"),
        ],
        cwd=output,
        env=env,
    )
    evidence: list[dict[str, object]] = []
    try:
        for project, relative in (
            (rendered.role, "molecule"),
            (rendered.collection, "extensions/molecule"),
        ):
            molecule = project / relative
            configure_scenario(project, molecule, backend, uuid.uuid4().hex[:12])
            if backend == "ee" and project == rendered.role:
                shutil.copyfile(
                    ROOT / "tests/fixtures/ee-negative.yml",
                    molecule / "ee/tasks/verify/zz-gate-negative.yml",
                )
            scenario = "ee" if backend == "ee" else "default"
            # Both standalone and collection roles resolve from the generated
            # source during infrastructure tests. The smoke gate separately
            # tests the collection installed from its built archive.
            env["ANSIBLE_COLLECTIONS_PATH"] = str(rendered.work / "collections")
            if (project / "galaxy.yml").exists():
                target = (
                    rendered.work
                    / "collections/ansible_collections"
                    / project.parent.name
                    / project.name
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(project, target_is_directory=True)
            lint_molecule(molecule, rendered)
            try:
                run(
                    ["molecule", "test", "--scenario-name", scenario],
                    cwd=project,
                    env=env,
                    timeout=5400,
                )
            finally:
                run(
                    ["molecule", "destroy", "--scenario-name", scenario],
                    cwd=project,
                    env=env,
                    timeout=600,
                )
            evidence.append(
                {"project": project.name, "scenario": scenario, "status": "passed"}
            )
    finally:
        if backend in ("podman", "ee"):
            # The separate store contains only resources created by this run.
            run(["podman", "system", "reset", "--force"], env=env, timeout=180)
    write_json(output / "integration.json", {"backend": backend, "results": evidence})


def main() -> int:
    """Run one infrastructure tier; missing prerequisites are fatal."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", choices=["podman", "libvirt", "ee"])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        integration(args.backend, args.output.resolve())
    except (GateError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
