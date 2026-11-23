"""手番進行（引く・右隣の飛ばし・不正操作）のテスト。"""

import unittest

from teiji.engine import (
    Action,
    IllegalAction,
    InvalidPosition,
    NotYourTurn,
    PHASE_DRAW,
    PHASE_PLAY,
    apply,
    draw_source,
    new_game,
)

from .helpers import draw_at, no_shuffle, pass_turn, set_hands


class TurnTests(unittest.TestCase):
    """引くフェーズと出すフェーズの遷移を確認する。"""

    def test_draw_position_without_shuffle(self):
        state = new_game(6, seed=1, first_player=0)
        state = no_shuffle(state)
        state = set_hands(
            state,
            {
                0: ["quota"],
                5: ["overtime", "quota", "quota"],
            },
        )
        drawn = draw_at(state, 0, position=1)
        self.assertEqual(
            [card.id for card in drawn.players[0].hand], ["quota", "quota"]
        )
        self.assertEqual(
            [card.id for card in drawn.players[5].hand], ["overtime", "quota"]
        )
        self.assertEqual(drawn.phase, PHASE_PLAY)
        self.assertEqual(drawn.current_player, 0)
        # 引いたカードの中身はイベントに出さない。
        draw_event = [e for e in drawn.events if e["kind"] == "draw"][-1]
        self.assertEqual(draw_event["payload"], {"from": 5, "to": 0, "position": 1})

    def test_draw_position_is_0_based_and_bounded(self):
        state = no_shuffle(new_game(3, seed=1, first_player=0))
        state = set_hands(state, {0: [], 2: ["quota", "quota"]})
        self.assertEqual(len(draw_at(state, 0, 0).players[0].hand), 1)
        self.assertEqual(len(draw_at(state, 0, 1).players[0].hand), 1)
        with self.assertRaises(InvalidPosition):
            draw_at(state, 0, 2)
        with self.assertRaises(InvalidPosition):
            draw_at(state, 0, -1)
        with self.assertRaises(InvalidPosition):
            apply(state, Action(type="draw", player=0, position=None))

    def test_shuffle_before_draw_is_enabled_by_default(self):
        state = new_game(4, seed=3, first_player=0)
        self.assertTrue(state.shuffle_before_draw)
        state = set_hands(state, {3: ["quota", "quota", "quota"]})
        rng_before = state.rng.getstate()
        drawn = draw_at(state, 0, position=0)
        self.assertNotEqual(state.rng.getstate(), drawn.rng.getstate())
        # 元の状態（深いコピー前）の RNG は変更されない。
        self.assertEqual(rng_before, state.rng.getstate())
        self.assertEqual(len(drawn.players[3].hand), 2)

    def test_draw_skips_retired_right_neighbor(self):
        state = new_game(6, seed=1, first_player=0)
        state.players[5].left = True
        state.players[5].hand = []
        state = no_shuffle(state)
        state = set_hands(state, {4: ["quota", "quota"], 0: ["overtime"]})
        self.assertEqual(draw_source(state, 0), 4)
        drawn = draw_at(state, 0, position=0)
        self.assertEqual(len(drawn.players[4].hand), 1)
        self.assertEqual(len(drawn.players[0].hand), 2)

    def test_non_actor_operation_is_rejected(self):
        state = new_game(4, seed=1, first_player=2)
        with self.assertRaises(NotYourTurn):
            draw_at(state, 0, 0)
        with self.assertRaises(NotYourTurn):
            pass_turn(state, 1)

    def test_wrong_phase_operations_are_rejected(self):
        state = new_game(4, seed=1, first_player=0)
        with self.assertRaises(IllegalAction):
            pass_turn(state, 0)
        playing = draw_at(state, 0, 0)
        with self.assertRaises(IllegalAction):
            draw_at(playing, 0, 0)

    def test_pass_moves_turn_to_left_neighbor(self):
        state = new_game(4, seed=1, first_player=1)
        state = draw_at(state, 1, 0)
        state = pass_turn(state, 1)
        self.assertEqual(state.current_player, 2)
        self.assertEqual(state.phase, PHASE_DRAW)

    def test_apply_does_not_mutate_original_state(self):
        state = no_shuffle(new_game(3, seed=1, first_player=0))
        before = [list(p.hand) for p in state.players]
        _ = draw_at(state, 0, 0)
        self.assertEqual([list(p.hand) for p in state.players], before)

    def test_event_sequence_starts_after_deal(self):
        state = draw_at(new_game(3, seed=1, first_player=0), 0, 0)
        self.assertEqual([event["seq"] for event in state.events], [0, 1])
        self.assertEqual([event["kind"] for event in state.events], ["deal", "draw"])


if __name__ == "__main__":
    unittest.main()
