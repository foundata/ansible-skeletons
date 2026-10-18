# Development

<!--
SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
SPDX-License-Identifier: GPL-3.0-or-later
-->

This repository ships skeletons for `ansible-galaxy`. Before a release, the
checks generate projects from all four skeletons, run their roles, and build
and install the generated collections.

Both module skeletons also render two identities and exercise installed fixture
modules against the `foundata.utils` release pinned in `tests/requirements.yml`.


## Table of contents<a id="toc"></a>

- [Prerequisites](#prerequisites)
- [Getting started](#getting-started)
- [Project structure](#project-structure)
- [Development standards](#development-standards)
- [Testing](#testing)
  - [Quick checks](#quick-checks)
  - [Compatibility matrix](#compatibility-matrix)
  - [Infrastructure tests](#infrastructure-tests)
  - [Updating dependencies](#updating-dependencies)
- [Releases](#releases)
  - [Release checklist](#release-checklist)
  - [Recovery](#recovery)
- [Troubleshooting](#troubleshooting)


## Prerequisites<a id="prerequisites"></a>

Install Git, [uv](https://docs.astral.sh/uv/), `shfmt`, `shellcheck`, and
`checkbashisms`. Python tools are installed from the committed `uv.lock`.
Python 3.12, 3.13 and 3.14 are needed for the full matrix; uv can supply them:

```sh
uv python install 3.12 3.13 3.14
uv sync --locked
```

uv installs the Python tools in isolated environments, foundata's
[`releasing`](https://foundata.com/en/projects/releasing/) among them.
`pyproject.toml` contains the development dependencies and configuration.


## Getting started<a id="getting-started"></a>

While editing templates or tooling, check what your changes affect:

```sh
./scripts/check.sh --changed
```

This runs the static checks, the repository tests and only the lanes and
module skeletons that your uncommitted changes affect. A change outside the
skeleton directories, such as in `scripts/` or `uv.lock`, selects everything.
The run prints the checks it skipped and never writes a passing
`checks.json`. It cannot be combined with `--resume` or `--integration`.
Before each commit, run the full quick check:

```sh
./scripts/check.sh
```

To retain generated projects for inspection, choose a directory that does not
already exist:

```sh
uv run --locked --group core221 python -m scripts.render \
  --output "${XDG_STATE_HOME:-${HOME}/.local/state}/ansible-skeletons/render-1"
```

The output contains a standalone role, a collection, and an isolated Ansible
configuration. Add `--defaults` to exercise initialization without extra
variables. The helper refuses to overwrite an existing directory.


## Project structure<a id="project-structure"></a>

|              Path               | Purpose |
| ------------------------------- | ------- |
| `role_default/`                 | Standalone role skeleton |
| `collection_default/`           | Collection skeleton, including its `run` role |
| `collection_modules_appliance/` | Management-path appliance module skeleton |
| `collection_modules/`           | Module skeleton with an immediate-write REST fixture |
| `scripts/render.py`             | Render a role and the selected collection skeleton with Galaxy |
| `scripts/smoke.py`              | Validate rendering, artifacts and role behavior |
| `scripts/check.sh`              | Local quality-check entry point |
| `scripts/integration.py`        | Generated Molecule scenarios with active fixtures |
| `scripts/release.py`            | Validate a release commit, then hand the tag to `releasing` |
| `tests/unit/`                   | Release and helper regression tests |
| `tests/fixtures/`               | Disposable role execution fixtures |
| `tests/requirements.yml`        | Pinned Galaxy dependencies for the checks |
| `pyproject.toml`, `uv.lock`     | Tool configuration, matrix and locked Python tools |

Galaxy consumes one `.j2` suffix when creating a project. Files named
`*.j2.j2` intentionally become runtime `*.j2` templates. Likewise, `FIXME`
instructions and `.yml.example` files are intentional parts of the product.
Do not reject all remaining Jinja expressions or placeholders as render errors.

Edit each skeleton directly in its own directory.
`scripts.render --profile collection_modules` or
`--profile collection_modules_appliance` renders a module skeleton. Their
rendered `DEVELOPMENT.md` explains the lifecycle base, the binding, the engine
and the two fixtures. Only the appliance variant carries act-once activation,
the management-path guard scaffold and the recovery drill.

`tests/unit/test_checks.py` lists the files that skeletons share. Copies of a
shared file are identical except inside variant regions, which wrap the few
lines a skeleton needs differently:

```jinja
{# skeleton-variant: dependencies #}
dependencies:
  "foundata.utils": ">=1.0.0,<2.0.0"
{# end skeleton-variant #}
```

Every copy declares the same regions in the same order; a region may be empty.
Galaxy renders the marker lines to nothing. Change the shared text in every
copy at once.


## Development standards<a id="development-standards"></a>

Follow the foundata
[Ansible playbooks](https://foundata.com/en/guidelines/ansible-playbooks/),
[Ansible roles and collections](https://foundata.com/en/guidelines/ansible-roles-collections/),
[shell](https://foundata.com/en/guidelines/shell-scripting/),
[Python](https://foundata.com/en/guidelines/python/),
and
[Markdown](https://foundata.com/en/guidelines/markdown/)
style guides. Use UTF-8 without a BOM, LF line endings, and a trailing newline.

Use Python for structured data and validation. Keep shell wrappers small. Add
regression tests for new behavior and bug fixes, and explain any lint
suppressions.

```sh
uv run --locked ruff format scripts tests
uv run --locked ruff check scripts tests
uv run --locked mypy scripts tests
```

Commit messages use one scoped subject, for example
`tests: verify generated collection installation`. Do not add attribution
trailers. Review the diff and stage only changes intended for the commit.


## Testing<a id="testing"></a>

### Quick checks<a id="quick-checks"></a>

Run `./scripts/check.sh` before each commit. It checks the working tree,
uncommitted changes included: Python and shell checks, Markdown linting, REUSE
validation, the helper tests and the default Ansible lane. The checks of
generated projects include:

- Initialization with default, explicit and quoted metadata.
- Collection builds, their contents and installation into an isolated
  collection path.
- Strict production-profile Ansible linting of both renders, then an autofix
  pass that must change nothing.
- The rendered collection's default Nox sessions in check-only mode, with tool
  versions pinned to the check environment, plus a stock-only `--no-venv` run.
  Repository checks require successful foundata rule verification.
- Both module skeletons under two identities: their default Nox sessions in
  check-only mode (Ruff and mypy include the integration targets' Python), a
  build, and runs of the installed definition-driven and hook-based fixture
  modules.

The check runs independent steps at the same time: the repository tests, the
lanes, `generated-lint` and each identity of both module skeletons. `--jobs N`
sets how many run at once, by default half the CPUs. Each step writes
`logs/<step>.log` below the check's output and prints one line when it starts
and one when it ends, with its duration. A failing step prints the end of its
log and the log's path. With `--jobs 1`, the steps run one after another in a
fixed order and the first failure ends the run. With more jobs, every step
whose prerequisites passed still runs, so a [resumed run](#check-results)
repeats only what failed.

Durations on a 16-core workstation with the default eight jobs, October 2026:

|                       Run                       |     Duration      | Longest chain of steps |
| ----------------------------------------------- | ----------------- | ---------------------- |
| Quick check                                     | about 5.5 minutes | `generated-lint`       |
| `--matrix`                                      | about 13 minutes  | the first module identity |
| Quick check with all three infrastructure tiers | about 11 minutes  | the tiers, one after another |
| `--matrix` with all three tiers                 | about 15 minutes  | the tiers, one after another |

To test a release candidate of foundata's ansible-lint rules, build its wheel
and pass it to override the released package:

```sh
./scripts/check.sh --foundata-wheel /absolute/path/to/ansible_lint_foundata-1.1.0rc1-py3-none-any.whl
```

To test against an unreleased `foundata.utils` commit, pass its source
directory with `--utils-source /path/to/utils` or set `SKELETONS_UTILS_SOURCE`.
Release checks always use the pinned release.

To run only the helper regression suite:

```sh
uv run --locked python -m pytest -q
```

Use `python -m pytest` so repository-local modules are importable without an
installation or test-specific `sys.path` changes. Warnings are errors and pytest
configuration is strict. Required suites must execute without failures or skips.

#### Keep, resume and remove results<a id="check-results"></a>

Without `--output`, the check works in a temporary directory below `/var/tmp`
and deletes it at the end, logs included. To keep the results, name a new
directory. It must not be on a tmpfs such as `/tmp`, because a check writes
several gigabytes there, container storage included:

```sh
output="${XDG_STATE_HOME:-${HOME}/.local/state}/ansible-skeletons/check-1"
./scripts/check.sh --output "${output}"
```

A passing check keeps only its evidence: `run.json`, `records/`, `logs/`, the
JUnit results `unit-python*.xml`, and `checks.json` with the collection
archives it lists. It deletes generated projects, virtual environments and
container storage, and reports how much it kept. `--keep` retains everything,
for example to inspect a render. A failed run keeps everything for diagnosis.

To continue a failed or interrupted run, for example after a network timeout,
pass its output to `--resume` with the same options as before:

```sh
./scripts/check.sh --resume "${output}"
```

The resumed run reuses every step that passed and reruns the failed and
missing ones, replacing what their earlier attempt left. It refuses when the
sources changed: each finished step leaves a record in `records/` with the
digest of the path, mode and content of the tracked files and of the
untracked files Git does not ignore. A step whose options changed, such as a
module identity resumed with `--matrix`, runs again. Containers an
interrupted ansible-test left in a module render's own Podman store are
removed first. A resumed run that passes prunes its output like any passing
run, and a pruned output cannot be resumed.

To delete an output you no longer need:

```sh
./scripts/check.sh --remove "${output}"
```

`--remove` takes one absolute path without symbolic links and refuses the
checkout, your home directory and their ancestors. The directory must hold the
`run.json` of a check, or be a release gate's evidence with `gate.json` and
`checks/run.json`. Files that containers wrote as subordinate users are removed
inside Podman's user namespace.


### Compatibility matrix<a id="compatibility-matrix"></a>

```sh
./scripts/check.sh --matrix
```

The matrix is defined in `[tool.skeletons]` in `pyproject.toml`. Each lane uses
a separate locked environment; no lane upgrades dependencies during a run.

For the module skeletons, the matrix adds:

- The first identity's sanity, unit and integration sessions on every core,
  and its Molecule scenario against the skeleton's own fake product,
  including the lost replies.
- The second identity's sanity session, once, on the newest released core.
  This identity renders maximum-length names and a quoted product, and sanity
  is the only ansible-test lane that reads those names.
- In the appliance scenario, a real drill of two areas against the fake
  product's console and resolver: `fixture` loses only DNS and observes the
  loss through its `observe.yml`; `fixture_nested` loses API access and
  uploads a helper through the console. Both restore.
- A drill of one area alone through the standalone playbook, after checking
  that the playbook refuses an unknown area, and proof that the drill skips
  without a console contract.

Unit tests cover the management-listener refusal in normal and check mode.
None of this qualifies a product's console recovery: that drill runs on the
collection's authorized lab before an area's first write that can affect the
management path.

The module renders' ansible-test lanes run their containers in a Podman store
of each render's own. It reads the images of your own Podman store read-only,
so an image already there is not pulled again; a missing image is pulled into
the render's store and removed with the output.

Review the matrix against the
[upstream maintenance policy](https://docs.ansible.com/ansible/latest/reference_appendices/release_and_maintenance.html#ansible-core-support-matrix)
when updating dependencies. Update the README compatibility statement together
with the configuration.

### Infrastructure tests<a id="infrastructure-tests"></a>

Overview:

|   Tier    |                               Coverage                               | Additional prerequisites |
| --------- | -------------------------------------------------------------------- | ------------------------ |
| `podman`  | Debian 13 and AlmaLinux 9, converge, idempotence, verify and destroy | Working rootless Podman  |
| `libvirt` | Debian 13 VM, converge, idempotence, verify and destroy              | Session libvirt, KVM access, QEMU, SSH and passt |
| `ee`      | Build an execution environment containing each generated artifact    | Rootless Podman; ansible-builder comes from uv |

```sh
./scripts/check.sh --integration podman
./scripts/check.sh --integration libvirt
./scripts/check.sh --integration ee
```

Tiers share Podman and libvirt state and run one at a time. They start once
the static checks, the repository tests and the default lane pass, while the
other quick steps continue.

For an infrastructure-only iteration:

```sh
uv run --locked --group core221 --group lint --group integration \
  python -m scripts.integration podman \
  --output "${XDG_STATE_HOME:-${HOME}/.local/state}/ansible-skeletons/podman-1"
```

Tests need network access for the pinned Galaxy collections, container images
and, for libvirt, the cloud image. A check downloads the Galaxy collections
once, into `galaxy/` below its output, and every later step, including the
module renders' Molecule scenarios, installs them from there.
`SKELETONS_VM_IMAGE` can name an absolute path to an existing
Debian-compatible cloud-init image. Each run uses unique VM and container
names. Podman tests use a separate disposable storage configuration, including
the execution-environment build and its image cleanup.


### Updating dependencies<a id="updating-dependencies"></a>

Change Python dependency groups and regenerate `uv.lock` deliberately:

```sh
uv lock --upgrade
./scripts/check.sh --matrix --integration podman --integration libvirt --integration ee
```

Review lock changes before committing. Galaxy pins live in
`tests/requirements.yml`; update them together with infrastructure validation.
The module renders' Molecule scenarios install only from these pins, so the
file must list every collection those scenarios require.


## Releases<a id="releases"></a>

A release is a tag on a validated commit.

The changelog section, the tag, the push, the GitHub release entry and the
publication checks run through
[`releasing`](https://foundata.com/en/projects/releasing/), whose declaration is
[`.releasing.toml`](./.releasing.toml). What stays in this repository is the
skeleton-specific gate (`scripts/release.py check`).


### Release checklist<a id="release-checklist"></a>

1. Run `./scripts/check.sh --changed` while editing and `./scripts/check.sh`
   before each commit, and resolve failures.
2. Choose the next Semantic Versioning `X.Y.Z` version and move the Unreleased
   entries into a dated section. The command writes the section, its release
   link and the Unreleased comparison link, and leaves the placeholder entry
   under the Unreleased heading. Do not change the development project's
   `0.0.0` version in `pyproject.toml`.

   ```sh
   version='X.Y.Z'
   uv run --locked release changelog release "${version}"
   ```

3. Review and commit the release preparation:

   ```sh
   git diff --check
   git diff
   git add CHANGELOG.md
   git commit -m "release: prepare ${version}"
   git status --short
   ```

   Implementation changes must already be committed. The final status must be
   empty, including untracked files. Do not amend this commit after validation.
4. Run the release gate into a new output directory outside the checkout:

   ```sh
   state="${XDG_STATE_HOME:-${HOME}/.local/state}"
   evidence="${state}/ansible-skeletons/release-${version}"
   uv run --locked python -m scripts.release check "${version}" \
     --output "${evidence}"
   ```

   Do not use a tmpfs such as `/tmp`: the integration tests keep their
   container storage below the output. On a 16-core workstation, the gate's
   checks with all three tiers took about 15 minutes in October 2026.

   `check.log` shows one line per step as it starts and ends; each step's
   output is in `checks/logs/<step>.log`. A successful run creates
   `report.json`; a failure keeps its diagnostics but creates no passing
   report. Keep the whole evidence directory until step 9; the later steps
   read it.

   When a step fails for a reason outside the sources, such as a network
   timeout, rerun the failed and missing steps into the same evidence:

   ```sh
   uv run --locked python -m scripts.release check "${version}" \
     --resume "${evidence}"
   ```

   The gate resumes only evidence of the same commit, base and backends. A
   resumed gate's report has the same form as a fresh one.
5. Create and inspect the local annotated tag:

   ```sh
   uv run --locked python -m scripts.release tag "${evidence}/report.json"
   git show "v${version}"
   ```

   The helper refuses unless the report belongs to `HEAD` with intact digests,
   the tree is clean, the remote answers, and no equal or newer release tag
   exists locally or on the remote. Nothing is pushed.
6. Push the branch, then the tag:

   ```sh
   uv run --locked release push "${version}"
   ```

   The branch goes first, so the tag never names a commit the remote has on no
   branch, and the command refuses a tag the branch does not contain.
7. Create the GitHub release from the changelog section. This needs an
   authenticated `gh`:

   ```sh
   uv run --locked release forge release-create "${version}"
   ```

8. Verify the published tag, the latest release and a fresh public clone:

   ```sh
   uv run --locked python -m scripts.release verify "${evidence}/report.json"
   ```

   The helper checks that the remote holds the tag and that the forge reports
   it as latest, then clones the tag anonymously and runs the render, build,
   installation and smoke tests on that clone, including the module
   skeletons' Molecule scenarios against their fake products. It starts no
   container or VM but takes several minutes.
9. Unless you archive the evidence, remove it:

   ```sh
   ./scripts/check.sh --remove "${evidence}"
   ```

Maintainers can bypass these local checks by invoking Git directly. Enforce
protected tags or equivalent forge rules separately if server-side enforcement
is needed. Any future CI should call the same commands.

### Recovery<a id="recovery"></a>

For a failed check, inspect its log, fix the cause, commit source changes, and
run the gate into a new evidence directory. `--resume` serves failures outside
the sources on the same commit only. Never edit a report or a step record to
turn a failure into success or carry it forward to another commit.

While no GitHub release exists, `uv run --locked release tag delete
"${version}"` removes a wrong tag locally and on the remote; it refuses once a
release exists. Do not move, replace or reuse a tag that consumers may have
checked out. Correct a defective release with a new patch version.

`uv run --locked release status "${version}"` reports where a release stands.
After an interrupted push or release creation, inspect the existing remote tag
and release entry and finish the missing publication step. Preserve the
validated commit and rerun publication verification when complete.


## Troubleshooting<a id="troubleshooting"></a>

- **The output path exists:** choose a new path; the helpers never merge a
  new result into an older run. To continue a failed run, use `--resume`.
- **The lock does not match:** review the dependency changes and regenerate
  the lock deliberately, as [Updating dependencies](#updating-dependencies)
  shows; release checks use `--locked`.
- **A socket path is too long:** the check binds its Unix sockets in a short
  private directory in `XDG_RUNTIME_DIR`, or `/tmp` without one, and refuses
  to start when a socket path there would exceed 103 bytes (Linux allows
  108). Shorten `XDG_RUNTIME_DIR`.
- **A tier's infrastructure is missing:** each tier's requirements are
  mandatory. Use quick checks on that machine, then run the release gate on a
  suitable host.
- **A step failed:** its log is `logs/<step>.log` below the output, or
  `checks/logs/<step>.log` in release evidence. A failed run also keeps the
  generated YAML, the isolated `ansible.cfg`, collection archives and
  Molecule diagnostics; `--keep` keeps them after a passing run.
