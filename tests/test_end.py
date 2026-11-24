"""通常終了（退社した全員が勝者・残業保持者が一人負け）のテスト。"""

import unittest

from teiji.engine import Action, IllegalAction, apply, is_game_over, legal_actions, new_game, result

from .helpers import draw_at, no_shuffle, play_pair, ready_to_play, set_hands


class EndTests(unittest.TestCase):
    """通常終了の判定と結果を確認する。"""

    def _play_to_end(self):
        """3人で席0→席1の順に退社させ、残業を持つ席2を残す。"""
        state = ready_to_play(no_shuffle(new_game(3, seed=1, first_player=0)))
        state = set_hands(
            state,
            {0: ["quota", "quota"], 1: ["quota", "quota"], 2: ["overtime"]},
        )
        state = play_pair(state, 0, "quota")
        self.assertEqual(state.finish_order, [0])
        self.assertFalse(is_game_over(state))
        self.assertIsNone(result(state))
        state = ready_to_play(state)
        return play_pair(state, 1, "quota")

    def test_normal_game_end(self):
        state = self._play_to_end()
        self.assertTrue(is_game_over(state))
        self.assertEqual(state.phase, "game_over")
        self.assertEqual(state.end_reason, "normal")
        self.assertIsNone(state.current_player)
        self.assertEqual(legal_actions(state), [])

    def test_result_has_all_winners_and_single_loser(self):
        state = self._play_to_end()
        self.assertEqual(
            result(state),
            {"type": "normal", "winners": [0, 1], "loser": 2},
        )

    def test_result_has_no_ranks_or_standings(self):
        # 順位はつけない。退社順は履歴イベントにだけ残す。
        state = self._play_to_end()
        outcome = result(state)
        self.assertEqual(set(outcome), {"type", "winners", "loser"})
        self.assertEqual(
            [event["payload"].get("rank") for event in state.events],
            [None] * len(state.events),
        )

    def test_game_over_event_carries_result(self):
        state = self._play_to_end()
        event = [event for event in state.events if event["kind"] == "game_over"][-1]
        self.assertEqual(event["scope"], "all")
        self.assertEqual(event["payload"], result(state))

    def test_actions_are_rejected_after_game_over(self):
        state = self._play_to_end()
        with self.assertRaises(IllegalAction):
            apply(state, Action(type="pass", player=2))
        with self.assertRaises(IllegalAction):
            draw_at(state, 2, 0)

    def test_labor_result_frame_is_available(self):
        # 労基終了も一人負けの結果フレーム（type だけが異なる）。
        state = self._play_to_end()
        state.end_reason = "labor"
        state.labor_info = {"loser": 2}
        self.assertEqual(
            result(state),
            {"type": "labor", "winners": [0, 1], "loser": 2},
        )


if __name__ == "__main__":
    unittest.main()
