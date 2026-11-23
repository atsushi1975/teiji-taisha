"""ランダムプレイ（終了性・カード保存則・seed 再現性）のテスト。"""

import random
import unittest

from teiji.engine import apply, is_game_over, legal_actions, new_game, result, to_dict

from .helpers import card_total

MAX_STEPS = 5000


def play_randomly(num_players: int, seed: int, chooser_seed: int):
    """全席が合法手からランダムに選んで対戦し、最終状態を返す。"""
    state = new_game(num_players, seed=seed)
    chooser = random.Random(chooser_seed)
    for step in range(MAX_STEPS):
        if is_game_over(state):
            return state, step
        assert card_total(state) == 49, f"カード総数が変化した: step={step}"
        actions = legal_actions(state)
        if not actions:
            raise AssertionError(f"合法手が空なのにゲームが終わらない: step={step}")
        state = apply(state, chooser.choice(actions))
    raise AssertionError(f"{MAX_STEPS} ステップ以内に終了しなかった")


class RandomPlayTests(unittest.TestCase):
    """ランダム対戦でも必ず終了し、状態が壊れないことを確認する。"""

    def test_all_player_counts_finish_and_preserve_cards(self):
        for num_players in range(3, 7):
            for seed in range(3):
                with self.subTest(num_players=num_players, seed=seed):
                    state, steps = play_randomly(num_players, seed, chooser_seed=seed + 100)
                    self.assertTrue(is_game_over(state))
                    self.assertLess(steps, MAX_STEPS)
                    self.assertEqual(card_total(state), 49)
                    outcome = result(state)
                    self.assertEqual(outcome["type"], "normal")
                    self.assertEqual(len(outcome["winners"]), num_players - 1)
                    loser = outcome["loser"]
                    self.assertTrue(
                        any(
                            card.id == "overtime"
                            for card in state.players[loser].hand
                        )
                    )
                    self.assertEqual(len(state.finish_order), num_players - 1)
                    self.assertEqual(
                        sorted(
                            player.finish_rank
                            for player in state.players
                            if player.left
                        ),
                        list(range(1, num_players)),
                    )

    def test_same_seed_is_reproducible(self):
        first, first_steps = play_randomly(4, seed=123, chooser_seed=456)
        second, second_steps = play_randomly(4, seed=123, chooser_seed=456)
        self.assertEqual(first_steps, second_steps)
        self.assertEqual(to_dict(first), to_dict(second))

    def test_different_seed_differs(self):
        first, _ = play_randomly(5, seed=1, chooser_seed=1)
        second, _ = play_randomly(5, seed=2, chooser_seed=1)
        self.assertNotEqual(to_dict(first)["players"], to_dict(second)["players"])


if __name__ == "__main__":
    unittest.main()
