"""配布（49枚を配り切る・端数・再現性）のテスト。"""

import unittest

from teiji.engine import (
    DECK_SIZE,
    Action,
    IllegalAction,
    InvalidSetup,
    apply,
    legal_actions,
    new_game,
)

from .helpers import card_total


class DealTests(unittest.TestCase):
    """準備フェーズの配布を確認する。"""

    def test_all_cards_are_dealt_for_every_player_count(self):
        for num_players in range(3, 7):
            with self.subTest(num_players=num_players):
                state = new_game(num_players, seed=1)
                self.assertEqual(card_total(state), DECK_SIZE)
                self.assertEqual(len(state.discarded), 0)
                self.assertEqual(sum(len(p.hand) for p in state.players), 49)
                identities = [
                    (card.id, card.copy) for player in state.players for card in player.hand
                ]
                self.assertEqual(len(set(identities)), 49)

    def test_remainder_goes_to_earlier_seats(self):
        # 49 ÷ n の端数は席0から1枚ずつ多く配る。
        expected = {
            3: [17, 16, 16],
            4: [13, 12, 12, 12],
            5: [10, 10, 10, 10, 9],
            6: [9, 8, 8, 8, 8, 8],
        }
        for num_players, sizes in expected.items():
            with self.subTest(num_players=num_players):
                state = new_game(num_players, seed=7)
                self.assertEqual([len(p.hand) for p in state.players], sizes)

    def test_first_player_is_fixed_or_random(self):
        self.assertEqual(new_game(4, seed=1, first_player=2).current_player, 2)
        first_by_seed = [new_game(4, seed=seed).current_player for seed in range(10)]
        self.assertEqual(len(set(first_by_seed)) > 1, True)

    def test_same_seed_reproduces_deal(self):
        first = new_game(5, seed=42)
        second = new_game(5, seed=42)
        self.assertEqual(
            [[(c.id, c.copy) for c in p.hand] for p in first.players],
            [[(c.id, c.copy) for c in p.hand] for p in second.players],
        )
        different = new_game(5, seed=43)
        self.assertNotEqual(
            [[(c.id, c.copy) for c in p.hand] for p in first.players],
            [[(c.id, c.copy) for c in p.hand] for p in different.players],
        )

    def test_invalid_player_counts(self):
        for num_players in (2, 7, 0, -1):
            with self.subTest(num_players=num_players):
                with self.assertRaises(InvalidSetup):
                    new_game(num_players)
        with self.assertRaises(InvalidSetup):
            new_game(True)
        with self.assertRaises(InvalidSetup):
            new_game(4.0)

    def test_invalid_first_player(self):
        with self.assertRaises(InvalidSetup):
            new_game(4, first_player=4)
        with self.assertRaises(InvalidSetup):
            new_game(4, first_player=-1)

    def test_pairs_are_not_played_at_deal_time(self):
        # 配った時点でペアが揃っていても、最初の手番では「引く」しかできない。
        state = new_game(3, seed=0, first_player=0)
        self.assertEqual(state.phase, "draw")
        actions = legal_actions(state)
        self.assertTrue(actions)
        self.assertEqual({action.type for action in actions}, {"draw"})

    def test_apply_before_draw_is_rejected_for_play(self):
        state = new_game(3, seed=0, first_player=0)
        with self.assertRaises(IllegalAction):
            # draw フェーズでは play は不可（手札にペアがあるかどうかに関わらず）
            apply(state, Action(type="play", player=0, card="quota"))


if __name__ == "__main__":
    unittest.main()
