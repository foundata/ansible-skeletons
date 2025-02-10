# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [Unreleased]

- ⚠️ Changed license from `Apache 2.0` to `GPL-3.0-or-later`
- ⚠️ Note for developers: `master` branch was renamed to `main`.
- role_default: Use argument validation instead of `{{ role_name }}_required_vars` / assert. (e729a5d, #1)
- role_default: Use argument validation instead of `{{ role_name }}_required_vars` / assert. (e729a5d, #1)
- role_default: Implement automatic search for plattform specific variables. (d783728, #12)
- role_default: Remove internal check to prevent calling task files directly. (70bf734, #9)
- role_default: Check for used facts, gather this subset if needed. (6191b1a, #13)
- role_default: Fix tag usage. (59bf486, #11)
- role_default: Implement automatic search for plattform specific tasks. (b75018e, #14)
- Use fully-qualified collection names (FQCN) everywhere.


## [1.0.0] - 2020-09-24

### Added

- All functionality and files, `role_default`


[unreleased]: https://github.com/foundata/ansible-skeletons/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/foundata/ansible-skeletons/releases/tag/v1.0.0
