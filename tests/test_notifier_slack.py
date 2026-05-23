from __future__ import annotations

from ploy_agent.notifier.rank import RankedPick
from ploy_agent.notifier.slack import (
    SlackFeedEntry,
    build_message_blocks,
    replace_action_block_with_status,
)


def _pick() -> RankedPick:
    return RankedPick(
        market_id="m1",
        strategy_id="cross_market_arb",
        question="Will Team A win?",
        end_date=None,
        mid=0.46,
        model_prob=0.61,
        market_prob=0.46,
        edge_cents=15.0,
        confidence=0.72,
        reasoning="Example reasoning",
        depth_1c=200.0,
        score=1.23,
    )


def test_build_message_blocks_pending_entry_has_buttons() -> None:
    blocks = build_message_blocks([SlackFeedEntry(pick=_pick(), rec_id=42, status="pending")])
    actions = [b for b in blocks if b.get("type") == "actions"]
    assert len(actions) == 1
    values = [el.get("value") for el in actions[0]["elements"]]
    assert values == ["42", "42"]


def test_build_message_blocks_actioned_entry_shows_status() -> None:
    blocks = build_message_blocks([SlackFeedEntry(pick=_pick(), rec_id=42, status="approved")])
    assert not [b for b in blocks if b.get("type") == "actions"]
    context = [b for b in blocks if b.get("type") == "context"]
    assert context
    assert "Approved" in context[0]["elements"][0]["text"]


def test_replace_action_block_with_status_swaps_buttons() -> None:
    blocks = build_message_blocks([SlackFeedEntry(pick=_pick(), rec_id=42, status="pending")])
    updated = replace_action_block_with_status(blocks, 42, "rejected", "U123")
    assert not [b for b in updated if b.get("type") == "actions"]
    context = [b for b in updated if b.get("type") == "context"]
    assert context
    assert "Rejected" in context[0]["elements"][0]["text"]
    assert "<@U123>" in context[0]["elements"][0]["text"]
