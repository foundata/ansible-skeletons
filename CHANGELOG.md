# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [Unreleased]

### Changed

- collection_default, role_default: Setup tasks are now split into separate "install" and "uninstall" subdirs. Managing installation and removal in separate files simplifies handling in real-world scenarios compared to implementing optional removal logic within the installation context.
- collection_default: Changed `antsibull-changelog` config to RST.

### Fixed

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


[unreleased]: https://github.com/foundata/ansible-skeletons/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v2.0.0
[1.0.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v1.0.0
