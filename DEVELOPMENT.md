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
To test a skeleton change against an unreleased `foundata.utils` commit, pass
`--utils-source /path/to/utils` to `scripts/check.sh` or set
`SKELETONS_UTILS_SOURCE`. Release checks always use the pinned release.


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
`pyproject.toml` contains development dependencies and configuration; its
`0.0.0` version belongs to the development setup. Skeleton releases use the
version in the dated changelog section and the corresponding `vX.Y.Z` Git tag,
which is what `.releasing.toml` declares.


## Getting started<a id="getting-started"></a>

Run the quick checks after editing templates or tooling:

```sh
./scripts/check.sh
```

To retain generated projects for inspection, choose a directory that does not
already exist:

```sh
uv run --locked --group core221 python -m scripts.render \
  --output /tmp/skeleton-examples
```

The output contains a standalone role, a collection, and an isolated Ansible
configuration. Add `--defaults` to exercise initialization without extra
variables. The helper refuses to overwrite an existing directory.


## Project structure<a id="project-structure"></a>

|              Path               | Purpose |
| ------------------------------- | ------- |
| `role_default/`                 | Standalone role skeleton |
| `collection_default/`           | Collection skeleton, including its `run` role |
| `collection_modules_appliance/` | Module-only collection skeleton |
| `collection_modules/`           | REST module-only collection skeleton |
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
`scripts.render --profile collection_modules_appliance` or
`--profile collection_modules` selects a module skeleton. Both variants run
through the render and release gates; the unit tests enforce their shared-file
list without a composition layer. Quick checks run their default nox sessions
in check-only mode; full matrix checks add the sanity, unit and integration
sessions, one chain at a time, and the Molecule scenario against the first
identity's own fake product.

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

`scripts/check.sh` accepts uncommitted changes and checks the working tree. It
runs Python and shell checks, Markdown linting, REUSE validation, helper tests,
and the default Ansible lane. Generated-resource checks cover (including but not
limited to):

- Initialization with defaults, explicit metadata, and quoted metadata.
- Collection build contents and installation into an isolated collection path.
- Strict production-profile Ansible linting of both renders, and a second
  autofix pass that must not change anything.
- The rendered collection's default Nox sessions in check-only mode, with tool
  versions pinned to the check environment, plus a stock-only `--no-venv` run.
  Repository checks require successful foundata rule verification.
- Both module skeletons under two identities: their default Nox sessions in
  check-only mode (Ruff and mypy including the integration targets' Python),
  a build, and the installed fixture modules.

To test a future release candidate, build its wheel and override the released
package. Example:

```sh
./scripts/check.sh --foundata-wheel /absolute/path/to/ansible_lint_foundata-1.1.0rc1-py3-none-any.whl
```

To retain results, including generated projects and collection archives:

```sh
./scripts/check.sh --output /tmp/skeleton-check-results
```

Run only the helper regression suite with:

```sh
uv run --locked python -m pytest -q
```

Use `python -m pytest` so repository-local modules are importable without an
installation or test-specific `sys.path` changes. Warnings are errors and pytest
configuration is strict. Required suites must execute without failures or skips.


### Compatibility matrix<a id="compatibility-matrix"></a>

```sh
./scripts/check.sh --matrix
```

The matrix is defined in `[tool.skeletons]` in `pyproject.toml`. Each lane uses
a separate locked environment; no lane upgrades dependencies during a run.

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

For an infrastructure-only iteration:

```sh
uv run --locked --group core221 --group lint --group integration \
  python -m scripts.integration podman --output /tmp/skeleton-podman-results
```

Tests need network access for pinned Galaxy collections, container images, and,
for libvirt, the cloud image. `SKELETONS_VM_IMAGE` can name an absolute path to
an existing Debian-compatible cloud-init image. Each run uses unique VM and
container names. Podman tests use a separate disposable storage configuration,
including the execution-environment build and its image cleanup.


### Updating dependencies<a id="updating-dependencies"></a>

Change Python dependency groups and regenerate `uv.lock` deliberately:

```sh
uv lock --upgrade
./scripts/check.sh --matrix --integration podman --integration libvirt --integration ee
```

Review lock changes before committing. Galaxy pins live in
`tests/requirements.yml`; update them together with infrastructure validation.


## Releases<a id="releases"></a>

A release is a tag on a validated commit.

The changelog section, the tag, the push, the GitHub release entry and the
publication checks run through
[`releasing`](https://foundata.com/en/projects/releasing/), whose declaration is
[`.releasing.toml`](./.releasing.toml). What stays in this repository is the
skeleton-specific gate (`scripts/release.py check`).


### Release checklist<a id="release-checklist"></a>

1. Run `./scripts/check.sh` during development and resolve failures.
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
   evidence="${HOME}/sk-${version}"
   uv run --locked python -m scripts.release check "${version}" \
     --output "${evidence}"
   ```

   Keep the resolved path at 29 bytes or shorter. The controller, Podman, the
   generated collection's `docs-check` workers and Molecule's SSH connections
   bind Unix sockets below it, and the gate refuses a longer path before it
   starts. Do not use a tmpfs such as `/tmp`: the integration tests keep their
   container storage below the output. The gate can run for several hours.

   Inspect `check.log` while the gate runs. A successful run creates
   `report.json`; a failure retains diagnostics but creates no passing report.
   Keep the whole evidence directory, including its `checks/` subdirectory.
5. Create and inspect the local annotated tag:

   ```sh
   uv run --locked python -m scripts.release tag "${evidence}/report.json"
   git show "v${version}"
   ```

   The helper checks that the report belongs to `HEAD` and that its checks,
   source archive and artifact digests are intact, and refuses when an equal or
   newer release tag exists locally or on the remote or when the remote cannot
   be asked. `release tag create` then refuses a dirty tree or a tag that
   already exists, and writes the annotated tag. Nothing is pushed.
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

   `release verify` checks that the remote holds the tag and that the forge
   reports it as latest. The helper then clones the tag anonymously and runs
   rendering, build, installation and role smoke tests on that clone,
   including the module skeletons' Molecule scenario against the fake product.
   No container or VM starts, but the run takes several minutes. At any
   point, `uv run --locked release status "${version}"` reports where the
   release stands.

Maintainers can bypass these local checks by invoking Git directly. Enforce
protected tags or equivalent forge rules separately if server-side enforcement
is needed. Any future CI should call the same commands.

### Recovery<a id="recovery"></a>

For a failed check, inspect its log, fix the cause, commit source changes, and
run the gate into a new evidence directory. Never edit a report to turn a
failure into success or carry it forward to another commit.

While no GitHub release exists, `uv run --locked release tag delete
"${version}"` removes a wrong tag locally and on the remote; it refuses once a
release exists. Do not move, replace or reuse a tag that consumers may have
checked out. Correct a defective release with a new patch version.

After an interrupted push or release creation, inspect the existing remote tag
and release entry and finish the missing publication step. Preserve the
validated commit and rerun publication verification when complete.


## Troubleshooting<a id="troubleshooting"></a>

- An existing output path is an error. Choose a new path; helpers never merge
  a new result into an older run.
- A lock mismatch is an error. Review dependency changes and regenerate the
  lock deliberately; release checks use `--locked`.
- Use retained output to inspect generated YAML, the isolated `ansible.cfg`,
  collection archives and Molecule diagnostics.
- Infrastructure requirements are mandatory for their tier. Use quick checks
  while developing on a machine without those capabilities, then run the full
  release gate on a suitable host.
- A release check takes longer because it provisions targets and builds
  execution environments. Its `check.log` holds command output, including the
  failing command's diagnostics.
