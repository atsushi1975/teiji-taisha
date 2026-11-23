"""効果（ノルマ・飲み会・エナドリ・会議・7効果）のテスト。"""

import unittest

from teiji.engine import Action, apply, is_game_over, new_game, result, view_for

from .helpers import cards, draw_at, no_shuffle, play_pair, ready_to_play, set_hands


def _state(num_players=3):
    """手番を席0に固定し、ペアを出すフェーズから始めるテスト用状態。"""
    return ready_to_play(no_shuffle(new_game(num_players, seed=1, first_player=0)))


def _effect_events(state):
    """効果イベントだけを抽出する。"""
    return [event for event in state.events if event["kind"] == "effect"]


class EffectsTests(unittest.TestCase):
    """1-A で実装した4効果と、1-B で実装した7効果を確認する。"""

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
            state,
            {0: ["drinking", "drinking", "quota"], 1: ["quota", "quota"]},
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
        self.assertEqual(state.extra_plays, 1)
        self.assertEqual(state.pending["step"], "select_target")
        after_target = apply(state, Action(type="target", player=0, target=1))
        after_position = apply(after_target, Action(type="position", player=0, position=0))
        self.assertEqual(after_position.extra_plays, 0)
        self.assertEqual(after_position.current_player, 1)

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

    # ------------------------------------------------------------------
    # 労基（labor）
    # ------------------------------------------------------------------

    def test_labor_with_overtime_ends_game_with_labor_result(self):
        state = _state(3)
        state = set_hands(
            state, {0: ["labor", "labor", "quota"], 1: ["overtime", "quota"], 2: ["quota", "quota"]}
        )
        played = play_pair(state, 0, "labor")
        self.assertEqual(played.pending["step"], "select_target")
        self.assertEqual(played.awaiting, [0])
        after_target = apply(played, Action(type="target", player=0, target=1))
        self.assertEqual(after_target.pending["step"], "select_position")
        after_position = apply(after_target, Action(type="position", player=0, position=0))
        self.assertTrue(is_game_over(after_position))
        self.assertEqual(after_position.end_reason, "labor")
        self.assertEqual(
            result(after_position),
            {"type": "labor", "big_win": 0, "big_lose": 1, "draws": [2]},
        )
        self.assertIsNone(after_position.pending)
        self.assertEqual(after_position.awaiting, [])

    def test_labor_with_other_card_returns_card_and_advances_turn(self):
        state = _state(3)
        state = set_hands(
            state, {0: ["labor", "labor", "quota"], 1: ["quota", "quota"], 2: ["quota", "quota"]}
        )
        played = play_pair(state, 0, "labor")
        after_target = apply(played, Action(type="target", player=0, target=1))
        after_position = apply(after_target, Action(type="position", player=0, position=0))
        self.assertIsNone(after_position.pending)
        self.assertEqual(after_position.phase, "draw")
        self.assertEqual(after_position.current_player, 1)
        # 対象の手札は元のまま（カードは戻す）。
        self.assertEqual([card.id for card in after_position.players[1].hand], ["quota", "quota"])
        self.assertEqual([card.id for card in after_position.players[0].hand], ["quota"])

    def test_labor_retires_actor_after_resolution(self):
        state = _state(3)
        state = set_hands(state, {0: ["labor", "labor"], 1: ["quota", "quota"], 2: ["quota", "quota"]})
        played = play_pair(state, 0, "labor")
        after_target = apply(played, Action(type="target", player=0, target=1))
        after_position = apply(after_target, Action(type="position", player=0, position=0))
        self.assertTrue(after_position.players[0].left)
        self.assertEqual(after_position.finish_order, [0])

    def test_labor_without_any_target_ends(self):
        state = _state(3)
        state = set_hands(state, {0: ["labor", "labor", "quota"], 1: [], 2: []})
        state.players[1].left = True
        state.players[2].left = True
        played = play_pair(state, 0, "labor")
        self.assertIsNone(played.pending)
        self.assertTrue(is_game_over(played))

    # ------------------------------------------------------------------
    # パワハラ（power_harassment）
    # ------------------------------------------------------------------

    def test_power_harassment_gives_card_and_retires_actor(self):
        state = _state(4)
        state = set_hands(
            state,
            {
                0: ["power_harassment", "power_harassment", "quota"],
                1: ["quota", "quota"],
                2: ["quota", "quota"],
                3: ["quota", "quota"],
            },
        )
        played = play_pair(state, 0, "power_harassment")
        self.assertEqual(played.pending["step"], "select_target")
        after_target = apply(played, Action(type="target", player=0, target=1))
        self.assertEqual(after_target.pending["step"], "select_position")
        after_position = apply(after_target, Action(type="position", player=0, position=0))
        self.assertTrue(after_position.players[0].left)
        self.assertIsNone(after_position.pending)
        self.assertEqual(after_position.phase, "draw")
        # 発動者が渡したのは手札の quota。受け取ったのは席1。
        self.assertEqual([card.id for card in after_position.players[1].hand], ["quota", "quota", "quota"])
        self.assertEqual(after_position.players[0].hand, [])

    def test_power_harassment_ends_when_actor_has_no_card(self):
        state = _state(3)
        state = set_hands(state, {0: ["power_harassment", "power_harassment"], 1: ["quota", "quota"], 2: ["quota", "quota"]})
        played = play_pair(state, 0, "power_harassment")
        # 発動者は退職済み（手札0枚）なので、対象を選んでもここで終了する。
        after_target = apply(played, Action(type="target", player=0, target=1))
        self.assertIsNone(after_target.pending)
        self.assertEqual(after_target.phase, "draw")

    # ------------------------------------------------------------------
    # 社内恋愛（romance）
    # ------------------------------------------------------------------

    def test_romance_shows_both_hands_to_each_other(self):
        state = _state(3)
        state = set_hands(
            state,
            {0: ["romance", "romance", "quota"], 1: ["quota", "quota"], 2: ["quota", "quota"]},
        )
        played = play_pair(state, 0, "romance")
        self.assertEqual(played.pending["step"], "select_target")
        after_target = apply(played, Action(type="target", player=0, target=1))
        shows = [event for event in after_target.events if event["kind"] == "show"]
        self.assertEqual(len(shows), 2)
        self.assertEqual(shows[0]["scope"], "seats")
        self.assertEqual(shows[0]["seats"], [0, 1])
        self.assertEqual(shows[1]["scope"], "seats")
        self.assertEqual(shows[1]["seats"], [1, 0])
        self.assertIsNone(after_target.pending)
        self.assertEqual(after_target.phase, "draw")
        # 手札は移動しない。
        self.assertEqual([card.id for card in after_target.players[0].hand], ["quota"])
        self.assertEqual([card.id for card in after_target.players[1].hand], ["quota", "quota"])

    # ------------------------------------------------------------------
    # 新人教育（training）
    # ------------------------------------------------------------------

    def test_training_takes_one_of_two_shown_cards(self):
        state = _state(3)
        state = set_hands(
            state,
            {0: ["training", "training", "quota"], 1: ["quota", "quota", "quota"], 2: ["quota", "quota", "quota"]},
        )
        played = play_pair(state, 0, "training")
        after_target = apply(played, Action(type="target", player=0, target=1))
        after_positions = apply(after_target, Action(type="positions", player=0, positions=[0, 1]))
        self.assertEqual(after_positions.pending["step"], "choose_take")
        self.assertEqual([card["id"] for card in after_positions.pending["data"]["viewed"]], ["quota", "quota"])
        taken = apply(after_positions, Action(type="take", player=0, position=0))
        self.assertIsNone(taken.pending)
        self.assertEqual([card.id for card in taken.players[0].hand], ["quota", "quota"])
        self.assertEqual(len(taken.players[1].hand), 2)

    def test_training_skip_takes_nothing(self):
        state = _state(3)
        state = set_hands(
            state,
            {0: ["training", "training", "quota"], 1: ["quota", "quota", "quota"], 2: ["quota", "quota", "quota"]},
        )
        played = play_pair(state, 0, "training")
        after_target = apply(played, Action(type="target", player=0, target=1))
        after_positions = apply(after_target, Action(type="positions", player=0, positions=[0, 1]))
        skipped = apply(after_positions, Action(type="skip", player=0))
        self.assertIsNone(skipped.pending)
        self.assertEqual(skipped.phase, "draw")
        # 手札は移動しない。
        self.assertEqual(len(skipped.players[0].hand), 1)
        self.assertEqual(len(skipped.players[1].hand), 3)

    # ------------------------------------------------------------------
    # 情報共有（info_share）
    # ------------------------------------------------------------------

    def test_info_share_gives_to_left_neighbor_after_all_input(self):
        state = _state(3)
        state = set_hands(
            state,
            {
                0: ["info_share", "info_share", "quota"],
                1: ["quota", "quota", "info_share"],
                2: ["drinking", "drinking", "info_share"],
            },
        )
        played = play_pair(state, 0, "info_share")
        self.assertEqual(played.awaiting, [0, 1, 2])
        self.assertEqual(played.pending["step"], "collect")
        after_first = apply(played, Action(type="select_card", player=0, position=0))
        self.assertEqual(after_first.awaiting, [1, 2])
        after_second = apply(after_first, Action(type="select_card", player=1, position=0))
        self.assertEqual(after_second.awaiting, [2])
        resolved = apply(after_second, Action(type="select_card", player=2, position=0))
        self.assertIsNone(resolved.pending)
        self.assertEqual(resolved.phase, "draw")
        # 全員の入力が揃ったら一斉に左隣へ渡す（0→1, 1→2, 2→0）。
        self.assertEqual([card.id for card in resolved.players[0].hand], ["drinking"])
        self.assertEqual([card.id for card in resolved.players[1].hand], ["quota", "info_share", "quota"])
        self.assertEqual([card.id for card in resolved.players[2].hand], ["drinking", "info_share", "quota"])

    # ------------------------------------------------------------------
    # 取引（trade）
    # ------------------------------------------------------------------

    def test_trade_exchanges_cards_after_both_input(self):
        state = _state(3)
        state = set_hands(
            state,
            {0: ["trade", "trade", "labor", "quota"], 1: ["quota", "quota", "training"], 2: ["quota", "quota", "quota"]},
        )
        played = play_pair(state, 0, "trade")
        after_target = apply(played, Action(type="target", player=0, target=1))
        self.assertEqual(after_target.pending["step"], "collect")
        after_actor_input = apply(after_target, Action(type="select_card", player=0, position=0))
        self.assertEqual(after_actor_input.awaiting, [1])
        after_target_input = apply(after_actor_input, Action(type="select_card", player=1, position=2))
        self.assertIsNone(after_target_input.pending)
        self.assertEqual([card.id for card in after_target_input.players[0].hand], ["quota", "training"])
        self.assertEqual([card.id for card in after_target_input.players[1].hand], ["quota", "quota", "labor"])

    # ------------------------------------------------------------------
    # タバコ休憩（smoke_break）
    # ------------------------------------------------------------------

    def test_smoke_break_plays_all_and_retires(self):
        state = _state(3)
        state = set_hands(
            state,
            {0: ["smoke_break", "smoke_break", "quota"], 1: ["smoke_break"], 2: ["quota", "quota"]},
        )
        played = play_pair(state, 0, "smoke_break")
        self.assertIsNone(played.pending)
        self.assertEqual(played.phase, "draw")
        # 席1が最後のタバコ休憩で退社する。
        self.assertTrue(played.players[1].left)
        self.assertEqual(played.finish_order, [1])
        self.assertEqual([card.id for card in played.players[0].hand], ["quota"])


if __name__ == "__main__":
    unittest.main()
