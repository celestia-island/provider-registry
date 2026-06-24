set dotenv-load

default:
    @just --list

sync source='all' providers='all':
    python3 scripts/update_models.py --source {{ source }} --entrypoint-mode --providers {{ providers }}

sync-individual source='all' providers='all':
    python3 scripts/update_models.py --source {{ source }} --entrypoint-mode --individual-models --providers {{ providers }}

# Merge master (config-table source of truth) into dev. Fast, no model fetches.
merge-dev:
    python3 scripts/merge_master_to_dev.py
