# iTerm Agent Fork

One-key iTerm2 forking and handoff for coding-agent CLIs.

This script installs two global iTerm2 shortcuts:

- `Cmd+Shift+F`: split the current pane and fork/resume the active agent in the same harness (e.g. codex -> codex fork).
- `Cmd+Shift+G`: split the current pane, show a small prompt to choose which harness you want to fork to (e.g. codex, claude, gemini, opencode, etc.), using [`casr`](https://github.com/Dicklesworthstone/cross_agent_session_resumer) for cross-agent session history transfer.

It currently detects Codex, Claude Code, Gemini CLI, and opencode by looking at the foreground process and its open session log.

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
- iTerm2 Python API runtime installed
- Python 3 in the iTerm2 script runtime
- The agent CLIs you want to use, such as `codex`, `claude`, `gemini`, or `opencode`
- Optional but recommended for cross-agent handoff: `casr`

The script always invokes `python3`, never bare `python`.

## Install

Clone the repo:

```sh
git clone git@github.com:pirate/iterm-agent-fork.git
cd iterm-agent-fork
```

Install the iTerm2 Python runtime if you have not already:

1. Open iTerm2.
2. Open `Scripts > Manage > Install Python Runtime`.
3. Allow iTerm2 to enable the Python API.

<img width="897" height="555" alt="Screenshot 2026-05-07 at 12 46 09 PM" src="https://github.com/user-attachments/assets/b9177854-e072-431f-b712-272d952cb2fe" />

<img width="513" height="119" alt="Screenshot 2026-05-07 at 12 46 55 PM" src="https://github.com/user-attachments/assets/9df3e41d-e311-4d41-84d1-05a607bdc7bd" />


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
- Cross-agent conversion quality depends on the source and target session formats supported by `casr`.
- Hidden reasoning is not transferred. For fallback prompt handoff, the script reads visible session logs, preserves compaction summaries where possible, and includes current git status.

## Uninstall

Remove the script from your `AutoLaunch` folder and restart iTerm2. If you want to remove the global key bindings too, delete the `fork_agent_here()` and `handoff_agent_here()` entries from iTerm2 preferences or bind those shortcuts to something else.
