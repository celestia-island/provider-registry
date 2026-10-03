#!/usr/bin/env python3
"""Behavioral pins for merge_model_dicts' two precedence regimes.

Run: python3 scripts/test_update_models.py  (exit 0 = all green)

The curation-first regime is the load-bearing one: the billing rate card
is hand-verified official data (PR #14), and the scheduled sync must never
clobber it with aggregator FX noise or stale mirror prices.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import update_models as um  # noqa: E402


def M(**kw):
    base = dict(
        id="x",
        name="X",
        context_window=1000000,
        max_output_tokens=128000,
        supports_vision=False,
        tags=[],
        pricing_input=0.0,
        pricing_output=0.0,
        pricing_cached=0.0,
        score=None,
        score_source="",
    )
    base.update(kw)
    return um.ModelInfo(**base)


def merge(existing, orr, md, **kw):
    return um.merge_model_dicts(existing, orr, md, **kw)


def test_official_price_survives_aggregator():
    out = merge(
        {"glm-5.3": M(id="glm-5.3", pricing_input=1.23, pricing_output=4.31, pricing_cached=0.31, tags=["flagship"])},
        {"glm-5.3": M(id="glm-5.3", pricing_input=1.4, pricing_output=4.4, pricing_cached=0.26)},
        {},
    )
    c = out["glm-5.3"]
    assert (c.pricing_input, c.pricing_output, c.pricing_cached) == (1.23, 4.31, 0.31), c
    assert c.tags == ["flagship"], c.tags


def test_blank_fields_get_filled():
    out = merge(
        {"glm-5.3": M(id="glm-5.3", pricing_input=0.0, pricing_output=0.0, pricing_cached=0.0)},
        {"glm-5.3": M(id="glm-5.3", pricing_input=1.4, pricing_output=4.4, pricing_cached=0.26)},
        {},
    )
    c = out["glm-5.3"]
    assert (c.pricing_input, c.pricing_output, c.pricing_cached) == (1.4, 4.4, 0.26), c


def test_new_model_added_from_source():
    out = merge({}, {"gpt-6-astra": M(id="gpt-6-astra", pricing_input=10.0, pricing_output=50.0, pricing_cached=1.0)}, {})
    c = out["gpt-6-astra"]
    assert (c.pricing_input, c.pricing_output, c.pricing_cached) == (10.0, 50.0, 1.0), c


def test_score_curation_and_fill():
    out = merge(
        {
            "a": M(id="a", score=77.0, score_source="design_arena"),
            "b": M(id="b", score=None),
        },
        {
            "a": M(id="a", score=63.3, score_source="openrouter"),
            "b": M(id="b", score=63.3, score_source="openrouter"),
        },
        {},
    )
    assert (out["a"].score, out["a"].score_source) == (77.0, "design_arena"), out["a"]
    assert (out["b"].score, out["b"].score_source) == (63.3, "openrouter"), out["b"]


def test_absent_from_sources_kept():
    out = merge({"old": M(id="old", pricing_input=2.0)}, {}, {})
    assert out["old"].pricing_input == 2.0


def test_capability_flags_curation():
    out = merge(
        {"m": M(id="m", supports_vision=False, supports_reasoning=True)},
        {"m": M(id="m", supports_vision=True, supports_reasoning=False)},
        {"m": M(id="m", supports_vision=True, supports_reasoning=False)},
    )
    assert out["m"].supports_vision is False and out["m"].supports_reasoning is True


def test_legacy_flag_restores_overwrite():
    out = merge(
        {"glm-5.3": M(id="glm-5.3", pricing_input=1.23, tags=["flagship"])},
        {"glm-5.3": M(id="glm-5.3", pricing_input=1.4, pricing_output=4.4)},
        {},
        aggregator_priority=True,
    )
    assert out["glm-5.3"].pricing_input == 1.4, out["glm-5.3"]


def test_curation_fill_skips_modelsdev_placeholder_zeros():
    # models.dev reports 0 for a blank field — the fill must fall through
    # to OpenRouter's real value instead of stamping the zero.
    out = merge(
        {"m": M(id="m", pricing_input=0.0, pricing_cached=0.0)},
        {"m": M(id="m", pricing_input=2.5, pricing_cached=0.25)},
        {"m": M(id="m", pricing_input=0.0, pricing_cached=0.0)},
    )
    assert (out["m"].pricing_input, out["m"].pricing_cached) == (2.5, 0.25), out["m"]


def test_curation_context_and_maxout_fill():
    out = merge(
        {"m": M(id="m", context_window=0, max_output_tokens=0)},
        {"m": M(id="m", context_window=1050000, max_output_tokens=128000)},
        {"m": M(id="m", context_window=0, max_output_tokens=0)},
    )
    assert out["m"].context_window == 1050000 and out["m"].max_output_tokens == 128000, out["m"]
    # All sources blank → the (meaningless) original survives the `or` guard
    # rather than being replaced by 0.
    out2 = merge(
        {"n": M(id="n", context_window=0, max_output_tokens=0)},
        {"n": M(id="n", context_window=0, max_output_tokens=0)},
        {},
    )
    assert out2["n"].context_window == 0, out2["n"]


def test_curation_tags_fill_prefers_modelsdev():
    out = merge(
        {"m": M(id="m", tags=[])},
        {"m": M(id="m", tags=["from-or"])},
        {"m": M(id="m", tags=["from-md"])},
    )
    assert out["m"].tags == ["from-md"], out["m"].tags
    out2 = merge(
        {"m": M(id="m", tags=[])},
        {"m": M(id="m", tags=["from-or"])},
        {},
    )
    assert out2["m"].tags == ["from-or"], out2["m"].tags


def test_entrypoint_block_curation_and_retention():
    new = [{"id": "glm-5.3", "supports_vision": True, "pricing": {"input_per_million": 1.4}}]
    old = [
        {"id": "glm-5.3", "supports_vision": False, "pricing": {"input_per_million": 1.23}},
        {"id": "glm-5.2", "supports_vision": False, "pricing": {"input_per_million": 1.23}},
    ]
    out = um.curate_entrypoint_blocks(new, old)
    assert out[0]["pricing"]["input_per_million"] == 1.23 and out[0]["supports_vision"] is False
    assert [b["id"] for b in out] == ["glm-5.3", "glm-5.2"], out  # retention, never dropped


def test_coding_plan_tier_stability():
    sel = um.preserve_coding_plan_tiers(
        {"deep": "glm-5.3", "normal": "glm-5.2"},
        {"deep": "glm-5.3-prime", "normal": "glm-5.3-prime", "basic": None},
        {"glm-5.3", "glm-5.2", "glm-5.3-prime"},
    )
    assert sel == {"deep": "glm-5.3", "normal": "glm-5.2", "basic": None}, sel
    # A vanished model releases its tier back to re-selection.
    sel2 = um.preserve_coding_plan_tiers(
        {"deep": "gone-model"}, {"deep": "glm-5.3", "normal": "glm-5.3"}, {"glm-5.3"}
    )
    assert sel2["deep"] == "glm-5.3", sel2


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    assert len(tests) >= 12, f"expected >=10 tests, found {len(tests)}"
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
