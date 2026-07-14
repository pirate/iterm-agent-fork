#!/usr/bin/env python3
import asyncio
import fcntl
import sys

sys.dont_write_bytecode = True

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


KEY_ACTION_INVOKE_SCRIPT_FUNCTION = 60
FORK_KEY_BINDINGS = (
    "0x66-0x120000-0x3",
    "0x66-0x120000",
    "0x46-0x120000-0x3",
    "0x46-0x120000",
)
HANDOFF_KEY_BINDINGS = (
    "0x67-0x120000-0x3",
    "0x67-0x120000",
    "0x47-0x120000-0x3",
    "0x47-0x120000",
)
RPC_INVOCATION = "fork_agent_here_v2()"
RPC_HANDOFF_INVOCATION = "handoff_agent_here_v2()"
UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
CODEX_SESSION_ID_RE = re.compile(
    r"/\.codex/sessions/.*/rollout-[^-]+-\d\d-\d\dT\d\d-\d\d-\d\d-"
    rf"({UUID_RE})\.jsonl$"
)
CLAUDE_SESSION_ID_RE = re.compile(
    rf"/\.claude/projects/.*/({UUID_RE})\.jsonl$"
)
GEMINI_SESSION_PATH_RE = re.compile(r"(/.*?/\.gemini/tmp/.*/chats/session-[^\s]+\.json)$")
OPENCODE_MESSAGE_PATH_RE = re.compile(
    r"(/.*?/\.local/share/opencode/storage/message/(ses_[^/\s]+)/[^/\s]+\.json)$"
)
# Matches the codex executable as a command token regardless of where the npm
# package places its binary (older builds: .../vendor/<triple>/codex/codex,
# newer builds: .../vendor/<triple>/bin/codex), including the node wrapper.
CODEX_COMMAND_RE = re.compile(r"(^|/)codex(\s|$)")
MAX_HANDOFF_CHARS = 50000
MAX_ENTRY_CHARS = 3000
MAX_TRANSCRIPT_ITEMS = 80
SKIPPED_USER_PREFIXES = (
    "<environment_context>",
    "# AGENTS.md instructions",
    "# CLAUDE.md instructions",
    "# GEMINI.md instructions",
)
STATUS_DIRECTORY = Path.home() / ".cache/iterm-agent-fork"
TAB_STATUS_PATH = STATUS_DIRECTORY / "tab-status.json"
TAB_STATUS_LOCK_PATH = STATUS_DIRECTORY / "tab-status.lock"
TAB_STATUS_DAEMON_ARG = "--tab-status-daemon"
TAB_STATUS_ONCE_ARG = "--tab-status-once"
TAB_STATUS_INTERVAL_SECONDS = 0.5
TAB_STATUS_MAX_AGE_SECONDS = 2
TAB_COLORS = {
    "codex": (255, 145, 35),
    "claude": (45, 115, 255),
    "gemini": (144, 238, 144),
    "opencode": (255, 45, 45),
}


def agent_name_for_command(command):
    if CODEX_COMMAND_RE.search(command):
        return "codex"
    if (
        "/claude" in command
        or " claude " in f" {command} "
        or command.endswith("/claude")
        or "com.anthropic.claude-code" in command
    ):
        return "claude"
    if " gemini " in f" {command} " or command.endswith("/gemini"):
        return "gemini"
    if " opencode " in f" {command} " or command.endswith("/opencode"):
        return "opencode"
    return None


def foreground_agent_identities():
    identities = {}
    rows = run_command(["ps", "-axo", "pid=,tty=,stat=,command="]).splitlines()
    for row in rows:
        parts = row.strip().split(None, 3)
        if len(parts) < 4:
            continue
        pid, tty_name, stat, command = parts
        if tty_name == "??" or "+" not in stat:
            continue
        agent = agent_name_for_command(command)
        if agent is None:
            continue
        identities.setdefault(
            f"/dev/{tty_name}",
            {"agent": agent, "pid": pid, "command": command},
        )
    return identities


def tab_status_snapshot():
    return {
        "version": 1,
        "updated": time.time(),
        "sessions": foreground_agent_identities(),
    }


def write_tab_status_snapshot(snapshot):
    STATUS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary_path = TAB_STATUS_PATH.with_suffix(f".{os.getpid()}.tmp")
    temporary_path.write_text(json.dumps(snapshot, separators=(",", ":")), encoding="utf-8")
    temporary_path.replace(TAB_STATUS_PATH)


def run_tab_status_daemon():
    STATUS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with open(TAB_STATUS_LOCK_PATH, "w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            write_tab_status_snapshot(tab_status_snapshot())
            time.sleep(TAB_STATUS_INTERVAL_SECONDS)


def ensure_tab_status_daemon():
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), TAB_STATUS_DAEMON_ARG],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def read_tab_statuses():
    try:
        payload = json.loads(TAB_STATUS_PATH.read_text(encoding="utf-8"))
        if time.time() - float(payload["updated"]) > TAB_STATUS_MAX_AGE_SECONDS:
            return {}
        sessions = payload["sessions"]
        return sessions if isinstance(sessions, dict) else {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def tab_status_identity_matches(tty, status):
    if not tty or not isinstance(status, dict):
        return False
    for process in foreground_processes_for_tty(tty):
        if (
            process["pid"] == str(status.get("pid"))
            and agent_name_for_command(process["command"]) == status.get("agent")
        ):
            return True
    return False


async def set_session_tab_color(session, agent):
    profile = iterm2.LocalWriteOnlyProfile()
    enabled = agent is not None
    profile.set_use_tab_color(enabled)
    profile.set_use_tab_color_light(enabled)
    profile.set_use_tab_color_dark(enabled)
    if enabled:
        color = iterm2.Color(*TAB_COLORS[agent])
        profile.set_tab_color(color)
        profile.set_tab_color_light(color)
        profile.set_tab_color_dark(color)
    await session.async_set_profile_properties(profile)


async def sync_tab_colors(app):
    applied = {}
    while True:
        try:
            statuses = read_tab_statuses()
            visible = set()
            for window in app.windows:
                for tab in window.tabs:
                    for session in tab.sessions:
                        tty = await session.async_get_variable("tty")
                        status = statuses.get(tty)
                        agent = (
                            status.get("agent")
                            if status and tab_status_identity_matches(tty, status)
                            else None
                        )
                        session_id = session.session_id
                        visible.add(session_id)
                        if agent is not None or session_id in applied:
                            if applied.get(session_id) != agent:
                                await set_session_tab_color(session, agent)
                                if agent is None:
                                    applied.pop(session_id, None)
                                else:
                                    applied[session_id] = agent
            for session_id in list(applied):
                if session_id not in visible:
                    applied.pop(session_id, None)
        except Exception as error:
            print("tab color sync:", error)
        await asyncio.sleep(TAB_STATUS_INTERVAL_SECONDS)


class PreferenceKey:
    def __init__(self, value):
        self.value = value


async def ensure_key_binding(connection):
    key_map = await iterm2.async_get_preference(connection, PreferenceKey("GlobalKeyMap"))
    key_map = dict(key_map or {})
    action = {
        "Action": KEY_ACTION_INVOKE_SCRIPT_FUNCTION,
        "Text": RPC_INVOCATION,
        "Version": 2,
        "Escaping": 0,
        "Apply Mode": 0,
    }
    handoff_action = {
        "Action": KEY_ACTION_INVOKE_SCRIPT_FUNCTION,
        "Text": RPC_HANDOFF_INVOCATION,
        "Version": 2,
        "Escaping": 0,
        "Apply Mode": 0,
    }

    for key in FORK_KEY_BINDINGS:
        key_map[key] = action
    for key in HANDOFF_KEY_BINDINGS:
        key_map[key] = handoff_action
    await iterm2.async_set_preference(connection, "GlobalKeyMap", key_map)


async def disable_application_key_reporting(session):
    # Codex's Kitty keyboard mode can be left enabled in a forked iTerm pane.
    # Keep the override session-local so existing profile settings stay intact.
    profile = iterm2.LocalWriteOnlyProfile({"Allow modifyOtherKeys": False})
    await session.async_set_profile_properties(profile)


def codex_executable():
    standalone = Path.home() / ".local/bin/codex"
    if standalone.is_file() and os.access(standalone, os.X_OK):
        return str(standalone)
    return shutil.which("codex") or "codex"


def run_command(args):
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    ).stdout


def lsof_name(line):
    parts = line.split(None, 8)
    if len(parts) < 9:
        return ""
    return parts[8]


def codex_session_from_pid(pid):
    if not pid:
        return None

    for line in run_command(["lsof", "-p", str(pid)]).splitlines():
        path = lsof_name(line)
        match = CODEX_SESSION_ID_RE.search(path)
        if match:
            return {"session_id": match.group(1), "path": path}

    return None


def claude_session_from_pid(pid):
    if not pid:
        return None

    for line in run_command(["lsof", "-p", str(pid)]).splitlines():
        path = lsof_name(line)
        match = CLAUDE_SESSION_ID_RE.search(path)
        if match:
            return {"session_id": match.group(1), "path": path}

    return None


def gemini_session_from_pid(pid):
    if not pid:
        return None

    for line in run_command(["lsof", "-p", str(pid)]).splitlines():
        match = GEMINI_SESSION_PATH_RE.search(lsof_name(line))
        if match:
            path = Path(match.group(1))
            return {"session_id": path.stem.removeprefix("session-"), "path": str(path)}

    return None


def opencode_session_from_pid(pid):
    if not pid:
        return None

    for line in run_command(["lsof", "-p", str(pid)]).splitlines():
        match = OPENCODE_MESSAGE_PATH_RE.search(lsof_name(line))
        if match:
            return {"session_id": match.group(2), "path": str(Path(match.group(1)).parent)}

    return None


def claude_project_dir_for_cwd(cwd):
    if not cwd:
        return None

    path = os.path.abspath(os.path.expanduser(cwd))
    encoded = re.sub(r"[^A-Za-z0-9]", "-", path)
    return Path.home() / ".claude" / "projects" / encoded


def latest_claude_session_for_cwd(cwd):
    project_dir = claude_project_dir_for_cwd(cwd)
    if not project_dir or not project_dir.is_dir():
        return None

    jsonl_files = sorted(
        project_dir.glob("*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not jsonl_files:
        return None

    match = re.fullmatch(UUID_RE, jsonl_files[0].stem)
    if not match:
        return None

    return {
        "name": "claude",
        "session_id": jsonl_files[0].stem,
        "path": str(jsonl_files[0]),
        "command": (
            "claude --dangerously-skip-permissions "
            f"--fork-session --resume {shlex.quote(jsonl_files[0].stem)}"
        ),
    }


def process_for_pid(pid):
    if not pid:
        return None

    rows = run_command(["ps", "-p", str(pid), "-o", "pid=,stat=,command="]).splitlines()
    if not rows:
        return None

    parts = rows[0].strip().split(None, 2)
    if len(parts) < 3:
        return None

    return {"pid": parts[0], "stat": parts[1], "command": parts[2]}


def foreground_processes_for_tty(tty):
    if not tty:
        return []

    tty_name = os.path.basename(tty)
    rows = run_command(["ps", "-t", tty_name, "-o", "pid=,stat=,command="]).splitlines()
    processes = []
    for row in rows:
        parts = row.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid, stat, command = parts
        if "+" not in stat:
            continue
        processes.append({"pid": pid, "stat": stat, "command": command})
    return processes


def truncate_text(text, limit=MAX_ENTRY_CHARS):
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def content_to_text(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            item_type = item.get("type")
            if item_type in ("text", "input_text", "output_text"):
                parts.append(item.get("text", ""))
            elif item_type in ("tool_use", "function_call"):
                name = item.get("name") or item.get("tool_name") or "tool"
                payload = item.get("input") or item.get("arguments") or {}
                parts.append(f"[tool call: {name} {json.dumps(payload, ensure_ascii=False)[:1000]}]")
            elif item_type in ("tool_result", "function_call_output"):
                parts.append(f"[tool result]\n{content_to_text(item.get('content') or item.get('output'))}")
            elif item_type in ("image", "input_image", "image_url"):
                parts.append("[image omitted]")
            else:
                parts.append(json.dumps(item, ensure_ascii=False)[:1000])
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def append_item(items, role, text):
    text = truncate_text(text)
    if role == "user" and text.startswith(SKIPPED_USER_PREFIXES):
        return
    if text:
        items.append({"role": role, "text": text})


def compacted_items(replacement_history):
    items = []
    for msg in replacement_history:
        role = msg.get("role")
        if role in ("user", "assistant"):
            append_item(items, role, content_to_text(msg.get("content")))
    return items


def parse_codex_session(path):
    items = []
    meta = {"compactions": 0}
    if not path or not Path(path).is_file():
        return {"items": items, "meta": meta}

    with open(path, "r", encoding="utf-8", errors="replace") as file:
        for raw_line in file:
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type")
            payload = entry.get("payload") or {}
            if entry_type == "session_meta":
                meta.update(payload)
                continue
            if entry_type == "turn_context":
                meta.setdefault("cwd", payload.get("cwd"))
                continue
            if entry_type == "compacted":
                # A compaction is already a condensed replacement for earlier history.
                # Drop pre-compaction raw events so old sessions do not overwhelm the target.
                meta["compactions"] += 1
                items = compacted_items(payload.get("replacement_history", []))
                if payload.get("message"):
                    append_item(items, "summary", payload.get("message"))
                continue
            if entry_type != "response_item":
                continue

            payload_type = payload.get("type")
            if payload_type == "message":
                role = payload.get("role")
                if role in ("user", "assistant"):
                    append_item(items, role, content_to_text(payload.get("content")))
            elif payload_type == "function_call":
                append_item(
                    items,
                    "tool",
                    f"{payload.get('name', 'tool')}({payload.get('arguments', '')})",
                )
            elif payload_type == "function_call_output":
                append_item(items, "tool_result", payload.get("output", ""))

    return {"items": items, "meta": meta}


def parse_claude_session(path):
    items = []
    meta = {"compactions": 0}
    if not path or not Path(path).is_file():
        return {"items": items, "meta": meta}

    with open(path, "r", encoding="utf-8", errors="replace") as file:
        for raw_line in file:
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            if entry.get("cwd"):
                meta.setdefault("cwd", entry.get("cwd"))
            if entry.get("sessionId"):
                meta.setdefault("id", entry.get("sessionId"))
            if entry.get("isCompactSummary") or entry.get("type") == "summary":
                meta["compactions"] += 1
                append_item(items, "summary", entry.get("summary") or entry.get("content") or entry)

            message = entry.get("message")
            if isinstance(message, dict):
                role = message.get("role")
                if role in ("user", "assistant"):
                    append_item(items, role, content_to_text(message.get("content")))
            if entry.get("toolUseResult"):
                append_item(items, "tool_result", entry.get("toolUseResult"))

    return {"items": items, "meta": meta}


def parse_gemini_session(path):
    items = []
    meta = {}
    if not path or not Path(path).is_file():
        return {"items": items, "meta": meta}

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {"items": items, "meta": meta}

    meta.update(
        {
            "id": data.get("sessionId"),
            "summary": data.get("summary"),
            "projectHash": data.get("projectHash"),
        }
    )
    for message in data.get("messages", []):
        role = message.get("type")
        if role == "gemini":
            role = "assistant"
        if role in ("user", "assistant"):
            append_item(items, role, message.get("content", ""))
        for call in message.get("toolCalls", []) or []:
            append_item(
                items,
                "tool",
                f"{call.get('name', 'tool')} {json.dumps(call.get('args', {}), ensure_ascii=False)[:1000]}",
            )
            if call.get("result"):
                append_item(items, "tool_result", call.get("result"))

    return {"items": items, "meta": meta}


def parse_opencode_session(path):
    items = []
    meta = {}
    path_obj = Path(path) if path else None
    if not path_obj or not path_obj.is_dir():
        return {"items": items, "meta": meta}

    for msg_file in sorted(path_obj.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            message = json.loads(msg_file.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue

        role = message.get("role")
        if message.get("sessionID"):
            meta.setdefault("id", message.get("sessionID"))
        path_info = message.get("path") or {}
        if isinstance(path_info, dict) and path_info.get("cwd"):
            meta.setdefault("cwd", path_info.get("cwd"))
        if role in ("user", "assistant"):
            text = message.get("text") or message.get("content")
            if not text and isinstance(message.get("summary"), dict):
                text = message["summary"].get("body") or message["summary"].get("title")
            append_item(items, role, text)

    return {"items": items, "meta": meta}


def parse_agent_session(agent):
    parsers = {
        "codex": parse_codex_session,
        "claude": parse_claude_session,
        "gemini": parse_gemini_session,
        "opencode": parse_opencode_session,
    }
    parser = parsers.get(agent["name"])
    if not parser:
        return {"items": [], "meta": {}}
    parsed = parser(agent.get("path"))
    parsed["meta"]["total_items"] = len(parsed.get("items", []))
    return parsed


def git_snapshot(cwd):
    if not cwd:
        return ""

    status = run_command(["git", "-C", cwd, "status", "--short", "--branch"]).strip()
    diff_stat = run_command(["git", "-C", cwd, "diff", "--stat"]).strip()
    if not status and not diff_stat:
        return "Not a git repository or no git state available."

    parts = []
    if status:
        parts.append("$ git status --short --branch\n" + truncate_text(status, 6000))
    if diff_stat:
        parts.append("$ git diff --stat\n" + truncate_text(diff_stat, 6000))
    return "\n\n".join(parts)


def render_handoff_prompt(agent, cwd):
    parsed = parse_agent_session(agent)
    meta = parsed.get("meta", {})
    items = parsed.get("items", [])[-MAX_TRANSCRIPT_ITEMS:]
    effective_cwd = meta.get("cwd") or cwd
    omitted_items = max(0, int(meta.get("total_items", len(items))) - len(items))

    lines = [
        "You are continuing a coding-agent session imported from another CLI.",
        "",
        "The previous session log has been read and condensed into this prompt.",
        "Treat the repository files and git state as source of truth. Hidden reasoning was not transferred.",
        "If something is ambiguous, inspect the codebase before acting.",
        "",
        f"Source agent: {agent.get('name')}",
        f"Source session: {agent.get('session_id') or meta.get('id') or 'unknown'}",
        f"Source log: {agent.get('path') or 'unknown'}",
        f"Working directory: {effective_cwd or cwd}",
        f"Loaded transcript entries: {len(items)} shown, {omitted_items} older entries omitted",
        f"Compactions detected: {meta.get('compactions', 0)}",
        "",
        "Current git state:",
        "```",
        git_snapshot(effective_cwd or cwd),
        "```",
        "",
        "Recent visible transcript:",
    ]

    for item in items:
        lines.extend(
            [
                "",
                f"### {item['role']}",
                item["text"],
            ]
        )

    prompt = "\n".join(lines).strip()
    if len(prompt) > MAX_HANDOFF_CHARS:
        prompt = prompt[:MAX_HANDOFF_CHARS] + f"\n\n...[handoff truncated to {MAX_HANDOFF_CHARS} chars]"
    return prompt


def write_handoff_launcher(cwd, agent):
    cwd = os.path.abspath(os.path.expanduser(cwd))
    temp_dir = Path(tempfile.mkdtemp(prefix="agent-handoff-"))
    handoff_file = temp_dir / "handoff.md"
    launcher_file = temp_dir / "choose-target.sh"
    handoff_file.write_text(render_handoff_prompt(agent, cwd), encoding="utf-8")

    source_ref = agent.get("session_id") or agent.get("path") or ""
    source_path = agent.get("path") or ""
    source_alias = {
        "claude": "cc",
        "codex": "cod",
        "gemini": "gmi",
        "opencode": "opc",
    }.get(agent.get("name"), "")
    native_command = agent.get("command") or ""
    launcher = f"""#!/bin/sh
set -u
cd {shlex.quote(cwd)} || exit 1
printf '\\nFork handoff from {agent["name"]} session {agent.get("session_id", "unknown")}\\n'
printf 'Choose target agent:\\n'
printf '  1) Claude Code\\n'
printf '  2) Codex\\n'
printf '  3) Gemini\\n'
printf '  4) opencode\\n'
printf '> '
IFS= read -r choice
source_ref={shlex.quote(source_ref)}
source_path={shlex.quote(source_path)}
source_alias={shlex.quote(source_alias)}
native_command={shlex.quote(native_command)}
handoff_file={shlex.quote(str(handoff_file))}

find_casr() {{
  if command -v casr >/dev/null 2>&1; then
    command -v casr
    return 0
  fi
  for candidate in "$HOME/.cargo/bin/casr" "$HOME/.local/bin/casr" "/opt/homebrew/bin/casr" "/usr/local/bin/casr"; do
    if [ -x "$candidate" ]; then
      printf '%s\\n' "$candidate"
      return 0
    fi
  done
  return 1
}}

fallback_prompt_handoff() {{
  target="$1"
  prompt=$(cat "$handoff_file")
  case "$target" in
    cc)
      exec claude --dangerously-skip-permissions "$prompt"
      ;;
    cod)
      exec codex --yolo "$prompt"
      ;;
    gmi)
      exec gemini --yolo --prompt-interactive "$prompt"
      ;;
    opc)
      exec opencode --prompt "$prompt"
      ;;
  esac
}}

extract_resume_command() {{
  python3 -c '
import json, re, sys
text = sys.stdin.read()

def json_resume_commands(value):
    if isinstance(value, dict):
        command = value.get("resume_command")
        if isinstance(command, str):
            yield command
        for item in value.values():
            yield from json_resume_commands(item)
    elif isinstance(value, list):
        for item in value:
            yield from json_resume_commands(item)

try:
    data = json.loads(text)
    candidates = list(json_resume_commands(data))
except Exception:
    candidates = []

if not candidates:
    for line in text.splitlines():
        if "resume_command" in line or re.match(r"^\\s*(claude|codex|gemini|opencode)\\b", line):
            candidates.append(line)

patterns = [
    r"(claude(?:\\s+--dangerously-skip-permissions)?\\s+(?:--resume|-r)\\s+[^\\s]+)",
    r"(codex(?:\\s+--yolo)?\\s+resume\\s+[^\\s]+)",
    r"(gemini(?:\\s+--yolo)?\\s+--resume\\s+[^\\s]+)",
    r"(opencode(?:\\s+--session\\s+[^\\s]+(?:\\s+--fork)?)?)",
]
for candidate in candidates:
    for pattern in patterns:
        match = re.search(pattern, candidate)
        if match:
            cmd = match.group(1)
            if cmd.startswith("claude --resume "):
                cmd = cmd.replace("claude --resume ", "claude --dangerously-skip-permissions --resume ", 1)
            elif cmd.startswith("claude -r "):
                cmd = cmd.replace("claude -r ", "claude --dangerously-skip-permissions -r ", 1)
            elif cmd.startswith("codex resume "):
                cmd = cmd.replace("codex resume ", "codex --yolo resume ", 1)
            elif cmd.startswith("gemini --resume "):
                cmd = cmd.replace("gemini --resume ", "gemini --yolo --resume ", 1)
            print(cmd)
            raise SystemExit(0)
raise SystemExit(1)
'
}}

run_casr_handoff() {{
  target="$1"
  if [ "$target" = "$source_alias" ] && [ -n "$native_command" ]; then
    printf 'Same-agent fork; skipping casr.\\n'
    printf 'Running: %s\\n' "$native_command"
    exec /bin/sh -lc "$native_command"
  fi

  casr_bin=$(find_casr) || {{
    printf 'casr not found; falling back to prompt handoff.\\n'
    fallback_prompt_handoff "$target"
  }}

  printf 'Converting with casr...\\n'
  casr_output=$("$casr_bin" resume "$target" "$source_ref" --source "$source_path" --json 2>&1)
  casr_status=$?
  if [ "$casr_status" -ne 0 ]; then
    printf 'casr conversion failed; falling back to prompt handoff.\\n'
    printf '%s\\n' "$casr_output"
    fallback_prompt_handoff "$target"
  fi

  resume_cmd=$(printf '%s' "$casr_output" | extract_resume_command) || {{
    printf 'casr did not return a recognizable resume command; falling back to prompt handoff.\\n'
    printf '%s\\n' "$casr_output"
    fallback_prompt_handoff "$target"
  }}

  printf 'Running: %s\\n' "$resume_cmd"
  exec /bin/sh -lc "$resume_cmd"
}}

case "$choice" in
  1|claude|Claude|c|C)
    run_casr_handoff cc
    ;;
  2|codex|Codex|x|X)
    run_casr_handoff cod
    ;;
  3|gemini|Gemini|g|G)
    run_casr_handoff gmi
    ;;
  4|opencode|OpenCode|opencode|o|O)
    run_casr_handoff opc
    ;;
  *)
    printf 'No target selected. Handoff saved at %s\\n' {shlex.quote(str(handoff_file))}
    exit 2
    ;;
esac
"""
    launcher_file.write_text(launcher, encoding="utf-8")
    launcher_file.chmod(0o700)
    return str(launcher_file)


def agent_for_process(process):
    command = process["command"]
    agent_name = agent_name_for_command(command)
    if agent_name == "codex":
        session = codex_session_from_pid(process["pid"])
        if session:
            return {
                "name": "codex",
                "session_id": session["session_id"],
                "path": session["path"],
                "command": (
                    f"{shlex.quote(codex_executable())} --yolo fork "
                    f"{shlex.quote(session['session_id'])}"
                ),
            }

    if agent_name == "claude":
        session = claude_session_from_pid(process["pid"])
        if session:
            return {
                "name": "claude",
                "session_id": session["session_id"],
                "path": session["path"],
                "command": (
                    "claude --dangerously-skip-permissions "
                    f"--fork-session --resume {shlex.quote(session['session_id'])}"
                ),
            }

    if agent_name == "gemini":
        session = gemini_session_from_pid(process["pid"])
        if session:
            return {
                "name": "gemini",
                "session_id": session["session_id"],
                "path": session["path"],
                "command": f"gemini --yolo --resume {shlex.quote(session['session_id'])}",
            }

    if agent_name == "opencode":
        session = opencode_session_from_pid(process["pid"])
        if session:
            return {
                "name": "opencode",
                "session_id": session["session_id"],
                "path": session["path"],
                "command": f"opencode --session {shlex.quote(session['session_id'])} --fork",
            }

    return None


async def agent_for_iterm_session(session, cwd):
    job_pid = await session.async_get_variable("jobPid")
    process = process_for_pid(job_pid)
    if process:
        agent = agent_for_process(process)
        if agent:
            return agent

    tty = await session.async_get_variable("tty")
    processes = foreground_processes_for_tty(tty)
    processes.sort(key=lambda p: (not CODEX_COMMAND_RE.search(p["command"]), p["pid"]))
    for process in processes:
        agent = agent_for_process(process)
        if agent:
            return agent

    agent = latest_claude_session_for_cwd(cwd)
    if agent:
        return agent

    return None


async def main(connection):
    app = await iterm2.async_get_app(connection)
    await ensure_key_binding(connection)
    ensure_tab_status_daemon()
    asyncio.ensure_future(sync_tab_colors(app))
    forks_in_progress = set()

    @iterm2.RPC
    async def fork_agent_here_v2(session_id=iterm2.Reference("id")):
        if session_id in forks_in_progress:
            return

        session = app.get_session_by_id(session_id)
        if session is None:
            return

        forks_in_progress.add(session_id)
        try:
            cwd = await session.async_get_variable("path") or "~"
            agent = await agent_for_iterm_session(session, cwd)
            if agent is None:
                await session.async_send_text(
                    "\n# fork_agent_here: could not find an active Codex, Claude, Gemini, or opencode session for this pane\n"
                )
                return

            child = await session.async_split_pane(vertical=True)
            if agent["name"] == "codex":
                await disable_application_key_reporting(child)

            # iTerm completes shell and PTY setup asynchronously after creating a split.
            await asyncio.sleep(0.25)
            command = f"cd {shlex.quote(cwd)} && {agent['command']}\n"
            await child.async_send_text(command)
        finally:
            forks_in_progress.discard(session_id)

    @iterm2.RPC
    async def handoff_agent_here_v2(session_id=iterm2.Reference("id")):
        session = app.get_session_by_id(session_id)
        if session is None:
            return

        cwd = await session.async_get_variable("path") or "~"
        agent = await agent_for_iterm_session(session, cwd)
        if agent is None:
            await session.async_send_text(
                "\n# fork_agent_here: could not find an active Codex, Claude, Gemini, or opencode session for this pane\n"
            )
            return
        if not agent.get("path"):
            await session.async_send_text(
                f"\n# fork_agent_here: found {agent['name']} but could not find its session log path\n"
            )
            return

        child = await session.async_split_pane(vertical=True)
        launcher = write_handoff_launcher(cwd, agent)
        await child.async_send_text(f"{shlex.quote(launcher)}\n")

    await fork_agent_here_v2.async_register(connection, timeout=10)
    await handoff_agent_here_v2.async_register(connection, timeout=10)


if __name__ == "__main__":
    if TAB_STATUS_DAEMON_ARG in sys.argv:
        run_tab_status_daemon()
    elif TAB_STATUS_ONCE_ARG in sys.argv:
        print(json.dumps(tab_status_snapshot(), indent=2))
    else:
        import iterm2

        iterm2.run_forever(main)
