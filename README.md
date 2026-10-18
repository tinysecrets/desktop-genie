# Desktop Genie

Canonical source repository for the live desktop Genie runtime.

Runtime config: ~/.config/genie
Launcher: ~/bin/genie

## Layout
- `genie*.py`, `puter_provider.py`: desktop Genie application and integrations
- `config/`: model/team/runtime configuration (secrets excluded)
- `bin/genie`: canonical launcher
- `assets/`: UI assets

## Intelligence stack (v2.3)

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

## Puter free bridge (v2.3)

Puter's OpenAI-compatible REST endpoints (`/puterai/openai/v1/*`) require a
**paid plan** (free accounts get `402 subscription_required`). The free path
is `puter.ai.*` through an authenticated Puter.js environment, so GENIE runs
Puter.js inside a persistent local Chromium profile (`~/.config/genie/puter-profile`):

```
genie puter login            # one-time: visible browser, sign in to Puter
genie puter status           # session/bridge health
genie puter verify           # prove the live Opus/Fable/Hermes catalog
genie puter models [filter]  # full live catalog via puter.ai.listModels()
```

Login robustness (v2.3.1): `puter.auth.signIn()` requires a user gesture and
can be popup-blocked or abandoned (`auth_window_closed`), so the flow tries
the SDK popup first, then automatically opens the normal `puter.com/login`
page in the window as the reliable path. If the window is closed early GENIE
fails fast with clear instructions instead of hanging; progress is reported
every 15s, and a successful sign-in immediately runs `genie puter verify`
against the live catalog.

Bridge behavior:
- After the one-time sign-in, all Puter brains run headless against the saved
  session — Hermes 405B / Fable / Opus participate in the automatic chain.
- The model catalog is discovered dynamically with `puter.ai.listModels()`;
  roster ids are resolved against the live catalog, and zero-cost `:free`
  variants are preferred (free-first).
- Calls use `puter.ai.chat(..., {normalize: true})`, so every vendor returns
  the OpenAI shape GENIE's tool loop expects.
- Routing is strictly free-only: paid REST fallback is disabled, even when a
  paid-provider token is present. A Puter model is eligible only when the live
  catalog explicitly reports numeric zero input and output cost; unknown-price
  entries are rejected.
- Free-plan limits: 30 AI requests / 10 s, 3 concurrent — ample for GENIE.

## Conversational memory (v2.1)

- The terminal assistant remembers the conversation across turns **and
  restarts** (bounded transcript persisted to `state/cli-chat.json`).
- The desktop app feeds structured history into the engine instead of
  inlined text.
- Session commands: `/help`, `/new` (clear memory), `/history`,
  `/save [path]` (export transcript as Markdown).

## Visual language (v2.3.2.1)

The desktop app wears the Wah-Lah Genie's own colors on a deep-black stage:
lamp **gold** for the wordmark and premium accents, tail **teal** for live
status, hair **magenta** for primary actions, sticker **cream** for text.
The mascot is drawn as a clean circular medallion (a circular alpha mask
crops the shipped sticker's black backdrop and checkerboard specks). One
palette feeds every surface — command center, Genie Mode, Live Workspace.

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
