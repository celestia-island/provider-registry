# Windows: PowerShell (the 5.1 floor ships with every Windows; pwsh 7 is
# NOT assumed). Linewise recipes must stay PS-5.1-safe: no `&&` chains,
# `cd X; cmd` instead of `cd X && cmd`. Bash-only recipes use
# [script('bash')] and need Git Bash (or WSL) when actually run.
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $PSDefaultParameterValues['*:Encoding']='utf8';"]
set unstable
set lists
set dotenv-load

# Shared celestia-devtools recipes — staged into .just/ (gitignored) by
# `celestia-devtools init`. `import?` keeps this justfile parseable before
# staging, so a fresh clone works without the shared file.
import? "./.just/celestia-devtools.just"

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

# Structural validation of the whole catalog (entrypoint/ + models/ + parse of
# descriptors/). This is exactly what the Catalog Validate CI job runs.
validate:
    python3 scripts/validate_catalog.py

# Structural validation of MDD descriptor TOML files (schema v1, plana types).
validate-descriptors:
    python3 scripts/validate_descriptors.py
