# Windows: PowerShell (the 5.1 floor ships with every Windows; pwsh 7 is
# NOT assumed). Linewise recipes must stay PS-5.1-safe: no `&&` chains,
# `cd X; cmd` instead of `cd X && cmd`. Bash-only recipes use
# [script('bash')] and need Git Bash (or WSL) when actually run.
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $PSDefaultParameterValues['*:Encoding']='utf8';"]
set unstable
set lists
set dotenv-load

default:
    @just --list

fmt:
    {{ if which("ruff") != "" { "ruff check --fix ." } else { "black ." } }}

sync source='all' providers='all':
    {{ if os_family() == "windows" { "python" } else { "python3" } }} scripts/update_models.py --source {{ source }} --entrypoint-mode --providers {{ providers }}

sync-individual source='all' providers='all':
    {{ if os_family() == "windows" { "python" } else { "python3" } }} scripts/update_models.py --source {{ source }} --entrypoint-mode --individual-models --providers {{ providers }}

# Propagate supports_vision flags from model cards into entrypoint entries.
sync-vision:
    python3 scripts/sync_supports_vision.py

# Merge master (config-table source of truth) into dev. Fast, no model fetches.
merge-dev:
    python3 scripts/merge_master_to_dev.py

# Structural validation of MDD descriptor TOML files.
validate-descriptors:
    python3 scripts/validate_descriptors.py
