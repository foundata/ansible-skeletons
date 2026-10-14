# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise generated Molecule infrastructure with active role assertions."""

import argparse
import json
import os
import re
import shutil
import socket
import sys
import tomllib
import uuid
from pathlib import Path

import yaml

from scripts.common import ROOT, GateError, require_tools, run, write_json
from scripts.controller import (
    SSH_CONTROL_DIRECTORY,
    SSH_CONTROL_LAYOUT,
    check_socket_layout,
    controller_environment,
)
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
    """Keep storage, locks and the rootless pause process inside this run."""
    output = output.resolve()
    env = controller_environment(output, env)
    storage = output / "podman"
    storage.mkdir()
    runtime = output / "r"
    runtime.mkdir(mode=0o700)
    (runtime / "libpod/tmp").mkdir(parents=True, mode=0o700)
    temporary = storage / "tmp"
    temporary.mkdir()
    config = storage / "storage.conf"
    config.write_text(
        "[storage]\n"
        'driver = "overlay"\n'
        f"runroot = {json.dumps(str(storage / 'run'))}\n"
        f"graphroot = {json.dumps(str(storage / 'graph'))}\n"
        f"rootless_storage_path = {json.dumps(str(storage / 'graph'))}\n",
        encoding="utf-8",
    )
    engine = storage / "containers.conf"
    engine.write_text(
        '[engine]\nlock_type = "file"\n'
        f"static_dir = {json.dumps(str(storage / 'graph/libpod'))}\n"
        f"tmp_dir = {json.dumps(str(runtime / 't'))}\n",
        encoding="utf-8",
    )
    isolated = {
        key: value
        for key, value in env.items()
        if key
        not in {
            "CONTAINERS_CONF_OVERRIDE",
            "CONTAINERS_CONF_MODULES",
            "CONTAINER_HOST",
            "CONTAINER_CONNECTION",
        }
    }
    return {
        **isolated,
        "CONTAINERS_STORAGE_CONF": str(config),
        "CONTAINERS_CONF": str(engine),
        "XDG_CONFIG_HOME": str(storage / "config"),
        "XDG_RUNTIME_DIR": str(runtime),
    }


def check_podman_paths(output: Path, env: dict[str, str]) -> None:
    """Refuse cleanup unless effective storage and runtime paths are private."""
    output = output.resolve()

    def private(value: object) -> Path:
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise GateError(f"Missing absolute Podman runtime path: {value!r}")
        path = Path(value).resolve()
        if path == output or not path.is_relative_to(output):
            raise GateError(f"Podman runtime path is outside this run: {value}")
        return path

    storage_conf = private(env.get("CONTAINERS_STORAGE_CONF"))
    engine_conf = private(env.get("CONTAINERS_CONF"))
    runtime = private(env.get("XDG_RUNTIME_DIR"))
    private(env.get("XDG_CONFIG_HOME"))
    private(env.get("TMPDIR"))
    if runtime.stat().st_mode & 0o077:
        raise GateError("Podman runtime directory must be private to its owner")
    with engine_conf.open("rb") as stream:
        engine = tomllib.load(stream)["engine"]
    if engine.get("lock_type") != "file":
        raise GateError("Podman cleanup requires run-local file locks")
    for key in ("static_dir", "tmp_dir"):
        private(engine.get(key))
    # JSON info omits static_dir and tmp_dir; debug info reports their effective
    # values, including a database override of the supplied configuration.
    output_text = run(
        [
            "podman",
            "--remote=false",
            "--log-level",
            "debug",
            "info",
            "--format",
            "json",
        ],
        env=env,
        timeout=60,
        capture=True,
    )
    start = output_text.find("{\n")
    if start < 0:
        raise GateError("Podman info did not report its effective paths")
    info = mapping(json.JSONDecoder().raw_decode(output_text[start:])[0])
    store = mapping(info.get("store"))
    for key in ("graphRoot", "runRoot", "volumePath", "imageCopyTmpDir"):
        private(store.get(key))
    if private(store.get("configFile")) != storage_conf:
        raise GateError("Podman selected another storage configuration")
    host = mapping(info.get("host"))
    if host.get("serviceIsRemote") is not False:
        raise GateError("Podman cleanup requires a local engine")
    private(mapping(host.get("remoteSocket")).get("path"))
    runtime_version = mapping(host.get("ociRuntime")).get("version")
    if not isinstance(runtime_version, str):
        raise GateError("Podman info omitted the OCI runtime version")
    rundirs = [
        line.removeprefix("rundir:").strip()
        for line in runtime_version.splitlines()
        if line.startswith("rundir:")
    ]
    if len(rundirs) != 1:
        raise GateError("Podman info omitted an unambiguous OCI state directory")
    private(rundirs[0])
    effective = {}
    for line in output_text.splitlines():
        match = re.search(r'msg=(".*")$', line)
        if match:
            message = json.loads(match[1])
            for label in (
                "graph root",
                "run root",
                "static dir",
                "tmp dir",
                "volume path",
            ):
                prefix = f"Using {label} "
                if message.startswith(prefix):
                    effective[label] = private(message[len(prefix) :])
    if set(effective) != {
        "graph root",
        "run root",
        "static dir",
        "tmp dir",
        "volume path",
    }:
        raise GateError("Podman debug info omitted an effective runtime path")
    for label, key in (("static dir", "static_dir"), ("tmp dir", "tmp_dir")):
        if effective[label] != private(engine[key]):
            raise GateError(f"Podman selected another {label}")


def reset_podman(output: Path, env: dict[str, str]) -> None:
    """Recheck the effective paths immediately before destructive cleanup."""
    check_podman_paths(output, env)
    run(
        ["podman", "--remote=false", "system", "reset", "--force"], env=env, timeout=180
    )


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


def molecule_command(action: str, scenario: str) -> list[str]:
    """Use the same scenario selection for execution and documented examples."""
    return ["molecule", action, "--scenario-name", scenario]


def integration(backend: str, output: Path) -> None:
    """Run both generated projects; keep failures and always destroy the targets."""
    check_socket_layout(output, SSH_CONTROL_LAYOUT)
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
    env = controller_environment(output, rendered.env)
    # SSH control sockets would otherwise live in ~/.ansible/cp of the rendered
    # home, too deep for the socket path limit.
    control = output / SSH_CONTROL_DIRECTORY
    control.mkdir(mode=0o700)
    env["ANSIBLE_SSH_CONTROL_PATH_DIR"] = str(control)
    if backend in ("podman", "ee"):
        env = podman_environment(output, env)
        check_podman_paths(output, env)
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
                    molecule_command("test", scenario),
                    cwd=project,
                    env=env,
                    timeout=5400,
                )
            finally:
                run(
                    molecule_command("destroy", scenario),
                    cwd=project,
                    env=env,
                    timeout=600,
                )
            evidence.append(
                {"project": project.name, "scenario": scenario, "status": "passed"}
            )
    finally:
        if backend in ("podman", "ee"):
            reset_podman(output, env)
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
