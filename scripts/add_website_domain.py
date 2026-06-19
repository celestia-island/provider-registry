#!/usr/bin/env python3
"""Add the mandatory `website_domain` field to provider configs.

`website_domain` is the canonical provider domain used in AI-agent co-author
trailers (e.g. ``zhipu.ai``, ``deepseek.com``). It is MANDATORY for every real
provider and is NOT derived from the API base_url (a provider may expose several
base_url hosts, e.g. zhipu_glm uses both open.bigmodel.cn and api.z.ai but the
canonical domain is zhipu.ai).

This script is idempotent: it only inserts the field where missing.
"""

from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parent.parent

# provider_id -> canonical website_domain (the "who served the model" identity)
WEBSITE_DOMAIN = {
    "anthropic": "anthropic.com",
    "openai": "openai.com",
    "google": "google.com",
    "xai": "x.ai",
    "mistral": "mistral.ai",
    "cohere": "cohere.com",
    "openrouter": "openrouter.ai",
    "fireworks": "fireworks.ai",
    "together": "together.ai",
    "cerebras": "cerebras.ai",
    "perplexity": "perplexity.ai",
    "zhipu_glm": "zhipu.ai",
    "deepseek": "deepseek.com",
    "qwen": "alibaba.com",
    "moonshot": "moonshot.cn",
    "doubao": "volcengine.com",
    "baidu": "baidu.com",
    "stepfun": "stepfun.com",
    "minimax": "minimax.chat",
    "silicon": "siliconflow.cn",
    "hunyuan": "tencent.com",
    "yi": "01.ai",
    "baichuan": "baichuan-ai.com",
    "mimo": "xiaomi.com",
}

# Custom / "bring your own endpoint" protocol templates: no fixed canonical domain.
COMPATIBLE_PROVIDERS = {
    "openai_compatible",
    "anthropic_compatible",
    "gemini_compatible",
}


def insert_after_line(lines, anchor_pattern, new_line):
    """Insert new_line after the first line matching anchor_pattern, if new_line absent."""
    text = "".join(lines)
    if "website_domain" in text:
        return lines, False
    for i, line in enumerate(lines):
        if re.search(anchor_pattern, line):
            indent = re.match(r"(\s*)", line).group(1)
            lines.insert(i + 1, f"{indent}website_domain = \"{new_line.split('\"')[1]}\"\n")
            return lines, True
    return lines, False


def process_entrypoints():
    changed = []
    ep_dir = REPO_ROOT / "entrypoint"
    for toml_file in sorted(ep_dir.glob("*/*.toml")):
        text = toml_file.read_text(encoding="utf-8")
        m = re.search(r'^provider_id\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if not m:
            print(f"SKIP (no provider_id): {toml_file.relative_to(REPO_ROOT)}")
            continue
        pid = m.group(1)
        if pid in COMPATIBLE_PROVIDERS:
            print(f"SKIP (compatible template): {toml_file.relative_to(REPO_ROOT)}")
            continue
        domain = WEBSITE_DOMAIN.get(pid)
        if not domain:
            print(f"WARN (unknown provider_id {pid!r}): {toml_file.relative_to(REPO_ROOT)}")
            continue
        if "website_domain" in text:
            continue
        lines = text.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if re.match(r'^provider_id\s*=', line):
                lines.insert(i + 1, f'website_domain = "{domain}"\n')
                break
        toml_file.write_text("".join(lines), encoding="utf-8")
        changed.append(toml_file.relative_to(REPO_ROOT))
    return changed


def process_registry():
    reg = REPO_ROOT / "registry.toml"
    text = reg.read_text(encoding="utf-8")
    changed = []
    lines = text.splitlines(keepends=True)
    out = []
    i = 0
    while i < len(lines):
        out.append(lines[i])
        m = re.match(r'^(\s*)id\s*=\s*"([^"]+)"', lines[i])
        if m and "website_domain" not in "".join(lines[i : i + 4]):
            pid = m.group(2)
            if pid in WEBSITE_DOMAIN:
                out.append(f'{m.group(1)}website_domain = "{WEBSITE_DOMAIN[pid]}"\n')
                changed.append(pid)
        i += 1
    if "mimo" not in text:
        out.append('\n[[chinese.developers]]\n')
        out.append('id = "mimo"\n')
        out.append('name = "Xiaomi MiMo"\n')
        out.append('website_domain = "xiaomi.com"\n')
        out.append('description = "Xiaomi MiMo models"\n')
        out.append('is_official = false\n')
        changed.append("mimo(registry add)")
    reg.write_text("".join(out), encoding="utf-8")
    return changed


def main():
    print("== registry.toml ==")
    reg_changed = process_registry()
    for c in reg_changed:
        print(f"  + {c}")
    print("== entrypoints ==")
    ep_changed = process_entrypoints()
    for c in ep_changed:
        print(f"  + {c}")
    print(f"\nDone. registry changes: {len(reg_changed)}, entrypoint files: {len(ep_changed)}")


if __name__ == "__main__":
    main()
