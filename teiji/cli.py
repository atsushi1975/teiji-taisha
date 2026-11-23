"""定時退社 CLI。

``python3 -m teiji.cli --players 4 --seed 1`` で起動する。
席0を人間、席1〜（プレイヤー数-1）をランダム手のBotとして動かす。

ルール判定は必ず ``teiji.engine`` の ``legal_actions`` / ``apply`` を通す。
CLI側でルールを実装しない。
"""

from __future__ import annotations

import argparse
import random
import sys
from typing import Callable, Optional

from .engine import (
    Action,
    GameState,
    apply,
    is_game_over,
    legal_actions,
    new_game,
    result,
    view_for,
)


HUMAN_SEAT = 0


def _format_action(action: Action) -> str:
    if action.type == "draw":
        return f"カードを引く（position={action.position}）"
    if action.type == "pass":
        return "ペアを出さずに手番を終える（パス）"
    if action.type == "play":
        return f"ペアを出す（{action.card}）"
    if action.type == "target":
        return f"対象を選ぶ（target={action.target}）"
    if action.type == "position":
        return f"位置を選ぶ（position={action.position}）"
    if action.type == "positions":
        return f"位置を選ぶ（positions={list(action.positions or [])}）"
    if action.type == "select_card":
        return f"手札から1枚選ぶ（position={action.position}）"
    if action.type == "take":
        return f"見たカードを自分の手札に加える（position={action.position}）"
    if action.type == "skip":
        return "新しい手札を取らない（skip）"
    if action.type == "recipient":
        return f"譲渡先を選ぶ（target={action.target}）"
    return action.type


def _bot_action(state: GameState, seat: int, seed: int) -> Action:
    """指定席のBotは、合法手からランダムに選ぶ。"""
    actions = legal_actions(state)
    if not actions:
        raise RuntimeError("合法手がありません（ゲーム終了済みのはず）")
    rng = random.Random(f"{seed}-{seat}")
    chosen = rng.choice(actions)
    if chosen.player != seat:
        # collect 系では awaiting 全員分の合法手を返す。自分が選べる手を選び直す。
        mine = [action for action in actions if action.player == seat]
        if not mine:
            raise RuntimeError(f"席{seat} はこの保留中効果で入力できません")
        chosen = rng.choice(mine)
    return chosen


def _human_action(state: GameState, actions: list[Action]) -> Action:
    """席0（人間）の入力。表示された合法手から番号で選ぶ。"""
    mine = [action for action in actions if action.player == HUMAN_SEAT]
    choices = mine if mine else actions
    print(f"[あなたの選択] {len(choices)} 手あります。番号を入力してください。")
    for index, action in enumerate(choices):
        print(f"  {index + 1}) {_format_action(action)}")
    while True:
        line = sys.stdin.readline()
        if not line:
            raise EOFError("入力が終了しました")
        text = line.strip()
        if not text.isdigit():
            print("番号を入力してください。")
            continue
        index = int(text) - 1
        if not (0 <= index < len(choices)):
            print("範囲外の番号です。")
            continue
        return choices[index]


def _print_view(state: GameState, seat: int) -> None:
    view = view_for(state, seat)
    print(f"[view] phase={view['phase']} current_player={view['current_player']} awaiting={view['awaiting']}")
    print(f"[view] players={[(p['seat'], p.get('hand_count'), p.get('left')) for p in view['players']]}")
    print(f"[view] pending={view['pending']}")
    for event in view["events"]:
        print(f"[view:event] {event}")


def play_game(
    num_players: int,
    seed: int,
    human_choice: Optional[Callable[[GameState, list[Action]], Action]] = None,
) -> GameState:
    """席0が人間（human_choice があればその手を、無ければランダム）でプレイする。"""
    state = new_game(num_players, seed=seed, first_player=HUMAN_SEAT)
    while not is_game_over(state):
        actions = legal_actions(state)
        if not actions:
            raise RuntimeError("合法手がありません。ゲーム終了済みのはずです。")
        actor = state.awaiting[0] if state.awaiting else state.current_player
        if human_choice is not None and actor == HUMAN_SEAT:
            action = human_choice(state, actions)
        else:
            action = _bot_action(state, actor, seed)
        state = apply(state, action)
    return state


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="定時退社 CLI（席0が人間、他はBot）")
    parser.add_argument("--players", type=int, default=3, help="プレイヤー人数（3-6）")
    parser.add_argument("--seed", type=int, default=1, help="Bot 用の乱数シード")
    parser.add_argument("--first-player", type=int, default=HUMAN_SEAT, help="最初の手番の席（デフォルト: 人間）")
    args = parser.parse_args(argv)

    state = new_game(args.players, seed=args.seed, first_player=args.first_player)
    while not is_game_over(state):
        actions = legal_actions(state)
        if not actions:
            raise RuntimeError("合法手がありません。ゲーム終了済みのはずです。")
        actor = state.awaiting[0] if state.awaiting else state.current_player
        if actor == HUMAN_SEAT:
            action = _human_action(state, actions)
        else:
            action = _bot_action(state, actor, args.seed)
        state = apply(state, action)

    outcome = result(state)
    print(f"ゲーム終了: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
