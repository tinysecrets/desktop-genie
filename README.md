# Desktop Genie

Canonical source repository for the live desktop Genie runtime.

Runtime config: ~/.config/genie
Launcher: ~/bin/genie

## Layout
- `genie*.py`, `puter_provider.py`: desktop Genie application and integrations
- `config/`: model/team/runtime configuration (secrets excluded)
- `bin/genie`: canonical launcher
- `assets/`: UI assets

## Safety
Runtime state, browser profiles, virtual environments, databases, logs, vault keys, and credentials are intentionally excluded.

## What's new in GENIE 2.1
- Persistent CLI conversation memory across turns and restarts
- `/help`, `/new`, `/history`, and `/save [path]` session controls
- Cooperative desktop STOP control for long-running wishes
- Structured conversation context between the desktop UI and engine
- Chat transcript export and COPY LAST response controls
- Live model selection is surfaced in the desktop activity stream
- Regression coverage for the new controls

## Development
Run `python3 -m unittest test_genie_regression.py`.
