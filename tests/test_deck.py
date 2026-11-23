"""デッキ構成のテスト。"""

import unittest

from teiji.engine import (
    CARD_SPECS,
    DECK_SIZE,
    STUB_EFFECT_IDS,
    build_deck,
    card_from_dict,
    card_to_dict,
)


class DeckTests(unittest.TestCase):
    """49枚の構成とカード変換を確認する。"""

    def test_deck_has_49_cards(self):
        self.assertEqual(DECK_SIZE, 49)
        deck = build_deck()
        self.assertEqual(len(deck), 49)

    def test_card_counts_by_type(self):
        expected = dict(CARD_SPECS)
        deck = build_deck()
        for card_id, count in expected.items():
            with self.subTest(card_id=card_id):
                self.assertEqual(sum(1 for card in deck if card.id == card_id), count)

    def test_twelve_card_types(self):
        deck = build_deck()
        self.assertEqual(len({card.id for card in deck}), 12)
        self.assertEqual(set(dict(CARD_SPECS)), {card.id for card in deck})

    def test_copy_numbers_are_unique_per_type(self):
        deck = build_deck()
        for card_id, count in CARD_SPECS:
            with self.subTest(card_id=card_id):
                copies = sorted(card.copy for card in deck if card.id == card_id)
                self.assertEqual(copies, list(range(1, count + 1)))

    def test_physical_cards_are_unique(self):
        deck = build_deck()
        identities = [(card.id, card.copy) for card in deck]
        self.assertEqual(len(set(identities)), 49)

    def test_stub_effect_ids(self):
        # 1-B で外す7効果。1-A ではペアを出せるだけで効果なし。
        self.assertEqual(
            STUB_EFFECT_IDS,
            {
                "labor",
                "romance",
                "training",
                "info_share",
                "trade",
                "power_harassment",
                "smoke_break",
            },
        )

    def test_card_dict_round_trip(self):
        from teiji.engine import Card

        card = Card(id="overtime", copy=1)
        self.assertEqual(card_from_dict(card_to_dict(card)), card)


if __name__ == "__main__":
    unittest.main()
