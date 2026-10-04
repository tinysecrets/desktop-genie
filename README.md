# Desktop Genie

Canonical source repository for the live desktop Genie runtime.

Runtime config: ~/.config/genie
Launcher: ~/bin/genie

## Layout
- `genie*.py`, `puter_provider.py`: desktop Genie application and integrations
- `config/`: model/team/runtime configuration (secrets excluded)
- `bin/genie`: canonical launcher
- `assets/`: UI assets

## Intelligence stack (v2.2)

GENIE is hybrid: **local brain + cloud muscle + automatic recovery.**
No manual model selection is required — GENIE classifies each task and walks
the matching brain chain from the roster in `config/ai-team.json`:

| Task class | Brain chain (first available wins) |
|---|---|
| simple | Local Ollama → Hermes 4 14B → OpenRouter FREE pool |
| complex | Opus 5.5 → Fable 5.1 → Hermes 4 405B → OpenRouter FREE pool → Local Ollama (emergency) |

Routing behavior:
- Simple tasks stay local and private whenever Ollama is up.
- Complex reasoning/coding escalates to the flagship brains automatically.
- Unavailable brains are probed live and skipped.
- A failed tier escalates to the next; if the FREE pool fails, GENIE
  recovers to local/offline brains (no network → stay local).
- The hard policy gate applies to every tier identically.

`genie doctor` reports the whole chain with live up/down status per brain.

## Conversational memory (v2.1)

- The terminal assistant remembers the conversation across turns **and
  restarts** (bounded transcript persisted to `state/cli-chat.json`).
- The desktop app feeds structured history into the engine instead of
  inlined text.
- Session commands: `/help`, `/new` (clear memory), `/history`,
  `/save [path]` (export transcript as Markdown).

## Desktop app controls

- **STOP** — the send button becomes a stop control while GENIE is working;
  cancellation is cooperative and checked between model calls and tool runs.
- **EXPORT CHAT** — save the conversation as Markdown.
- **COPY LAST** — copy GENIE's last answer to the clipboard.
- The activity stream shows the active brain/model and the routed chain.

## Safety
Runtime state, browser profiles, virtual environments, databases, logs, vault keys, and credentials are intentionally excluded.

## Development
Run `python3 -m unittest test_genie_regression.py`.
