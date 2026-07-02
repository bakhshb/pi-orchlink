#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="orchlink"
DEFAULT_REPO_URL="https://github.com/bakhshb/pi-orchlink.git"
DEFAULT_REF="main"

INSTALL_DIR="${ORCHLINK_INSTALL_DIR:-$HOME/.local/share/orchlink}"
BIN_DIR="${ORCHLINK_BIN_DIR:-$HOME/.local/bin}"
REPO_URL="${ORCHLINK_REPO_URL:-$DEFAULT_REPO_URL}"
REF="${ORCHLINK_REF:-$DEFAULT_REF}"
PYTHON_BIN="${ORCHLINK_PYTHON:-python3}"
FORCE=0
UNINSTALL=0

if [[ -t 1 ]]; then
  BOLD='\033[1m'
  GREEN='\033[32m'
  YELLOW='\033[33m'
  RED='\033[31m'
  RESET='\033[0m'
else
  BOLD=''
  GREEN=''
  YELLOW=''
  RED=''
  RESET=''
fi

log() { printf "%b[orchlink]%b %s\n" "$GREEN" "$RESET" "$*"; }
warn() { printf "%b[orchlink warn]%b %s\n" "$YELLOW" "$RESET" "$*"; }
die() { printf "%b[orchlink error]%b %s\n" "$RED" "$RESET" "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Orchlink installer

Usage:
  install.sh [options]
  curl -fsSL https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/install.sh | bash

Options:
  --repo URL       Git repository to install from. Default: $DEFAULT_REPO_URL
  --ref REF        Git branch, tag, or commit. Default: $DEFAULT_REF
  --dir PATH       Install directory. Default: ~/.local/share/orchlink
  --bin-dir PATH   Command symlink directory. Default: ~/.local/bin
  --python PATH    Python executable. Default: python3
  --force          Replace an existing non-git install directory
  --uninstall      Remove installed files and command symlinks
  -h, --help       Show this help

Environment overrides:
  ORCHLINK_REPO_URL, ORCHLINK_REF, ORCHLINK_INSTALL_DIR, ORCHLINK_BIN_DIR, ORCHLINK_PYTHON
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      [[ $# -ge 2 ]] || die "--repo requires a URL"
      REPO_URL="$2"
      shift 2
      ;;
    --ref)
      [[ $# -ge 2 ]] || die "--ref requires a branch, tag, or commit"
      REF="$2"
      shift 2
      ;;
    --dir)
      [[ $# -ge 2 ]] || die "--dir requires a path"
      INSTALL_DIR="$2"
      shift 2
      ;;
    --bin-dir)
      [[ $# -ge 2 ]] || die "--bin-dir requires a path"
      BIN_DIR="$2"
      shift 2
      ;;
    --python)
      [[ $# -ge 2 ]] || die "--python requires a path"
      PYTHON_BIN="$2"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

is_interactive() {
  [[ -t 1 && -r /dev/tty ]]
}

prompt_yes_no() {
  local prompt="$1"
  local reply=""
  is_interactive || return 1
  printf "%s" "$prompt" > /dev/tty
  IFS= read -r reply < /dev/tty || return 1
  case "${reply,,}" in
    y|yes) return 0 ;;
    *) return 1 ;;
  esac
}

python_version_ok() {
  "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

python_venv_ok() {
  local temp_dir=""
  temp_dir="$(mktemp -d 2>/dev/null || mktemp -d -t orchlink-venv)"
  if "$PYTHON_BIN" -m venv "$temp_dir/venv" >/dev/null 2>&1; then
    rm -rf "$temp_dir"
    return 0
  fi
  rm -rf "$temp_dir"
  return 1
}

sudo_prefix() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    printf ""
  elif command_exists sudo; then
    printf "sudo "
  else
    printf ""
  fi
}

dependency_install_command() {
  local sudo=""
  sudo="$(sudo_prefix)"
  if command_exists apt-get; then
    printf "%sapt-get update && %sapt-get install -y python3 python3-venv git\n" "$sudo" "$sudo"
  elif command_exists dnf; then
    printf "%sdnf install -y python3 git\n" "$sudo"
  elif command_exists pacman; then
    printf "%spacman -Sy --needed python git\n" "$sudo"
  elif command_exists zypper; then
    printf "%szypper install -y python3 git\n" "$sudo"
  elif command_exists brew; then
    printf "brew install python git\n"
  else
    return 1
  fi
}

print_dependency_help() {
  cat <<EOF

Install the missing requirements, then rerun this installer.

Examples:
  Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y python3 python3-venv git
  Fedora:        sudo dnf install -y python3 git
  Arch:          sudo pacman -Sy --needed python git
  openSUSE:      sudo zypper install -y python3 git
  macOS:         brew install python git
EOF
}

collect_missing_requirements() {
  local needs_git="$1"
  MISSING_REQUIREMENTS=()
  CUSTOM_PYTHON_PROBLEM=""

  if [[ "$needs_git" -eq 1 ]] && ! command_exists git; then
    MISSING_REQUIREMENTS+=("git")
  fi

  if ! command_exists "$PYTHON_BIN"; then
    if [[ "$PYTHON_BIN" == "python3" ]]; then
      MISSING_REQUIREMENTS+=("python3")
    else
      CUSTOM_PYTHON_PROBLEM="Python command not found: $PYTHON_BIN. Install Python 3.11+ or pass --python with a valid interpreter."
    fi
    return
  fi

  if ! python_version_ok; then
    if [[ "$PYTHON_BIN" == "python3" ]]; then
      MISSING_REQUIREMENTS+=("Python 3.11+")
    else
      CUSTOM_PYTHON_PROBLEM="$PYTHON_BIN is not Python 3.11+. Install Python 3.11+ or pass --python with a compatible interpreter."
    fi
  elif ! python_venv_ok; then
    MISSING_REQUIREMENTS+=("python3-venv")
  fi
}

using_local_source() {
  [[ -n "$LOCAL_SOURCE_DIR" && -f "$LOCAL_SOURCE_DIR/pyproject.toml" && -d "$LOCAL_SOURCE_DIR/src/orchlink" && -z "${ORCHLINK_REPO_URL:-}" ]]
}

ensure_requirements() {
  local needs_git="$1"
  local install_cmd=""
  collect_missing_requirements "$needs_git"

  if [[ -n "$CUSTOM_PYTHON_PROBLEM" ]]; then
    die "$CUSTOM_PYTHON_PROBLEM"
  fi
  if [[ "${#MISSING_REQUIREMENTS[@]}" -eq 0 ]]; then
    return
  fi

  warn "Orchlink requires Python 3.11+ with venv support and Git."
  warn "Missing requirements:"
  for item in "${MISSING_REQUIREMENTS[@]}"; do
    printf "  - %s\n" "$item" >&2
  done

  if ! install_cmd="$(dependency_install_command)"; then
    print_dependency_help >&2
    die "Could not detect a supported package manager to install requirements automatically."
  fi

  printf "\nDetected install command:\n  %s\n" "$install_cmd" >&2
  if prompt_yes_no "Would you like Orchlink to install missing requirements now? [y/N] "; then
    log "Installing missing requirements"
    if ! bash -c "$install_cmd"; then
      print_dependency_help >&2
      die "Requirement installation failed."
    fi
    collect_missing_requirements "$needs_git"
    if [[ -n "$CUSTOM_PYTHON_PROBLEM" ]]; then
      die "$CUSTOM_PYTHON_PROBLEM"
    fi
    if [[ "${#MISSING_REQUIREMENTS[@]}" -eq 0 ]]; then
      log "Requirements installed. Continuing."
      return
    fi
    print_dependency_help >&2
    die "Requirements are still missing after installation: ${MISSING_REQUIREMENTS[*]}"
  fi

  print_dependency_help >&2
  die "Missing requirements. Install them and rerun this installer."
}

expand_path() {
  local value="$1"
  case "$value" in
    ~) printf "%s\n" "$HOME" ;;
    ~/*) printf "%s/%s\n" "$HOME" "${value#~/}" ;;
    *) printf "%s\n" "$value" ;;
  esac
}

INSTALL_DIR="$(expand_path "$INSTALL_DIR")"
BIN_DIR="$(expand_path "$BIN_DIR")"
VENV_DIR="$INSTALL_DIR/.venv"
MARKER_FILE="$INSTALL_DIR/.orchlink-install"

script_dir=""
script_source="${BASH_SOURCE[0]:-$0}"
if [[ "$script_source" != "bash" && "$script_source" != "/dev/fd/"* && -f "$script_source" ]]; then
  script_dir="$(cd "$(dirname "$script_source")" && pwd -P)"
fi

LOCAL_SOURCE_DIR="${ORCHLINK_SOURCE_DIR:-}"
if [[ -z "$LOCAL_SOURCE_DIR" && -n "$script_dir" && -f "$script_dir/pyproject.toml" && -d "$script_dir/src/orchlink" ]]; then
  LOCAL_SOURCE_DIR="$script_dir"
fi

uninstall() {
  log "Removing command symlinks from $BIN_DIR"
  rm -f "$BIN_DIR/orch" "$BIN_DIR/orchlink"
  if [[ -d "$INSTALL_DIR" ]]; then
    log "Removing $INSTALL_DIR"
    rm -rf "$INSTALL_DIR"
  fi
  log "Uninstalled Orchlink"
}

copy_local_source() {
  local source_dir="$1"
  log "Installing from local source: $source_dir"
  if [[ -d "$INSTALL_DIR" && "$(cd "$source_dir" && pwd -P)" == "$(cd "$INSTALL_DIR" && pwd -P)" ]]; then
    touch "$MARKER_FILE"
    return
  fi
  if [[ -e "$INSTALL_DIR" ]]; then
    if [[ -f "$MARKER_FILE" || "$FORCE" -eq 1 ]]; then
      rm -rf "$INSTALL_DIR"
    else
      die "$INSTALL_DIR already exists. Re-run with --force to replace it."
    fi
  fi
  mkdir -p "$INSTALL_DIR"
  (
    cd "$source_dir"
    tar \
      --exclude='.git' \
      --exclude='.venv' \
      --exclude='.orch' \
      --exclude='.opencode' \
      --exclude='__pycache__' \
      --exclude='.pytest_cache' \
      --exclude='*.pyc' \
      -cf - .
  ) | (cd "$INSTALL_DIR" && tar -xf -)
  touch "$MARKER_FILE"
}

clone_or_update_repo() {
  require_command git
  mkdir -p "$(dirname "$INSTALL_DIR")"
  if [[ -d "$INSTALL_DIR/.git" && "$FORCE" -eq 0 ]]; then
    log "Updating existing checkout in $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch --tags --prune origin
    git -C "$INSTALL_DIR" checkout "$REF"
    git -C "$INSTALL_DIR" pull --ff-only origin "$REF" 2>/dev/null || true
    touch "$MARKER_FILE"
    return
  fi
  if [[ -e "$INSTALL_DIR" ]]; then
    if [[ -f "$MARKER_FILE" || "$FORCE" -eq 1 ]]; then
      rm -rf "$INSTALL_DIR"
    else
      die "$INSTALL_DIR already exists and is not managed by this installer. Re-run with --force to replace it."
    fi
  fi
  log "Cloning $REPO_URL#$REF into $INSTALL_DIR"
  if ! git clone --depth 1 --branch "$REF" "$REPO_URL" "$INSTALL_DIR"; then
    rm -rf "$INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
    git -C "$INSTALL_DIR" checkout "$REF"
  fi
  touch "$MARKER_FILE"
}

install_package() {
  require_command "$PYTHON_BIN"
  log "Creating virtual environment: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  log "Installing Orchlink package"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null
  "$VENV_DIR/bin/python" -m pip install -e "$INSTALL_DIR"
  mkdir -p "$BIN_DIR"
  rm -f "$BIN_DIR/orchlink"
  ln -sf "$VENV_DIR/bin/orch" "$BIN_DIR/orch"
}

print_success() {
  log "Installed Orchlink"
  printf "\n%sCommands:%s\n" "$BOLD" "$RESET"
  printf "  %s/orch\n" "$BIN_DIR"
  printf "\n"
  if command -v orch >/dev/null 2>&1; then
    log "orch is available on PATH: $(command -v orch)"
  else
    warn "$BIN_DIR is not on PATH for this shell. Add this line to your shell profile:"
    printf "  export PATH=\"%s:\$PATH\"\n" "$BIN_DIR"
  fi
  printf "\nNext steps:\n"
  printf "  cd /path/to/your/project\n"
  printf "  orch init\n"
  printf "  orch lead    # terminal 1\n"
  printf "  orch work    # terminal 2\n"
}

main() {
  if [[ "$UNINSTALL" -eq 1 ]]; then
    uninstall
    exit 0
  fi

  log "Installing Orchlink to $INSTALL_DIR"
  if using_local_source; then
    ensure_requirements 0
    copy_local_source "$LOCAL_SOURCE_DIR"
  else
    ensure_requirements 1
    clone_or_update_repo
  fi
  install_package
  "$BIN_DIR/orch" --help >/dev/null
  print_success
}

main "$@"
