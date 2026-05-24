# Development

This file provides additional information for maintainers and contributors.


## Testing

Nothing special or automated yet. Therefore just some hints for manual testing:

* Create dummy resources to see if everything renders and do linting. The helper script at the end of the file may help with that.
* Check if the outcome fits our [Ansible style guide](https://github.com/foundata/guidelines/blob/master/ansible-style-guide.md).
* Create resources with your skeleton for a simple program and test if everything works out conceptually as expected.


## Releases

1. Do proper [Testing](#testing). Continue only if everything is fine.
2. Determine the next version number. This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
3. Update the [`CHANGELOG.md`](./CHANGELOG.md). Insert a section for the new release. Do not forget the comparison link at the end of the file.
4. If everything is fine: commit the changes, tag the release and push:
   ```console
   version="<FIXME version>"
   git add "./CHANGELOG.md"
   git commit -m "Release preparations: v${version}"

   git tag "v${version}" "$(git rev-parse --verify HEAD)" -m "version ${version}"
   git show "v${version}"

   git push origin main --follow-tags
   ```
   If something minor went wrong (like missing `CHANGELOG.md` update), delete the tag and start over:
   ```console
   git tag -d "v${version}" # delete the old tag locally
   git push origin ":refs/tags/v${version}" # delete the old tag remotely
   ```
   This is *only* possible if there was no [GitHub release](https://github.com/foundata/ansible-skeletons/releases/). Use a new patch version number otherwise.
5. Use [GitHub's release feature](https://github.com/foundata/ansible-skeletons/releases/new), select the tag you pushed and create a new release:
   * Use `v<version>` as title
   * A description is optional. In doubt, use `See CHANGELOG.md for more information about this release.`
6. Check if the GitHub API delivers the correct version as `latest`:
   ```console
   curl -s -L https://api.github.com/repos/foundata/ansible-skeletons/releases/latest | jq -r '.tag_name' | sed -e 's/^v//g'
   ```


## Miscellaneous

### Encoding

* Use UTF-8 encoding with `LF` (Line Feed `\n`) line endings *without* [BOM](https://en.wikipedia.org/wiki/Byte_order_mark) for all files.

### Helper script

```bash
#!/usr/bin/env bash
#
# Helper for Ansible skeleton development (render, lint, and run the resources
# to easily spot errors or problems).
#
# This script performs the following actions:
#
# 1. Creates a temporary working directory including all necessary tools
#   (Python venv, Ansible, etc.)
# 2. Initializes an Ansible role using ansible-galaxy with the appropriate
#   skeleton
# 3. Initializes an Ansible collection using ansible-galaxy with the appropriate
#    skeleton
# 4. Lints the created resource
# 5. Runs a dummy playbook to instantiate the dummy resources
#
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: foundata GmbH (https://foundata.com)


# version of the script
readonly version="1.1.0"

# config
MSG_SCRIPTNAME=1 # Enable script name as prefix for msg()
readonly ansible_collection_namespace="foundata"
readonly ansible_collection_name="foobar"
readonly ansible_role_name="foobar"
readonly ansible_extravar_author_default="FIXME ${USER}"
readonly ansible_extravar_authors_default='["FIXME User <user@example.com>"]' # JSON list
readonly ansible_extravar_company_default="FIXME your org"
readonly ansible_extravar_description_collection_default="Ansible collection to manage foobar"
readonly ansible_extravar_description_role_default="Ansible role to manage foobar"
readonly ansible_extravar_repository_default="https://FIXME.example.com/repo/"
readonly ansible_extravar_issues_default="https://FIXME.example.com/repo/issues/"
readonly ansible_extravar_homepage_default="https://FIXME.example.com"
readonly ansible_extravar_min_ansible_version_default="2.16.0"


# --- BOILERPLATE START v1.1.1 ---
# Consistent environment for predictable tool and shell behavior
export PATH="${PATH:-'/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin'}"
if command -v locale >/dev/null 2>&1; then
  for locale_candidate in 'C.UTF-8' 'C.utf8' 'en_US.UTF-8' 'UTF-8' 'C'; do
    if LC_ALL="${locale_candidate}" locale charmap >/dev/null 2>&1; then
      export LC_ALL="${locale_candidate}"
      break
    fi
  done
else
  export LC_ALL='C'
fi
readonly LC_ALL
unset locale_candidate
set -u                                                      # no uninitialized variables
set -o 2>/dev/null | grep -Fq 'pipefail' && set +o pipefail # disable pipefail as it's non-POSIX

# Configure msg() messages (override via environment or inline where needed)
: "${DEBUG:=0}"          # 0: No debug messages. 1: Print debug messages.
: "${MSG_TIMESTAMP:=0}"  # 0: No timestamp (TS) prefix. 1: Unix TS. 2: ISO TS.
: "${MSG_SCRIPTNAME:=0}" # 0: No script name prefix. 1: Enable script name prefix

# Formatting codes (ANSI if STDOUT is TTY and NO_COLOR is empty; empty otherwise)
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  x1b=$(printf '\033') # escape byte (0x1b) (shown as ^[ in most editors)
  # terminfo|termcap comments for reference; alternative for ancient systems:
  # FMT_FOO="$(tput terminfo_foo 2>/dev/null || tput termcap_foo 2>/dev/null)"
  FMT_RESET="${x1b}(B${x1b}[m" # sgr0|me (G0 charset/US-ASCII, attributes reset)
  FMT_BOLD="${x1b}[1m"         # bold|md
  FMT_UL="${x1b}[4m"           # smul|us
  FMT_SO="${x1b}[7m"           # smso|so (standout, reverse video)
  FMT_RED="${x1b}[31m"         # setaf N|AF N (1=red)
  FMT_GREEN="${x1b}[32m"       # setaf N|AF N (2=green)
  FMT_YELLOW="${x1b}[33m"      # setaf N|AF N (3=yellow)
  FMT_BLUE="${x1b}[34m"        # setaf N|AF N (4=blue)
  unset x1b
else
  FMT_RESET='' FMT_BOLD='' FMT_UL='' FMT_SO='' FMT_RED='' FMT_GREEN='' FMT_YELLOW='' FMT_BLUE=''
fi
# shellcheck disable=SC2034 # boilerplate variables, needed but may be unused
readonly FMT_RESET FMT_BOLD FMT_UL FMT_SO FMT_RED FMT_GREEN FMT_YELLOW FMT_BLUE

###
# Print formatted messages to STDOUT or STDERR.
# Options:
#   -e, --error     Print error message (bold red) to STDERR.
#   -w, --warning   Print warning message (bold yellow) to STDERR.
#   -s, --success   Print success message (bold green) to STDOUT.
#   -i, --info      Print info message (bold blue) to STDOUT.
#   -d, --debug     Print debug message (standout) to STDOUT (only if DEBUG=1).
# Globals:
#   DEBUG          - If 0, suppresses -d/--debug messages.
#   MSG_TIMESTAMP  - 1: Enable Unix timestamp as prefix.
#                    2: Enable ISO timestamp as prefix.
#   MSG_SCRIPTNAME - 1: Enable script name as prefix.
# Arguments:
#   $1 - Optional flag (see "Options").
#   $@ - Message to print.
# Outputs:
#   Formatted message to STDOUT or STDERR depending on flag.
msg() {
  local _msg_fd='1' _msg_color='' _msg_prefix='' _msg_fmt=''
  case "${1:-}" in
    '-e' | '--error')
      _msg_fd='2'
      _msg_color="${FMT_BOLD}${FMT_RED}"
      ;;
    '-w' | '--warning')
      _msg_fd='2'
      _msg_color="${FMT_BOLD}${FMT_YELLOW}"
      ;;
    '-s' | '--success')
      _msg_fd='1'
      _msg_color="${FMT_BOLD}${FMT_GREEN}"
      ;;
    '-i' | '--info')
      _msg_fd='1'
      _msg_color="${FMT_BOLD}${FMT_BLUE}"
      ;;
    '-d' | '--dbg' | '--debug')
      [ "${DEBUG:-0}" = 0 ] && return 0
      _msg_fd='1'
      _msg_color="${FMT_SO}"
      ;;
    *) false ;;
  esac && shift
  case "${MSG_TIMESTAMP:-0}" in
    '1') _msg_prefix="[$(date '+%s')] " ;;                  # non-POSIX but widely available: %s
    '2') _msg_prefix="[$(date '+%Y-%m-%dT%H:%M:%S%z')] " ;; # non-POSIX but widely available: %z
    *) ;;
  esac
  case "${MSG_SCRIPTNAME:-0}" in
    '1') _msg_prefix="[${0##*/}] ${_msg_prefix}" ;;
    *) ;;
  esac
  _msg_fmt="${_msg_color}${_msg_prefix}$*${FMT_RESET}"
  [ "${_msg_fd}" = '2' ] && printf '%s\n' "${_msg_fmt}" >&2 || printf '%s\n' "${_msg_fmt}"
}

###
# Manage cleanup commands on exit/interrupt (LIFO order).
# Globals:
#   _TRAP_STACK - Newline-separated list of commands (newest first).
#                 Modified by push/pop/run operations.
# Arguments:
#   $1      - Action: push (add to stack), pop (remove last (no execute)),
#             or run (execute all & clear).
#   $2      - Command to register (required for push).
# Returns:
#   0 on success, 1 on invalid usage.
# Example:
#   trap_stack push 'rm -rf "/tmp/mydir"'
#   trap_stack pop
#   trap_stack run
_TRAP_STACK=''
trap_stack() {
  case "${1:-}" in
    'push')
      # line break is needed (stack delimiter)
      _TRAP_STACK="${2:?Command required}${_TRAP_STACK:+
${_TRAP_STACK}}"
      trap 'trap_stack run' EXIT
      trap 'trap_stack run; exit 130' INT
      trap 'trap_stack run; exit 143' TERM
      ;;
    'pop')
      _TRAP_STACK="$(printf '%s\n' "${_TRAP_STACK}" | tail -n +2)"
      [ -z "${_TRAP_STACK}" ] && trap - EXIT INT TERM
      ;;
    'run')
      while [ -n "${_TRAP_STACK}" ]; do
        eval "$(printf '%s\n' "${_TRAP_STACK}" | head -n 1)" || true
        _TRAP_STACK="$(printf '%s\n' "${_TRAP_STACK}" | tail -n +2)"
      done
      trap - EXIT INT TERM
      ;;
    *)
      printf 'Usage: trap_stack push|pop|run [cmd]\n' >&2
      return 1
      ;;
  esac
}

###
# Check if commands are available.
# Options:
#   -r  Required mode: exit with error if any command is missing.
# Arguments:
#   $@ - Command names to check.
# Returns:
#   0 if all commands exist.
#   1 if any missing (or exit 1 if -r is set)
check_cmd() {
  local required=0
  [ "${1}" = "-r" ] && required=1 && shift
  for cmd; do
    command -v "${cmd}" >/dev/null 2>&1 && continue
    [ "${required}" = 1 ] || return 1
    msg -e "Required command not found: ${cmd}"
    exit 1
  done
}

###
# Run a command that should never fail. If the command fails, print an error
# and exit immediately.
# Arguments:
#   $@ - Command and arguments to execute.
# Outputs:
#   Error message to STDERR on failure.
# Returns:
#   0 on success.
#   >0 (the original exit code of the command) on failure.
ensure() {
  local exit_code
  "$@"
  exit_code="$?"
  if [ "${exit_code}" -ne 0 ]; then
    msg -e "Command failed (exit code ${exit_code}): $*"
    exit "${exit_code}"
  fi
  return 0
}

# Convenience wrappers (see the used functions for documentation)
require_cmd() { check_cmd -r "$@"; }
# --- BOILERPLATE END v1.1.1 ---

###
# Parse command line arguments.
# Globals:
#   opt_bar - Set to 1 if -b is given.
#   opt_foo - Set to value of -f argument.
# Arguments:
#   $@ - Command line arguments.
# Returns:
#   0 on success, 2 on usage error.
parse_args() {
  opt_source_dir_path="${HOME}/dev/ansible-skeletons" # -s
  opt_show_version="0" # -v

  # Leading ':' silences STDERR, 'x:' needs value, 'x' is a flag.
  OPTIND='1' OPTARG='' OPT=''
  while getopts ':hs:v' OPT; do
    case "${OPT}" in
      # path of the directory with the skeleton repository sources
      's')
          opt_source_dir_path="${OPTARG}"
          if ! [ -d "${opt_source_dir_path}" ]
          then
              opt_source_dir_path=''
              printf '%s: invalid value for "-%c", ignoring it.\n' "$(basename "${0}")" "${opt}" 1>&2
          fi
          ;;


      # show version flag
      'v')
          opt_show_version='1' # flag currently not being used, but still setting the value for code consistency
          printf '%s\n' "${version}"
          exit 0
          ;;

      # show help
      'h')
        filename="${0##*/}"
        # <<- allows tab indentation (stripped by the shell; no spaces). The
        # text is left unindented to avoid formatter/linter issues with mixed
        # tabs and spaces as content uses spaces for mandoc/groff formatting.
        mantext="$(
          cat <<-DELIM
.TH ${filename} 1
.SH NAME
${filename} - Helper for Ansible skeleton development (render, lint, and
run the resources).

.SH SYNOPSIS
.B ${filename}
.PP
.BI "[-s " "/path/to/ansible-skeleton-sources" "]"

.SH DESCRIPTION
See https://github.com/foundata/ansible-skeletons/blob/main/DEVELOPMENT.md
for a detailed description.

.SH OPTIONS
.TP
.B -s
Path where the Ansible skeleton source repository can be found.
Defaults to "~/dev/ansible-skeletons"
.TP
.B -h
Print this help.
.TP
.B -v
Print the script's version number, then exit.

.SH EXIT STATUS
This program returns 0 if it succeeds. It returns a nonzero status on failure
and 2 for command-line syntax errors (e.g. usage of an unknown option).

.SH AUTHOR
Andreas Haerter <ah@foundata.com>
DELIM
        )"
        if check_cmd 'mandoc'; then
          printf '%s' "${mantext}" | mandoc -Tascii -man | more
        elif check_cmd 'groff'; then
          printf '%s' "${mantext}" | groff -Tascii -man | more
        else
          msg -e "Neither 'mandoc' nor 'groff' is available, cannot display help"
          exit 1
        fi
        unset filename mantext
        exit 0
        ;;

      *)
        msg -e "Unknown option '${OPTARG}' (or missing option value). Use '-h' to get usage instructions."
        exit 2
        ;;
    esac
  done
  unset opt OPTARG
  shift $((OPTIND - 1)) && OPTIND='1' # delete processed options, reset index
}

###
# Main entry point.
# Arguments:
#   $@ - Command line arguments.
main() {
  trap_stack 'push' "rm -f '${tmp_file:-}'"

  parse_args "$@"

  # set data
  readonly source_dir_path="${opt_source_dir_path}"

  # check if needed commands and tools are available
  require_cmd 'declare' 'mktemp' 'python'

  # check the source dir
  if ! [ -d "${source_dir_path}/.git" ]
     ! [ -d "${source_dir_path}/collection_default" ] ||
     ! [ -d "${source_dir_path}/role_default" ]
  then
    msg -e "'${source_dir_path}' is no valid Ansible skeleton source directory."
    exit 1
  else
    msg "'${source_dir_path}' seems to be a valid Ansible skeleton source directory."
  fi

  # search if there is already a working directory
  msg 'Searching for an already existing, temporary working directory...'
  : "${TMPDIR:=/tmp}" # if env var ${TMPDIR} is empty, set its value to /tmp
  working_dir_found="false"
  path_working_dir=""
  for dir in "${TMPDIR}/ansible-skeletons-"*
  do
    if [ -d "${dir}" ]
    then
      path_working_dir="${dir}"
      working_dir_found="true"
      msg "Found an already existing working directory at '${path_working_dir}'"
      break;
    fi
  done

  # create a new one if needed
  if [ "${working_dir_found}" != "true" ]
  then
    msg 'No working directory was found, going to to create one.'
    # securely create a temporary dir
    mask_save="$(umask)"; umask 077 # temporarily change mask
    tempdir="$(mktemp -d "${TMPDIR}/ansible-skeletons-XXXXXXXXXXXXXX")" || tempdir='';
    umask "${mask_save}"; unset mask_save # restore mask
    if [ -z "${tempdir}" ] || ! [ -d "${tempdir}" ]
    then
      msg -e "Creation of temporary directory failed:\n${tempdir}"
      exit 1
    fi
    path_working_dir="${tempdir}"
    msg "Create a working directory at '${path_working_dir}'"
  fi
  unset working_dir_found

  # deactivate any virtual environment (there should be none in this script context but better safe than sorry)
  if declare -F "deactivate" > /dev/null 2>&1
  then
    deactivate
    msg 'Deactivated currently active Python virtual environment.'
  fi

  # create python virtual environment if needed
  msg 'Searching for an already existing Python virtual environment...'
  path_virtualenv="${path_working_dir}/.venv-ansible-skeletons"
  path_pip="${path_virtualenv}/bin/pip"
  if ! [ -d "${path_virtualenv}" ] ||
     ! [ -f "${path_pip}" ]
  then
    if ! python -m "venv" "${path_virtualenv}"
    then
      msg -e "Creation of Python virtual environment failed:\n'${path_virtualenv}'"
      exit 1
    fi
    msg "Created a Python virtual environment at '${path_virtualenv}'"
  else
    msg "Found an already existing Python virtual environment at '${path_virtualenv}'"
  fi

  # activate new virtual environment
  source "${path_virtualenv}/bin/activate"
  pipcheck="$(which pip)"
  if [ -z "${pipcheck}" ] ||
    [ "${pipcheck}" != "${path_pip}" ]
  then
    msg -e "Activation of virtual env at '${path_virtualenv}' failed (pip not found under '${path_pip}')"
    exit 1
  else
    msg 'Activated virtual env, printing pip and python path and versioning information:'
    which pip
    which python
    pip --version
    python --version
  fi

  # create an Ansible config file
  msg "Creating '${path_working_dir}/ansible.cfg'"
  printf "%s\n" "$(cat <<-DELIM
## Ansible configuration for this env
# Debug hint: ansible-config dump --only-changed -t all

[defaults]
home = ${path_working_dir}/.ansible

## Dependencies
# - install collections into [current dir]/dependencies/ansible_collections/namespace/collection_name
# - install roles into [current dir]/dependencies/roles/role_name
collections_path = ${path_working_dir}/dependencies/collections
roles_path = ${path_working_dir}/dependencies/roles

display_args_to_stdout = true
nocows = true
DELIM
)" > "${path_working_dir}/ansible.cfg"

  # create a playbook
  msg "Creating '${path_working_dir}/playbook.yml'"
  printf "%s\n" "$(cat <<-DELIM
---

- hosts: localhost
  gather_facts: false
  tasks:

    - name: "Print a message"
      ansible.builtin.debug:
        msg: "This task runs before the example role (stand alone)"


    - name: "Include the example role (stand alone)"
      ansible.builtin.include_role:
        name: "${ansible_role_name}"
      vars:
        ${ansible_role_name}_service_state: "unmanaged"
      tags:
        - "always"
        - "${ansible_role_name}_always"
        - "${ansible_role_name}_setup"
        - "${ansible_role_name}_config"
        - "${ansible_role_name}_service"


    - name: "Print a message"
      ansible.builtin.debug:
        msg: "This task runs before the example role (from collection)"


    - name: "Include the example role (from collection)"
      ansible.builtin.include_role:
        name: "${ansible_collection_namespace}.${ansible_collection_name}.run"
      vars:
        run_${ansible_collection_name}_service_state: "unmanaged"
      tags:
        - "always"
        - "run_${ansible_collection_name}_always"
        - "run_${ansible_collection_name}_setup"
        - "run_${ansible_collection_name}_config"
        - "run_${ansible_collection_name}_service"
DELIM
)" > "${path_working_dir}/playbook.yml"

  # install or upgrade needed tools
  msg ''
  msg ''
  msg ''
  msg ''
  msg 'Installing or upgrading needed Ansible tools via pip.'
  ensure pip install pip setuptools --upgrade
  ensure pip install ansible-core ansible-lint reuse antsibull-changelog --upgrade

  # cleanup (best effort)
  if check_cmd 'gio'
  then
    # will likely fail as trashing on system internal mounts is not supported
    if gio trash "${path_working_dir}/dependencies" > /dev/null 2>&1
    then
      msg "Moved '${path_working_dir}/dependencies' into trash."
    fi
  fi
  if [ -d "${path_working_dir}/dependencies/" ]
     [ -d "${path_working_dir}/dependencies/collections/" ] &&
     [ -d "${path_working_dir}/dependencies/roles/" ]
  then
    if rm -rf "${path_working_dir}/dependencies/"
    then
      msg "deleted '${path_working_dir}/dependencies/' (cleanup)."
    fi
  fi

  # create needed directories
  ensure mkdir -p "${path_working_dir}/dependencies/collections/ansible_collections"
  ensure mkdir -p "${path_working_dir}/dependencies/roles"

  # misc cleanup tasks (best effort)
  trap_stack 'push' "rm -rf '${path_working_dir}/dependencies/roles/.ansible'"

  # open dir after script finished
  if check_cmd 'nautilus'
  then
      trap_stack 'push' "msg 'Opening working dir in GNOME Nautilus.'; nautilus '${path_working_dir}' > /dev/null 2>&1 &"
  fi

  # create the resources
  msg ''
  msg ''
  msg ''
  msg ''
  msg '############################################################################'
  msg '# Creating a new collection based on'
  msg "#   ${source_dir_path}/collection_default"
  msg '# in'
  msg "#   ${path_working_dir}/dependencies/collections/ansible_collections/${ansible_collection_namespace}/${ansible_collection_name}"
  msg '############################################################################'
  cd "${path_working_dir}/dependencies/collections/ansible_collections/"
  ensure ansible-galaxy collection init \
    --collection-skeleton "${source_dir_path}/collection_default" \
    --extra-var "{"authors": ${ansible_extravar_authors_default}}" \
    --extra-var "company='${ansible_extravar_company_default}'" \
    --extra-var "description='${ansible_extravar_description_collection_default}'" \
    --extra-var "repository='${ansible_extravar_repository_default}'" \
    --extra-var "issues='${ansible_extravar_issues_default}'" \
    --extra-var "homepage='${ansible_extravar_homepage_default}'" \
    --extra-var "min_ansible_version='${ansible_extravar_min_ansible_version_default}'" \
    --force \
    "${ansible_collection_namespace}.${ansible_collection_name}"
  msg ''
  msg ''
  msg ''
  msg ''
  msg '############################################################################'
  msg '# Creating a new stand-alone role based on'
  msg "#   ${source_dir_path}/role_default"
  msg '# in'
  msg "#   ${path_working_dir}/dependencies/roles/${ansible_role_name}"
  msg '############################################################################'
  cd "${path_working_dir}/dependencies/roles"
  ensure ansible-galaxy role init \
    --role-skeleton "${source_dir_path}/role_default" \
    --extra-var "author='${ansible_extravar_author_default}'" \
    --extra-var "company='${ansible_extravar_company_default}'" \
    --extra-var "description='${ansible_extravar_description_role_default}'" \
    --extra-var "repository_url='${ansible_extravar_repository_default}'" \
    --extra-var "issue_tracker_url='${ansible_extravar_issues_default}'" \
    --extra-var "homepage_url='${ansible_extravar_homepage_default}'" \
    --extra-var "min_ansible_version='2.16.0'" \
    --force \
    "${ansible_role_name}"

  # install dependencies needed for linting an executing the created resources
  msg ''
  msg ''
  msg ''
  msg ''
  msg 'Installing or upgrading needed Ansible collections and roles via ansible-galaxy.'
  ensure cd "${path_working_dir}"
  for req_file in $(find "${path_working_dir}" -name "requirements.yml" -type f)
  do
    msg "Installing from '${req_file}'"
    ensure ansible-galaxy role install -r "${req_file}" # --requirements-file is not available for role install, using short option name
    ensure ansible-galaxy collection install --requirements-file "${req_file}"
    msg ''
    msg ''
  done

  # playbook run
  msg ''
  msg ''
  msg ''
  msg ''
  msg '############################################################################'
  msg '# Starting a dummy playbook run.'
  msg '############################################################################'
  ensure cd "${path_working_dir}"
  ensure ansible-playbook "${path_working_dir}/playbook.yml"
  ensure cd "$(dirname "${0}")"

  # linting
  msg ''
  msg ''
  msg ''
  msg ''
  msg '############################################################################'
  msg '# Linting the collection.'
  msg '############################################################################'
  # Notes:
  # - yaml[comments] is skipped as there are #comments without additional space, which
  #   are related to FIXMEs and should be easy to remove without requiring indentation
  #   adjustments afterwards.
  ensure ansible-lint \
    --profile production \
    --strict \
    --skip-list 'yaml[comments]' \
    "${path_working_dir}/dependencies/collections/ansible_collections/${ansible_collection_namespace}/${ansible_collection_name}"

  if [ -d "${path_working_dir}/dependencies/collections/ansible_collections/${ansible_collection_namespace}/${ansible_collection_name}/changelogs/fragments" ]
  then
    # Only the 'init' command can be used outside an Ansible checkout and outside a
    # collection repository, or inside one without changelogs/config.yaml.
    ensure cd "${path_working_dir}/dependencies/collections/ansible_collections/${ansible_collection_namespace}/${ansible_collection_name}/"
    ensure antsibull-changelog lint
  fi
  if [ -f "${path_working_dir}/dependencies/collections/ansible_collections/${ansible_collection_namespace}/${ansible_collection_name}/changelogs/changelog.yaml" ]
  then
    # Only the 'init' command can be used outside an Ansible checkout and outside a
    # collection repository, or inside one without changelogs/config.yaml.
    ensure cd "${path_working_dir}/dependencies/collections/ansible_collections/${ansible_collection_namespace}/${ansible_collection_name}/"
    ensure antsibull-changelog lint-changelog-yaml "changelogs/changelog.yaml"
  fi
  msg ''
  msg ''
  msg ''
  msg ''
  msg '############################################################################'
  msg '# Linting the stand-alone role.'
  msg '############################################################################'
  # Notes:
  # - yaml[comments] is skipped as there are #comments without additional space, which
  #   are related to FIXMEs and should be easy to remove without requiring indentation
  #   adjustments afterwards.
  # - /molecule is excluded and will be linted in a dedicated run later
  ansible-lint \
    --profile production \
    --strict \
    --skip-list 'yaml[comments]' \
    --exclude "${path_working_dir}/dependencies/roles/${ansible_role_name}/molecule" \
    "${path_working_dir}/dependencies/roles/${ansible_role_name}"
  # Notes:
  # - yaml[comments] is skipped as there are #comments without additional space, which
  #   are related to FIXMEs and should be easy to remove without requiring indentation
  #   adjustments afterwards.
  # - /molecule was excluded before and now will will be linted with|
  #   var-naming[no-role-prefix] excluded (this rule makes no sense in a Molecule dir)
  ansible-lint \
    --profile production \
    --strict \
    --skip-list 'yaml[comments],var-naming[no-role-prefix]' \
    "${path_working_dir}/dependencies/roles/${ansible_role_name}/molecule"

  # deactivate any virtual environment (there should be none in this script context but better safe than sorry)
  if declare -F "deactivate" > /dev/null 2>&1
  then
      deactivate
      msg 'Deactivated currently active Python virtual environment.'
  fi
}

main "$@"
```