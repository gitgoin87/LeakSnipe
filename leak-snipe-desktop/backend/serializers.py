"""JSON serializers for LeakSnipe domain objects."""

from __future__ import annotations

from typing import Any, Dict, List

from models import Hand


def hand_to_summary(hand: Hand, settings: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "hand_id": hand.hand_id,
        "site": hand.site,
        "date": hand.date.isoformat() if hand.date else None,
        "game_type": hand.game_type,
        "table_name": hand.table_name,
        "hero_cards": hand.hero_cards,
        "hero_won": hand.hero_won,
        "hero_position": hand.hero_position,
        "hero_name": hand.hero_name(settings),
        "pot": hand.pot,
        "is_tournament": hand.is_tournament,
        "tags": list(hand.tags),
    }


def hands_to_summaries(hands: List[Hand], settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [hand_to_summary(h, settings) for h in hands]
