"""ペア判定（種類で判定・1組だけ・残業不可）のテスト。"""

import unittest

from teiji.engine import (
    Action,
    IllegalAction,
    OvertimeNotPlayable,
    PairNotAvailable,
    apply,
    legal_actions,
    new_game,
)

from .helpers import cards, no_shuffle, play_pair, ready_to_play, set_hands


class PairTests(unittest.TestCase):
    """ペアの出し方を確認する。"""

    def test_pair_is_detected_by_card_id(self):
        state = ready_to_play(no_shuffle(new_game(3, seed=1, first_player=0)))
        state = set_hands(state, {0: ["overtime", "quota", "quota", "meeting"]})
        state = play_pair(state, 0, "quota")
        self.assertEqual(state.discarded, cards("quota", "quota"))
        self.assertEqual(state.players[0].hand, cards("overtime", "meeting"))
        # 同種のどの copy を出したかは区別しない。
        self.assertEqual([card.copy for card in state.discarded], [1, 2])

    def test_three_of_a_kind_plays_only_one_pair(self):
        state = ready_to_play(no_shuffle(new_game(3, seed=1, first_player=0)))
        state = set_hands(state, {0: ["quota", "quota", "quota", "overtime"]})
        play_actions = [a for a in legal_actions(state) if a.type == "play"]
        self.assertEqual([a.card for a in play_actions], ["quota"])
        played = play_pair(state, 0, "quota")
        self.assertEqual(len(played.discarded), 2)
        self.assertEqual(
            [card.id for card in played.players[0].hand], ["quota", "overtime"]
        )
        # 残りの1枚は手札に残り、同じ手番では追加で出せない（エナドリなし）。
        self.assertEqual(played.current_player, 1)

    def test_two_different_pairs_can_be_legal(self):
        state = ready_to_play(no_shuffle(new_game(3, seed=1, first_player=0)))
        state = set_hands(
            state, {0: ["quota", "quota", "meeting", "meeting", "overtime"]}
        )
        play_actions = [a for a in legal_actions(state) if a.type == "play"]
        self.assertEqual({a.card for a in play_actions}, {"quota", "meeting"})
        self.assertEqual(len(play_actions), 2)

    def test_single_card_is_not_a_pair(self):
        state = ready_to_play(no_shuffle(new_game(3, seed=1, first_player=0)))
        state = set_hands(state, {0: ["quota", "overtime"]})
        self.assertFalse([a for a in legal_actions(state) if a.type == "play"])
        with self.assertRaises(PairNotAvailable):
            play_pair(state, 0, "quota")

    def test_overtime_cannot_be_played(self):
        state = ready_to_play(no_shuffle(new_game(3, seed=1, first_player=0)))
        state = set_hands(state, {0: ["overtime", "labor", "labor"]})
        self.assertFalse(
            [a for a in legal_actions(state) if a.card == "overtime"]
        )
        with self.assertRaises(OvertimeNotPlayable):
            play_pair(state, 0, "overtime")
        # 労基はスタブでもペアとして出せる。
        played = play_pair(state, 0, "labor")
        self.assertEqual(played.discarded, cards("labor", "labor"))

    def test_play_action_without_card_is_rejected(self):
        state = ready_to_play(no_shuffle(new_game(3, seed=1, first_player=0)))
        state = set_hands(state, {0: ["quota", "quota"]})
        with self.assertRaises(IllegalAction):
            apply(state, Action(type="play", player=0))


if __name__ == "__main__":
    unittest.main()
