set dotenv-load

default:
    @just --list

sync source='all' providers='all':
    python3 scripts/update_models.py --source {{ source }} --entrypoint-mode --providers {{ providers }}

sync-all:
    just sync all

sync-openrouter:
    just sync openrouter

sync-modelsdev:
    just sync modelsdev

sync-individual source='all' providers='all':
    python3 scripts/update_models.py --source {{ source }} --entrypoint-mode --individual-models --providers {{ providers }}
