# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [Unreleased]

- Nothing worth mentioning right now.


## [2.2.1] - 2025-04-19

### Fixed

- collection_default, role_default: Fix off-by-one and templating errors in setup task examples (e077d6f)


## [2.2.0] - 2025-04-05

### Added

- collection_default: [Molecule](https://ansible.readthedocs.io/projects/molecule/) support with a default scenario using [Podman](https://podman.io/docs/installation) and several [integration test targets](https://github.com/orgs/foundata/repositories?q=oci-*-itt). (4c8a01c, #3)
- role_default: [Molecule](https://ansible.readthedocs.io/projects/molecule/) support with a default scenario using [Podman](https://podman.io/docs/installation) and several [integration test targets](https://github.com/orgs/foundata/repositories?q=oci-*-itt). (f57cd625, #3)

### Changed

- collection_default: Init task: Improve check of supported platforms from `vars/main.yml`, support listing a `os_family` value. (ca0d4c1)
- role_default: Init task: Improve check of supported platforms from `vars/main.yml`, support listing a `os_family` value. (ca0d4c1)

### Fixed

- collection_default: Fix `run_run_` typo in check for package state `latest`. (624cdb5)
- collection_default: Fix left-over usage of role meta data `platforms` key. (228b3a3)


## [2.1.0] - 2025-03-02

### Changed

- collection_default, role_default: Setup tasks are now split into separate `install` and `uninstall` sub-directories. Managing installation and removal in separate files simplifies handling in real-world scenarios compared to implementing optional removal logic within the installation context. (5bf9580)
- collection_default: Changed `antsibull-changelog` config to RST. (ec7fe76)

### Fixed

- collection_default, role_default: Use dedicated loop var in main entry point to prevent "variable 'item' is already in use" warnings. (5d13ea1)
- collection_default: .gitignore: Moved file into the correct directory, added `antsibull-changelog`. (33613eb)


## [2.0.0] - 2025-02-17

### Added

- collection_default: Added first version (#2)
- role_default: Check for used facts and gather this subset if needed. (6191b1a, #13)
- role_default: Implement automatic search for platform-specific variables. (d783728, #12)

### Changed

- ⚠️ Changed license from `Apache 2.0` to `GPL-3.0-or-later`.
- ⚠️ Note for developers: the `master` branch was renamed to `main`.
- role_default: Use an internal variable for platform compatibility checks. (d0ed55c, #4)
- role_default: Use argument validation instead of `{{ role_name }}_required_vars` / `assert`. (e729a5d, #1)
- role_default: Remove internal check to prevent calling task files directly. (70bf734, #9)

### Fixed

- role_default: Fixed tag usage. (59bf486, #11)
- Use fully qualified collection names (FQCN) everywhere.


## [1.0.0] - 2020-09-24

### Added

- All functionality and files, `role_default`


[unreleased]: https://github.com/foundata/ansible-skeletons/compare/v2.2.1...HEAD
[2.2.1]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.2.1
[2.2.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.2.0
[2.1.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.1.0
[2.0.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.0.0
[1.0.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v1.0.0
