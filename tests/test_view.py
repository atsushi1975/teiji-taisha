"""view_for（他人の手札非公開・枚数公開・イベント公開範囲）のテスト。"""

import unittest

from teiji.engine import InvalidSetup, new_game, view_for, visible_events

from .helpers import cards, play_pair, ready_to_play, set_hands


class ViewTests(unittest.TestCase):
    """席ごとの公開情報を確認する。"""

    def test_other_players_hands_are_hidden_but_counts_are_public(self):
        state = new_game(4, seed=1, first_player=0)
        state = set_hands(
            state,
            {
                0: ["quota", "quota", "overtime"],
                1: ["labor", "labor", "labor"],
                2: ["overtime", "quota"],
                3: ["meeting", "meeting"],
            },
        )
        mine = view_for(state, 0)
        self.assertEqual(len(mine["players"]), 4)
        self.assertEqual(
            mine["players"][0]["hand"],
            [{"id": card.id, "copy": card.copy} for card in cards("quota", "quota", "overtime")],
        )
        for entry in mine["players"][1:]:
            with self.subTest(seat=entry["seat"]):
                self.assertNotIn("hand", entry)
        for entry in mine["players"]:
            with self.subTest(seat=entry["seat"]):
                self.assertNotIn("finish_rank", entry)
                self.assertNotIn("rank", entry)
        self.assertEqual(
            [entry["hand_count"] for entry in mine["players"]], [3, 3, 2, 2]
        )
        self.assertEqual(mine["viewer"], 0)
        self.assertEqual(mine["phase"], "draw")
        self.assertEqual(mine["current_player"], 0)
        self.assertIsNone(mine["result"])

    def test_event_scope_all_is_visible_to_everyone(self):
        state = new_game(3, seed=1, first_player=0)
        state = ready_to_play(set_hands(state, {0: ["quota", "quota", "overtime"]}))
        state = play_pair(state, 0, "quota")
        for viewer in range(3):
            with self.subTest(viewer=viewer):
                kinds = [event["kind"] for event in view_for(state, viewer)["events"]]
                self.assertIn("deal", kinds)
                self.assertIn("play", kinds)
                self.assertIn("effect", kinds)

    def test_event_scope_seats_is_visible_only_to_listed_seats(self):
        state = new_game(3, seed=1, first_player=0)
        state.events.append(
            {
                "seq": len(state.events),
                "kind": "private_view",
                "scope": "seats",
                "seats": [1, 2],
                "payload": {"secret": True},
            }
        )
        self.assertNotIn(
            "private_view", [event["kind"] for event in view_for(state, 0)["events"]]
        )
        self.assertIn(
            "private_view", [event["kind"] for event in view_for(state, 1)["events"]]
        )
        self.assertIn(
            "private_view", [event["kind"] for event in view_for(state, 2)["events"]]
        )
        self.assertEqual(
            [event["seq"] for event in visible_events(state, 2)], [0, 1]
        )

    def test_1a_events_are_all_public(self):
        state = new_game(3, seed=1, first_player=0)
        for event in state.events:
            self.assertEqual(event["scope"], "all")
            self.assertNotIn("seats", event)

    def test_invalid_viewer(self):
        state = new_game(3, seed=1, first_player=0)
        with self.assertRaises(InvalidSetup):
            view_for(state, -1)
        with self.assertRaises(InvalidSetup):
            view_for(state, 3)
        with self.assertRaises(InvalidSetup):
            view_for(state, True)


if __name__ == "__main__":
    unittest.main()
