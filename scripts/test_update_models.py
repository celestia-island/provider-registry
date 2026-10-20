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


def test_malformed_record_skipped_not_fatal():
    # Audit preg-R1-5: a single malformed record degrades to a skip.
    bad = {"id": "bad/record", "pricing": {"prompt": "not-a-number"}}
    assert um.parse_openrouter_model(bad) is None


def test_negative_pricing_rejected():
    # Audit preg-R1-3: negative upstream pricing is refused at ingestion.
    neg = {"id": "bad/pricing", "pricing": {"prompt": "-0.5", "completion": "0.1"}}
    assert um.parse_openrouter_model(neg) is None


def test_absurd_pricing_rejected():
    absurd = {"id": "bad/pricing", "pricing": {"prompt": "999999", "completion": "0.1"}}
    assert um.parse_openrouter_model(absurd) is None


# ------------------------------------------------------------
# Audit 2026-10-21: twin spellings, tier ranking, models.dev hardening
# ------------------------------------------------------------

def test_twin_spelling_folds_onto_disk_card():
    # OpenRouter spells "claude-haiku-5.5", the disk card (models.dev
    # spelling) is "claude-haiku-5-5" — one model, one card, disk spelling.
    existing = um.fold_by_canonical({"claude-haiku-5-5": M(id="claude-haiku-5-5", pricing_input=0.1)})
    or_feed = um.rekey_onto_existing(
        {"claude-haiku-5.5": M(id="claude-haiku-5.5", pricing_input=0.1, score=65.5, score_source="design_arena")},
        existing,
    )
    assert list(or_feed) == ["claude-haiku-5-5"], or_feed
    merged = um.merge_model_dicts(existing, or_feed, {})
    assert set(merged) == {"claude-haiku-5-5"}, merged
    assert merged["claude-haiku-5-5"].score == 65.5  # blank filled, not a twin


def test_new_twin_not_minted():
    # Both feeds carry a NEW model under different spellings: the merge
    # must emit a single card (models.dev spelling outside the openrouter
    # dir, OpenRouter spelling inside it).
    or_feed = {"mistral-large-4-0": M(id="mistral-large-4-0", score=58.2, score_source="design_arena")}
    md_feed = {"mistral-large-4": M(id="mistral-large-4", pricing_input=0.68, pricing_output=2.09)}
    um.resolve_shared_new_ids(or_feed, md_feed, prefer_openrouter=False)
    assert set(or_feed) == {"mistral-large-4"} and set(md_feed) == {"mistral-large-4"}
    merged = um.merge_model_dicts({}, or_feed, md_feed)
    assert set(merged) == {"mistral-large-4"}, merged

    or_feed2 = {"openai/gpt-future": M(id="openai/gpt-future")}
    md_feed2 = {"openai/gpt-future-0": M(id="openai/gpt-future-0")}
    um.resolve_shared_new_ids(or_feed2, md_feed2, prefer_openrouter=True)
    assert set(md_feed2) == {"openai/gpt-future"}, md_feed2


def test_modelsdev_malformed_skipped_not_fatal():
    # The models.dev parser never inherited #19's tolerance — one bad
    # record used to abort the whole run.
    bad = {"id": "bad/md-record", "cost": {"input": "oops"}}
    assert um.parse_modelsdev_model(bad, "x") is None


def test_modelsdev_negative_pricing_rejected():
    neg = {"id": "bad/md-pricing", "cost": {"input": -0.5, "output": 0.1}}
    assert um.parse_modelsdev_model(neg, "x") is None


def test_family_prefix_keeps_tiers_in_family():
    # models.dev alibaba-cn lists hosted GLM/MiniMax models alongside Qwen;
    # a higher-scored glm must not become the *qwen* entrypoint's deep tier.
    models = [
        M(id="glm-5.3", score=61.4, score_source="design_arena", supports_reasoning=True),
        M(id="qwen3.8-max", tags=["flagship"], supports_reasoning=True),
        M(id="qwen3.8-plus", pricing_input=1.2),
        M(id="qwen3.8-flash", tags=["cost_effective"], pricing_input=0.05),
    ]
    sel = um.select_tier_models_from_list(models, family_prefixes=um.PROVIDER_FAMILY_PREFIXES["qwen"])
    assert sel["deep"].startswith("qwen3.8"), sel
    assert sel["normal"].startswith("qwen3.8"), sel
    assert sel["basic"].startswith("qwen3.8"), sel


def test_non_chat_and_deprecated_excluded_from_tiers():
    models = [
        M(id="gpt-5.4-image-2", score=90.0, score_source="design_arena"),  # image: no chat tier
        M(id="old-flagship", score=80.0, score_source="design_arena", tags=["deprecated"]),
        M(id="gpt-6-astra", score=76.9, score_source="artificial_analysis", supports_reasoning=True),
    ]
    sel = um.select_tier_models_from_list(models, family_prefixes=um.PROVIDER_FAMILY_PREFIXES["openai"])
    assert sel["deep"] == "gpt-6-astra", sel


def test_voxtral_never_enters_chat_tiers():
    # Audit R2: voxtral-mini-latest (an ASR model with a 0-price
    # placeholder card) was mistral's basic tier.
    models = [
        M(id="mistral-large-4", tags=["flagship"], supports_reasoning=True),
        M(id="codestral-2508", tags=["coding"], pricing_input=0.3),
        M(id="voxtral-mini-latest", tags=["cost_effective"], pricing_input=0.0),
        M(id="ministral-3b-latest", tags=["cost_effective"], pricing_input=0.05),
    ]
    sel = um.select_tier_models_from_list(models, family_prefixes=um.PROVIDER_FAMILY_PREFIXES["mistral"])
    assert "voxtral" not in (sel["deep"] or ""), sel
    assert "voxtral" not in (sel["normal"] or ""), sel
    assert "voxtral" not in (sel["basic"] or ""), sel


def test_basic_tier_skips_zero_price_placeholder():
    # A 0 input price is a feed-gap placeholder; ASC price sort must not
    # crown it as the budget pick.
    models = [
        M(id="model-flag", tags=["flagship"], supports_reasoning=True),
        M(id="model-chat", pricing_input=1.0),
        M(id="placeholder-budget", tags=["cost_effective"], pricing_input=0.0),
        M(id="real-budget", tags=["cost_effective"], pricing_input=0.4),
    ]
    sel = um.select_tier_models_from_list(models)
    assert sel["basic"] == "real-budget", sel


def test_stale_cost_effective_tag_no_longer_bars_normal():
    # Audit R2: 'flash' was dropped from cost_effective TAG_PATTERNS
    # (2026-10-21) but the tag stays stamped on existing cards
    # (additive-only sync). A stale tag must not keep the current-gen
    # mainline flash out of the normal tier — that left google normal on
    # gemma-4-31b-it while every gemini flash sat excluded.
    models = [
        M(id="gemini-4-argon", tags=["flagship", "cost_effective"],
          score=66.16, score_source="design_arena", supports_reasoning=True),
        M(id="gemini-3.8-flash", tags=["cost_effective"],
          score=60.11, score_source="design_arena", supports_reasoning=True),
        M(id="gemma-4-31b-it", score=43.4, score_source="artificial_analysis"),
    ]
    sel = um.select_tier_models_from_list(
        models, family_prefixes=um.PROVIDER_FAMILY_PREFIXES["google"]
    )
    assert sel["normal"] == "gemini-3.8-flash", sel
    # A genuine budget line (id still matches a current cost_effective
    # pattern, e.g. "lite") stays excluded from normal.
    models2 = [
        M(id="glm-5", tags=["flagship"], supports_reasoning=True),
        M(id="glm-5.2-air", tags=["cost_effective"], score=70.0, score_source="design_arena"),
        M(id="glm-5.1", score=60.0, score_source="design_arena"),
    ]
    sel2 = um.select_tier_models_from_list(
        models2, family_prefixes=um.PROVIDER_FAMILY_PREFIXES["zhipu_glm"]
    )
    assert sel2["normal"] == "glm-5.1", sel2


def test_score_outranks_stale_reasoning_pick():
    # deepseek-reasoner is an unscored alias that merely *claims* reasoning;
    # a scored current-gen model must take the deep tier.
    # Audit R2 update: deepseek-v4-flash is DeepSeek's mainline mid tier
    # (stale cost_effective tag no longer bars it), so it lands in NORMAL;
    # the only remaining candidate for basic is the unpriced alias, which
    # the 0-price guard correctly refuses.
    models = [
        M(id="deepseek-reasoner", supports_reasoning=True),
        M(id="deepseek-v4-pro-0813", score=68.8, score_source="artificial_analysis"),
        M(id="deepseek-v4-flash", score=39.5, score_source="design_arena", tags=["cost_effective"], pricing_input=0.15),
    ]
    sel = um.select_tier_models_from_list(models, family_prefixes=um.PROVIDER_FAMILY_PREFIXES["deepseek"])
    assert sel["deep"] == "deepseek-v4-pro-0813", sel
    assert sel["normal"] == "deepseek-v4-flash", sel
    assert sel["basic"] is None, sel


def test_entrypoint_twin_blocks_deduped():
    # Twin-spelled blocks denote one model: keep the existing curated block,
    # never emit both spellings.
    new = [{"id": "claude-opus-5.5"}]
    old = [
        {"id": "claude-opus-5-5", "pricing": {"input_per_million": 1.25}},
        {"id": "claude-opus-5.5", "pricing": {"input_per_million": 9.9}},
    ]
    out = um.curate_entrypoint_blocks(new, old)
    ids = [b["id"] for b in out]
    assert ids == ["claude-opus-5-5"], ids
    assert out[0]["pricing"]["input_per_million"] == 1.25  # existing block wins


def test_fold_keeps_entrypoint_spelling_like_curate():
    # Audit R2 (spelling contract): the existing catalog is built
    # entrypoint-first (load_existing_catalog), so the fold winner is
    # the ENTRYPOINT block spelling — exactly the spelling
    # curate_entrypoint_blocks emits. Before this, the fold saw only
    # dir-sorted spellings and minimax normal flipped between
    # "MiniMax-M3" and "minimax-m3" across runs (default pointing at a
    # block id that was never emitted).
    ep_card = M(id="minimax-m3", pricing_input=0.3, context_window=0)
    dir_card = M(id="MiniMax-M3", pricing_input=0.3,
                 context_window=204800, tags=["cost_effective"])
    existing = {"minimax-m3": ep_card, "MiniMax-M3": dir_card}  # ep first
    folded = um.fold_by_canonical(dict(existing))
    assert set(folded) == {"minimax-m3"}, folded
    # the dir twin only fills blanks on the kept card
    assert folded["minimax-m3"].context_window == 204800
    assert folded["minimax-m3"].tags == ["cost_effective"]
    # feeds rekey onto the same spelling …
    feed = um.rekey_onto_existing(
        {"MiniMax-M3": M(id="MiniMax-M3", score=61.2, score_source="design_arena")},
        folded,
    )
    assert set(feed) == {"minimax-m3"}, feed
    # … and curate keeps the entrypoint block spelling for that model.
    blocks = um.curate_entrypoint_blocks(
        [{"id": "minimax-m3", "pricing": {"input_per_million": 0.3}}],
        [{"id": "minimax-m3", "pricing": {"input_per_million": 0.3}}],
    )
    assert [b["id"] for b in blocks] == ["minimax-m3"], blocks


def test_individual_pass_score_backfill():
    # The --individual-models pass feeds the merged dict through the
    # openrouter slot: a score fetched for a pre-existing card must land.
    existing = {"gpt-6-astra": M(id="gpt-6-astra", pricing_input=10.0)}
    merged = {"gpt-6-astra": M(id="gpt-6-astra", pricing_input=10.0, score=76.9, score_source="artificial_analysis")}
    out = um.merge_model_dicts(existing, merged, {})
    assert (out["gpt-6-astra"].score, out["gpt-6-astra"].score_source) == (76.9, "artificial_analysis"), out


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    assert len(tests) >= 12, f"expected >=10 tests, found {len(tests)}"
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except Exception as e:  # a crashing test must fail the run, not abort it
            failed += 1
            print(f"  FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
