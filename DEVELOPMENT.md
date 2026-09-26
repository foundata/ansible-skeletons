# Development

This repository ships skeletons for `ansible-galaxy`. Before a release, the
checks generate projects from both skeletons, run their roles, and build and
install the generated collection.


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

uv installs the Python tools in isolated environments. `pyproject.toml` contains
development dependencies and configuration; its `0.0.0` version belongs to the
development setup. Skeleton releases use the version in the dated changelog
section and the corresponding `vX.Y.Z` Git tag.


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

|            Path             | Purpose |
| --------------------------- | ------- |
| `role_default/`             | Standalone role skeleton |
| `collection_default/`       | Collection skeleton, including its `run` role |
| `scripts/render.py`         | Render both skeletons with the real Galaxy CLI |
| `scripts/smoke.py`          | Validate rendering, artifacts and role behavior |
| `scripts/check.sh`          | Local quality-check entry point |
| `scripts/integration.py`    | Generated Molecule scenarios with active fixtures |
| `scripts/release.py`        | Validate a release commit and create its local tag |
| `tests/unit/`               | Release and helper regression tests |
| `tests/fixtures/`           | Disposable role execution fixtures |
| `tests/requirements.yml`    | Pinned Galaxy dependencies for infrastructure tests |
| `pyproject.toml`, `uv.lock` | Tool configuration, matrix and locked Python tools |

Galaxy consumes one `.j2` suffix when creating a project. Files named
`*.j2.j2` intentionally become runtime `*.j2` templates. Likewise, `FIXME`
instructions and `.yml.example` files are intentional parts of the product.
Do not reject all remaining Jinja expressions or placeholders as render errors.


## Development standards<a id="development-standards"></a>

Follow the foundata
[Ansible](https://github.com/foundata/guidelines/blob/main/ansible-style-guide.md),
[shell](https://github.com/foundata/guidelines/blob/main/shell-scripting-style-guide.md),
[Python](https://github.com/foundata/guidelines/blob/main/python-style-guide.md),
and
[Markdown](https://github.com/foundata/guidelines/blob/main/markdown-style-guide.md)
style guides. Use UTF-8 without a BOM, LF line endings, and a trailing newline.

Use Python for structured data and validation. Keep shell wrappers small and
check important exit statuses explicitly. Add regression tests for new behavior
and bug fixes, and explain any lint suppressions.

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
and the default Ansible lane. Generated-resource checks cover:

- Initialization with defaults, explicit metadata, and quoted metadata.
- Expected output files, parsed YAML and TOML, and preserved runtime templates.
- Strict production-profile Ansible linting and collection changelog linting.
- Collection build contents and installation into an isolated collection path.
- Both roles' initialization, argument validation, package-task expressions,
  removal of disposable files, and use from a caller loop.

The package backend in the localhost smoke fixture is a test double, so these
checks do not install or remove controller packages. File operations stay in
the generated workspace. Infrastructure tests use the actual generated roles.

Generated Ansible linting excludes `yaml[comments]` because removable example
comments intentionally omit the space after `#`. Molecule infrastructure also
excludes `var-naming[no-role-prefix]`, since those playbooks do not belong to a
role. Its playbooks are linted separately by the infrastructure command.

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

The matrix is defined in `[tool.skeletons]` in `pyproject.toml`. It covers
Ansible core 2.19 on Python 3.12 and 3.13, and core 2.20 and 2.21 on Python
3.12, 3.13 and 3.14. Helper tests run on all three Python versions. Each lane
uses a separate locked environment; no lane upgrades dependencies during a run.

Review the matrix against the
[upstream maintenance policy](https://docs.ansible.com/ansible/latest/reference_appendices/release_and_maintenance.html#ansible-core-support-matrix)
when updating dependencies. Update the README compatibility statement together
with the configuration.

### Infrastructure tests<a id="infrastructure-tests"></a>

Each tier runs both generated projects. The fixture adds converge and verify
tasks because the shipped `.yml.example` files alone do not execute a role.
Missing prerequisites or failed cleanup make the command fail.

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

The EE tier also removes a role's `tasks/main.yml` temporarily and checks that
the shipped verification rejects the incomplete installation. Container tags
and the default cloud-image URL can change; Python and Galaxy dependency pins
do not lock those images.

The release gate always requires Podman. Changes to shared or default Molecule
infrastructure also require libvirt; changes to shared or EE infrastructure,
collection build metadata, or execution-environment metadata require EE.
Changes to the gate, fixtures, or dependency locks require all three tiers.
When no earlier reachable release tag exists, all three tiers are required.
Required tiers cannot be skipped.

### Updating dependencies<a id="updating-dependencies"></a>

Change Python dependency groups and regenerate `uv.lock` deliberately:

```sh
uv lock --upgrade
./scripts/check.sh --matrix --integration podman --integration libvirt --integration ee
```

Review lock changes before committing. Galaxy pins live in
`tests/requirements.yml`; update them together with infrastructure validation.
Generated projects retain their own dependency requirements, independently of
the development gate's pins.


## Releases<a id="releases"></a>

Release checks must pass on the exact committed revision being tagged. The
tagging helper rejects reports from other commits. The checker exports Git
content into a fresh directory, runs all required checks there, and retains the
source archive, generated collection archives, logs and a report outside the
checkout. It never creates a tag, pushes, or publishes.

The generated collections are retained for inspection as test artifacts.
Consumers use the skeletons from the tagged source repository; this project
does not publish a Python package or an Ansible collection.

### Release checklist<a id="release-checklist"></a>

1. Run `./scripts/check.sh` during development and resolve failures.
2. Choose the next Semantic Versioning `X.Y.Z` version. Move the Unreleased
   entries in `CHANGELOG.md` into a dated release section and update both its
   release link and the Unreleased comparison link. Leave
   `- Nothing worth mentioning right now.` under the Unreleased heading so it
   passes Markdown linting. Do not change the development project's `0.0.0`
   version in `pyproject.toml`.
3. Review and commit the complete release preparation:

   ```sh
   version='X.Y.Z'
   git diff --check
   git diff
   git add CHANGELOG.md
   git diff --cached
   git commit -m "release: prepare ${version}"
   git status --short
   ```

   Implementation changes must already be committed. The final status must be
   empty, including untracked files. Do not amend this commit after validation.
4. Run the release gate, choosing a new output directory outside the checkout:

   ```sh
   evidence="../ansible-skeletons-release-${version}"
   uv run --locked python -m scripts.release check "${version}" \
     --output "${evidence}"
   ```

   Inspect `check.log` while the gate runs. A successful run creates
   `report.json`; a failure retains diagnostics but creates no passing report.
   Keep the whole evidence directory, including its `checks/` subdirectory.
5. Create and inspect the local annotated tag:

   ```sh
   uv run --locked python -m scripts.release tag "${evidence}/report.json"
   git show "v${version}"
   ```

   The helper checks the commit, required checks, source and artifact hashes,
   and local and remote release tags. An equal or newer version on either side
   blocks tagging, as does a failed remote query. The helper does not push
   anything.
6. Push the reviewed branch and then this specific tag:

   ```sh
   git push origin main
   git push origin "refs/tags/v${version}"
   ```

   Confirm that the release commit belongs to the branch being pushed. Do not
   use `--follow-tags`, which could also publish unrelated local tags.
7. Create the GitHub release for that existing tag, with title `vX.Y.Z` and
   notes from the matching changelog section. This needs an authenticated
   maintainer account. No PyPI or Galaxy publication is involved.
8. Verify the published tag, latest stable release and a fresh public clone:

   ```sh
   uv run --locked python -m scripts.release verify "${evidence}/report.json"
   ```

   This command needs authenticated `gh` for the API query. It checks the public
   clone's commit and runs rendering, build, installation and role smoke tests.

Maintainers can bypass these local checks by invoking Git directly. Enforce
protected tags or equivalent forge rules separately if server-side enforcement
is needed. Any future CI should call the same commands.

### Recovery<a id="recovery"></a>

For a failed check, inspect its log, fix the cause, commit source changes, and
run the gate into a new evidence directory. Never edit a report to turn a
failure into success or carry it forward to another commit.

An unpublished local tag may be deleted after inspection. Once a tag is pushed,
do not move, replace or reuse it. Correct a defective release with a new patch
version, even if the forge release entry has not yet been created.

After an interrupted push or release creation, inspect the existing remote tag
and release entry and finish the missing publication step. Preserve the
validated commit and rerun publication verification when complete.

Integration with foundata's `releasing` project is deferred because it does not
yet support generic source repositories such as this one. Add that support
separately and keep the skeleton-specific checks here.


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
