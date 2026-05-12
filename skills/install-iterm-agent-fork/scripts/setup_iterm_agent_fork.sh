#!/usr/bin/env bash
set -euo pipefail

repo_url="https://github.com/pirate/iterm-agent-fork.git"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
default_repo_dir="$(cd "${script_dir}/../../.." && pwd)"
repo_dir="$default_repo_dir"
default_scripts_dir="${HOME}/Library/Application Support/iTerm2/Scripts"
scripts_dir="${ITERM_AGENT_FORK_SCRIPTS_DIR:-}"
scripts_dir_explicit=0
install_casr=1
launch_script=1
configure_iterm=1
dry_run=0

usage() {
  cat <<'USAGE'
Usage: setup_iterm_agent_fork.sh [options]

Install pirate/iterm-agent-fork into iTerm2's AutoLaunch scripts folder and
launch the API script. The script also enables the iTerm2 Python API and
configures a custom iTerm2 scripts directory when one is explicitly provided.

Options:
  --repo-dir PATH       Checkout path. Default: current iterm-agent-fork checkout
  --scripts-dir PATH    iTerm scripts directory. Default: existing iTerm custom scripts dir, otherwise ~/Library/Application Support/iTerm2/Scripts
  --repo-url URL        Git repository URL. Default: https://github.com/pirate/iterm-agent-fork.git
  --no-casr             Do not install casr
  --no-configure-iterm  Do not write iTerm2 API or scripts-folder preferences
  --no-launch           Do not launch the iTerm2 API script
  --dry-run             Print actions without making changes
  -h, --help            Show this help

Environment overrides:
  ITERM_AGENT_FORK_REPO_DIR
  ITERM_AGENT_FORK_SCRIPTS_DIR
  ITERM_AGENT_FORK_REPO_URL
USAGE
}

repo_dir="${ITERM_AGENT_FORK_REPO_DIR:-$repo_dir}"
repo_url="${ITERM_AGENT_FORK_REPO_URL:-$repo_url}"
if [[ -n "${ITERM_AGENT_FORK_SCRIPTS_DIR:-}" ]]; then
  scripts_dir_explicit=1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-dir)
      repo_dir="$2"
      shift 2
      ;;
    --scripts-dir)
      scripts_dir="$2"
      scripts_dir_explicit=1
      shift 2
      ;;
    --repo-url)
      repo_url="$2"
      shift 2
      ;;
    --no-casr)
      install_casr=0
      shift
      ;;
    --no-configure-iterm)
      configure_iterm=0
      shift
      ;;
    --no-launch)
      launch_script=0
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if [[ "$dry_run" -eq 0 ]]; then
    "$@"
  fi
}

run_allow_fail() {
  printf '+'
  printf ' %q' "$@"
  printf ' || true\n'
  if [[ "$dry_run" -eq 0 ]]; then
    "$@" >/dev/null 2>&1 || true
  fi
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

stop_existing_api_script() {
  local script_path="$1"
  local pids=() pid command
  while read -r pid command; do
    if [[ "$command" == *"$script_path"* ]]; then
      pids+=("$pid")
    fi
  done < <(ps ax -o pid=,command=)

  if [[ "${#pids[@]}" -gt 0 ]]; then
    run_allow_fail kill -TERM "${pids[@]}"
    if [[ "$dry_run" -eq 0 ]]; then
      sleep 1
    fi
  fi
}

need_cmd git
need_cmd python3
if [[ "$launch_script" -eq 1 ]]; then
  need_cmd osascript
fi

repo_dir="${repo_dir/#\~/$HOME}"

read_configured_scripts_dir() {
  local use_custom custom_dir
  use_custom="$(defaults read com.googlecode.iterm2 UseCustomScriptsFolder 2>/dev/null || true)"
  if [[ "$use_custom" == "1" || "$use_custom" == "true" || "$use_custom" == "YES" ]]; then
    custom_dir="$(defaults read com.googlecode.iterm2 CustomScriptsFolder 2>/dev/null || true)"
    if [[ -n "$custom_dir" ]]; then
      printf '%s\n' "$custom_dir"
    fi
  fi
}

if [[ -z "$scripts_dir" ]]; then
  scripts_dir="$(read_configured_scripts_dir)"
fi
if [[ -z "$scripts_dir" ]]; then
  scripts_dir="$default_scripts_dir"
fi
scripts_dir="${scripts_dir/#\~/$HOME}"
autolaunch_dir="${scripts_dir%/}/AutoLaunch"
source_script="${repo_dir%/}/fork_agent_here.py"
installed_script="${autolaunch_dir}/fork_agent_here.py"

if [[ ! -d "$repo_dir/.git" ]]; then
  if [[ -e "$repo_dir" ]]; then
    echo "Refusing to overwrite non-git path: $repo_dir" >&2
    exit 1
  fi
  run mkdir -p "$(dirname "$repo_dir")"
  run git clone "$repo_url" "$repo_dir"
fi

if [[ ! -f "$source_script" ]]; then
  echo "Missing source script: $source_script" >&2
  exit 1
fi

run python3 -m py_compile "$source_script"
run mkdir -p "$autolaunch_dir"

if [[ -f "$installed_script" ]] && ! cmp -s "$source_script" "$installed_script"; then
  backup="${installed_script}.bak.$(date +%Y%m%d-%H%M%S)"
  run cp "$installed_script" "$backup"
fi

run install -m 755 "$source_script" "$installed_script"
run python3 -m py_compile "$installed_script"

if [[ "$configure_iterm" -eq 1 ]]; then
  run defaults write com.googlecode.iterm2 EnableAPIServer -bool true
  run defaults write com.googlecode.iterm2 AITermAPI -int 2
  if [[ "$scripts_dir_explicit" -eq 1 ]]; then
    run defaults write com.googlecode.iterm2 UseCustomScriptsFolder -bool true
    run defaults write com.googlecode.iterm2 CustomScriptsFolder "$scripts_dir"
  elif [[ "$scripts_dir" == "$default_scripts_dir" ]]; then
    # Keep the standard iTerm2 scripts directory selected when no custom path is requested.
    run defaults write com.googlecode.iterm2 UseCustomScriptsFolder -bool false
    run_allow_fail defaults delete com.googlecode.iterm2 CustomScriptsFolder
  fi
fi

if [[ "$install_casr" -eq 1 ]]; then
  casr_path=""
  for candidate in \
    "$(command -v casr 2>/dev/null || true)" \
    "$HOME/.cargo/bin/casr" \
    "$HOME/.local/bin/casr" \
    "/opt/homebrew/bin/casr" \
    "/usr/local/bin/casr"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      casr_path="$candidate"
      break
    fi
  done

  if [[ -z "$casr_path" ]]; then
    need_cmd cargo
    run cargo install --git https://github.com/Dicklesworthstone/cross_agent_session_resumer
    casr_path="$HOME/.cargo/bin/casr"
  fi

  if [[ "$dry_run" -eq 0 && -x "$casr_path" ]]; then
    "$casr_path" --version
  else
    echo "casr expected at: $casr_path"
  fi
fi

if [[ "$launch_script" -eq 1 ]]; then
  stop_existing_api_script "$installed_script"
  run osascript -e "tell application \"iTerm2\" to launch API script named \"$installed_script\""
fi

if [[ "$dry_run" -eq 0 ]]; then
  if defaults read com.googlecode.iterm2 GlobalKeyMap 2>/dev/null | grep -q 'fork_agent_here()'; then
    echo "Verified iTerm key binding for fork_agent_here()."
  else
    echo "iTerm key binding not visible yet. If the Python runtime just installed, rerun after it finishes." >&2
  fi

  if defaults read com.googlecode.iterm2 GlobalKeyMap 2>/dev/null | grep -q 'handoff_agent_here()'; then
    echo "Verified iTerm key binding for handoff_agent_here()."
  else
    echo "iTerm handoff key binding not visible yet. If the Python runtime just installed, rerun after it finishes." >&2
  fi
fi

echo "Installed script: $installed_script"
