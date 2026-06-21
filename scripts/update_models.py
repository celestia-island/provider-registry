#!/usr/bin/env python3
"""
Provider Model Update Script

Sync model lists from multiple sources to TOML configuration files. Supports incremental merge (additive only, no deletions).

Sources:
  - OpenRouter API (openrouter): https://openrouter.ai/api/v1/models
  - models.dev API (modelsdev):  https://models.dev/api.json

Usage:
    python scripts/update_models.py --source all --entrypoint-mode
    python scripts/update_models.py --source openrouter --entrypoint-mode
    python scripts/update_models.py --source modelsdev --entrypoint-mode
    python scripts/update_models.py --providers openai,zhipu_glm

Both OpenRouter API and models.dev API do not require API keys and are directly accessible.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent / "utils"))
import cli_format as cf


# ============================================================
# Configuration
# ============================================================

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"
MODELSDEV_API_URL = "https://models.dev/api.json"

# Provider ID mapping: OpenRouter provider -> our provider ID
PROVIDER_ID_MAP = {
    'openai': 'openai',
    'anthropic': 'anthropic',
    'google': 'google',
    'deepseek': 'deepseek',
    'qwen': 'qwen',
    'mistralai': 'mistral',
    'meta-llama': 'meta',
    'minimax': 'minimax',
    'x-ai': 'xai',
    'z-ai': 'zhipu_glm',
    'moonshotai': 'moonshot',
}

# Provider ID mapping: models.dev provider -> our provider ID
# Multiple models.dev providers can map to the same our provider ID
MODELSDEV_PROVIDER_MAP = {
    'openai': 'openai',
    'anthropic': 'anthropic',
    'google': 'google',
    'deepseek': 'deepseek',
    'mistral': 'mistral',
    'xai': 'xai',
    'cohere': 'cohere',
    'openrouter': 'openrouter',
    'stepfun': 'stepfun',
    'minimax': 'minimax',
    'minimax-cn': 'minimax',
    'alibaba': 'qwen',
    'alibaba-cn': 'qwen',
    'alibaba-coding-plan': 'qwen',
    'alibaba-coding-plan-cn': 'qwen',
    'zhipuai': 'zhipu_glm',
    'zhipuai-coding-plan': 'zhipu_glm',
    'moonshotai': 'moonshot',
    'moonshotai-cn': 'moonshot',
    'siliconflow': 'silicon',
    'siliconflow-cn': 'silicon',
    'togetherai': 'together',
    'fireworks-ai': 'fireworks',
}

# When merging pricing from models.dev, prefer these providers (domestic CN first)
MODELSDEV_PREFERRED_ORDER = {
    'qwen': ['alibaba-cn', 'alibaba'],
    'zhipu_glm': ['zhipuai'],
    'moonshot': ['moonshotai'],
    'minimax': ['minimax-cn', 'minimax'],
    'silicon': ['siliconflow-cn', 'siliconflow'],
}

# Only these providers have subscription plans (coding plans)
PROVIDERS_WITH_PLANS = ['openai', 'zhipu_glm', 'anthropic', 'minimax', 'moonshot', 'qwen']

# ============================================================
# Entrypoint Configuration
# ============================================================
# List of entrypoint TOML files that should be updated by this script
# Format: (provider_id, entrypoint_file, description)
# For providers with multiple entrypoints (like GLM), each entrypoint file is listed separately
ENTRYPOINT_FILES = [
    # International Model Developers
    ('anthropic', 'default.toml', 'Anthropic official API'),
    ('openai', 'default.toml', 'OpenAI official API'),
    ('google', 'default.toml', 'Google Gemini official API'),
    ('xai', 'default.toml', 'xAI Grok official API'),
    ('mistral', 'default.toml', 'Mistral AI official API'),
    ('cohere', 'default.toml', 'Cohere official API'),

    # International Platform Operators
    ('openrouter', 'default.toml', 'OpenRouter multi-provider API'),
    ('fireworks', 'default.toml', 'Fireworks AI'),
    ('together', 'default.toml', 'Together AI'),

    # Chinese Model Developers
    ('zhipu_glm', 'default.toml', 'Zhipu GLM domestic one-time'),
    ('zhipu_glm', 'international.toml', 'Zhipu GLM international one-time'),
    ('zhipu_glm', 'coding_lite_domestic.toml', 'Zhipu GLM coding plan lite (domestic)'),
    ('zhipu_glm', 'coding_pro_domestic.toml', 'Zhipu GLM coding plan pro (domestic)'),
    ('zhipu_glm', 'coding_max_domestic.toml', 'Zhipu GLM coding plan max (domestic)'),
    ('zhipu_glm', 'coding_lite_international.toml', 'Zhipu GLM coding plan lite (international)'),
    ('zhipu_glm', 'coding_pro_international.toml', 'Zhipu GLM coding plan pro (international)'),
    ('zhipu_glm', 'coding_max_international.toml', 'Zhipu GLM coding plan max (international)'),
    ('deepseek', 'default.toml', 'DeepSeek official API'),
    ('qwen', 'default.toml', 'Qwen domestic one-time'),
    ('moonshot', 'default.toml', 'Moonshot official API'),
    # Chinese Platform Operators
    ('doubao', 'default.toml', 'Doubao (ByteDance)'),
    ('baidu', 'default.toml', 'Baidu ERNIE'),
    ('stepfun', 'default.toml', 'Stepfun'),
    ('minimax', 'default.toml', 'MiniMax AI'),
    ('silicon', 'default.toml', 'SiliconFlow'),

    # Compatible APIs (these don't need model updates, just config)
    # ('anthropic_compatible', 'default.toml', 'Anthropic-compatible custom endpoint'),
    # ('openai_compatible', 'default.toml', 'OpenAI-compatible custom endpoint'),
    # ('gemini_compatible', 'default.toml', 'Gemini-compatible custom endpoint'),
]

# Providers with multiple entrypoints that need special handling
MULTI_ENTRYPOINT_PROVIDERS = {
    'zhipu_glm': [
        'default.toml',
        'international.toml',
        'coding_lite_domestic.toml',
        'coding_pro_domestic.toml',
        'coding_max_domestic.toml',
        'coding_lite_international.toml',
        'coding_pro_international.toml',
        'coding_max_international.toml',
    ],
}

# For coding plans, select only N curated models for each tier
CODING_PLAN_MODEL_LIMIT = 3

# Model ID prefixes to exclude (deprecated/legacy patterns)
EXCLUDE_PATTERNS = [
    ':extended',
    ':free',
    '-0314',
    '-0613',
    '-1106',
    '-0125',
    '-preview',  # Exclude preview versions unless they're the only version
]

# Tags to add based on model name patterns
TAG_PATTERNS = {
    'flagship': ['gpt-5.4', 'gpt-5-pro', 'o3-pro', 'glm-5', 'claude-4'],
    'reasoning': ['o1', 'o2', 'o3', 'o4', 'reasoner', 'thinking', 'glm-z1'],
    'coding': ['codex', 'coder'],
    'vision': ['vision', 'v-', '-v', '4o', 'multimodal'],
    'cost_effective': ['mini', 'flash', 'air', 'turbo', 'lite'],
    'free': ['free', ':free'],
    'legacy': ['gpt-3', 'gpt-4-', 'glm-4-', 'claude-3-'],
}


# ============================================================
# Data Classes
# ============================================================

@dataclass
class ModelInfo:
    """Model information"""
    id: str
    name: str
    context_window: int
    max_output_tokens: int
    supports_vision: bool = False
    supports_function_calling: bool = True
    supports_streaming: bool = True
    supports_reasoning: bool = False
    tags: List[str] = field(default_factory=list)
    pricing_input: float = 0.0
    pricing_output: float = 0.0
    pricing_cached: float = 0.0

    def to_toml_dict(self) -> Dict[str, Any]:
        """Convert to TOML dictionary format (full format, for individual model files)"""
        result = {
            'id': self.id,
            'name': self.name,
            'context_window': self.context_window,
            'max_output_tokens': self.max_output_tokens,
            'supports_vision': self.supports_vision,
            'supports_function_calling': self.supports_function_calling,
            'supports_streaming': self.supports_streaming,
            'supports_reasoning': self.supports_reasoning,
            'tags': self.tags if self.tags else [],
            'pricing': {
                'input_per_million': self.pricing_input,
                'output_per_million': self.pricing_output,
            },
        }
        if self.pricing_cached > 0:
            result['pricing']['cached_per_million'] = self.pricing_cached
        return result

    def to_entrypoint_dict(self) -> Dict[str, Any]:
        """Convert to entrypoint TOML format (simplified format, only id and pricing)
        Other parameters are read from model files in the models/ directory"""
        result = {
            'id': self.id,
        }
        # Only include pricing if it has values
        if self.pricing_input > 0 or self.pricing_output > 0:
            pricing = {}
            if self.pricing_input > 0:
                pricing['input_per_million'] = self.pricing_input
            if self.pricing_output > 0:
                pricing['output_per_million'] = self.pricing_output
            if self.pricing_cached > 0:
                pricing['cached_per_million'] = self.pricing_cached
            result['pricing'] = pricing
        return result


# ============================================================
# OpenRouter API Client
# ============================================================

def fetch_openrouter_models() -> List[Dict]:
    """Fetch all models from OpenRouter API"""
    cf.info("Fetching model list from OpenRouter API...")

    try:
        req = urllib.request.Request(
            OPENROUTER_API_URL,
            headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())

        models = data.get('data', [])
        print(f"   Found {len(models)} models")
        return models
    except urllib.error.URLError as e:
        cf.err(f"Failed to fetch model list: {e}")
        return []
    except json.JSONDecodeError as e:
        cf.err(f"Failed to parse JSON: {e}")
        return []


def parse_openrouter_model(model_data: Dict) -> Optional[ModelInfo]:
    """Parse OpenRouter model data"""
    model_id = model_data.get('id', '')

    # Skip excluded patterns
    for pattern in EXCLUDE_PATTERNS:
        if pattern in model_id:
            return None

    # Extract provider from model ID (e.g., "openai/gpt-4" -> "openai")
    if '/' not in model_id:
        return None

    provider_prefix, model_name = model_id.split('/', 1)

    # Get pricing (OpenRouter prices are per token, convert to per million)
    pricing = model_data.get('pricing', {})
    prompt_price = float(pricing.get('prompt', 0)) * 1_000_000
    completion_price = float(pricing.get('completion', 0)) * 1_000_000
    cache_price = float(pricing.get('input_cache_read', 0)) * 1_000_000

    # Get context length
    context_length = model_data.get('context_length', 128000)

    # Get architecture info
    arch = model_data.get('architecture', {})
    input_modalities = arch.get('input_modalities', ['text'])
    output_modalities = arch.get('output_modalities', ['text'])

    # Determine capabilities
    supports_vision = 'image' in input_modalities or 'vision' in model_name.lower()
    supports_reasoning = any(p in model_name.lower() for p in ['o1', 'o2', 'o3', 'o4', 'reasoner', 'thinking'])

    # Generate tags based on patterns
    tags = []
    model_lower = model_name.lower()
    for tag, patterns in TAG_PATTERNS.items():
        for pattern in patterns:
            if pattern in model_lower:
                if tag not in tags:
                    tags.append(tag)
                break

    # Remove conflicting tags
    if 'flagship' in tags and 'legacy' in tags:
        tags.remove('legacy')

    # Clean model name: remove provider prefix from API name if present
    api_name = model_data.get('name', model_name)
    # Remove common provider prefixes like "Anthropic: ", "OpenAI: ", "Z.ai: ", etc.
    for prefix in ['Anthropic: ', 'OpenAI: ', 'Google: ', 'Z.ai: ', 'Zhipu: ', 'MoonshotAI: ',
                    'MiniMax: ', 'Qwen: ', 'Mistral AI: ', 'Meta: ', 'DeepSeek: ',
                    'Cohere: ', 'AI21: ', 'Perplexity: ', 'xAI: ']:
        if api_name.startswith(prefix):
            api_name = api_name[len(prefix):]
            break

    return ModelInfo(
        id=model_name,  # Use just the model name without provider prefix
        name=api_name,  # Use cleaned name without provider prefix
        context_window=context_length,
        max_output_tokens=min(context_length // 4, 16384),  # Estimate
        supports_vision=supports_vision,
        supports_function_calling=True,
        supports_streaming=True,
        supports_reasoning=supports_reasoning,
        tags=tags,
        pricing_input=round(prompt_price, 2),
        pricing_output=round(completion_price, 2),
        pricing_cached=round(cache_price, 4) if cache_price > 0 else 0,
    )


def group_models_by_provider(models: List[Dict]) -> Dict[str, List[ModelInfo]]:
    """Group models by provider"""
    result = {}

    for model_data in models:
        model_id = model_data.get('id', '')
        if '/' not in model_id:
            continue

        provider_prefix, _ = model_id.split('/', 1)

        # Map to our provider ID
        our_provider_id = PROVIDER_ID_MAP.get(provider_prefix)
        if not our_provider_id:
            continue

        # Parse model info
        model_info = parse_openrouter_model(model_data)
        if model_info:
            if our_provider_id not in result:
                result[our_provider_id] = []
            result[our_provider_id].append(model_info)

    return result


# ============================================================
# models.dev API Client
# ============================================================

def fetch_modelsdev_models() -> Dict[str, Dict]:
    """Fetch all models from models.dev API, returns {provider_id: {model_id: model_data}}"""
    cf.info("Fetching model list from models.dev API...")

    try:
        req = urllib.request.Request(
            MODELSDEV_API_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": "entelecheia-model-sync/2.0",
            }
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode())

        total_models = sum(len(p.get('models', {})) for p in data.values())
        print(f"   Found {len(data)} providers, {total_models} models")
        return data
    except urllib.error.URLError as e:
        cf.err(f"Failed to fetch models.dev data: {e}")
        return {}
    except json.JSONDecodeError as e:
        cf.err(f"Failed to parse models.dev JSON: {e}")
        return {}


def parse_modelsdev_model(model_data: Dict, provider_id: str) -> Optional[ModelInfo]:
    """Parse models.dev model data"""
    model_id = model_data.get('id', '')
    if not model_id:
        return None

    for pattern in EXCLUDE_PATTERNS:
        if pattern in model_id:
            return None

    cost = model_data.get('cost', {})
    pricing_input = float(cost.get('input', 0))
    pricing_output = float(cost.get('output', 0))
    pricing_cached = float(cost.get('cache_read', 0))

    limit = model_data.get('limit', {})
    context_window = int(limit.get('context', 128000))
    max_output_tokens = int(limit.get('output', min(context_window // 4, 16384)))

    modalities = model_data.get('modalities', {})
    input_modalities = modalities.get('input', ['text'])
    supports_vision = 'image' in input_modalities

    supports_reasoning = bool(model_data.get('reasoning', False))
    supports_function_calling = bool(model_data.get('tool_call', True))

    name = model_data.get('name', model_id)

    tags = []
    model_lower = model_id.lower()
    for tag, patterns in TAG_PATTERNS.items():
        for pattern in patterns:
            if pattern in model_lower:
                if tag not in tags:
                    tags.append(tag)
                break
    if 'flagship' in tags and 'legacy' in tags:
        tags.remove('legacy')

    status = model_data.get('status')
    if status == 'deprecated' and 'deprecated' not in tags:
        tags.append('deprecated')

    return ModelInfo(
        id=model_id,
        name=name,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        supports_vision=supports_vision,
        supports_function_calling=supports_function_calling,
        supports_streaming=True,
        supports_reasoning=supports_reasoning,
        tags=tags,
        pricing_input=round(pricing_input, 4),
        pricing_output=round(pricing_output, 4),
        pricing_cached=round(pricing_cached, 6) if pricing_cached > 0 else 0,
    )


def group_modelsdev_by_provider(raw_data: Dict) -> Dict[str, List[ModelInfo]]:
    """Group models.dev data by our provider ID"""
    result = {}

    for dev_provider_id, provider_data in raw_data.items():
        our_provider_id = MODELSDEV_PROVIDER_MAP.get(dev_provider_id)
        if not our_provider_id:
            continue

        models = provider_data.get('models', {})
        for model_id, model_data in models.items():
            model_info = parse_modelsdev_model(model_data, our_provider_id)
            if model_info:
                if our_provider_id not in result:
                    result[our_provider_id] = []
                result[our_provider_id].append(model_info)

    total = sum(len(v) for v in result.values())
    print(f"   Mapped to {len(result)} local providers, {total} models")
    return result


# ============================================================
# Multi-Source Merge
# ============================================================

def merge_model_dicts(
    existing: Dict[str, ModelInfo],
    openrouter: Dict[str, ModelInfo],
    modelsdev: Dict[str, ModelInfo],
) -> Dict[str, ModelInfo]:
    """Merge model data from multiple sources (additive only, no deletions)

    Priority:
      - pricing: models.dev > OpenRouter > existing
      - capabilities: models.dev > OpenRouter > existing
      - new models: added from any source
      - existing models absent from sources: kept unchanged
    """
    merged = {}

    all_ids = set()
    all_ids.update(existing.keys())
    all_ids.update(openrouter.keys())
    all_ids.update(modelsdev.keys())

    for model_id in sorted(all_ids):
        e = existing.get(model_id)
        o = openrouter.get(model_id)
        m = modelsdev.get(model_id)

        if m:
            base = m
        elif o:
            base = o
        else:
            base = e

        if base is None:
            continue

        merged[model_id] = ModelInfo(
            id=model_id,
            name=base.name,
            context_window=base.context_window,
            max_output_tokens=base.max_output_tokens,
            supports_vision=base.supports_vision,
            supports_function_calling=base.supports_function_calling,
            supports_streaming=base.supports_streaming,
            supports_reasoning=base.supports_reasoning,
            tags=base.tags,
            pricing_input=base.pricing_input,
            pricing_output=base.pricing_output,
            pricing_cached=base.pricing_cached,
        )

        if m:
            # models.dev has priority, but it sometimes omits pricing/context
            # (returns 0) for newly-listed models. Don't let those placeholder
            # zeros clobber the real OpenRouter values — fall back to OpenRouter
            # for any field models.dev reports as 0.
            if m.pricing_input > 0 or o is None or o.pricing_input <= 0:
                merged[model_id].pricing_input = m.pricing_input
            else:
                merged[model_id].pricing_input = o.pricing_input
            if m.pricing_output > 0 or o is None or o.pricing_output <= 0:
                merged[model_id].pricing_output = m.pricing_output
            else:
                merged[model_id].pricing_output = o.pricing_output
            if m.pricing_cached > 0:
                merged[model_id].pricing_cached = m.pricing_cached
            elif o is not None:
                merged[model_id].pricing_cached = o.pricing_cached
            if m.context_window > 0:
                merged[model_id].context_window = m.context_window
            elif o is not None and o.context_window > 0:
                merged[model_id].context_window = o.context_window
            if m.max_output_tokens > 0:
                merged[model_id].max_output_tokens = m.max_output_tokens
            elif o is not None and o.max_output_tokens > 0:
                merged[model_id].max_output_tokens = o.max_output_tokens
            merged[model_id].supports_vision = m.supports_vision
            merged[model_id].supports_reasoning = m.supports_reasoning
            merged[model_id].supports_function_calling = m.supports_function_calling
        elif o:
            if o.pricing_input > 0:
                merged[model_id].pricing_input = o.pricing_input
            if o.pricing_output > 0:
                merged[model_id].pricing_output = o.pricing_output
            if o.pricing_cached > 0:
                merged[model_id].pricing_cached = o.pricing_cached
            if e is None or e.context_window == 128000:
                merged[model_id].context_window = o.context_window

    return merged


def load_existing_models_from_entrypoint(
    provider_id: str, entrypoint_file: str, config_dir: Path
) -> Dict[str, ModelInfo]:
    """Load model list from existing entrypoint file"""
    config = load_entrypoint_config(provider_id, entrypoint_file, config_dir)
    if not config:
        return {}

    result = {}
    models = config.get('entrypoint', {}).get('models', [])
    for m in models:
        mid = m.get('id', '')
        if not mid:
            continue
        pricing = m.get('pricing', {})
        result[mid] = ModelInfo(
            id=mid,
            name=mid,
            context_window=0,
            max_output_tokens=0,
            pricing_input=pricing.get('input_per_million', 0),
            pricing_output=pricing.get('output_per_million', 0),
            pricing_cached=pricing.get('cached_per_million', 0),
        )
    return result


def load_existing_models_from_dir(
    provider_id: str, config_dir: Path
) -> Dict[str, ModelInfo]:
    """Load existing model files from models/ directory"""
    models_dir = config_dir / "models" / provider_id
    if not models_dir.exists():
        return {}

    import toml  # type: ignore[import-untyped]
    result = {}
    for toml_file in sorted(models_dir.glob("*.toml")):
        try:
            with open(toml_file, 'r', encoding='utf-8') as f:
                data = toml.load(f)
            model = data.get('model', {})
            mid = model.get('id', '')
            if not mid:
                continue
            pricing = model.get('pricing', {})
            tags = model.get('tags', [])
            result[mid] = ModelInfo(
                id=mid,
                name=model.get('name', mid),
                context_window=model.get('context_window', 0),
                max_output_tokens=model.get('max_output_tokens', 0),
                supports_vision=model.get('supports_vision', False),
                supports_function_calling=model.get('supports_function_calling', True),
                supports_streaming=model.get('supports_streaming', True),
                supports_reasoning=model.get('supports_reasoning', False),
                tags=tags if tags else [],
                pricing_input=pricing.get('input_per_million', 0),
                pricing_output=pricing.get('output_per_million', 0),
                pricing_cached=pricing.get('cached_per_million', 0),
            )
        except Exception:
            continue
    return result


# ============================================================
# TOML Config Operations
# ============================================================

def get_entrypoint_path(provider_id: str, entrypoint_file: str, config_dir: Path) -> Path:
    """Get entrypoint config file path"""
    return config_dir / "entrypoint" / provider_id / entrypoint_file


def load_entrypoint_config(provider_id: str, entrypoint_file: str, config_dir: Path) -> Optional[Dict]:
    """Load entrypoint config file"""
    config_path = get_entrypoint_path(provider_id, entrypoint_file, config_dir)
    if not config_path.exists():
        return None

    try:
        import toml  # type: ignore[import-untyped]
        with open(config_path, 'r', encoding='utf-8') as f:
            return toml.load(f)
    except Exception as e:
        cf.warn(f"Failed to load {provider_id}/{entrypoint_file}: {e}")
        return None


def load_existing_config(provider_id: str, config_dir: Path) -> Optional[Dict]:
    """Load existing config file (legacy format, kept for backward compatibility)"""
    config_path = config_dir / f"{provider_id}.toml"
    if not config_path.exists():
        return None

    try:
        import toml  # type: ignore[import-untyped]
        with open(config_path, 'r', encoding='utf-8') as f:
            return toml.load(f)
    except Exception as e:
        cf.warn(f"Failed to load {provider_id}.toml: {e}")
        return None


def save_entrypoint_config(provider_id: str, entrypoint_file: str, config: Dict, config_dir: Path):
    """Save entrypoint config file"""
    config_path = get_entrypoint_path(provider_id, entrypoint_file, config_dir)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import toml  # type: ignore[import-untyped]

        with open(config_path, 'w', encoding='utf-8') as f:
            # Write entrypoint section
            if 'entrypoint' in config:
                ep = config['entrypoint']

                # Write header comment
                f.write(f"# {ep.get('name', {}).get('en', provider_id)}\n")
                if 'description' in ep:
                    f.write(f"# {ep['description']}\n")

                # Write [entrypoint] section
                f.write('\n[entrypoint]\n')
                for key, value in ep.items():
                    # Skip keys that are handled separately
                    if key in ['name', 'description', 'defaults', 'models']:
                        continue
                    if isinstance(value, str):
                        f.write(f'{key} = "{value}"\n')
                    elif isinstance(value, bool):
                        f.write(f'{key} = {str(value).lower()}\n')
                    elif isinstance(value, list):
                        f.write(f'{key} = [')
                        f.write(', '.join(f'"{v}"' for v in value))
                        f.write(']\n')
                    elif isinstance(value, dict):
                        f.write(f'\n[entrypoint.{key}]\n')
                        for sk, sv in value.items():
                            if isinstance(sv, str):
                                f.write(f'{sk} = "{sv}"\n')
                            elif isinstance(sv, bool):
                                f.write(f'{sk} = {str(sv).lower()}\n')
                            elif isinstance(sv, (int, float)):
                                f.write(f'{sk} = {sv}\n')
                    else:
                        f.write(f'{key} = {value}\n')

            # Write entrypoint.name section (8 languages)
            if 'entrypoint' in config and 'name' in config['entrypoint']:
                f.write('\n[entrypoint.name]\n')
                names = config['entrypoint']['name']
                for lang in ['en', 'zhs', 'zht', 'ja', 'ko', 'fr', 'es', 'ru']:
                    if lang in names:
                        f.write(f'{lang} = "{names[lang]}"\n')

            # Write entrypoint.defaults section
            if 'entrypoint' in config and 'defaults' in config['entrypoint']:
                f.write('\n[entrypoint.defaults]\n')
                defaults = config['entrypoint']['defaults']
                for tier in ['deep', 'normal', 'basic']:
                    if tier in defaults and defaults[tier] and isinstance(defaults[tier], str):
                        f.write(f'{tier} = "{defaults[tier]}"\n')
                # Write nested dicts (e.g., max_concurrent)
                for key, value in defaults.items():
                    if isinstance(value, dict):
                        f.write(f'\n[entrypoint.defaults.{key}]\n')
                        for sk, sv in value.items():
                            if isinstance(sv, str):
                                f.write(f'{sk} = "{sv}"\n')
                            elif isinstance(sv, bool):
                                f.write(f'{sk} = {str(sv).lower()}\n')
                            else:
                                f.write(f'{sk} = {sv}\n')

            # Write models
            if 'entrypoint' in config and 'models' in config['entrypoint']:
                for model in config['entrypoint']['models']:
                    f.write('\n[[entrypoint.models]]\n')
                    for key, value in model.items():
                        if key == 'pricing':
                            continue
                        if isinstance(value, str):
                            f.write(f'{key} = "{value}"\n')
                        elif isinstance(value, bool):
                            f.write(f'{key} = {str(value).lower()}\n')
                        elif isinstance(value, (int, float)):
                            f.write(f'{key} = {value}\n')
                        elif isinstance(value, list):
                            f.write(f'{key} = [')
                            f.write(', '.join(f'"{v}"' for v in value))
                            f.write(']\n')

                    # Write pricing
                    if 'pricing' in model:
                        f.write('\n[entrypoint.models.pricing]\n')
                        for pk, pv in model['pricing'].items():
                            if isinstance(pv, float):
                                f.write(f'{pk} = {pv}\n')
                            else:
                                f.write(f'{pk} = {pv}\n')

        cf.ok(f"Saved {provider_id}/{entrypoint_file}")
    except Exception as e:
        cf.err(f"Failed to save {provider_id}/{entrypoint_file}: {e}")


def save_config(provider_id: str, config: Dict, config_dir: Path):
    """Save config file, maintaining correct TOML order (provider first, models after)"""
    config_path = config_dir / f"{provider_id}.toml"

    try:
        import toml  # type: ignore[import-untyped]

        # Separate provider config and models
        provider_config = {k: v for k, v in config.items() if k != 'models'}
        models_list = config.get('models', [])

        with open(config_path, 'w', encoding='utf-8') as f:
            # Write provider config first
            if provider_config:
                toml.dump(provider_config, f)
                f.write('\n')

            # Write models last
            if models_list:
                for model in models_list:
                    # Write model as [[models]]
                    f.write('[[models]]\n')
                    model_copy = {k: v for k, v in model.items() if k != 'pricing'}
                    for key, value in model_copy.items():
                        if isinstance(value, str):
                            f.write(f'{key} = "{value}"\n')
                        elif isinstance(value, bool):
                            f.write(f'{key} = {str(value).lower()}\n')
                        elif isinstance(value, (int, float)):
                            f.write(f'{key} = {value}\n')
                        elif isinstance(value, list):
                            f.write(f'{key} = [')
                            f.write(', '.join(f'"{v}"' for v in value))
                            f.write(']\n')

                    # Write pricing
                    if 'pricing' in model:
                        f.write('\n[models.pricing]\n')
                        for pk, pv in model['pricing'].items():
                            if isinstance(pv, float):
                                f.write(f'{pk} = {pv}\n')
                            else:
                                f.write(f'{pk} = {pv}\n')

                    f.write('\n')

        cf.ok(f"Saved {provider_id}.toml")
    except Exception as e:
        cf.err(f"Failed to save {provider_id}.toml: {e}")


def save_individual_model_files(
    provider_id: str,
    models: List[ModelInfo],
    config_dir: Path,
) -> int:
    """Save individual TOML files for each model to the models/ directory"""
    models_dir = config_dir / "models" / provider_id
    models_dir.mkdir(parents=True, exist_ok=True)

    saved_count = 0

    for model in models:
        # Sanitize model ID for filename (replace / and other special chars)
        filename = model.id.replace('/', '_').replace(':', '-') + '.toml'
        model_path = models_dir / filename

        try:
            with open(model_path, 'w', encoding='utf-8') as f:
                # Write header comment
                f.write(f"# {model.name}\n")
                f.write(f"# Provider: {provider_id}\n")
                f.write(f"\n")

                # Write [model] section
                f.write("[model]\n")
                f.write(f'id = "{model.id}"\n')
                f.write(f'name = "{model.name}"\n')
                f.write(f'provider_id = "{provider_id}"\n')
                f.write(f'\n')

                # Write capabilities
                f.write(f"context_window = {model.context_window}\n")
                f.write(f"max_output_tokens = {model.max_output_tokens}\n")
                f.write(f"supports_vision = {str(model.supports_vision).lower()}\n")
                f.write(f"supports_function_calling = {str(model.supports_function_calling).lower()}\n")
                f.write(f"supports_streaming = {str(model.supports_streaming).lower()}\n")
                f.write(f"supports_reasoning = {str(model.supports_reasoning).lower()}\n")

                # Write tags
                if model.tags:
                    tags_str = '", "'.join(model.tags)
                    f.write(f'tags = ["{tags_str}"]\n')
                else:
                    f.write(f"tags = []\n")

                # Write pricing
                f.write(f"\n[model.pricing]\n")
                f.write(f"input_per_million = {model.pricing_input}\n")
                f.write(f"output_per_million = {model.pricing_output}\n")
                if model.pricing_cached > 0:
                    f.write(f"cached_per_million = {model.pricing_cached}\n")

            saved_count += 1
        except Exception as e:
            cf.err(f"Failed to save model file {filename}: {e}")

    cf.ok(f"Saved {saved_count} model files for {provider_id}")
    return saved_count


def select_tier_models_from_list(models: List[ModelInfo], limit: int = 3) -> Dict[str, str]:
    """Select N curated models from the list for each request tier"""
    import re

    # Sort models by capability and price
    def extract_version(model_id: str) -> float:
        match = re.search(r'(\d+)(?:\.(\d+))?', model_id.lower())
        if match:
            major = float(match.group(1))
            minor = float(match.group(2)) if match.group(2) else 0.0
            return major + minor / 10.0
        return 0.0

    def sort_key(m: ModelInfo):
        has_reasoning = m.supports_reasoning and 'legacy' not in m.tags
        has_flagship = 'flagship' in m.tags and 'legacy' not in m.tags
        version = extract_version(m.id)
        price = m.pricing_input
        # Sort: reasoning first, then flagship, then by version DESC, then by price DESC
        return (not has_reasoning, not has_flagship, -version, -price)

    sorted_models = sorted(models, key=sort_key)

    # Select models for each tier
    reasoning_models = [m for m in sorted_models if m.supports_reasoning and 'legacy' not in m.tags]
    flagship_models = [m for m in sorted_models if 'flagship' in m.tags and not m.supports_reasoning and 'legacy' not in m.tags]
    cost_effective_models = [m for m in sorted_models if 'cost_effective' in m.tags or 'free' in m.tags]

    # Deep tier: best reasoning or flagship
    if reasoning_models:
        deep = reasoning_models[0].id
    elif flagship_models:
        deep = flagship_models[0].id
    else:
        deep = sorted_models[0].id if sorted_models else None

    # Normal tier: flagship or high-end model (not cheapest)
    if flagship_models and len(flagship_models) > 1:
        normal = flagship_models[1].id
    elif flagship_models:
        normal = flagship_models[0].id
    elif cost_effective_models and len(cost_effective_models) > 1:
        # Pick second best cost_effective for normal tier
        normal = cost_effective_models[1].id
    else:
        normal = sorted_models[1].id if len(sorted_models) > 1 else deep

    # Basic tier: most cost-effective
    if cost_effective_models:
        basic = cost_effective_models[0].id
    else:
        basic = sorted_models[-1].id if sorted_models else normal

    return {
        'deep': deep,
        'normal': normal,
        'basic': basic,
    }


def update_entrypoint_config(
    provider_id: str,
    entrypoint_file: str,
    models: List[ModelInfo],
    config_dir: Path,
    is_coding_plan: bool = False,
    update_defaults: bool = True,
) -> bool:
    """Update entrypoint config file"""
    is_coding_plan_entrypoint = any(x in entrypoint_file for x in ['coding_lite', 'coding_pro', 'coding_max'])

    cf.header(f"Updating Entrypoint: {provider_id}/{entrypoint_file}")
    if is_coding_plan_entrypoint:
        cf.info("Coding plan - selecting 3 models")

    # Load existing config
    existing_config = load_entrypoint_config(provider_id, entrypoint_file, config_dir)
    if not existing_config:
        cf.warn("Config file does not exist, skipping")
        return False

    # Keep existing entrypoint config, only update models
    config = existing_config.copy()

    # For coding plans, limit models to a curated selection
    if is_coding_plan_entrypoint:
        # Select top 3 models for each tier
        selected = select_tier_models_from_list(models, limit=3)
        # Get selected model objects
        selected_models = []
        for tier in ['deep', 'normal', 'basic']:
            model_id = selected.get(tier)
            if model_id:
                for m in models:
                    if m.id == model_id:
                        selected_models.append(m)
                        break

        # Remove duplicates while preserving order
        seen = set()
        unique_models = []
        for m in selected_models:
            if m.id not in seen:
                seen.add(m.id)
                unique_models.append(m)

        # Use simplified entrypoint format (only id + pricing)
        config['entrypoint']['models'] = [m.to_entrypoint_dict() for m in unique_models]

        # Update defaults to match selected models
        if update_defaults and 'defaults' in config['entrypoint']:
            old_defaults = config['entrypoint']['defaults']
            preserved_keys = {
                k: v for k, v in old_defaults.items()
                if k not in ('deep', 'normal', 'basic')
            }
            config['entrypoint']['defaults'] = {**selected, **preserved_keys}

        print(f"   Selected {len(unique_models)} models:")
        for m in unique_models:
            print(f"   - {m.id}: {m.name}")
    else:
        # For non-coding plans, include all models (up to 50)
        def model_sort_key(m: ModelInfo):
            has_recommended = 'recommended' in m.tags or 'flagship' in m.tags
            has_legacy = 'legacy' in m.tags
            return (not has_recommended, has_legacy, -m.pricing_input, m.name.lower())

        sorted_models = sorted(models, key=model_sort_key)

        # Deduplicate
        seen_ids = set()
        unique_models = []
        for model in sorted_models:
            if model.id not in seen_ids:
                seen_ids.add(model.id)
                unique_models.append(model)

        # Use simplified entrypoint format (only id + pricing)
        config['entrypoint']['models'] = [m.to_entrypoint_dict() for m in unique_models[:50]]

        print(f"   Updated {len(unique_models[:50])} models")

        # Update defaults
        if update_defaults:
            selected = select_tier_models_from_list(unique_models, limit=3)
            if 'defaults' in config['entrypoint']:
                old_defaults = config['entrypoint']['defaults']
                preserved_keys = {
                    k: v for k, v in old_defaults.items()
                    if k not in ('deep', 'normal', 'basic')
                }
                config['entrypoint']['defaults'] = {**selected, **preserved_keys}

            cf.ok("Default models selected:")
            print(f"      Deep:   {selected.get('deep', 'N/A')}")
            print(f"      Normal: {selected.get('normal', 'N/A')}")
            print(f"      Basic:  {selected.get('basic', 'N/A')}")

    # Save
    save_entrypoint_config(provider_id, entrypoint_file, config, config_dir)
    return True


def update_provider_config(
    provider_id: str,
    models: List[ModelInfo],
    config_dir: Path,
    update_defaults: bool = False
) -> bool:
    """Update provider config file"""
    cf.header(f"Updating Provider: {provider_id}")

    if not update_defaults:
        cf.info("Skipping default model update (use --update-defaults to enable)")

    # Load existing config
    existing_config = load_existing_config(provider_id, config_dir)
    if not existing_config:
        cf.warn("Config file does not exist, skipping")
        return False

    # Keep existing provider config, only update models
    config = existing_config.copy()

    # Sort models: recommended/flagship first, then by version (newer first), then by name
    def extract_version(model_id: str) -> tuple:
        """Extract version tuple for sorting (higher versions first)"""
        import re
        # Match patterns like qwen3.5, gpt-5.4, glm-4.7
        match = re.search(r'(\d+)(?:\.(\d+))?', model_id)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2)) if match.group(2) else 0
            return (major, minor)
        return (0, 0)

    def model_sort_key(m: ModelInfo):
        has_recommended = 'recommended' in m.tags or 'flagship' in m.tags
        has_legacy = 'legacy' in m.tags
        version = extract_version(m.id)
        # Sort: recommended first, then by version DESCENDING (newer first), then alphabetically
        return (not has_recommended, has_legacy, (-version[0], -version[1]), m.name.lower())

    sorted_models = sorted(models, key=model_sort_key)

    # Deduplicate by ID (keep first occurrence)
    seen_ids = set()
    unique_models = []
    for model in sorted_models:
        if model.id not in seen_ids:
            seen_ids.add(model.id)
            unique_models.append(model)

    # Convert to TOML format
    config['models'] = [m.to_toml_dict() for m in unique_models[:50]]  # Limit to 50 models

    print(f"   Updated {len(unique_models[:50])} models")
    for m in unique_models[:5]:
        print(f"   - {m.id}: {m.name}")

    # Update default models with smart selection (only if --update-defaults is set)
    if update_defaults and 'provider' in config:
        # Clean up any duplicate top-level defaults (keep only defaults.models)
        defaults = config['provider'].get('defaults', {})

        # Remove top-level tier keys if models sub-dict exists
        if 'models' in defaults:
            for tier in ['deep', 'normal', 'basic']:
                if tier in defaults and tier in defaults['models']:
                    del defaults[tier]  # Remove duplicate

        if 'models' not in defaults:
            defaults['models'] = {}

        cf.info("Using simple heuristic algorithm to select default models...")

        # Smart model selection for each tier
        reasoning_models = [m for m in unique_models if m.supports_reasoning and 'legacy' not in m.tags]
        coding_models = [m for m in unique_models if 'coding' in m.tags and 'legacy' not in m.tags]
        flagship_models = [m for m in unique_models if 'flagship' in m.tags and not m.supports_reasoning and 'legacy' not in m.tags]
        # Regular models: those without special tags (but not cost_effective only)
        regular_models = [m for m in unique_models if not m.supports_reasoning and 'legacy' not in m.tags
                        and 'flagship' not in m.tags and 'cost_effective' not in m.tags]
        basic_models = [m for m in unique_models if 'cost_effective' in m.tags]

        # Helper function to extract version number from model ID
        def extract_version_numeric(model_id: str) -> float:
            """Extract numeric version from model ID (e.g., m2.7 -> 2.7, m1 -> 1.0)"""
            import re
            # Match patterns like m2.7, m2.5, glm-4.7, gpt-5.4
            match = re.search(r'(\d+)(?:\.(\d+))?', model_id.lower())
            if match:
                major = float(match.group(1))
                minor = float(match.group(2)) if match.group(2) else 0.0
                return major + minor / 10.0
            return 0.0

        # Deep tier: reasoning models first, then flagship, then by version/price
        if reasoning_models:
            reasoning_models.sort(key=lambda m: m.pricing_input, reverse=True)
            defaults['models']['deep'] = reasoning_models[0].id
        elif flagship_models:
            flagship_models.sort(key=lambda m: m.pricing_input, reverse=True)
            defaults['models']['deep'] = flagship_models[0].id
        elif regular_models:
            regular_models.sort(key=lambda m: m.pricing_input, reverse=True)
            defaults['models']['deep'] = regular_models[0].id
        else:
            # Fallback: use cost_effective models, but pick by version and context
            # Prefer higher version numbers and larger context windows
            if basic_models:
                basic_models.sort(key=lambda m: (extract_version_numeric(m.id), m.context_window, m.pricing_input), reverse=True)
                defaults['models']['deep'] = basic_models[0].id

        # Normal tier: coding models first (for coding plans), then flagship, then balanced
        if coding_models:
            coding_models.sort(key=lambda m: m.pricing_input, reverse=True)
            defaults['models']['normal'] = coding_models[0].id
        elif flagship_models:
            flagship_models.sort(key=lambda m: m.pricing_input, reverse=True)
            defaults['models']['normal'] = flagship_models[0].id
        elif regular_models:
            regular_models.sort(key=lambda m: m.pricing_input, reverse=True)
            defaults['models']['normal'] = regular_models[0].id
        else:
            # Fallback: use cost_effective models with balanced performance
            # Prefer high version but not highest price (balanced tier)
            if basic_models:
                # Sort by version DESC, then by price ASC (prefer balanced options)
                balanced_models = sorted(basic_models,
                                       key=lambda m: (-extract_version_numeric(m.id), m.pricing_input))
                # Pick the second or third highest version for "normal" tier (balanced)
                if len(balanced_models) >= 2:
                    # For MiniMax: M2.7 is highest version, pick second highest or same version with lower price
                    defaults['models']['normal'] = balanced_models[1].id if balanced_models[1].pricing_input < balanced_models[0].pricing_input else balanced_models[0].id
                else:
                    defaults['models']['normal'] = balanced_models[0].id

        # Basic tier: cost-effective models (cheapest)
        if basic_models:
            basic_models.sort(key=lambda m: m.pricing_input)  # ASC for basic
            defaults['models']['basic'] = basic_models[0].id
        elif regular_models:
            # Fallback to cheapest regular model
            regular_models.sort(key=lambda m: m.pricing_input)
            defaults['models']['basic'] = regular_models[0].id if regular_models else None

        cf.ok("Default models selected:")
        print(f"      Deep:   {defaults['models'].get('deep', 'N/A')}")
        print(f"      Normal: {defaults['models'].get('normal', 'N/A')}")
        print(f"      Basic:  {defaults['models'].get('basic', 'N/A')}")

        config['provider']['defaults'] = defaults

    # Save
    save_config(provider_id, config, config_dir)
    return True


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Multi-source sync provider model config (OpenRouter + models.dev)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Sources:
  openrouter  - OpenRouter API (default)
  modelsdev   - models.dev API (SST/Anomaly)
  all         - Fetch both and merge (models.dev pricing priority)

Examples:
  # Sync from all sources (recommended)
  python scripts/update_models.py --source all --entrypoint-mode

  # Sync from models.dev only
  python scripts/update_models.py --source modelsdev --entrypoint-mode

  # Sync from OpenRouter only (legacy behavior)
  python scripts/update_models.py --entrypoint-mode

  # Sync specific providers
  python scripts/update_models.py --source all --providers openai,zhipu_glm

  # Generate individual model files
  python scripts/update_models.py --source all --individual-models
        """
    )
    parser.add_argument(
        '--source',
        type=str,
        default='openrouter',
        choices=['openrouter', 'modelsdev', 'all'],
        help='Data source: openrouter (default), modelsdev, all (merge both)'
    )
    parser.add_argument(
        '--providers',
        type=str,
        default='all',
        help='Providers to update (comma-separated, default all)'
    )
    parser.add_argument(
        '--config-dir',
        type=str,
        default='.',
        help='Config file directory (defaults to repository root)'
    )
    parser.add_argument(
        '--entrypoint-mode',
        action='store_true',
        help='Use new entrypoint mode to update config (recommended)'
    )
    parser.add_argument(
        '--with-plans-only',
        action='store_true',
        help='Only update providers with subscription plans'
    )
    parser.add_argument(
        '--update-defaults',
        action='store_true',
        help='Update default model selection (enabled by default in entrypoint mode)'
    )
    parser.add_argument(
        '--individual-models',
        action='store_true',
        help='Generate individual model toml files in the models/ directory (one file per model)'
    )

    args = parser.parse_args()

    use_entrypoint_mode = args.entrypoint_mode
    if not use_entrypoint_mode:
        cf.warn("It is recommended to use --entrypoint-mode to update the new entrypoint config")
        cf.info("Using legacy mode to update configs in res/ directory...")

    config_dir = Path(__file__).parent.parent / args.config_dir
    config_dir.mkdir(parents=True, exist_ok=True)

    update_defaults = args.update_defaults or use_entrypoint_mode

    source = args.source

    # === Fetch from sources ===
    or_models_by_provider: Dict[str, Dict[str, ModelInfo]] = {}
    md_models_by_provider: Dict[str, Dict[str, ModelInfo]] = {}

    if source in ('openrouter', 'all'):
        cf.header("Source 1/2: OpenRouter")
        all_or_models = fetch_openrouter_models()
        if all_or_models:
            grouped = group_models_by_provider(all_or_models)
            or_models_by_provider = {
                pid: {m.id: m for m in models}
                for pid, models in grouped.items()
            }
            print(f"   {len(or_models_by_provider)} providers, "
                  f"{sum(len(v) for v in or_models_by_provider.values())} models")
        elif source == 'openrouter':
            cf.err("Failed to fetch OpenRouter model list")
            sys.exit(1)

    if source in ('modelsdev', 'all'):
        cf.header("Source 2/2: models.dev")
        raw_md = fetch_modelsdev_models()
        if raw_md:
            grouped = group_modelsdev_by_provider(raw_md)
            md_models_by_provider = {
                pid: {m.id: m for m in models}
                for pid, models in grouped.items()
            }
            print(f"   {len(md_models_by_provider)} providers, "
                  f"{sum(len(v) for v in md_models_by_provider.values())} models")
        elif source == 'modelsdev':
            cf.err("Failed to fetch models.dev data")
            sys.exit(1)

    # === Determine merged models per provider ===
    def get_merged_models(provider_id: str) -> List[ModelInfo]:
        or_models = or_models_by_provider.get(provider_id, {})
        md_models = md_models_by_provider.get(provider_id, {})

        if source == 'all' and use_entrypoint_mode:
            existing_ep = {}
            for _, ep_file, _ in ENTRYPOINT_FILES:
                if _ == provider_id:
                    existing_ep = load_existing_models_from_entrypoint(
                        provider_id, ep_file, config_dir
                    )
                    break
            existing_models_dir = load_existing_models_from_dir(provider_id, config_dir)
            existing = {**existing_ep, **existing_models_dir}

            merged = merge_model_dicts(existing, or_models, md_models)
            return list(merged.values())
        elif source == 'all':
            existing = load_existing_models_from_dir(provider_id, config_dir)
            merged = merge_model_dicts(existing, or_models, md_models)
            return list(merged.values())
        elif source == 'modelsdev':
            if not md_models:
                return []
            return list(md_models.values())
        else:
            if not or_models:
                return []
            return list(or_models.values())

    def get_merged_models_dict(provider_id: str) -> Dict[str, ModelInfo]:
        return {m.id: m for m in get_merged_models(provider_id)}

    # === Determine target providers ===
    if args.providers == 'all':
        all_provider_ids = set()
        all_provider_ids.update(or_models_by_provider.keys())
        all_provider_ids.update(md_models_by_provider.keys())
        target_providers = sorted(all_provider_ids)
    else:
        target_providers = [p.strip() for p in args.providers.split(',')]

    # === Entrypoint mode ===
    if use_entrypoint_mode:
        success_count = 0

        if args.providers == 'all':
            entrypoints_to_update = ENTRYPOINT_FILES
        else:
            entrypoints_to_update = [
                (p, f, d) for p, f, d in ENTRYPOINT_FILES
                if p in target_providers
            ]

        for provider_id, entrypoint_file, description in entrypoints_to_update:
            models = get_merged_models(provider_id)
            if not models:
                cf.warn(f"Skipping {provider_id}/{entrypoint_file} (no model data found)")
                continue

            is_coding_plan = 'coding_' in entrypoint_file

            success = update_entrypoint_config(
                provider_id,
                entrypoint_file,
                models,
                config_dir,
                is_coding_plan=is_coding_plan,
                update_defaults=update_defaults,
            )
            if success:
                success_count += 1

        cf.header(f"Done! Successfully updated {success_count}/{len(entrypoints_to_update)} entrypoint files (source={source})")
    else:
        if args.providers == 'all':
            if args.with_plans_only:
                filtered = PROVIDERS_WITH_PLANS
            else:
                filtered = target_providers
        else:
            filtered = target_providers

        success_count = 0
        for provider_id in filtered:
            models = get_merged_models(provider_id)
            if not models:
                cf.warn(f"No models found for {provider_id}")
                continue

            success = update_provider_config(
                provider_id,
                models,
                config_dir / "res",
                update_defaults=update_defaults
            )
            if success:
                success_count += 1

        cf.header(f"Done! Successfully updated {success_count}/{len(filtered)} providers (source={source})")

    # === Individual model files ===
    if args.individual_models:
        cf.header("Generating individual model toml files (incremental mode)...")

        total_models = 0
        for provider_id in target_providers:
            models_dict = get_merged_models_dict(provider_id)
            if not models_dict:
                continue

            existing = load_existing_models_from_dir(provider_id, config_dir)

            merged = merge_model_dicts(existing, {}, models_dict)
            models_list = list(merged.values())

            count = save_individual_model_files(provider_id, models_list, config_dir)
            total_models += count

        cf.header(f"Done! Generated {total_models} model files for {len(target_providers)} providers")


if __name__ == '__main__':
    main()
