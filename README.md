# iTerm Agent Fork

One-key iTerm2 forking and handoff for coding-agent CLIs.

This script installs two global iTerm2 shortcuts:

- `Cmd+Shift+F`: split the current pane and fork/resume the active agent in the same harness (e.g. codex -> codex fork).
- `Cmd+Shift+G`: split the current pane, show a small prompt to choose which harness you want to fork to (e.g. codex, claude, gemini, opencode, etc.), using [`casr`](https://github.com/Dicklesworthstone/cross_agent_session_resumer) for cross-agent session history transfer.

It currently detects Codex, Claude Code, Gemini CLI, and opencode by looking at the foreground process and its open session log.

The AutoLaunch script also runs a local tab-status daemon. It detects the foreground agent in each iTerm TTY and applies a session-local tab color: orange for Codex, blue for Claude, green for Gemini, and red for opencode. The iTerm script verifies the daemon's PID and agent identity against the live foreground process before applying a color, so stale TTY data is ignored.

<img width="1284" height="344" alt="image" src="https://github.com/user-attachments/assets/7793c104-8d66-49d9-b24d-642a38be3ca9" />


## Behavior

Native same-agent forking does not use `casr`:

- Codex to Codex: `codex --yolo fork <session-id>`
- Claude to Claude: `claude --dangerously-skip-permissions --fork-session --resume <session-id>`
- Gemini to Gemini: `gemini --yolo --resume <session-id>`
- opencode to opencode: `opencode --session <session-id> --fork`

Cross-agent handoff uses `casr` when available:

- Codex to Claude, Gemini, or opencode
- Claude to Codex, Gemini, or opencode
- Gemini to Codex, Claude, or opencode
- opencode to Codex, Claude, or Gemini

If `casr` is missing or conversion fails, the script falls back to a prompt-based handoff that summarizes recent visible conversation context and current git state.

## Requirements

- macOS with iTerm2
- The agent CLIs you want to use, such as `codex`, `claude`, `gemini`, or `opencode`
- Optional but recommended for cross-agent handoff: `casr`

The script always invokes `python3`, never bare `python`. The automated installer enables the iTerm2 Python API and launches the script so iTerm2 can install or initialize its Python runtime when needed.

## Install

Clone the repo:

```sh
git clone https://github.com/pirate/iterm-agent-fork.git
cd iterm-agent-fork
```

### Automated install

Run the setup helper:

```sh
skills/install-iterm-agent-fork/scripts/setup_iterm_agent_fork.sh
```

The helper enables the iTerm2 Python API, installs the script into iTerm2's default `AutoLaunch` directory, and launches the API script. Launching the script lets iTerm2 install or finish installing its Python runtime when needed.

```text
$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/fork_agent_here.py
```

Most users do not need to configure iTerm2's custom scripts folder. If you already use one, pass it explicitly with `--scripts-dir "$CUSTOM_ITERM_SCRIPTS_DIR"`. Use `--no-casr` to skip installing `casr`, `--no-configure-iterm` to avoid writing iTerm2 preferences, or `--no-launch` to skip launching the API script.

### Manual install

If you are not using the setup helper, install the iTerm2 Python runtime if you have not already:

1. Open iTerm2.
2. Open `Scripts > Manage > Install Python Runtime`.
3. Allow iTerm2 to enable the Python API.

<img width="897" height="555" alt="Screenshot 2026-05-07 at 12 46 09 PM" src="https://github.com/user-attachments/assets/b9177854-e072-431f-b712-272d952cb2fe" />

<img width="513" height="119" alt="Screenshot 2026-05-07 at 12 46 55 PM" src="https://github.com/user-attachments/assets/9df3e41d-e311-4d41-84d1-05a607bdc7bd" />

Copy the script into your iTerm2 scripts `AutoLaunch` folder. For the default iTerm2 scripts location:

```sh
mkdir -p "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
cp fork_agent_here.py "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/fork_agent_here.py"
chmod +x "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/fork_agent_here.py"
```

If you use a custom synced iTerm scripts folder, copy it into that folder's `AutoLaunch` directory instead.

Then start or restart the script from iTerm2:

```sh
osascript -e 'tell application "iTerm2" to launch API script named "'"$HOME"'/Library/Application Support/iTerm2/Scripts/AutoLaunch/fork_agent_here.py"'
```

For a custom scripts folder, replace the path in the command above with your installed script path.

## Install casr

`casr` is only needed for cross-agent transfers. Same-agent forks do not use it.

https://github.com/Dicklesworthstone/cross_agent_session_resumer

One install option:

```sh
cargo install --git https://github.com/Dicklesworthstone/cross_agent_session_resumer
```

The script looks for `casr` in:

- `PATH`
- `$HOME/.cargo/bin/casr`
- `$HOME/.local/bin/casr`
- `/opt/homebrew/bin/casr`
- `/usr/local/bin/casr`

## Usage

Start an agent in an iTerm pane, then:

- Press `Cmd+Shift+F` to fork it into a split pane using the same agent.
- Press `Cmd+Shift+G` to choose a target agent in the new split pane.

The handoff menu accepts numbers or names:

```text
1) Claude Code
2) Codex
3) Gemini
4) opencode
```

## Notes

- The script writes iTerm2 global key bindings for `Cmd+Shift+F` and `Cmd+Shift+G`.
- If those shortcuts already do something else in your iTerm config, change the key constants near the top of `fork_agent_here.py`.
- The daemon writes a generic, local snapshot to `$HOME/.cache/iterm-agent-fork/tab-status.json`. It contains a version, timestamp, and foreground-agent identity keyed by TTY; other local tools may consume it, but the iTerm script does not depend on any external status producer.
- Cross-agent conversion quality depends on the source and target session formats supported by `casr`.
- Hidden reasoning is not transferred. For fallback prompt handoff, the script reads visible session logs, preserves compaction summaries where possible, and includes current git status.

## Uninstall

Remove the script from your `AutoLaunch` folder and restart iTerm2. If you want to remove the global key bindings too, delete the `fork_agent_here_v2()` and `handoff_agent_here_v2()` entries from iTerm2 preferences or bind those shortcuts to something else.
