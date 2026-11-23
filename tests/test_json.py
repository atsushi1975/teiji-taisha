"""to_dict / from_dict の往復（乱数状態込み）のテスト。"""

import json
import random
import unittest

from teiji.engine import Action, apply, from_dict, legal_actions, new_game, to_dict

from .helpers import card_total, no_shuffle


class JsonTests(unittest.TestCase):
    """JSON 互換 dict への変換と復元を確認する。"""

    def test_round_trip_preserves_everything(self):
        state = no_shuffle(new_game(4, seed=11, first_player=0))
        chooser = random.Random(99)
        for _ in range(12):
            actions = legal_actions(state)
            if not actions:
                break
            state = apply(state, chooser.choice(actions))

        data = json.loads(json.dumps(to_dict(state)))
        restored = from_dict(data)
        self.assertEqual(to_dict(restored), to_dict(state))
        self.assertEqual(card_total(restored), 49)
        self.assertEqual(restored.rng.randint(0, 10**9), state.rng.randint(0, 10**9))

    def test_restored_state_continues_identically(self):
        state = no_shuffle(new_game(5, seed=7))
        chooser = random.Random(3)
        for _ in range(8):
            state = apply(state, chooser.choice(legal_actions(state)))
        restored = from_dict(json.loads(json.dumps(to_dict(state))))
        actions = legal_actions(state)
        if actions:
            action = actions[0]
            self.assertEqual(to_dict(apply(restored, action)), to_dict(apply(state, action)))

    def test_action_dict_round_trip(self):
        for action in (
            Action(type="draw", player=1, position=2),
            Action(type="pass", player=0),
            Action(type="play", player=2, card="energy", target=None),
        ):
            with self.subTest(action=action):
                self.assertEqual(Action.from_dict(action.to_dict()), action)
        self.assertEqual(
            Action.from_dict(json.loads(json.dumps(Action(type="draw", player=0, position=0).to_dict()))),
            Action(type="draw", player=0, position=0),
        )

    def test_shuffle_flag_and_pending_fields_round_trip(self):
        state = no_shuffle(new_game(3, seed=5))
        state.awaiting = [1, 2]
        state.pending = {"effect": "info_share", "actor": 0, "responses": {}}
        state.extra_plays = 2
        state.end_reason = "normal"
        restored = from_dict(json.loads(json.dumps(to_dict(state))))
        self.assertEqual(restored.shuffle_before_draw, False)
        self.assertEqual(restored.awaiting, [1, 2])
        self.assertEqual(restored.pending, state.pending)
        self.assertEqual(restored.extra_plays, 2)
        self.assertEqual(restored.end_reason, "normal")


if __name__ == "__main__":
    unittest.main()
