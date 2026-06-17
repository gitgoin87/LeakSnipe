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
        "hero_player": getattr(hand, "hero_player", ""),
        "pot": hand.pot,
        "is_tournament": hand.is_tournament,
        "tags": list(hand.tags),
    }


def hand_to_detail(hand: Hand, settings: Dict[str, Any]) -> Dict[str, Any]:
    payload = hand_to_summary(hand, settings)
    payload.update(
        {
            "board_cards": hand.board_cards,
            "streets": hand.streets,
            "players": {str(k): v for k, v in hand.players.items()},
            "winners": hand.winners,
            "raw_text": hand.raw_text,
            "max_seats": hand.max_seats,
            "button_seat": hand.button_seat,
            "rake": hand.rake,
        }
    )
    return payload


def hands_to_summaries(hands: List[Hand], settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [hand_to_summary(h, settings) for h in hands]
