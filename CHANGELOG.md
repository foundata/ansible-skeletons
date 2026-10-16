# Changelog

<!--
SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
SPDX-License-Identifier: GPL-3.0-or-later
-->

Notable, user-facing changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [Unreleased]

### Changed

- Rename `collection_modules_grid` to `collection_modules_appliance`. Select
  the variant by management-path configuration, global activation and
  per-family console recovery, not by a grid-style API.
- Both module variants: replace the definition engine's independent
  `Engine.run` and result boundary with hooks on one lifecycle base. Keep
  `Engine(module, resource, client)` for definition-driven reads.
- Separate activation ownership: the verifying helper stays shared; act-once
  activation and its hooks ship only in the appliance variant.
- collection_default, collection_modules, collection_modules_appliance: the
  noxfile sets `nox.options.default_venv_backend = "uv|virtualenv"`. Nox
  creates session environments with uv when it is installed and falls back
  to virtualenv otherwise; `--default-venv-backend` still overrides it.
- checks: the second module identity keeps its render checks, default Nox
  sessions, build and installed modules, plus one ansible-test sanity run on
  the newest released core. Sanity, units and integration on every core stay
  with the first identity.
- checks: independent steps run at the same time, `--jobs N` at once and by
  default half the CPUs, each with its own log under `logs/` and its own
  temporary directory; infrastructure tiers stay one at a time.
- checks: one download of the pinned Galaxy collections per run serves the
  generated lint, the module renders and the infrastructure tiers. The module
  renders' ansible-test containers use a Podman store of each render's own
  that reads your images read-only instead of pulling them again.

### Fixed

- Both module variants: honour `Binding.compatibility_always` on lifecycle
  entry points and information reads, and make the shared noop and info
  contract tests follow that policy.
- collection_default, collection_modules, collection_modules_grid: the
  runtime check's `subprocess.run` call passes `check=False`, which pylint's
  `subprocess-run-check` demanded in every ansible-test sanity lane of a
  rendered collection.

### Added

- checks: `--resume <output>` reruns only the failed and missing steps of an
  earlier run of the same sources; `scripts.release check --resume` does the
  same for the evidence of the same commit.
- checks: `--changed` runs the static checks, the repository tests and only
  the lanes and module skeletons whose sources differ from `HEAD`; it never
  writes a passing report.
- Both module variants: skeleton-owned `_lifecycle.py`, a product-bound
  `_binding.py`, and synthetic `test_lifecycle.py` coverage for family hooks,
  custom operations, staged writes and lost-response settlement.
- Both module variants: a nested family scaffold with hook contracts, module
  documentation, unit and file-only integration tests, default scenario
  tasks, a verifier slot, a Usage placeholder and a copy checklist.
- Both module variants: one parameterized response-loss harness, exercised
  for create, update and delete by both fixture families.
- Appliance variant: `_act_once.py` with preflight and bounded readiness
  hooks, its shared tests, a management-path guard scaffold, recovery and
  single-writer prerequisites, and `verified: unverifiable` semantics.
- Appliance variant: a conditional last-stage recovery drill, generic fresh
  API and DNS probes, fail-closed family hooks, the console lab contract and
  a standalone single-family drill playbook. No recovery scenario is added.
- Rendered Boundaries documentation names the base, engine, activation,
  harnesses and shared tests as skeleton-owned invariants, plus the
  appliance-only activation and drill files.
- collection_default, collection_modules, collection_modules_grid: a default
  `runtime-check` Nox session checks a built artifact with only `meta/`,
  `plugins/`, `roles/` and `playbooks/` retained. It checks imports,
  documentation and role syntax, and runs existing unit tests outside the
  stripped collection. Skeleton runtime smoke tests also use the stripped
  installation.
- collection_default, collection_modules, collection_modules_grid:
  `requirements-dev.txt` requires `releasing>=7.0.0`, whose `release build`
  runs sanity tests on the committed revision before building the archive.
  The file is excluded from Galaxy archives.
- collection_default, collection_modules, collection_modules_grid: a default
  `helper-lint` Nox session checks Python under `extensions/molecule/`,
  `roles/*/files/`, `tools/` and `scripts/` with Ruff, without executing helpers
  or adding them to mypy's scope. Local runs fix imports and format; `CI=true`
  only checks. Explicit CI session lists must include `helper-lint`.
- collection_default: an explicit `ruff.toml` using the module skeletons'
  lint and formatting policy.

### Removed

- The `collection_modules_grid` initialization path; use
  `collection_modules_appliance`.
- The engine's separate lifecycle sequence and local client/schema binding.
  Invariant tests and product bindings migrate to `_binding.py`; family
  implementations use the base's hooks rather than patching `_engine`.


## [2.10.0] - 2026-10-15

### Added

- collection_default, collection_modules, collection_modules_grid,
  role_default: an optional `company_url` input. When set, the copyright holder
  in license headers, `REUSE.toml` and the README notice reads
  `<company> <<company_url>>`; Galaxy's `company` field keeps the plain name.
- collection_default, collection_modules, collection_modules_grid: a
  `.releasing.toml` for foundata's `release` command. Set its `repository`
  before the first release.
- collection_modules: a module-only skeleton for REST products with integer
  identifiers, by-name 404 absence, empty-page pagination, native JSON and
  immediate writes. Its fixture exercises a reference, a write-only password
  and a deletion guard. It has no apply module.
- collection_modules_grid: a module-only skeleton for grid-style APIs with UUID
  identifiers, paginated searches with a declared total, add, set and delete
  actions, string-encoded values and a deletion guard. Its apply module serves
  products that separate saved from active state.

### Changed

- collection_modules, collection_modules_grid: the README's Example section is
  a Usage section, one subsection per administration task with the tasks of
  one runnable example, the play shown once.
- All skeletons: `.yamllint` forbids non-empty flow sequences; lists are
  written one item per line, as the playbook guide says.
- All skeletons: `.gitattributes` uses the guide's wording.
- collection_modules, collection_modules_grid: the shared `_activation.py`
  docstring states the lost-reply rule: verification continues only when a
  named object drifted before, and `always` on objects already in sync ends
  with the outcome unknown.
- collection_modules, collection_modules_grid: no `docs/architecture.md`
  template any more. Rationale lives beside the code: `DEVELOPMENT.md` gains
  a Boundaries section that names the skeleton-owned files, `_compat.py` and
  `meta/runtime.yml` state their reasons inline, and `FIXME.md` sends the
  rule a module utility enforces into that file's docstring. The smoke test
  no longer requires the record or the skeleton revision inside it.
- All skeletons: rendered files carry the SPDX headers of the licensing guide,
  and REUSE checks the complete tree. README notices follow the guide;
  foundata's own renders leave out the skeleton credit.
- (collection|role)_default: maximum-length names need DocSmith 2.4.0 or newer.
  The collection's `docs` session installs it.
- All skeletons: `.gitignore` keeps the blank line below its repository heading
  and uses a portable wildcard for macOS Finder icon metadata.
- All skeletons: one shared `.ansible-lint`. It excludes `tools/` and
  `changelogs/.plugin-cache.yaml` and treats the Molecule inventory templates
  as inventories.
- All skeletons: `requirements.txt` starts with a generic header.
- (collection|role)_default: one revised Molecule README for both skeletons. It
  covers the task directories of every phase.

### Fixed

- checks: the release gate gives Molecule's SSH control sockets and the
  generated collection's nox environments short run-private directories, and
  refuses an evidence path that is too long for its sockets before it starts.
  The release checklist's example path fits.
- collection_default: `changelogs/changelog.yaml` starts without a placeholder
  release, so the first `release_summary` fragment no longer fails.
- (collection|role)_default: the ansible-core requirement is checked before
  the first templated task, so an older core reports the role's message
  instead of a Jinja load error.
- (collection|role)_default: the README licensing section says "an SPDX
  software bill of materials" instead of "a SPDX".
- (collection|role)_default: long namespace, collection and role names no
  longer push comments, neutralized file content or README lines past the YAML
  and Markdown line limits.
- collection_default: `build_ignore` excludes `.venv` and `.cache`, so a local
  virtual environment or tool cache no longer ends up in the archive.
- role_default: `.gitignore` also ignores the rumdl cache, like the collection
  skeletons.
- (collection|role)_default: comments in the Molecule scenario configurations
  point to the existing Molecule README.
- (collection|role)_default: the Molecule README no longer requires the unused
  Molecule podman plugin, and its execution environment link works again.


## [2.9.0] - 2026-10-05

### Changed

- collection_default: `build_ignore` and `.gitignore` cover the Ruff, mypy,
  pytest and rumdl caches, the Ruff and mypy configuration and `tests/output`,
  which `ansible-test` writes. `DEVELOPMENT.md` notes the `docs-check`
  failure under Python 3.14 in deep checkouts.
- collection_default: `DEVELOPMENT.md` and the README select the sanity
  sessions by name (`nox -s ansible-test-sanity`); the tag form selects
  nothing with antsibull-nox.
- (collection|role)_default: the default ansible-core minimum is 2.19. The
  templates write Jinja's own filters and tests as `ansible.builtin.<name>`,
  which ansible-core resolves from 2.19; older cores never ran the rendered
  content. The `ansible-test` lanes below 2.19 disappear with the floor.

### Fixed

- collection_default: `noxfile.py` names its unused loop variables `_name`
  and `_kind`; `ansible-test sanity` rejected `_` (pylint `disallowed-name`).


## [2.8.0] - 2026-10-05

### Added

- (collection|role)_default: `.ansible-docsmith.toml`, which lets DocSmith
  2.3.0 format the READMEs with rumdl before it compares or writes. The
  collection README carries the role table of contents DocSmith generates.
- (collection|role)_default: all foundata lint rules enabled when the extension
  is installed. Collection Nox checks install and verify it automatically.
- collection_default: `noxfile.py` and `antsibull-nox.toml`. A plain `nox`
  applies the project's rewrites and then checks; `CI=true nox` only checks.
  Own sessions: `ansible-lint` with the foundata rules, `docs` with DocSmith,
  `markdown` with rumdl, `changelog` with antsibull-changelog, and `molecule`,
  which passes arguments after `--` to Molecule and otherwise runs the
  `default` scenario, virtual machines one at a time.
- collection_default: `DEVELOPMENT.md` with the prerequisites and the `nox`
  commands, and a "Through nox" section in the Molecule README.
- collection_default: `tests/config.yml`, which declares Python 3.9 as the
  lowest version for module code on managed nodes.
- (collection|role)_default: `.ansible-lint` with the `production` profile and
  the skipped `use-loop` rule of the Ansible guidelines. The repository checks
  lint generated roles and collections with it, Molecule resources included.
- (collection|role)_default: descriptions for the entry point and the three
  base options in `meta/argument_specs.yml`. `antsibull-docs` requires them.
- (collection|role)_default: `.yamllint` for explicit YAML style checks, strict
  lint warnings, and role-prefixed loop variable checks. Molecule task files
  include the required YAML document markers.

### Changed

- (collection|role)_default: every skeleton file is a `.j2` template, including
  the lint, nox, DocSmith and Molecule inventory files.
- (collection|role)_default: the README links to the licensing files start
  with `./`, as the Markdown guide requires.
- (collection|role)_default: the three base options declare their default in
  `meta/argument_specs.yml` instead of `required: true`. Role argument
  validation never enforced the requirement, because `defaults/main.yml`
  supplies the values, and the generated documentation now shows them.
- (collection|role)_default: `ansible-lint --fix` leaves a fresh render
  unchanged. Short `or` conditions are on one line, the long one is a folded
  scalar, and comments after the last list item start at column one.
- (collection|role)_default: the role README carries the documentation
  DocSmith generates from `meta/argument_specs.yml`, formatted by rumdl, with
  the variables table padded for the role's name. `defaults/main.yml` carries
  the generated comments.
- (collection|role)_default: the Markdown templates follow the shipped
  `.rumdl.toml`; `FIXME.md` links to `roles/run/meta/main.yml`.
- Repository checks run the rendered collection's default `nox` sessions in
  check-only mode under constraints from the check environment, after
  `antsibull-nox lint-config`. The Galaxy import session runs unpinned,
  because `galaxy-importer` pins an older `ansible-lint` than the foundata
  rules need.
- (collection|role)_default: Molecule VM templates take explicit `tpl_*`
  inputs, inventory builders keep computed state separate from their inputs,
  and module parameters follow the guideline's order.
- (collection|role)_default: generated changelog output is excluded from style
  linting; dedicated changelog validation remains enabled. Molecule tasks follow
  the custom rules, with a documented permission-preserving EE staging
  exception.
- (collection|role)_default: YAML templates use one blank line between tasks
  and no blank line immediately after document markers or task/block headers.
- (collection|role)_default: commented-out examples in YAML files start with
  `# `, as the `yaml[comments]` rule of `ansible-lint` requires. Multi-line
  examples keep their indentation when an editor uncomments them.
- Releases run through foundata's `releasing` tool for the changelog section,
  the tag, the push, the GitHub release entry and the publication checks. The
  skeleton-specific gate and its retained evidence stay in `scripts/release.py`.


## [2.7.0] - 2026-09-26

### Added

- Development checks for generated roles and collections across the supported
  Ansible/Python versions, with Podman, libvirt and execution-environment tests.
- Release validation and local tagging commands that require passing checks
  for the exact release commit and reject stale or modified test results.
- (collection|role)_default: `.rumdl.toml`, the `rumdl` configuration of the
  foundata Markdown style guide. The collection skeleton keeps it out of the
  built artifact through `build_ignore`.


### Changed

- Development guide now covers the helper commands, test requirements and
  release procedure, including recovery after failed checks or publication.
- collection_default: `build_ignore` keeps more repository-only files out of the
  built artifact: repository hosting and editor configuration, coding agent
  files, OS metadata, and the `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md` and
  `DEVELOPMENT.md` contributor documents. The list is grouped by purpose.


### Fixed

- collection_default: initialization without extra variables provides defaults
  for company and minimum Ansible version.
- (collection|role)_default: quoted author, company and description values no
  longer produce invalid YAML or TOML metadata.
- role_default: package installation reads the standalone role's autoupgrade
  variable instead of a nonexistent variable prefixed with `run_`.
- (collection|role)_default: execution-environment builds install standalone
  roles at the correct directory level. Verification now fails when a role
  directory or its `tasks/main.yml` is missing.


## [2.6.2] - 2026-08-28

### Fixes

- (collection|role)_default: every loop in the generated task files now uses a
  private, purpose-specific loop variable instead of `item`. A role rendered
  from an older skeleton rebinds the caller's `item` when it is included from a
  loop and its parameters reference that `item`, which can silently hand the
  role an empty value. (f474786)


## [2.6.1] - 2026-08-05

### Fixes

- (collection|role)_default: coerce boolean arguments in Jinja expressions,
  apply ansible.builtin.bool. (c12178f)
- style: normalize categorical string facts (3f9273a)



## [2.6.0] - 2026-07-30

### Added

- collection_default, role_default: Molecule VM platforms (`type: "libvirt"`)
  support two new capabilities for testing storage management and locally built
  golden images: an `extra_disks` platform key (list of `serial`/`size`
  mappings; each disk is attached with its serial set and appears as
  `/dev/disk/by-id/virtio-<serial>` inside the instance) and absolute local
  paths as `image:` value (used as-is instead of being downloaded, e.g. for a
  locally baked Proxmox VE image). (c5cffe0)


### Fixes

- collection_default, role_default: Molecule VM instances no longer stall during
  the SSH authentication. [passt](https://passt.top/) advertises an MTU of 65520
  via DHCP; guests that enslave the interface into a bridge (e.g. Proxmox VE
  with `vmbr0`) then run a bridge whose MTU their port cannot carry, which
  silently drops larger packets. The domain definition now sets a conventional
  MTU of 1500. (14ea77d)
- collection_default, role_default: The Molecule `destroy` step now also removes
  instances that a failed `create` step left behind. Both destroy playbooks are
  driven by the inventory, which `create` writes only after all instances are
  up, so instances created before the failure were unknown to them (leaked
  virtual machines keep their memory allocated until removed by hand). (041a188)
- collection_default, role_default: The SSH probes of the libvirt `create` step
  no longer block indefinitely when an instance authenticates but hangs
  guest-side (e.g. a degraded boot); a hard timeout turns such hangs into
  regular retries. (1841c67)
- collection_default, role_default: The Molecule `cleanup` step tolerates
  instances that no longer exist, so a stale inventory from an interrupted run
  no longer aborts the test sequence before `destroy` can reset the state.
  (40da16b)



## [2.5.0] - 2026-07-29

### Added

- collection_default, role_default: The Molecule `default` scenario now selects
  its test backend per platform via a `type` key: `"podman"` (container, the
  default when omitted, fully backward compatible) or `"libvirt"` (QEMU/KVM
  virtual machine booted from a vendor cloud image via a session libvirt daemon,
  `qemu:///session`, without root privileges, `libvirt` group membership or
  polkit rules). VM platforms enable tests containers cannot cover (kernel
  settings, nested virtualization, Podman inside the test target); mixed
  container/VM scenarios work in one run. Implementation: dispatcher playbooks
  (`create-dispatch.yml`, `destroy-dispatch.yml`) import the backend-specific
  create/destroy playbooks which filter the platform list, compose a shared
  inventory plus instance configuration and no-op silently when no platform
  matches. The VM backend uses cloud-init NoCloud seed images, qcow2
  backing-file overlays over a local image cache, passt user networking with one
  deterministic SSH port forward per platform, a resource guard (memory/disk,
  override via `MOLECULE_VM_IGNORE_RESOURCES=1`), a boot console log per
  instance and a VNC console for `virt-manager` access. Commented `libvirt`
  alternates for every platform ship in `molecule.yml`. (9604572)
- collection_default, role_default: ignore Ansible Galaxy build artifacts
  (`<namespace>-<name>-<version>.tar.gz`) in `.gitignore` (6e6773d)


### Changed

- collection_default: Added
  [Ansible DocSmith](https://foundata.com/en/projects/ansible-docsmith/)
  `TOC-FULL` markers for the `run` role to the collection `README.md` template,
  enabling automatic inline TOC generation.


### Fixes

- collection_default, role_default: remove a stray double quote from the Debian
  hint in the neutralize-file comment (51c1b46)
- collection_default, role_default: fix the fact-gathering skip in
  `tasks/init.yml` never triggering in some cases; `__*_used_facts` is now a
  mapping of `gather_subset` name to the `ansible_facts` keys it provides, so
  subset names and fact keys can no longer be confused.) (5ef968f)



## [2.4.1] - 2026-05-25

### Fixes

- collection_default, role_default: avoid bool coercion on templated Windows
  check (Molecule, Podman) (e31baa5)


## [2.4.0] - 2026-05-22

### Added

- collection_default, role_default: Add generic task to remove unwanted files
  and directories (5980d70)
- collection_default, role_default: Add generic task to neutralize config files
  (89e61ca)


### Changed

- collection_default run role, role_default: Add Ansible DocSmith markers to
  README.md (details: <https://foundata.com/en/projects/ansible-docsmith/>)
  (5b38a02)
- collection_default, role_default: Add Ubuntu 26.04 LTS (Resolute Raccoon)
  support (89e61ca)
- collection_default, role_default: Add Fedora 44 support, remove EOL Fedora 42
  from roles and tests. (89e61ca)
- collection_default, role_default: Add openSUSE Leap 16.0 support, remove EOL
  openSUSE Leap 15.6 from roles and tests.
- collection_default, role_default: Add Debian 13 (Trixie) support, remove EOL
  Debian 11 (Bullseye) from roles and tests.
- collection_default, role_default: Add Fedora 43 support, remove EOL Fedora 41
  from roles and tests.
- collection_default, role_default: Exclude "extensions/molecule" from build
  artifacts. (645604b)


### Fixed

- collection_default, role_default: Molecule, prevent idempotence test failures
  from unrelated global state changes (9775a92)
- collection_default, role_default: Fix a bug that the task "Init | Gather
  role-specific facts" did not process the specific facts listed to be gathered
  in the main variables file. (84de375)


## [2.3.0] - 2025-05-02

### Changed

- collection_default, role_default: Reverse task inclusion of main role entry
  point order for better platform handling. (f2a3202)


### Fixed

- collection_default, role_default: Fix a bug that prevented inclusion of more
  than two specific task files in the sequence in main role entry point.
  (f2a3202)


## [2.2.2] - 2025-04-21

### Changed

- collection_default, role_default: Add Fedora 42 support, remove EOL Fedora 40
  from roles and tests. (705fcc9)


### Fixed

- collection_default, role_default: Molecule: correct path resolution for
  prepare tasks includes. (31ba386)


## [2.2.1] - 2025-04-19

### Fixed

- collection_default, role_default: Fix off-by-one and templating errors in
  setup task examples. (e077d6f)


## [2.2.0] - 2025-04-05

### Added

- collection_default: [Molecule](https://docs.ansible.com/projects/molecule/)
  support with a default scenario using
  [Podman](https://podman.io/docs/installation) and several
  [integration test targets](https://github.com/orgs/foundata/repositories?q=oci-*-itt).
  (4c8a01c, #3)
- role_default: [Molecule](https://docs.ansible.com/projects/molecule/) support
  with a default scenario using [Podman](https://podman.io/docs/installation)
  and several
  [integration test targets](https://github.com/orgs/foundata/repositories?q=oci-*-itt).
  (f57cd625, #3)

### Changed

- collection_default: Init task: Improve check of supported platforms from
  `vars/main.yml`, support listing a `os_family` value. (ca0d4c1)
- role_default: Init task: Improve check of supported platforms from
  `vars/main.yml`, support listing a `os_family` value. (ca0d4c1)

### Fixed

- collection_default: Fix `run_run_` typo in check for package state `latest`.
  (624cdb5)
- collection_default: Fix left-over usage of role meta data `platforms` key.
  (228b3a3)


## [2.1.0] - 2025-03-02

### Changed

- collection_default, role_default: Setup tasks are now split into separate
  `install` and `uninstall` sub-directories. Managing installation and removal
  in separate files simplifies handling in real-world scenarios compared to
  implementing optional removal logic within the installation context. (5bf9580)
- collection_default: Changed `antsibull-changelog` config to RST. (ec7fe76)

### Fixed

- collection_default, role_default: Use dedicated loop var in main entry point
  to prevent "variable 'item' is already in use" warnings. (5d13ea1)
- collection_default: .gitignore: Moved file into the correct directory, added
  `antsibull-changelog`. (33613eb)


## [2.0.0] - 2025-02-17

### Added

- collection_default: Added first version (#2)
- role_default: Check for used facts and gather this subset if needed. (6191b1a,
  #13)
- role_default: Implement automatic search for platform-specific variables.
  (d783728, #12)

### Changed

- ⚠️ Changed license from `Apache 2.0` to `GPL-3.0-or-later`.
- ⚠️ Note for developers: the `master` branch was renamed to `main`.
- role_default: Use an internal variable for platform compatibility checks.
  (d0ed55c, #4)
- role_default: Use argument validation instead of
  `{{ role_name }}_required_vars` / `assert`. (e729a5d, #1)
- role_default: Remove internal check to prevent calling task files directly.
  (70bf734, #9)

### Fixed

- role_default: Fixed tag usage. (59bf486, #11)
- Use fully qualified collection names (FQCN) everywhere.


## [1.0.0] - 2020-09-24

### Added

- All functionality and files, `role_default`


[unreleased]: https://github.com/foundata/ansible-skeletons/compare/v2.10.0...HEAD
[2.10.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.10.0
[2.9.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.9.0
[2.8.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.8.0
[2.7.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.7.0
[2.6.2]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.6.2
[2.6.1]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.6.1
[2.6.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.6.0
[2.5.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.5.0
[2.4.1]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.4.1
[2.4.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.4.0
[2.3.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.3.0
[2.2.2]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.2.2
[2.2.1]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.2.1
[2.2.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.2.0
[2.1.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.1.0
[2.0.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.0.0
[1.0.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v1.0.0
