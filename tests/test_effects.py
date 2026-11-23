"""効果4種（ノルマ・飲み会・エナドリ・会議）とスタブのテスト。"""

import unittest
from unittest.mock import patch

from teiji.engine import STUB_EFFECT_IDS, STUB_EFFECTS, new_game, view_for

from .helpers import draw_at, ready_to_play

from .helpers import cards, no_shuffle, play_pair, set_hands


def _state(num_players=3):
    """手番を席0に固定し、ペアを出すフェーズから始めるテスト用状態。"""
    return ready_to_play(no_shuffle(new_game(num_players, seed=1, first_player=0)))


def _effect_events(state):
    """効果イベントだけを抽出する。"""
    return [event for event in state.events if event["kind"] == "effect"]


class EffectsTests(unittest.TestCase):
    """1-A で実装した4効果を確認する。"""

    def test_quota_has_no_effect(self):
        state = _state(4)
        state = set_hands(state, {0: ["quota", "quota", "overtime"]})
        played = play_pair(state, 0, "quota")
        self.assertEqual(_effect_events(played)[-1]["payload"]["effect"], "quota")
        self.assertNotIn("stub", _effect_events(played)[-1]["payload"])
        self.assertIsNone(played.drinking_blocked)
        self.assertEqual(played.extra_plays, 0)
        self.assertEqual(played.meeting_declared, [])
        self.assertEqual(played.finish_order, [])
        self.assertEqual(played.current_player, 1)

    def test_drinking_blocks_next_player_until_draw(self):
        state = _state(4)
        state = set_hands(
            state, {0: ["drinking", "drinking", "quota"], 1: ["quota", "quota"]}
        )
        played = play_pair(state, 0, "drinking")
        self.assertEqual(played.drinking_blocked, 1)
        self.assertEqual(played.current_player, 1)
        self.assertEqual(_effect_events(played)[-1]["payload"]["target"], 1)

        skipped = draw_at(played, 1, 0)
        self.assertEqual(skipped.phase, "draw")
        self.assertEqual(skipped.current_player, 2)
        self.assertIsNone(skipped.drinking_blocked)
        self.assertEqual(len(skipped.players[1].hand), 3)
        effect = [
            event
            for event in skipped.events
            if event["kind"] == "effect"
            and event["payload"].get("skipped_play") is True
        ]
        self.assertEqual(len(effect), 1)

    def test_energy_allows_one_extra_pair_in_same_turn(self):
        state = _state()
        state = set_hands(state, {0: ["energy", "energy", "quota", "quota", "overtime"]})
        after_energy = play_pair(state, 0, "energy")
        self.assertEqual(after_energy.extra_plays, 1)
        self.assertEqual(after_energy.current_player, 0)
        self.assertEqual(after_energy.phase, "play")

        after_quota = play_pair(after_energy, 0, "quota")
        self.assertEqual(after_quota.extra_plays, 0)
        self.assertEqual(after_quota.current_player, 1)
        self.assertEqual(after_quota.discarded, cards("energy", "energy", "quota", "quota"))

    def test_energy_chain_can_extend_repeatedly(self):
        state = _state()
        state = set_hands(
            state,
            {
                0: [
                    "energy", "energy",
                    "energy", "energy",
                    "energy", "energy",
                    "quota", "quota",
                    "meeting", "meeting",
                    "labor", "labor",
                ]
            },
        )
        state = play_pair(state, 0, "energy")
        self.assertEqual(state.extra_plays, 1)
        state = play_pair(state, 0, "energy")
        self.assertEqual(state.extra_plays, 2)
        state = play_pair(state, 0, "energy")
        self.assertEqual(state.extra_plays, 3)
        self.assertEqual(state.current_player, 0)
        state = play_pair(state, 0, "quota")
        self.assertEqual(state.extra_plays, 2)
        state = play_pair(state, 0, "meeting")
        self.assertEqual(state.extra_plays, 1)
        state = play_pair(state, 0, "labor")
        self.assertEqual(state.extra_plays, 0)
        self.assertEqual(state.current_player, 1)

    def test_energy_pair_can_be_played_without_extra_pair(self):
        # 追加で出せるペアがなくても、エナドリのペア自体は通常どおり出せる。
        state = _state()
        state = set_hands(state, {0: ["energy", "energy"]})
        played = play_pair(state, 0, "energy")
        self.assertEqual(played.discarded, cards("energy", "energy"))
        self.assertTrue(played.players[0].left)
        self.assertEqual(played.current_player, 1)

    def test_meeting_declares_overtime_holder_publicly(self):
        state = _state()
        state = set_hands(
            state,
            {
                0: ["meeting", "meeting", "meeting", "meeting", "quota"],
                1: ["overtime", "quota"],
                2: ["quota", "quota"],
            },
        )
        played = play_pair(state, 0, "meeting")
        self.assertEqual(played.meeting_declared, [1])
        self.assertEqual(_effect_events(played)[-1]["payload"]["declared"], [1])
        for viewer in range(3):
            with self.subTest(viewer=viewer):
                self.assertEqual(view_for(played, viewer)["meeting_declared"], [1])
        # 既に宣言済みの席がもう一度宣言されても重複しない。
        already = set_hands(
            _state(),
            {0: ["meeting", "meeting", "quota"], 1: ["overtime"], 2: ["quota"]},
        )
        already.meeting_declared = [1]
        played_again = play_pair(already, 0, "meeting")
        self.assertEqual(played_again.meeting_declared, [1])

    def test_meeting_declares_actual_overtime_holder(self):
        state = _state()
        state = set_hands(
            state,
            {
                0: ["meeting", "meeting", "overtime", "quota"],
                1: ["quota"],
                2: ["quota"],
            },
        )
        # 自分が残業を持っていても会議は出せる（原作の補足）。
        played = play_pair(state, 0, "meeting")
        self.assertEqual(played.meeting_declared, [0])
        self.assertEqual(played.players[0].hand, cards("overtime", "quota"))

    def test_stub_effects_are_playable_but_have_no_effect(self):
        # STUB_EFFECTS: 1-B で実装する7効果。現状はペアを出せるだけ。
        self.assertTrue(STUB_EFFECTS)
        for card_id in sorted(STUB_EFFECT_IDS):
            with self.subTest(card_id=card_id):
                state = set_hands(_state(), {0: [card_id, card_id, "quota"]})
                played = play_pair(state, 0, card_id)
                self.assertEqual(played.discarded, cards(card_id, card_id))
                self.assertEqual(played.players[0].hand, cards("quota"))
                self.assertIsNone(played.drinking_blocked)
                self.assertEqual(played.extra_plays, 0)
                self.assertEqual(played.meeting_declared, [])
                self.assertEqual(played.finish_order, [])
                event = _effect_events(played)[-1]
                self.assertEqual(event["payload"]["effect"], card_id)
                self.assertTrue(event["payload"]["stub"])

    def test_stub_flag_off_raises_not_implemented(self):
        state = set_hands(_state(), {0: ["labor", "labor", "quota"]})
        with patch("teiji.engine.STUB_EFFECTS", False):
            with self.assertRaises(NotImplementedError):
                play_pair(state, 0, "labor")


if __name__ == "__main__":
    unittest.main()
