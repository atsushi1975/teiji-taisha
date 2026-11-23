"""退社（出す・引かれる・対象から外れる）のテスト。"""

import unittest

from teiji.engine import draw_source, new_game

from .helpers import (
    cards,
    draw_at,
    no_shuffle,
    pass_turn,
    play_pair,
    ready_to_play,
    set_hands,
)


class RetireTests(unittest.TestCase):
    """退社の条件と退社者の扱いを確認する。"""

    def test_retire_after_playing_pair(self):
        state = ready_to_play(no_shuffle(new_game(3, seed=1, first_player=0)))
        state = set_hands(state, {0: ["quota", "quota"]})
        played = play_pair(state, 0, "quota")
        self.assertTrue(played.players[0].left)
        self.assertEqual(played.players[0].hand, [])
        self.assertEqual(played.players[0].finish_rank, 1)
        self.assertEqual(played.finish_order, [0])
        self.assertEqual(len(played.discarded), 2)
        self.assertEqual(played.current_player, 1)

    def test_retire_after_being_drawn_from(self):
        state = no_shuffle(new_game(3, seed=1, first_player=0))
        state = set_hands(state, {0: ["quota"], 2: ["overtime"]})
        drawn = draw_at(state, 0, position=0)
        self.assertTrue(drawn.players[2].left)
        self.assertEqual(drawn.players[2].hand, [])
        self.assertEqual(drawn.players[2].finish_rank, 1)
        self.assertEqual(drawn.players[0].hand, cards("quota", "overtime"))
        self.assertEqual(drawn.phase, "play")

    def test_retired_player_is_not_a_draw_source(self):
        state = new_game(4, seed=1, first_player=0)
        state.players[3].left = True
        state.players[3].hand = []
        state = no_shuffle(state)
        state = set_hands(state, {0: [], 2: ["quota", "quota"]})
        self.assertEqual(draw_source(state, 0), 2)
        drawn = draw_at(state, 0, position=0)
        self.assertEqual(len(drawn.players[2].hand), 1)
        self.assertEqual(len(drawn.players[0].hand), 1)

    def test_retired_player_is_not_an_effect_target(self):
        state = new_game(4, seed=1, first_player=0)
        state.players[1].left = True
        state.players[1].hand = []
        state = ready_to_play(set_hands(state, {0: ["drinking", "drinking", "quota"]}))
        played = play_pair(state, 0, "drinking")
        # 飲み会は退社済みの席1を飛ばして席2を対象にする。
        self.assertEqual(played.drinking_blocked, 2)

    def test_retired_player_turn_is_skipped(self):
        state = new_game(3, seed=1, first_player=1)
        state.players[2].left = True
        state.players[2].hand = []
        state = no_shuffle(state)
        state = set_hands(state, {0: ["quota", "quota"], 1: ["quota"]})
        state = draw_at(state, 1, 0)
        state = pass_turn(state, 1)
        self.assertEqual(state.current_player, 0)

    def test_effect_of_retired_actor_is_finished_by_target(self):
        # 退社した人が発動した効果は、対象になった人が最後まで処理する（ルール 3.6）。
        state = new_game(3, seed=1, first_player=0)
        state = ready_to_play(
            set_hands(
                state,
                {0: ["drinking", "drinking"], 1: ["quota"], 2: ["quota", "quota"]},
            )
        )
        played = play_pair(state, 0, "drinking")
        self.assertTrue(played.players[0].left)
        self.assertEqual(played.drinking_blocked, 1)
        skipped = draw_at(played, 1, 0)
        self.assertEqual(skipped.current_player, 2)
        self.assertIsNone(skipped.drinking_blocked)


if __name__ == "__main__":
    unittest.main()
