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

## Development
Run `python3 -m unittest test_genie_regression.py`.
