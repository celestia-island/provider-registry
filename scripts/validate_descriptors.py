#!/usr/bin/env python3
"""Structural validator for MDD (Model Deployment Descriptor) TOML files.

Mirrors the schema v1 in plana-celestia-types (MddDescriptor et al.).
Usage: python3 scripts/validate_descriptors.py [path ...]  (default: descriptors/)
Exit code 0 = all files valid; 1 = violations found.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ENUMS = {
    "component_kind": {"encoder", "dit", "vae", "decoder", "tokenizer", "other"},
    "dtype": {"f32", "f16", "bf16", "i8", "u8", "token_ids", "text", "other"},
    "engine": {
        "llama_cpp", "vllm", "sglang", "candle", "ollama",
        "cloud", "external_api", "native",
    },
    "entry_kind": {"file", "url", "registry", "gguf"},
    "runtime_status": {"ready", "planned", "unavailable"},
    "pipeline_phase": {"pre", "iterative", "post"},
    "task_kind": {
        "text_generation", "embedding", "image_generation",
        "video_generation", "audio_generation", "other",
    },
    "placement": {"single_card", "multi_card", "cloud", "external"},
}

REQUIRED_TOP = ("schema_version", "model", "components", "deploy", "scale")
OPTIONAL_STR = {"family", "arch", "description", "notes", "cache_key",
                "shape", "format", "compute_capability", "sha256", "default"}
OPTIONAL_NUM = {"size_multiplier", "min_vram_mb", "min_ram_mb", "min_vram_gb",
                "size_bytes", "size_gb", "peak_gb", "bytes_per_token",
                "kv_cache_bytes_per_token", "tokens_per_second",
                "requests_per_second", "total_parameters_b"}


def err(errors: list[str], path: Path, msg: str) -> None:
    errors.append(f"{path}: {msg}")


def check_string(d, key, where, errors, path, required=True):
    if key not in d:
        if required:
            err(errors, path, f"missing required key '{key}' in {where}")
        return
    if not isinstance(d[key], str):
        err(errors, path, f"'{key}' in {where} must be a string")


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        return [f"{path}: TOML parse error: {e}"]

    for key in REQUIRED_TOP:
        if key not in data:
            err(errors, path, f"missing required top-level key '{key}'")

    if "schema_version" in data and data["schema_version"] != 1:
        err(errors, path, f"unsupported schema_version {data['schema_version']}")

    model = data.get("model", {})
    check_string(model, "id", "model", errors, path)
    check_string(model, "name", "model", errors, path)
    check_string(model, "architecture", "model", errors, path)

    components = data.get("components", [])
    if not isinstance(components, list) or not components:
        err(errors, path, "components must be a non-empty array of tables")
    comp_ids: list[str] = []
    for i, comp in enumerate(components):
        where = f"components[{i}]"
        check_string(comp, "id", where, errors, path)
        cid = comp.get("id")
        if isinstance(cid, str):
            if cid in comp_ids:
                err(errors, path, f"duplicate component id '{cid}'")
            comp_ids.append(cid)
        kind = comp.get("kind")
        if kind not in ENUMS["component_kind"]:
            err(errors, path, f"{where}: kind '{kind}' not in {sorted(ENUMS['component_kind'])}")
        deps = comp.get("dependencies", [])
        if not isinstance(deps, list):
            err(errors, path, f"{where}: dependencies must be an array")
        for dep in deps:
            if dep not in comp_ids:
                err(errors, path, f"{where}: dependency '{dep}' is not a known component id")
        for io_kind in ("inputs", "outputs"):
            for io in comp.get(io_kind, []):
                if not isinstance(io, dict) or "name" not in io or "dtype" not in io:
                    err(errors, path, f"{where}: {io_kind} entries need name+dtype")
                elif io.get("dtype") not in ENUMS["dtype"]:
                    err(errors, path, f"{where}: {io_kind} dtype '{io.get('dtype')}' invalid")
        runtimes = comp.get("runtimes", [])
        if not isinstance(runtimes, list) or not runtimes:
            err(errors, path, f"{where}: runtimes must be a non-empty array")
        for j, rt in enumerate(runtimes):
            rwhere = f"{where}.runtimes[{j}]"
            eng = rt.get("engine")
            if eng not in ENUMS["engine"]:
                err(errors, path, f"{rwhere}: engine '{eng}' invalid")
            if rt.get("status") not in ENUMS["runtime_status"]:
                err(errors, path, f"{rwhere}: status '{rt.get('status')}' invalid")
            entry = rt.get("entry")
            if not isinstance(entry, dict) or entry.get("kind") not in ENUMS["entry_kind"]:
                err(errors, path, f"{rwhere}: entry.kind required and must be in {sorted(ENUMS['entry_kind'])}")
            check_string(entry, "path", f"{rwhere}.entry", errors, path)
            for q in rt.get("quantizations", []):
                if not isinstance(q, dict) or "id" not in q or "bits" not in q:
                    err(errors, path, f"{rwhere}: quantization entries need id+bits")
                if isinstance(q, dict) and q.get("bits", 0) not in range(1, 65):
                    err(errors, path, f"{rwhere}: quantization bits must be 1..64")
            hw = rt.get("hardware")
            if hw is not None and not isinstance(hw, dict):
                err(errors, path, f"{rwhere}: hardware must be a table")

    deploy = data.get("deploy", {})
    pipeline = deploy.get("pipeline", [])
    if not isinstance(pipeline, list):
        err(errors, path, "deploy.pipeline must be an array of tables")
    seen_stages = set()
    for i, stage in enumerate(pipeline):
        swhere = f"deploy.pipeline[{i}]"
        check_string(stage, "id", swhere, errors, path)
        sid = stage.get("id")
        if sid in seen_stages:
            err(errors, path, f"{swhere}: duplicate stage id '{sid}'")
        seen_stages.add(sid)
        if stage.get("phase") not in ENUMS["pipeline_phase"]:
            err(errors, path, f"{swhere}: phase '{stage.get('phase')}' invalid")
    api = deploy.get("api", {})
    if not isinstance(api, dict) or api.get("task") not in ENUMS["task_kind"]:
        err(errors, path, f"deploy.api.task must be in {sorted(ENUMS['task_kind'])}")
    for side in ("submit", "result"):
        schema = api.get(side, {})
        if not isinstance(schema, dict):
            err(errors, path, f"deploy.api.{side} must be a table")
            continue
        check_string(schema, "name", f"deploy.api.{side}", errors, path)
        for p in schema.get("params", []):
            if not isinstance(p, dict) or "name" not in p or "dtype" not in p:
                err(errors, path, f"deploy.api.{side}.params entries need name+dtype")

    scale = data.get("scale", {})
    if not isinstance(scale, dict):
        err(errors, path, "scale must be a table")
    tiers = scale.get("tiers", [])
    if not isinstance(tiers, list) or not tiers:
        err(errors, path, "scale.tiers must be a non-empty array of tables")
    for i, tier in enumerate(tiers):
        twhere = f"scale.tiers[{i}]"
        check_string(tier, "id", twhere, errors, path)
        for eng in tier.get("engines", []):
            if eng not in ENUMS["engine"]:
                err(errors, path, f"{twhere}: engine '{eng}' invalid")
        if tier.get("placement") not in ENUMS["placement"]:
            err(errors, path, f"{twhere}: placement '{tier.get('placement')}' invalid")

    return errors


def main(argv: list[str]) -> int:
    roots = [Path(p) for p in argv] if argv else [Path("descriptors")]
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(sorted(root.rglob("*.toml")))
        else:
            print(f"{root}: no such file or directory", file=sys.stderr)
            return 1
    failures = 0
    for f in files:
        for msg in validate(f):
            print(msg, file=sys.stderr)
            failures += 1
    print(f"validated {len(files)} descriptor(s), {failures} violation(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
