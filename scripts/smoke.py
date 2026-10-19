# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check real Galaxy rendering, collection packaging and role execution."""

import argparse
import contextlib
import importlib.metadata
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

import yaml

from scripts.common import ROOT, GateError, matrix, run, write_json
from scripts.controller import NOX_LINK, linked, runtime_directory
from scripts.render import (
    MODULE_PROFILES,
    PRODUCTS,
    Rendered,
    example_metadata,
    render,
    skeleton_revision,
)


def check_tree(source: Path, destination: Path) -> None:
    """Verify every skeleton file survives one rendering pass and data parses."""
    for name in ("scripts", "pyproject.toml"):
        if (destination / name).exists():
            raise GateError(f"Generated project contains repository tooling: {name}")
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
    # The guide's tail format keeps a blank line below the section heading.
    ignore = (destination / ".gitignore").read_bytes()
    if b"\n### Repository-specific files\n\n" not in ignore:
        raise GateError("Rendered .gitignore lost the guide's tail format")
    # ansible-test's line-endings sanity test rejects any carriage return, so
    # the macOS icon entry is a wildcard instead of the upstream "Icon\r\r".
    if b"\r" in ignore or b"\nIcon?\n" not in ignore:
        raise GateError("Rendered .gitignore lost the portable macOS icon pattern")
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


# foundata's own renders get its author sentence and no skeleton credit.
FOUNDATA_COMPANY = "foundata GmbH"
FOUNDATA_URL = "https://foundata.com"
FOUNDATA_AUTHOR = (
    "This [project](https://foundata.com/en/projects/) was created and is "
    "maintained\nby [foundata](https://foundata.com/).\n"
)
SKELETON_CREDIT = "Initially based on an\n[Ansible skeleton]"


def holder(metadata: dict[str, object]) -> str:
    """Return the copyright holder: the company, then its URL in angle brackets."""
    company, url = str(metadata["company"]), metadata.get("company_url")
    return f"{company} <{url}>" if url else company


# REUSE-IgnoreStart
def check_holders(project: Path, expected: str) -> None:
    """Assert every license header of a fresh render names the holder."""
    for path in sorted(project.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for value in re.findall(
            r"SPDX-FileCopyrightText:[ \t]*(.*?)[ \t]*$", text, re.MULTILINE
        ):
            if value != expected:
                raise GateError(f"License header names {value!r}: {path}")


# REUSE-IgnoreEnd


def check_attribution(project: Path, metadata: dict[str, object]) -> None:
    """Assert the README's notice and author section match the holder."""
    company = str(metadata["company"])
    readme = (project / "README.md").read_text(encoding="utf-8")
    notice = re.compile(
        r"^<!-- should match SPDX-PackageSupplier -->\n"
        rf"Copyright \(c\) \d{{4}}, {re.escape(holder(metadata))}$",
        re.MULTILINE,
    )
    if not notice.search(readme):
        raise GateError(f"README copyright notice lost the guide's form: {project}")
    foundata = company.startswith("foundata GmbH")
    author = (
        FOUNDATA_AUTHOR
        if foundata
        else f"This project was created and is maintained by {company}.\n"
    )
    if author not in readme:
        raise GateError(f"README author sentence does not match the holder: {project}")
    if (SKELETON_CREDIT in readme) == foundata:
        raise GateError(f"README skeleton credit does not match the holder: {project}")


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
        if reuse["SPDX-PackageSupplier"] != holder(metadata):
            raise GateError("REUSE metadata changed company")
        check_attribution(project, metadata)
        check_holders(project, holder(metadata))


def build_collection(rendered: Rendered) -> Path:
    """Build, inspect and install a collection without consulting Galaxy."""
    excluded: tuple[str, ...] = (
        ".ansible/cache",
        ".github/test.yml",
        ".vscode/settings.json",
        "AGENTS.md",
        "stale.tar.gz",
        "tools/inventory/1.0.yml",
        "tools/sentinel.py",
        "tests/output/sentinel",
        ".cache/sentinel",
        ".venv/sentinel",
        ".mypy_cache/sentinel",
        ".ruff_cache/sentinel",
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
        payload = (
            ("roles/run/tasks/main.yml",)
            if rendered.profile == "collection_default"
            else (
                "plugins/modules/fixture.py",
                "plugins/modules/fixture_nested.py",
                "plugins/modules/fixture_nested_info.py",
                "plugins/modules/fixture_info.py",
                *(
                    ("plugins/modules/fixture_apply.py",)
                    if rendered.profile == "collection_modules_appliance"
                    else ()
                ),
                "CHANGELOG.rst",
                "tests/unit/plugins/conftest.py",
            )
        )
        for required in (
            "MANIFEST.json",
            "FILES.json",
            *payload,
            "meta/runtime.yml",
            "README.md",
            "LICENSES/GPL-3.0-or-later.txt",
        ):
            if required not in names:
                raise GateError(f"Collection artifact omitted {required}")
        for name in names:
            if (
                name
                in (
                    *excluded,
                    ".rumdl.toml",
                    ".ansible-lint",
                    ".yamllint",
                    "pyproject.toml",
                    "requirements-dev.txt",
                    "uv.lock",
                    "noxfile.py",
                    "antsibull-nox.toml",
                    "CODE_OF_CONDUCT.md",
                )
                or name.startswith(
                    (
                        "extensions/molecule/",
                        "scripts/",
                        "tools/",
                        "tests/output",
                        ".cache/",
                        ".venv/",
                    )
                )
                or any(
                    part
                    in {
                        "__pycache__",
                        ".mypy_cache",
                        ".ruff_cache",
                        ".pytest_cache",
                        ".nox",
                    }
                    for part in Path(name).parts
                )
            ):
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
    installed = (
        Path(rendered.env["ANSIBLE_COLLECTIONS_PATH"])
        / "ansible_collections"
        / rendered.collection.parent.name
        / rendered.collection.name
    )
    # Test fixtures stay outside the stripped collection; runtime code does not.
    if (installed / "tests").is_dir():
        shutil.copytree(installed / "tests", rendered.work / "artifact-tests")
    checker = runpy.run_path(str(rendered.collection / "tests/runtime_check.py"))
    checker["strip_runtime"](installed)
    rendered.env["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] = "false"
    rendered.env["PYTHONPATH"] = ""
    return archive


# The management name the appliance fixture's drill withdraws from the fake
# product's resolver: its first label names the fixture record.
RECOVERY_NAME = "molecule_fixture_dns.fixture.invalid"


@contextlib.contextmanager
def fake_product(rendered: Rendered, state: Path) -> Iterator[dict[str, str]]:
    """Run the render's fake product and yield its scenario environment.

    The appliance's credentials file also carries the lab contract of the
    fake console, resolver and management address, so its scenario drills.
    """
    state.mkdir()
    credentials = state / "credentials"
    credentials.write_text("SYNTHETIC-KEY:SYNTHETIC-SECRET", encoding="utf-8")
    credentials.chmod(0o600)
    fake = (
        rendered.collection
        / "tests/integration/targets/setup_fixture_fake/files/fake_product.py"
    )
    with (state / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [sys.executable, str(fake), str(state), "3600"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _attempt in range(300):
                if (state / "port").is_file() or server.poll() is not None:
                    break
                time.sleep(0.1)
            if not (state / "port").is_file():
                raise GateError("The fake product did not report its port")
            port = (state / "port").read_text(encoding="utf-8").strip()
            contract: dict[str, object] = {
                "key": "SYNTHETIC-KEY",
                "secret": "SYNTHETIC-SECRET",
            }
            if rendered.profile == "collection_modules_appliance":
                dns_port = (state / "dns-port").read_text(encoding="utf-8").strip()
                contract.update(
                    console={
                        "argv": [sys.executable, str(fake), "console", str(state)]
                    },
                    dns={
                        "host": "127.0.0.1",
                        "port": int(dns_port),
                        "name": RECOVERY_NAME,
                    },
                    management={"address": "127.0.0.1", "client": "127.0.0.1"},
                )
            scenario_credentials = state / "credentials.json"
            write_json(scenario_credentials, contract)
            yield {
                "MOLECULE_PRODUCT_URL": f"http://127.0.0.1:{port}/api",
                "MOLECULE_PRODUCT_CREDENTIALS": str(scenario_credentials),
            }
        finally:
            server.terminate()
            server.wait(timeout=30)


def molecule_with_fake(rendered: Rendered) -> None:
    """Run the module Molecule scenario against the render's own fake product."""
    with fake_product(rendered, rendered.work / "molecule-fake") as product:
        run(
            ["uv", "run", "--python", "3.13", "noxfile.py", "-s", "molecule"],
            cwd=rendered.collection,
            env={**rendered.env, "CI": "true", **product},
            timeout=1800,
        )


def drill_alone(rendered: Rendered) -> None:
    """Prove the drill's skip, then drill one subsystem of the installed artifact.

    The standalone playbook must refuse an unknown subsystem and name the ones
    it found, then drill the nested subsystem, upload included, against a fresh
    fake.
    """
    molecule = rendered.collection / "extensions/molecule"
    play = rendered.work / "recovery-skip.yml"
    write_json(
        play,
        [
            {
                "name": "Skip the drill without a console contract",
                "hosts": "localhost",
                "connection": "local",
                "gather_facts": False,
                "vars": {"__product_credentials": {"key": "k", "secret": "s"}},
                "tasks": [
                    {
                        "ansible.builtin.include_tasks": {
                            "file": str(
                                molecule / "default/tasks/side_effect/zz_recovery.yml"
                            )
                        }
                    }
                ],
            }
        ],
    )
    skipped = run(
        ["ansible-playbook", "-i", "localhost,", str(play)],
        cwd=rendered.work,
        env=rendered.env,
        capture=True,
    )
    if "Recovery drill skipped" not in skipped:
        raise GateError("The drill stage did not skip without a console contract")
    command = [
        "ansible-playbook",
        "-i",
        str(molecule / "default/inventory/hosts.yml"),
        str(molecule / "shared/playbooks/recovery.yml"),
    ]
    with fake_product(rendered, rendered.work / "recovery-fake") as product:
        env = {
            **rendered.env,
            **product,
            "MOLECULE_SCENARIO_DIRECTORY": str(molecule / "default"),
            "MOLECULE_RECOVERY_SCENARIO": "default",
        }
        refused = run(
            command,
            cwd=rendered.work,
            env={**env, "MOLECULE_RECOVERY_SUBSYSTEM": "unknown"},
            capture=True,
            allowed_exit_codes=(2,),
        )
        if "subsystems of default: fixture, fixture_nested" not in refused:
            raise GateError(
                "The standalone drill did not name the scenario's subsystems"
            )
        drilled = run(
            command,
            cwd=rendered.work,
            env={**env, "MOLECULE_RECOVERY_SUBSYSTEM": "fixture_nested"},
            capture=True,
        )
        print(drilled, flush=True)
        if '\\"subsystem\\": \\"fixture_nested\\"' not in drilled:
            raise GateError("The standalone drill recorded no drill of the subsystem")


def pinned_utils_version(root: Path = ROOT) -> str:
    """Return the foundata.utils release pinned with the other gate dependencies."""
    requirements = yaml.safe_load(
        (root / "tests/requirements.yml").read_text(encoding="utf-8")
    )
    versions = [
        str(entry["version"])
        for entry in requirements.get("collections", [])
        if isinstance(entry, dict) and entry.get("name") == "foundata.utils"
    ]
    if len(versions) != 1:
        raise GateError("tests/requirements.yml must pin foundata.utils exactly once")
    return versions[0]


def pinned_archives(directory: Path, root: Path = ROOT) -> dict[str, Path]:
    """Map each pinned Galaxy collection to its archive downloaded for this run."""
    requirements = yaml.safe_load(
        (root / "tests/requirements.yml").read_text(encoding="utf-8")
    )
    archives = {}
    for entry in requirements.get("collections", []):
        name, version = str(entry["name"]), str(entry["version"])
        archive = directory / f"{name.replace('.', '-')}-{version}.tar.gz"
        if not archive.is_file():
            raise GateError(f"The run's Galaxy downloads lack {name} {version}")
        archives[name] = archive
    return archives


def install_utils(
    rendered: Rendered, source: Path | None = None, collections: Path | None = None
) -> None:
    """Install the pinned foundata.utils release, or build a local source instead.

    ``collections`` names the run's downloads of the pinned collections; without
    it, the pinned release comes from Galaxy.
    """
    if source is None:
        run(
            [
                "ansible-galaxy",
                "collection",
                "install",
                "--no-deps",
                "-p",
                rendered.env["ANSIBLE_COLLECTIONS_PATH"],
                str(pinned_archives(collections)["foundata.utils"])
                if collections
                else f"foundata.utils:=={pinned_utils_version()}",
            ],
            env=rendered.env,
        )
        return
    if not (source / "galaxy.yml").is_file():
        raise GateError("--utils-source must be the foundata.utils source collection")
    metadata = yaml.safe_load((source / "galaxy.yml").read_text(encoding="utf-8"))
    if (metadata.get("namespace"), metadata.get("name")) != ("foundata", "utils"):
        raise GateError("Expected the foundata.utils dependency")
    artifacts = rendered.work / "dependency-artifacts"
    artifacts.mkdir()
    run(
        ["ansible-galaxy", "collection", "build", "--output-path", str(artifacts)],
        cwd=source,
        env=rendered.env,
    )
    archive = artifacts / f"foundata-utils-{metadata['version']}.tar.gz"
    run(
        [
            "ansible-galaxy",
            "collection",
            "install",
            "--no-deps",
            "-p",
            rendered.env["ANSIBLE_COLLECTIONS_PATH"],
            str(archive),
        ],
        env=rendered.env,
    )


def install_scenario_collections(rendered: Rendered, collections: Path) -> None:
    """Install the Molecule scenario's collections from the run's downloads.

    antsibull-nox then finds them installed beside foundata.utils and calls
    Galaxy for none of them. A scenario collection that tests/requirements.yml
    does not pin is an error.
    """
    requirements = yaml.safe_load(
        (
            rendered.collection / "extensions/molecule/default/requirements.yml"
        ).read_text(encoding="utf-8")
    )
    names = [
        entry if isinstance(entry, str) else str(entry["name"])
        for entry in requirements.get("collections") or []
    ]
    archives = pinned_archives(collections)
    missing = sorted(set(names) - set(archives))
    if missing:
        raise GateError(
            "Pin the Molecule scenario's collections in tests/requirements.yml: "
            + ", ".join(missing)
        )
    if names:
        run(
            [
                "ansible-galaxy",
                "collection",
                "install",
                "--no-deps",
                "-p",
                rendered.env["ANSIBLE_COLLECTIONS_PATH"],
                *(str(archives[name]) for name in names),
            ],
            env=rendered.env,
        )


def exercise_modules(rendered: Rendered) -> None:
    """Resolve docs; run the installed fixture payload against its loopback server."""
    namespace, name = rendered.collection.parent.name, rendered.collection.name
    installed = (
        Path(rendered.env["ANSIBLE_COLLECTIONS_PATH"])
        / "ansible_collections"
        / namespace
        / name
    )
    modules = ("fixture", "fixture_nested", "fixture_nested_info", "fixture_info") + (
        ("fixture_apply",) if rendered.profile == "collection_modules_appliance" else ()
    )
    for module in modules:
        docs = run(
            ["ansible-doc", "--json", f"{namespace}.{name}.{module}"],
            cwd=rendered.work,
            env=rendered.env,
            capture=True,
        )
        document = json.loads(docs)[f"{namespace}.{name}.{module}"]["doc"]
        if not Path(document["filename"]).is_relative_to(installed):
            raise GateError("ansible-doc resolved outside the installed artifact")
    play = rendered.work / "module-artifact.yml"
    write_json(
        play,
        [
            {
                "name": "Exercise installed fixture modules",
                "hosts": "localhost",
                "connection": "local",
                "gather_facts": False,
                "become": False,
                "vars": {"ansible_python_interpreter": sys.executable},
                "roles": [role],
            }
            for role in modules
        ],
    )
    run(
        ["ansible-playbook", "-i", "localhost,", str(play)],
        cwd=rendered.work,
        env={
            **rendered.env,
            "ANSIBLE_ROLES_PATH": str(
                rendered.work / "artifact-tests/integration/targets"
            ),
        },
    )


# Namespace and name of each module render. The second identity has
# maximum-length names and a quoted product.
IDENTITIES = (("skeleton_test", "module_fixture"), ("n" * 64, "c" * 64))


def newest_released_core(root: Path = ROOT) -> str:
    """Return the newest ansible-core series of the locked matrix, such as 2.21."""
    series = []
    for lane in matrix(root):
        match = re.fullmatch(r"core([1-9])([0-9]+)", lane.group)
        if match is None:
            raise GateError(f"Matrix group does not name a core series: {lane.group}")
        series.append((int(match[1]), int(match[2])))
    major, minor = max(series)
    return f"{major}.{minor}"


def identity_sessions(index: int, *, full: bool) -> list[list[str]]:
    """Return the nox session sets of one identity; an empty set is the defaults.

    Every check runs the default sessions (lint, docs, licenses, Markdown,
    changelog) in check-only mode; the full matrix adds ansible-test lanes. The
    first identity runs sanity, units and integration on every core. The second
    identity tests the render, and of those lanes only sanity reads its names,
    through validate-modules and ansible-doc. It runs sanity once, on the newest
    released core, instead of every lane on every core.
    """
    if not full:
        return [[]]
    if index == 0:
        return [
            [],
            ["ansible-test-sanity"],
            ["ansible-test-units"],
            ["ansible-test-integration"],
        ]
    return [[], [f"ansible-test-sanity-{newest_released_core()}"]]


def user_image_store() -> str | None:
    """Return the graph root of the user's own Podman store, if Podman runs."""
    if shutil.which("podman") is None:
        return None
    store = run(
        ["podman", "info", "--format", "{{.Store.GraphRoot}}"], capture=True
    ).strip()
    return store if Path(store).is_dir() else None


def private_podman_store(
    rendered: Rendered, run_root: Path, images: str | None = None
) -> None:
    """Keep the Podman store of a render's ansible-test containers to itself.

    Rootless Podman keeps images and containers below the relocated HOME, but
    every store of the user shares one run root. Identities that run at the
    same time must not share the run state of their stores, so each gets a run
    root of its own in the runtime directory. ``images`` adds a store
    read-only, so ansible-test finds the images it already holds instead of
    pulling every image again into each render.
    """
    home = Path(rendered.env["HOME"])
    config = home / ".config/containers/storage.conf"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "[storage]\n"
        'driver = "overlay"\n'
        f"graphroot = {json.dumps(str(home / '.local/share/containers/storage'))}\n"
        f"runroot = {json.dumps(str(run_root))}\n"
        + (
            f"\n[storage.options]\nadditionalimagestores = {json.dumps([images])}\n"
            if images
            else ""
        ),
        encoding="utf-8",
    )


def remove_render_containers(render: Path, log: TextIO | None = None) -> None:
    """Remove containers that an interrupted ansible-test left in a render's store.

    Only a store below the render qualifies; the user's own store never does.
    Podman has not used a store whose run root is missing, or the run that
    made it removed its containers before the run root went; calling Podman
    then would only create the run root again.
    """
    home = render / "home"
    config = home / ".config/containers/storage.conf"
    if not config.is_file():
        return
    storage = tomllib.loads(config.read_text(encoding="utf-8")).get("storage", {})
    run_root = storage.get("runroot")
    if not isinstance(run_root, str) or not Path(run_root).is_absolute():
        raise GateError(
            f"The render's Podman store names no absolute run root: {render}"
        )
    if not Path(run_root).exists():
        return
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("CONTAINER", "XDG_CONFIG_HOME", "XDG_DATA_HOME"))
    }
    env["HOME"] = str(home)
    store = run(
        ["podman", "info", "--format", "{{.Store.GraphRoot}}"],
        env=env,
        capture=True,
        log=log,
    ).strip()
    if not Path(store).resolve().is_relative_to(render.resolve()):
        raise GateError(f"Podman did not select the render's own store: {store}")
    run(["podman", "rm", "--all", "--force", "--time", "0"], env=env, log=log)


def module_smoke(
    output: Path,
    *,
    utils_source: Path | None = None,
    profile: str = "collection_modules_appliance",
    full: bool = False,
    identity: int | None = None,
    collections: Path | None = None,
) -> None:
    """Render distinct identities and exercise the installed module skeleton.

    With ``identity``, only that one runs, beside the other identity's own run
    in the same output directory; the check runs each identity as a step.
    """
    output.mkdir(parents=True, exist_ok=identity is not None)
    indexes = range(len(IDENTITIES)) if identity is None else (identity,)
    if 0 in indexes:
        check_missing_product(output, profile)
    with runtime_directory() as runtime:
        try:
            for index in indexes:
                module_identity(
                    output,
                    index,
                    utils_source=utils_source,
                    profile=profile,
                    full=full,
                    collections=collections,
                    runtime=runtime,
                )
        finally:
            # An interrupted ansible-test leaves its containers running; their
            # run state goes with the runtime directory.
            for index in indexes:
                remove_render_containers(output / IDENTITIES[index][1])
    if identity is None:
        write_json(output / "profile.json", {"profile": profile, "full": full})


def check_missing_product(output: Path, profile: str) -> None:
    """Require initialization without metadata to name the product variable."""
    missing = run(
        [
            "ansible-galaxy",
            "collection",
            "init",
            "--collection-skeleton",
            str(ROOT / profile),
            "--init-path",
            str(output / "missing-metadata"),
            "missing.metadata",
        ],
        capture=True,
        allowed_exit_codes=(1,),
    )
    if "product" not in missing:
        raise GateError(
            "Name-only initialization did not identify the missing product variable"
        )


def module_identity(
    output: Path,
    index: int,
    *,
    utils_source: Path | None,
    profile: str,
    full: bool,
    runtime: Path,
    collections: Path | None = None,
) -> None:
    """Render one identity and run its checks, build and installed modules."""
    namespace, name = IDENTITIES[index]
    metadata = example_metadata(punctuation=bool(index))
    if index == 0:
        # The first identity also lints foundata's own README variant.
        metadata["company"] = FOUNDATA_COMPANY
        metadata["company_url"] = FOUNDATA_URL
    # A double quote in the second name must survive every rendered file.
    metadata["product"] = PRODUCTS[profile] if index == 0 else 'Other "Quoted" Product'
    if index == 0:
        metadata.pop("version")
        metadata.pop("min_ansible_version")
    else:
        metadata["version"] = "2.3.0"
        metadata["min_ansible_version"] = "2.19.1"
    metadata["skeleton_revision"] = skeleton_revision()
    rendered = render(
        output / name,
        namespace=namespace,
        collection_name=name,
        metadata=metadata,
        profile=profile,
    )
    check_tree(ROOT / profile, rendered.collection)
    for package in (rendered.collection / "tests/unit").rglob("__init__.py"):
        if package.stat().st_size:
            raise GateError(f"Unit package marker is not empty: {package}")
    galaxy = yaml.safe_load((rendered.collection / "galaxy.yml").read_text())
    if galaxy["dependencies"] != {"foundata.utils": ">=1.0.0,<2.0.0"}:
        raise GateError("Module profile omitted the utility dependency")
    if galaxy["version"] != metadata.get("version", "1.0.0"):
        raise GateError("Module profile changed the collection version")
    client = (rendered.collection / "plugins/module_utils/_client.py").read_text()
    if f'USER_AGENT = "{namespace}.{name}/{galaxy["version"]}"' not in client:
        raise GateError(
            "Module user agent must identify the collection and its version"
        )
    scenario = rendered.collection / "extensions/molecule/default"
    molecule = yaml.safe_load((scenario / "molecule.yml").read_text())
    inventory = scenario / molecule["provisioner"]["inventory"]["links"]["hosts"]
    if inventory.name != "hosts.yml" or "{{ ansible_playbook_python }}" not in (
        inventory.read_text()
    ):
        raise GateError(
            "Molecule inventory changed its filename or runtime expressions"
        )
    meta = yaml.safe_load((rendered.collection / "meta/runtime.yml").read_text())
    if meta["requires_ansible"] != ">=" + str(
        metadata.get("min_ansible_version", "2.19.0")
    ):
        raise GateError("Module profile changed the core floor")
    for removed in (
        "roles",
        "meta/execution-environment.yml",
        ".ansible-docsmith.toml",
        "extensions/molecule/ee",
    ):
        if (rendered.collection / removed).exists():
            raise GateError(f"Module profile retained {removed}")
    if any(
        path.is_dir() and not any(path.iterdir())
        for path in rendered.collection.rglob("*")
    ):
        raise GateError("Module render contains empty directories")
    with (rendered.collection / "REUSE.toml").open("rb") as stream:
        reuse = tomllib.load(stream)
    if reuse["SPDX-PackageSupplier"] != holder(metadata):
        raise GateError("REUSE metadata changed company")
    check_attribution(rendered.collection, metadata)
    check_holders(rendered.collection, holder(metadata))
    install_utils(rendered, utils_source, collections)
    if collections is not None:
        install_scenario_collections(rendered, collections)
    private_podman_store(rendered, runtime / f"p{index}", user_image_store())
    run(["git", "init", "--quiet"], cwd=rendered.collection)
    run(["git", "add", "."], cwd=rendered.collection)
    for sessions in identity_sessions(index, full=full):
        run(
            [
                "uv",
                "run",
                "--python",
                "3.13",
                "noxfile.py",
                *(["-s", *sessions] if sessions else []),
            ],
            cwd=rendered.collection,
            env={**rendered.env, "CI": "true"},
            timeout=7200,
        )
    if index == 0:
        check_short_tmpdir(rendered)
    if full and index == 0:
        molecule_with_fake(rendered)
    build_collection(rendered)
    exercise_modules(rendered)
    if full and index == 0 and profile == "collection_modules_appliance":
        drill_alone(rendered)


# Runs as a noxfile beside the render's own, which it loads first, so the
# session below meets the render's TMPDIR handling under the real nox.
TMPDIR_PROBE = """\
# /// script
# dependencies = ["nox>=2025.02.09", "antsibull-nox"]
# ///
import os
import runpy
import sys

import nox

# nox works in the probe's directory; the render's noxfile reads its own.
os.chdir(os.environ["PROBE_COLLECTION"])
runpy.run_path("noxfile.py")

PROOF = \"\"\"
import multiprocessing, os
from multiprocessing.managers import BaseManager
temporary = os.environ["TMPDIR"]
assert len(os.fsencode(temporary)) <= 64, temporary
assert os.path.realpath(temporary) == os.path.realpath(os.environ["PROBE_TMP"])
with BaseManager(ctx=multiprocessing.get_context("spawn")) as manager:
    address = manager.address
assert isinstance(address, str), address
assert address.startswith(os.path.join(temporary, "")), (address, temporary)
\"\"\"


@nox.session(name="tmpdir-probe", python=False, default=False)
def tmpdir_probe(session: nox.Session) -> None:
    tmpdir = session.create_tmp()
    session.run(
        sys.executable, "-c", PROOF, env={"PROBE_TMP": tmpdir}, external=True
    )


if __name__ == "__main__":
    nox.main()
"""


def check_short_tmpdir(rendered: Rendered) -> None:
    """Prove that a render's nox sessions bind sockets below a short TMPDIR.

    The rendered noxfile shortens each session's TMPDIR by wrapping
    nox.Session.create_tmp. The probe runs a real session from an environment
    directory too deep for a socket path and starts a real multiprocessing
    manager there; a nox or antsibull-nox change that bypasses the wrapper
    fails here, not silently in a collection.
    """
    probe = rendered.work / "tmpdir_probe.py"
    probe.write_text(TMPDIR_PROBE, encoding="utf-8")
    envdir = rendered.work / ("deep-" + "d" * 80)
    if len(os.fsencode(envdir / "tmpdir-probe/tmp")) <= 64:
        raise GateError("The TMPDIR probe's environment is not deep enough")
    run(
        [
            "uv",
            "run",
            "--python",
            "3.13",
            str(probe),
            "-f",
            str(probe),
            "-s",
            "tmpdir-probe",
            "--envdir",
            str(envdir),
        ],
        cwd=rendered.collection,
        env={
            **rendered.env,
            "CI": "true",
            "PROBE_COLLECTION": str(rendered.collection),
        },
        timeout=1800,
    )


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
        "ANSIBLE_INJECT_FACT_VARS": "false",
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
    gathers = [
        row
        for row in results
        if row.get("action") == "ansible.builtin.setup"
        and row["task_path"].startswith(f"{role_path}/")
    ]
    if len(gathers) != 2:
        raise GateError(
            "Role must gather initially and after partial facts, but reuse complete cached facts"
        )
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


def check_molecule_contract(rendered: Rendered) -> None:
    """Render VM inputs and compose inventory without creating containers or VMs."""
    for project, relative in (
        (rendered.role, "molecule"),
        (rendered.collection, "extensions/molecule"),
    ):
        shared = project / relative / "shared"
        work = rendered.work / f"molecule-contract-{project.name}"
        (work / "inventory").mkdir(parents=True)
        public_key = "ssh-ed25519 AAAATEST fixture"
        (work / "id_ed25519.pub").write_text(public_key + "\n", encoding="utf-8")
        inventory_path = work / "inventory/molecule_inventory.yml"
        inventory_path.write_text(
            "all:\n  children:\n    molecule:\n      hosts:\n        existing: {}\n",
            encoding="utf-8",
        )
        vm = {
            "name": "fixture-vm",
            "type": "libvirt",
            "ssh_port": 2244,
            "memory": 1536,
            "vcpus": 3,
            "extra_disks": [{"serial": "data", "size": "1G"}],
        }
        (work / "vm" / str(vm["name"])).mkdir(parents=True)
        platforms = [vm, {"name": "fixture-container", "image": "example"}]
        plays = []
        for backend in ("podman", "libvirt", "podman"):
            source = yaml.safe_load(
                (shared / f"playbooks/create-{backend}.yml").read_text(encoding="utf-8")
            )[0]
            initial = source["pre_tasks"][0]
            if initial.get("ansible.builtin.set_fact") != {
                "__testing_inventory": "{{ molecule_inventory }}"
            }:
                raise GateError("Missing computed-inventory initialization")
            tasks = [initial]
            tasks.extend(
                task
                for task in source["tasks"]
                if task["name"].endswith(
                    (
                        " | Create inventory data, add container to groups",
                        " | Create inventory data, add instances to groups",
                        " | Store inventory data as molecule_inventory.yml",
                    )
                )
            )
            if len(tasks) != 3:
                raise GateError("Missing inventory composition tasks")
            if backend == "libvirt":
                templates = [
                    task
                    for task in source["tasks"]
                    if "ansible.builtin.template" in task
                ]
                if len(templates) != 2:
                    raise GateError("Expected cloud-init and libvirt templates")
                tasks.extend(templates)
            plays.append(
                {
                    "name": f"Check {backend} inputs",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": {
                        **source["vars"],
                        "molecule_ephemeral_directory": str(work),
                        "molecule_yml": {"platforms": platforms},
                        "ansible_python_interpreter": sys.executable,
                    },
                    "tasks": tasks,
                }
            )
        playbook = shared / "playbooks/gate-contract.yml"
        playbook.write_text(yaml.safe_dump(plays, sort_keys=False), encoding="utf-8")
        try:
            run(
                ["ansible-playbook", "--inventory", "localhost,", str(playbook)],
                cwd=project,
                env=rendered.env,
            )
        finally:
            playbook.unlink()
        inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
        hosts = inventory["all"]["children"]["molecule"]["hosts"]
        if set(hosts) != {"existing", "fixture-container", "fixture-vm"}:
            raise GateError("Mixed-backend inventory lost or added hosts")
        if hosts["fixture-vm"]["ansible_port"] != 2244:
            raise GateError("VM inventory lost the SSH port")
        user_data = yaml.safe_load(
            (work / "vm/fixture-vm/user-data").read_text(encoding="utf-8")
        )
        if user_data["users"][0]["ssh_authorized_keys"] != [public_key]:
            raise GateError("Cloud-init lost the explicit SSH key input")
        domain = ET.parse(work / "vm/fixture-vm/domain.xml").getroot()
        if (
            domain.findtext("name") != "fixture-vm"
            or domain.findtext("memory") != "1536"
            or domain.findtext("vcpu") != "3"
            or domain.findtext("devices/disk/serial") != "data"
        ):
            raise GateError("Domain template lost platform settings")


def smoke(output: Path, *, lint: bool = False, collections: Path | None = None) -> None:
    """Run a complete rendering and consumer smoke test in the current lane."""
    output.mkdir(parents=True, exist_ok=False)
    names = ("skeleton_test", "example_app", "example_role")
    # Long names push generated lines past the YAML and Markdown limits.
    long_names = (
        "n" * 64,
        "c" * 64,
        "r" * 64,
    )
    for case, metadata, (namespace, collection_name, role_name) in (
        ("defaults", None, names),
        ("explicit", example_metadata(), names),
        ("punctuation", example_metadata(punctuation=True), names),
        ("long-names", example_metadata(punctuation=True), long_names),
        (
            "foundata",
            {
                **example_metadata(),
                "company": FOUNDATA_COMPANY,
                "company_url": FOUNDATA_URL,
            },
            names,
        ),
    ):
        rendered = render(
            output / case,
            metadata=metadata,
            namespace=namespace,
            collection_name=collection_name,
            role_name=role_name,
        )
        check_tree(ROOT / "role_default", rendered.role)
        check_tree(ROOT / "collection_default", rendered.collection)
        if metadata is not None:
            check_metadata(rendered, metadata)
        if case == "long-names" and lint:
            lint_resources(rendered, collections)
        if case != "explicit":
            continue
        if lint:
            lint_resources(rendered, collections)
        check_molecule_contract(rendered)
        build_collection(rendered)
        exercise_role(rendered, collection=False)
        exercise_role(rendered, collection=True)
    write_json(
        output / "environment.json",
        {
            "python": sys.version,
            "ansible-core": importlib.metadata.version("ansible-core"),
            "PyYAML": importlib.metadata.version("PyYAML"),
            "foundata-rules-required": lint,
            "ansible-lint-foundata": (
                importlib.metadata.version("ansible-lint-foundata") if lint else None
            ),
        },
    )


def lint_resources(rendered: Rendered, collections: Path | None = None) -> None:
    """Lint generated resources, Molecule included, with their own configuration."""
    # The syntax check of the Molecule playbooks needs the collections they use,
    # from the run's downloads when there are any.
    run(
        [
            "ansible-galaxy",
            "collection",
            "install",
            "--no-deps",
            *(
                [str(archive) for archive in pinned_archives(collections).values()]
                if collections
                else ["--requirements-file", str(ROOT / "tests/requirements.yml")]
            ),
        ],
        cwd=rendered.work,
        env=rendered.env,
    )
    for project in (rendered.role, rendered.collection):
        lint_project(project, rendered.env)
        check_fix_stability(project, rendered.env)
    # The role has no nox; the collection's remaining checks run through its nox.
    run(["reuse", "lint"], cwd=rendered.role, env=rendered.env)
    check_nox_gate(rendered)


def lint_project(project: Path, env: dict[str, str]) -> None:
    """Require the installed extension before checking a generated project."""
    run(
        [
            sys.executable,
            "-m",
            "ansible_lint_foundata.cli",
            "verify",
            "--project-dir",
            str(project),
        ],
        cwd=project,
        env=env,
    )
    run(
        [sys.executable, "-m", "ansiblelint", "--offline", "--strict"],
        cwd=project,
        env=env,
    )


def lint_constraints() -> str:
    """Pin effective distributions, including local wheels in uv overlays."""
    names = {
        re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        for distribution in importlib.metadata.distributions()
    }
    requirements = []
    for name in sorted(names):
        distribution = importlib.metadata.distribution(name)
        source = json.loads(distribution.read_text("direct_url.json") or "{}")
        requirements.append(
            f"{name} @ {source['url']}"
            if "archive_info" in source
            else f"{name}=={distribution.version}"
        )
    return "\n".join(requirements) + "\n"


def default_sessions(listing: str) -> list[str]:
    """Return the sessions a plain ``nox`` would run, from ``nox --list`` output."""
    return [
        line.split()[1]
        for line in listing.splitlines()
        if line.startswith("* ") and len(line.split()) > 1
    ]


def check_nox_gate(rendered: Rendered) -> None:
    """Run the generated collection's default nox sessions in check-only mode.

    Tool versions are pinned to this environment through constraints.
    """
    constraints = rendered.work / "lint-constraints.txt"
    constraints.write_text(lint_constraints(), encoding="utf-8")
    # Antsibull's collection setup needs repository metadata for the staged copy.
    run(["git", "init", "--quiet"], cwd=rendered.collection)
    run(["git", "add", "."], cwd=rendered.collection)
    run(["antsibull-nox", "lint-config"], cwd=rendered.collection, env=rendered.env)
    # nox, antsibull-nox and Python keep the link's short path for the
    # docs-check forkserver socket; the environments stay in the render.
    environment = rendered.work / "nox"
    environment.mkdir()
    listing = run(
        ["nox", "--list"], cwd=rendered.collection, env=rendered.env, capture=True
    )
    # galaxy-importer caps ansible-lint below what the foundata rules need
    # (26.8 at galaxy-importer 0.4.43), so the import check runs unpinned in a
    # second invocation. Once its cap includes 26.9, add galaxy-importer to the
    # "lint" group in pyproject.toml and drop this exception.
    unpinned = {"build-import-check"}
    sessions = [name for name in default_sessions(listing) if name not in unpinned]
    if not sessions:
        raise GateError("The generated noxfile lists no default session")
    env = {
        **rendered.env,
        "CI": "true",
        "UV_CONSTRAINT": str(constraints),
        "PIP_CONSTRAINT": str(constraints),
    }
    with (
        runtime_directory() as runtime,
        linked(runtime / NOX_LINK, environment) as link,
    ):
        command = [
            "nox",
            "--sessions",
            *sessions,
            "--force-python",
            f"{sys.version_info.major}.{sys.version_info.minor}",
            "--default-venv-backend",
            "uv",
            "--envdir",
            str(link),
        ]
        output = run(
            command,
            cwd=rendered.collection,
            env=env,
            timeout=1800,
            capture=True,
        )
        print(output, end="")
        if "Verified discovery and execution of 17 foundata rules." not in output:
            raise GateError("Nox session did not verify the adopted foundata rules")
        run(
            [
                "nox",
                "--sessions",
                *sorted(unpinned),
                *command[command.index("--force-python") :],
            ],
            cwd=rendered.collection,
            env={**rendered.env, "CI": "true"},
            timeout=1800,
        )

    stock = rendered.work / "stock-lint"
    run(["uv", "venv", "--python", sys.executable, str(stock)], env=env)
    run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(stock / "bin/python"),
            "--constraint",
            str(constraints),
            "ansible-lint",
            "antsibull-nox",
            "nox",
        ],
        env=env,
    )
    output = run(
        [
            str(stock / "bin/python"),
            "-m",
            "nox",
            "--session",
            "ansible-lint",
            "--no-venv",
        ],
        cwd=rendered.collection,
        env={
            **env,
            "PATH": os.pathsep.join((str(stock / "bin"), env["PATH"])),
            "VIRTUAL_ENV": str(stock),
        },
        timeout=1200,
        capture=True,
    )
    print(output, end="")
    if (
        "ansible-lint-foundata is not installed; running stock rules only."
        not in output
    ):
        raise GateError("Stock-only Nox passthrough did not warn about missing rules")


def check_fix_stability(project: Path, env: dict[str, str]) -> None:
    """Require a second native autofix pass to leave generated YAML unchanged."""
    command = ["ansible-lint", "--offline", "--fix", "--project-dir", str(project)]
    # Ansible Lint 26.9 returns 8 when fixes changed files, including layout only.
    run(command, cwd=project, env=env, allowed_exit_codes=(0, 8))
    files = {
        path: path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
        and path.suffix in (".yml", ".yaml")
        and not {".cache", ".ansible", ".nox", ".venv"}.intersection(
            path.relative_to(project).parts
        )
    }
    run(command, cwd=project, env=env)
    changed = [
        str(path.relative_to(project))
        for path, data in files.items()
        if path.read_bytes() != data
    ]
    if changed:
        raise GateError(
            f"Second autofix pass changed YAML: {', '.join(sorted(changed))}"
        )


def main() -> int:
    """Run one lane; the outer check command owns matrix selection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--lint", action="store_true")
    parser.add_argument(
        "--profile",
        choices=["collection_default", *MODULE_PROFILES],
        default="collection_default",
    )
    parser.add_argument(
        "--utils-source",
        type=Path,
        help="Local foundata.utils source to test instead of the pinned release",
    )
    parser.add_argument("--ansible-tests", action="store_true")
    parser.add_argument(
        "--collections",
        type=Path,
        help="The run's downloads of the pinned Galaxy collections",
    )
    parser.add_argument(
        "--identity",
        type=int,
        choices=range(len(IDENTITIES)),
        help="Run only this module identity; both run by default",
    )
    args = parser.parse_args()
    try:
        if args.profile in MODULE_PROFILES:
            module_smoke(
                args.output.resolve(),
                utils_source=args.utils_source.resolve() if args.utils_source else None,
                profile=args.profile,
                full=args.ansible_tests,
                identity=args.identity,
                collections=args.collections.resolve() if args.collections else None,
            )
        else:
            smoke(
                args.output.resolve(),
                lint=args.lint,
                collections=args.collections.resolve() if args.collections else None,
            )
    except (GateError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
