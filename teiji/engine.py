"""定時退社（MottainaiGames 製）のゲームエンジン。

ルール判定だけを持つ純粋なロジック。画面・通信・I/O には一切依存しない。
乱数は GameState が持つ ``random.Random`` に限定し、seed で再現できる。

設計の要点
----------
* 49枚を全員に配り切る。山札はなく、出したカードは ``discarded`` に積む。
* 手番は「引く（強制）→ 出す（任意）」の順。ペアはカードの種類（id）で判定する。
* ``legal_actions`` で合法手を列挙し、``apply`` で適用する。
* ``apply`` は状態を破壊せず、新しい ``GameState`` を返す（内部で deepcopy）。
* ``awaiting`` / ``pending`` は効果入力待ち（1-B の同時選択含む）の枠。
* 公開情報と非公開情報は ``view_for`` で分ける。イベントログも2層（``scope``）で分ける。
* ``to_dict`` / ``from_dict`` で JSON と相互変換できる（乱数状態込み）。
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# 定数（仮決めの決め事はここに集約する）
# ---------------------------------------------------------------------------

MIN_PLAYERS = 3
MAX_PLAYERS = 6

# (カードID, 枚数)。日本語名との対応は init.md の 3.2 を参照。
CARD_SPECS: tuple[tuple[str, int], ...] = (
    ("overtime", 1),          # 残業。ペアにならず、場に出せない
    ("labor", 8),             # 労基
    ("quota", 4),             # ノルマ
    ("drinking", 4),          # 飲み会
    ("romance", 4),           # 社内恋愛
    ("training", 4),          # 新人教育
    ("info_share", 4),        # 情報共有
    ("trade", 4),             # 取引
    ("energy", 4),            # エナドリ
    ("meeting", 4),           # 会議
    ("power_harassment", 4),  # パワハラ
    ("smoke_break", 4),       # タバコ休憩
)
DECK_SIZE = sum(count for _card_id, count in CARD_SPECS)

# 引くとき、引かれる側の手札を毎回シャッフルしてから位置を適用する（仮決め）。
# False なら配布直後の手札位置として適用する（テスト・検証用）。
SHUFFLE_BEFORE_DRAW = True

PHASE_DRAW = "draw"
PHASE_PLAY = "play"
PHASE_EFFECT_INPUT = "effect_input"
PHASE_GAME_OVER = "game_over"

END_REASON_NORMAL = "normal"
END_REASON_LABOR = "labor"  # 1-B で労基終了を実装。まだ発火しないわけではない。


# ---------------------------------------------------------------------------
# 例外
# ---------------------------------------------------------------------------


class TeijiError(Exception):
    """エンジン共通の例外基底。"""


class InvalidSetup(TeijiError):
    """人数・席番号・first_player などセットアップの不正。"""


class IllegalAction(TeijiError):
    """不正な手。"""


class NotYourTurn(IllegalAction):
    """手番でないプレイヤーが操作した。"""


class InvalidTarget(IllegalAction):
    """対象の席が不正（範囲外・退社者・自分自身など）。"""


class InvalidPosition(IllegalAction):
    """引く位置が引かれる側の手札の範囲外。"""


class PairNotAvailable(IllegalAction):
    """出せるペアが手札にない。"""


class OvertimeNotPlayable(PairNotAvailable):
    """『残業』を場に出そうとした。"""


# ---------------------------------------------------------------------------
# カード
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Card:
    """1枚のカード。同種カードは ``copy``（1始まり）で区別する。"""

    id: str
    copy: int = 1


def build_deck() -> list[Card]:
    """49枚のデッキを生成する（シャッフルはしない）。"""
    deck: list[Card] = []
    for card_id, count in CARD_SPECS:
        for copy_index in range(1, count + 1):
            deck.append(Card(id=card_id, copy=copy_index))
    return deck


def card_to_dict(card: Card) -> dict[str, Any]:
    """カードを JSON 互換 dict に変換する。"""
    return {"id": card.id, "copy": card.copy}


def card_from_dict(data: dict[str, Any]) -> Card:
    """``card_to_dict`` の出力からカードを復元する。"""
    return Card(id=str(data["id"]), copy=int(data.get("copy", 1)))


# ---------------------------------------------------------------------------
# 状態
# ---------------------------------------------------------------------------


@dataclass
class PlayerState:
    seat: int
    hand: list[Card] = field(default_factory=list)
    left: bool = False  # 退社したか（順位はつけない）


@dataclass
class GameState:
    players: list[PlayerState]
    discarded: list[Card]  # 場に出したカード（末尾が直近）
    current_player: Optional[int]
    phase: str = PHASE_DRAW  # draw | play | effect_input | game_over
    awaiting: list[int] = field(default_factory=list)  # 今入力を待っている席
    pending: Optional[dict[str, Any]] = None  # 保留中の効果（5.9 のスキーマ）
    extra_plays: int = 0  # エナドリによる追加出し回数
    drinking_blocked: Optional[int] = None  # 飲み会で次に出せない席
    # 会議が発動された時点で「持っています」と宣言した席の履歴（全員公開）。
    # 宣言後に『残業』は移動し得るため、現在の残業保持者を表す状態ではない。
    # UI / CPU はこの値ではなくゲーム開始からのイベント履歴から行方を推定する前提とする。
    meeting_declared: list[int] = field(default_factory=list)
    finish_order: list[int] = field(default_factory=list)  # 退社した順の席
    end_reason: str = ""  # "" | "normal" | "labor"
    labor_info: Optional[dict[str, Any]] = None  # 労基終了の詳細（枠のみ）
    shuffle_before_draw: bool = SHUFFLE_BEFORE_DRAW
    events: list[dict[str, Any]] = field(default_factory=list)
    rng: random.Random = field(default_factory=random.Random)


@dataclass(frozen=True)
class Action:
    """1回の操作。``position`` は引かれる側の手札内の位置（0始まり）。"""

    type: str
    player: int
    position: Optional[int] = None  # draw 用
    card: Optional[str] = None  # play 用（Card.id）
    target: Optional[int] = None  # effect_input / 将来の効果用（1-A では未使用）
    positions: Optional[list[int]] = None  # 効果入力用（例: 新人教育の2枚位置）

    def to_dict(self) -> dict[str, Any]:
        """アクションを JSON 互換 dict に変換する。"""
        return {
            "type": self.type,
            "player": self.player,
            "position": self.position,
            "card": self.card,
            "target": self.target,
            "positions": None if self.positions is None else list(self.positions),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Action":
        """``to_dict`` の出力からアクションを復元する。"""
        positions = data.get("positions")
        return Action(
            type=str(data["type"]),
            player=int(data["player"]),
            position=None if data.get("position") is None else int(data["position"]),
            card=None if data.get("card") is None else str(data["card"]),
            target=None if data.get("target") is None else int(data["target"]),
            positions=None if positions is None else [int(value) for value in positions],
        )


# ---------------------------------------------------------------------------
# 基本ヘルパ
# ---------------------------------------------------------------------------


def next_alive(state: GameState, seat: int, direction: int) -> int:
    """``seat`` から ``direction`` 方向で次の在籍者（退社していない席）を返す。

    ``direction=+1`` が左隣（手番順の次）、``-1`` が右隣（手番順の前）。
    全員が退社済みの場合は ``seat`` をそのまま返す（呼び出し側で終了判定する）。
    """
    count = len(state.players)
    for step in range(1, count + 1):
        candidate = (seat + direction * step) % count
        if not state.players[candidate].left:
            return candidate
    return seat


def draw_source(state: GameState, seat: int) -> int:
    """``seat`` がカードを引く相手（右隣の在籍者）の席番号を返す。"""
    return next_alive(state, seat, -1)


def _actor(state: GameState) -> Optional[int]:
    """今操作してよい席。awaiting があればその席、なければ手番の席。"""
    if state.awaiting:
        return state.awaiting[0]
    return state.current_player


def _log(
    state: GameState,
    kind: str,
    payload: dict[str, Any],
    *,
    scope: str = "all",
    seats: Optional[list[int]] = None,
) -> None:
    """構造化イベントを1件追加する。``seq`` は 0 始まりの連番。"""
    event: dict[str, Any] = {
        "seq": len(state.events),
        "kind": kind,
        "scope": scope,
        "payload": dict(payload),
    }
    if scope == "seats":
        event["seats"] = list(seats or [])
    state.events.append(event)


def _log_move(
    state: GameState,
    move: str,
    *,
    frm: Any,
    to: Any,
    cards: list[Card],
) -> None:
    """カード移動を「誰から誰へ・何枚か」の公開イベントで記録する（中身なし）。"""
    _log(state, move, {"from": frm, "to": to, "count": len(cards)})
    if cards:
        # 当事者（from と to）だけに中身を見せる。from == to の見せ合いは別イベントを使う。
        _log(
            state,
            "card",
            {"move": move, "from": frm, "to": to, "cards": [card_to_dict(card) for card in cards]},
            scope="seats",
            seats=[frm, to],
        )


def _log_reveal(
    state: GameState,
    *,
    frm: Any,
    to: Any,
    cards: list[Card],
    scope: str = "all",
    seats: Optional[list[int]] = None,
) -> None:
    """公開（全員が見る）か、当事者のみが見るかを決めてイベントを残す。"""
    _log(
        state,
        "reveal",
        {
            "from": frm,
            "to": to,
            "count": len(cards),
            "cards": [card_to_dict(card) for card in cards],
        },
        scope=scope,
        seats=seats,
    )


def _log_show(
    state: GameState,
    *,
    frm: int,
    to: int,
    cards: list[Card],
) -> None:
    """2者間の見せ合い。移動は伴わず、情報の移動として当事者のみの中身を残す。"""
    _log(
        state,
        "show",
        {
            "from": frm,
            "to": to,
            "cards": [card_to_dict(card) for card in cards],
        },
        scope="seats",
        seats=[frm, to],
    )


def _visible(event: dict[str, Any], seat: int) -> bool:
    """そのイベントが ``seat`` から見えるか。"""
    if event.get("scope") == "all":
        return True
    return seat in (event.get("seats") or [])


# ---------------------------------------------------------------------------
# ゲーム開始
# ---------------------------------------------------------------------------


def new_game(
    num_players: int,
    seed: Optional[int] = None,
    first_player: Optional[int] = None,
) -> GameState:
    """新しいゲームを初期化する。

    Args:
        num_players: プレイヤー人数（3〜6）。
        seed: 乱数シード。同じ seed と同じ操作で同じ展開になる。
        first_player: 最初の手番の席（None ならランダム。テスト用の固定値）。
    """
    if isinstance(num_players, bool) or not isinstance(num_players, int):
        raise InvalidSetup("プレイヤー人数は整数で指定してください")
    if not (MIN_PLAYERS <= num_players <= MAX_PLAYERS):
        raise InvalidSetup(
            f"プレイヤー人数は {MIN_PLAYERS}〜{MAX_PLAYERS} 人です（指定: {num_players}）"
        )
    if isinstance(first_player, bool) or (
        first_player is not None and not isinstance(first_player, int)
    ):
        raise InvalidSetup("最初の手番は整数の席番号で指定してください")
    if first_player is not None and not (0 <= first_player < num_players):
        raise InvalidSetup(f"最初の手番の席番号が範囲外です: {first_player}")

    rng = random.Random(seed)
    resolved_first = first_player if first_player is not None else rng.randrange(num_players)

    # 49枚をシャッフルし、端数は先頭の席から1枚ずつ多く配る。
    deck = build_deck()
    rng.shuffle(deck)
    base, extra = divmod(DECK_SIZE, num_players)
    players: list[PlayerState] = []
    offset = 0
    for seat in range(num_players):
        count = base + (1 if seat < extra else 0)
        hand = deck[offset : offset + count]
        offset += count
        players.append(PlayerState(seat=seat, hand=hand))

    state = GameState(
        players=players,
        discarded=[],
        current_player=resolved_first,
        phase=PHASE_DRAW,
        shuffle_before_draw=SHUFFLE_BEFORE_DRAW,
        rng=rng,
    )
    # 配った時点でペアが揃っていても、すぐには出さない。自分の手番まで持つ。
    _log(
        state,
        "deal",
        {
            "deck_size": DECK_SIZE,
            "hand_sizes": [len(player.hand) for player in players],
            "first_player": resolved_first,
        },
    )
    return state


# ---------------------------------------------------------------------------
# 合法手の列挙
# ---------------------------------------------------------------------------


def _legal_effect_actions(state: GameState, actor: int) -> list[Action]:
    """`pending` に応じて、入力待ちの席 ``actor`` の効果入力の合法手を返す。"""
    if state.pending is None:
        return []
    effect = str(state.pending.get("effect") or "")
    step = str(state.pending.get("step") or "")
    data = state.pending.get("data") or {}

    def candidates(predicate) -> list[int]:
        return [seat for seat in range(len(state.players)) if predicate(seat)]

    def alive_except(seat: int) -> bool:
        return not state.players[seat].left and seat != actor

    if effect == "labor":
        if step == "select_target":
            seats = candidates(lambda s: s != actor and not state.players[s].left and len(state.players[s].hand) >= 1)
            return [Action(type="target", player=actor, target=t) for t in seats]
        if step == "select_position":
            order = [card_from_dict(card) for card in data.get("order") or []]
            return [Action(type="position", player=actor, position=p) for p in range(len(order))]
        if step == "choose_recipient":
            owner = int(data.get("target") or 0)
            seats = [
                seat
                for seat in range(len(state.players))
                if seat != owner and (seat == actor or not state.players[seat].left)
            ]
            return [Action(type="recipient", player=owner, target=t) for t in seats]
        return []
    if effect == "power_harassment":
        if step == "select_target":
            seats = candidates(lambda s: s != actor and not state.players[s].left)
            return [Action(type="target", player=actor, target=t) for t in seats]
        if step == "select_position":
            return [
                Action(type="position", player=actor, position=p)
                for p in range(len(state.players[actor].hand))
            ]
        return []
    if effect == "romance":
        if step == "select_target":
            seats = candidates(alive_except)
            return [Action(type="target", player=actor, target=t) for t in seats]
        return []
    if effect == "training":
        if step == "select_target":
            seats = candidates(lambda s: s != actor and not state.players[s].left and len(state.players[s].hand) >= 1)
            return [Action(type="target", player=actor, target=t) for t in seats]
        if step == "select_positions":
            target = int(data.get("target") or 0)
            hand_size = len(state.players[target].hand)
            count = 2 if hand_size >= 2 else 1
            seat_hand = state.players[target].hand
            combos: list[tuple[int, ...]] = []
            if count == 1:
                combos = [(index,) for index in range(hand_size)]
            else:
                for first in range(hand_size):
                    for second in range(first + 1, hand_size):
                        combos.append((first, second))
            return [Action(type="positions", player=actor, positions=list(combo)) for combo in combos]
        if step == "choose_take":
            actions: list[Action] = []
            if not state.players[actor].left:
                for index in range(len(data.get("viewed") or [])):
                    actions.append(Action(type="take", player=actor, position=index))
            actions.append(Action(type="skip", player=actor))
            return actions
        return []
    if effect == "info_share":
        if step == "collect":
            actions: list[Action] = []
            for seat in state.awaiting:
                if seat != actor:
                    continue
                actions.extend(
                    Action(type="select_card", player=seat, position=position)
                    for position in range(len(state.players[seat].hand))
                )
            return actions
        return []
    if effect == "trade":
        if step == "select_target":
            seats = candidates(lambda s: s != actor and not state.players[s].left and len(state.players[s].hand) >= 1)
            return [Action(type="target", player=actor, target=t) for t in seats]
        if step == "collect":
            actions = []
            for seat in state.awaiting:
                if seat != actor:
                    continue
                actions.extend(
                    Action(type="select_card", player=seat, position=position)
                    for position in range(len(state.players[seat].hand))
                )
            return actions
        return []
    return []


def legal_actions(state: GameState, seat: Optional[int] = None) -> list[Action]:
    """現在入力待ちの席が選べる手をすべて返す。終了時は空リスト。

    ``awaiting`` が空なら手番の席、非空ならその席の入力として扱う
    （1-B の同時選択用）。``seat`` を指定するとその席の視点で合法手を返す
    （将来の CPU 用。指定した席が待っていなければ空リスト）。
    """
    if state.phase == PHASE_GAME_OVER:
        return []
    if state.phase == PHASE_EFFECT_INPUT:
        actor = _actor(state)
        if actor is None:
            return []
        if seat is None:
            seat = actor
        if seat not in state.awaiting:
            return []
        return _legal_effect_actions(state, seat)
    actor = _actor(state)
    if actor is None or (seat is not None and seat != actor):
        return []
    if state.phase == PHASE_DRAW:
        source = draw_source(state, actor)
        return [
            Action(type="draw", player=actor, position=position)
            for position in range(len(state.players[source].hand))
        ]
    if state.phase == PHASE_PLAY:
        actions: list[Action] = [Action(type="pass", player=actor)]
        hand = state.players[actor].hand
        seen: set[str] = set()
        for card in hand:
            if card.id in seen or card.id == "overtime":
                continue
            seen.add(card.id)
            if sum(1 for held in hand if held.id == card.id) >= 2:
                # 同じ種類を3枚以上持っていても、出せるのは1ペアだけ。
                actions.append(Action(type="play", player=actor, card=card.id))
        return actions
    return []


# ---------------------------------------------------------------------------
# 手の適用
# ---------------------------------------------------------------------------


def _action_key(action: Action) -> tuple:
    """アクションを比較用のタプルに変換する。"""
    return (
        action.type,
        action.player,
        action.position,
        action.card,
        action.target,
        tuple(action.positions) if action.positions is not None else None,
    )


def _is_legal(state: GameState, action: Action) -> bool:
    """その状態で ``action`` が合法手かどうか（awaiting / pending 含む）。"""
    return any(_action_key(legal) == _action_key(action) for legal in legal_actions(state, action.player))


def _validate(state: GameState, action: Action) -> None:
    """アクションの共通検証。不正なら TeijiError 系の例外を投げる。"""
    if not isinstance(action, Action):
        raise IllegalAction("Action を指定してください")
    if state.phase == PHASE_GAME_OVER:
        raise IllegalAction("ゲームは終了しています")
    actor = _actor(state)
    if actor is None:
        raise IllegalAction("操作を待っている席がいません")
    if state.awaiting:
        if action.player not in state.awaiting:
            raise NotYourTurn(f"席{action.player} は入力待ちの席ではありません")
    elif action.player != actor:
        raise NotYourTurn(f"入力待ちは席{actor} です（操作: 席{action.player}）")

    if action.type == "draw":
        if state.phase != PHASE_DRAW:
            raise IllegalAction("今はカードを引くフェーズではありません")
        source = draw_source(state, actor)
        hand_size = len(state.players[source].hand)
        if isinstance(action.position, bool) or not isinstance(action.position, int):
            raise InvalidPosition("引く位置は整数で指定してください")
        if not (0 <= action.position < hand_size):
            raise InvalidPosition(
                f"引く位置 {action.position} は範囲外です（手札 {hand_size} 枚）"
            )
    elif action.type == "pass":
        if state.phase != PHASE_PLAY:
            raise IllegalAction("今はカードを出すフェーズではありません")
    elif action.type == "play":
        if state.phase != PHASE_PLAY:
            raise IllegalAction("今はカードを出すフェーズではありません")
        if not isinstance(action.card, str):
            raise IllegalAction("出すペアの種類（card）を指定してください")
        hand = state.players[actor].hand
        if action.card == "overtime":
            raise OvertimeNotPlayable("『残業』は場に出せません")
        if sum(1 for card in hand if card.id == action.card) < 2:
            raise PairNotAvailable(f"手札に {action.card} のペアがありません")
    elif action.type in (
        "target",
        "position",
        "positions",
        "select_card",
        "take",
        "skip",
        "recipient",
    ):
        if state.phase != PHASE_EFFECT_INPUT:
            raise IllegalAction("今は効果の入力フェーズではありません")
        if not _is_legal(state, action):
            raise IllegalAction(
                f"不正な効果入力です: type={action.type} player={action.player} "
                f"position={action.position} positions={action.positions} target={action.target}"
            )
        if action.type == "target" and action.target is not None and isinstance(action.target, int):
            pending = state.pending or {}
            data = pending.get("data") or {}
            if action.type == "recipient":
                owner = int(data.get("target") or 0)
                if action.player != owner:
                    raise InvalidTarget("譲渡先の選択は公開されたパワハラの持ち主が行います")
                recipient = action.target
                if recipient == owner or (state.players[recipient].left and recipient != _actor(state)):
                    raise InvalidTarget("譲渡先は自分自身または退社者ではいけません")
            else:
                target = action.target
                actor_seat = actor
                if target == actor_seat or state.players[target].left:
                    raise InvalidTarget("対象には自分自身や退社者を指定できません")
    else:
        raise IllegalAction(f"未知のアクション種別です: {action.type}")


def apply(state: GameState, action: Action) -> GameState:
    """手を適用した新しい ``GameState`` を返す。

    不正な手は :class:`TeijiError` 系の例外で拒否する。元の ``state`` は変更しない。
    """
    _validate(state, action)
    new_state = copy.deepcopy(state)
    if action.type == "draw":
        _apply_draw(new_state, action)
    elif action.type == "pass":
        _apply_pass(new_state, action)
    elif action.type == "play":
        _apply_play(new_state, action)
    elif action.type in (
        "target",
        "position",
        "positions",
        "select_card",
        "take",
        "skip",
        "recipient",
    ):
        _apply_effect_input(new_state, action)
    else:
        raise IllegalAction(f"未知のアクション種別です: {action.type}")
    return new_state


def _apply_draw(state: GameState, action: Action) -> None:
    """カードを引く。引いたあとに退社・飲み会の自動手番終了を処理する。"""
    assert action.position is not None
    actor = _actor(state)
    assert actor is not None
    source = draw_source(state, actor)
    hand = state.players[source].hand

    # 引かれる側の手札を毎回シャッフルしてから位置を適用する（仮決め）。
    # 位置を選ぶ操作自体は残す。シャッフルしても引いたカードの中身はログに出さない。
    if state.shuffle_before_draw:
        state.rng.shuffle(hand)
    card = hand.pop(action.position)
    state.players[actor].hand.append(card)
    # 公開: 誰から誰へ・何枚か。中身は含めない（非公開の card イベントに分ける）。
    _log_move(state, "draw", frm=source, to=actor, cards=[card])

    if not hand:
        _retire(state, source, reason="drawn")
    if _check_game_end(state):
        return

    if state.drinking_blocked == actor:
        # 飲み会の対象は「引く」だけ行って手番を終える（play フェーズには入らない）。
        state.drinking_blocked = None
        _log(state, "effect", {"effect": "drinking", "seat": actor, "skipped_play": True})
        _end_turn(state)
        return

    state.phase = PHASE_PLAY


def _apply_pass(state: GameState, action: Action) -> None:
    """ペアを出さずに手番を終える。エナドリの追加権もここで破棄する。"""
    actor = _actor(state)
    assert actor is not None
    _log(state, "pass", {"seat": actor})
    _end_turn(state)


def _apply_play(state: GameState, action: Action) -> None:
    """ペアを1組だけ場に出し、効果を発動して手番を進める。"""
    assert action.card is not None
    actor = _actor(state)
    assert actor is not None
    player = state.players[actor]

    # 同じ種類のどの2枚かは区別しない。3枚以上持っていても2枚だけ出す。
    cards = [card for card in player.hand if card.id == action.card][:2]
    for card in cards:
        player.hand.remove(card)
    state.discarded.extend(cards)
    # 場に出したカードはルール場（全員から見える）へ移る。移動イベントに中身を残す。
    _log(state, "play", {"from": actor, "to": "discarded", "count": len(cards)})
    _log_reveal(
        state,
        frm=actor,
        to="discarded",
        cards=cards,
        scope="all",
    )

    entered_pending = False
    # 『労基』だけは発動者の退社判定を効果解決後に遅らせる（3.6 / 5.12）。
    # それ以外（即時効果・入力を要する効果）は、ペアを出した時点で退社判定を行う。
    deferred_retire = action.card == "labor"
    if not deferred_retire and not player.hand:
        _retire(state, actor, reason="played")

    entered_pending = _apply_effect(state, actor, action.card)

    if entered_pending:
        # 効果が入力を要する。労基だけは退社判定を効果解決後に遅らせるため、
        # 発動者の退社はここでは行わない（deferred_retire の場合）。
        # pending 中は終了判定しない。最終的に _resolve_pending / finalize で行う。
        if _check_game_end(state):
            return
        if state.phase != PHASE_EFFECT_INPUT:
            _advance_after_effect(state, actor, action.card)
        return

    if not deferred_retire and state.players[actor].left:
        # 即時効果を処理したあとに退社した場合は、終了判定してから手番を進める。
        if _check_game_end(state):
            return
        _end_turn(state)
        return
    _advance_after_effect(state, actor, action.card)


def _advance_after_effect(state: GameState, actor: int, card_id: str) -> None:
    """効果を解決した後に、手番・エナドリ・退社を確定する。"""
    if state.players[actor].left:
        _end_turn(state)
        return
    if card_id == "energy":
        state.extra_plays += 1
    elif state.extra_plays > 0:
        state.extra_plays -= 1
    if state.extra_plays > 0:
        # 追加出しがある場合は同じ席で play フェーズを継続する。
        state.phase = PHASE_PLAY
        return
    _end_turn(state)


def _start_pending(
    state: GameState,
    *,
    effect: str,
    played_card: str,
    actor: int,
    step: str,
    targets: list[int],
    data: dict[str, Any],
) -> None:
    """効果入力待ちの状態（``phase="effect_input"``）へ遷移する。"""
    state.pending = {
        "effect": effect,
        "played_card": played_card,
        "actor": actor,
        "step": step,
        "targets": list(targets),
        "responses": {},
        "data": data,
    }
    state.awaiting = list(targets)
    state.phase = PHASE_EFFECT_INPUT


def _apply_effect(state: GameState, actor: int, card_id: str) -> bool:
    """ペアを出した効果を処理する。

    効果が入力を要する場合は ``phase="effect_input"`` へ遷移する（戻り値 ``True``）。
    即時解決できる効果は戻り値 ``False`` で、通常どおり手番へ戻る処理を呼ぶ。

    Returns:
        True: 効果入力待ちになった（``phase`` は ``effect_input`` のまま）。
        False: 効果を解決済みで、手番進行へ進む。
    """
    if card_id == "quota":
        # ノルマ: 効果なし。
        _log(state, "effect", {"effect": "quota", "seat": actor})
        return False
    if card_id == "drinking":
        # 飲み会: 次の手番の人はカードを出せない（退社済みは飛ばす）。
        target = next_alive(state, actor, 1)
        state.drinking_blocked = target
        _log(state, "effect", {"effect": "drinking", "seat": actor, "target": target})
        return False
    if card_id == "energy":
        # エナドリ: 追加出し回数を増やす（解決は _apply_play 側）。
        _log(state, "effect", {"effect": "energy", "seat": actor})
        return False
    if card_id == "meeting":
        # 会議: 残業を持つ人を全員に公開する。重複宣言は無視する。
        declared = [
            player.seat
            for player in state.players
            if not player.left and any(card.id == "overtime" for card in player.hand)
        ]
        for seat in declared:
            if seat not in state.meeting_declared:
                state.meeting_declared.append(seat)
        _log(state, "effect", {"effect": "meeting", "seat": actor, "declared": declared})
        return False
    if card_id == "labor":
        # 労基: 発動者が対象を選び、対象の裏向き手札から位置で1枚を公開する。
        # 発動者の退社は効果解決後に遅らせる（3.6 / 5.12）。
        _start_pending(
            state, effect="labor", played_card="labor", actor=actor,
            step="select_target", targets=[actor], data={},
        )
        _log(state, "effect", {"effect": "labor", "seat": actor, "step": "select_target", "targets": [actor]})
        if not _legal_effect_actions(state, actor):
            # 対象がいないため、即座に「元の手札に戻す」側で解決する。
            _resolve_labor_after_position(state, state.pending)
        return True
    if card_id == "power_harassment":
        # パワハラ: 発動者が対象を選び、自分の手札から1枚を渡す（入力を要する）。
        _start_pending(
            state, effect="power_harassment", played_card="power_harassment", actor=actor,
            step="select_target", targets=[actor], data={},
        )
        _log(state, "effect", {"effect": "power_harassment", "seat": actor, "step": "select_target", "targets": [actor]})
        if not _legal_effect_actions(state, actor):
            # 対象がいないため、即座に効果を終了（渡せる手札が無い扱い）する。
            _finish_pending_effect(state, state.pending)
        return True
    if card_id == "romance":
        # 社内恋愛: 発動者が対象を選んで見せ合う（入力を要する）。
        _start_pending(
            state, effect="romance", played_card="romance", actor=actor,
            step="select_target", targets=[actor], data={},
        )
        _log(state, "effect", {"effect": "romance", "seat": actor, "step": "select_target", "targets": [actor]})
        if not _legal_effect_actions(state, actor):
            # 対象がいないため、即座に効果を終了する。
            _finish_pending_effect(state, state.pending)
        return True
    if card_id == "training":
        # 新人教育: 発動者が対象を選び、2枚を見て任意に1枚を取る（入力を要する）。
        _start_pending(
            state, effect="training", played_card="training", actor=actor,
            step="select_target", targets=[actor], data={},
        )
        _log(state, "effect", {"effect": "training", "seat": actor, "step": "select_target", "targets": [actor]})
        if not _legal_effect_actions(state, actor):
            # 対象がいないため、即座に効果を終了する。
            _finish_pending_effect(state, state.pending)
        return True
    if card_id == "info_share":
        # 情報共有: 在籍者全員が手札から1枚選び、揃ってから一斉に左隣へ渡す。
        participants = [player.seat for player in state.players if not player.left]
        _start_pending(
            state, effect="info_share", played_card="info_share", actor=actor,
            step="collect", targets=participants, data={},
        )
        state.awaiting = list(participants)
        _log(state, "effect", {"effect": "info_share", "seat": actor, "targets": list(participants)})
        return True
    if card_id == "trade":
        # 取引: 発動者が対象を選び、2人同時選択で1枚ずつ交換する（入力を要する）。
        _start_pending(
            state, effect="trade", played_card="trade", actor=actor,
            step="select_target", targets=[actor], data={},
        )
        _log(state, "effect", {"effect": "trade", "seat": actor, "step": "select_target", "targets": [actor]})
        if not _legal_effect_actions(state, actor):
            # 対象がいないため、即座に効果を終了する。
            _finish_pending_effect(state, state.pending)
        return True
    if card_id == "smoke_break":
        # タバコ休憩: 在籍者全員が手札のタバコ休憩を全て場に出す。即時解決。
        _resolve_smoke_break(state, actor)
        _log(state, "effect", {"effect": "smoke_break", "seat": actor})
        return False

    raise IllegalAction(f"未知のカード種別です: {card_id}")


def _resolve_smoke_break(state: GameState, actor: int) -> None:
    """タバコ休憩: 在籍者全員（発動者を含む）が手札のタバコ休憩を全て場に出す。"""
    retiring: list[int] = []
    for player in state.players:
        if player.left:
            continue
        smoke_cards = [card for card in player.hand if card.id == "smoke_break"]
        for card in smoke_cards:
            player.hand.remove(card)
        state.discarded.extend(smoke_cards)
        _log(state, "play", {"from": player.seat, "to": "discarded", "count": len(smoke_cards)})
        if smoke_cards:
            _log_reveal(state, frm=player.seat, to="discarded", cards=smoke_cards, scope="all")
        if not player.hand and player.seat not in retiring:
            retiring.append(player.seat)
    if retiring:
        # 発動者の左隣から手番順に退社させる（仮決め）。
        order: list[int] = []
        for step in range(1, len(state.players) + 1):
            seat = (actor + step) % len(state.players)
            if seat in retiring and not state.players[seat].left:
                order.append(seat)
        for seat in order:
            _retire(state, seat, reason="smoke_break")


def _move_card(state: GameState, frm: int, to: int, card: Card, *, move: str = "give") -> None:
    """カードを席から席へ移す（手札と場の合計を49枚に保つ）。``move`` は give（渡す）/ add（加える）。"""
    state.players[frm].hand.remove(card)
    state.players[to].hand.append(card)
    _log_move(state, move, frm=frm, to=to, cards=[card])


def _retire_if_empty(state: GameState, seat: int, *, reason: str) -> None:
    """手札が0枚の席を退社させる（既に退社済みなら何もしない）。"""
    player = state.players[seat]
    if player.left or player.hand:
        return
    _retire(state, seat, reason=reason)


def _finish_pending_effect(state: GameState, pending: dict[str, Any]) -> None:
    """保留中の効果を解決し終わった後の後処理（退社・手番・エナドリ・終了）。"""
    effect = str(pending.get("effect") or "")
    actor = int(pending.get("actor") or 0)
    played_card = str(pending.get("played_card") or "")
    state.pending = None
    state.awaiting = []
    if effect == "labor":
        # 労基: 発動者の退社は効果解決後に行う。
        _retire_if_empty(state, actor, reason="played")
        if _check_game_end(state):
            return
        _advance_after_effect(state, actor, played_card)
        return
    if state.players[actor].left:
        _end_turn(state)
        return
    _advance_after_effect(state, actor, played_card)


def _resolve_labor_after_position(state: GameState, pending: dict[str, Any]) -> None:
    """労基の位置選択が済んだ後の解決（公開 → 分岐 → 退社・終了）。"""
    data = pending.get("data")
    if data is None:
        data = {}
        pending["data"] = data
    revealed = data.get("revealed") or {}
    card = (
        Card(id=str(revealed.get("id") or ""), copy=int(revealed.get("copy") or 1))
        if revealed
        else None
    )
    actor = int(pending.get("actor") or 0)
    if card is None or card.id not in ("overtime", "power_harassment"):
        # 対象がいない／対象の手札に該当カードが無い場合は、元の手札に戻す（移動なし）。
        _retire_if_empty(state, actor, reason="played")
        _finish_pending_effect(state, pending)
        return
    if card.id == "overtime":
        big_win = actor
        big_lose = int(data.get("target") or 0)
        draws = sorted(
            player.seat
            for player in state.players
            if player.seat != big_win and player.seat != big_lose
        )
        state.end_reason = END_REASON_LABOR
        state.labor_info = {"big_win": big_win, "big_lose": big_lose, "draws": draws}
        state.phase = PHASE_GAME_OVER
        state.current_player = None
        state.awaiting = []
        state.pending = None
        state.extra_plays = 0
        state.drinking_blocked = None
        _log(state, "game_over", result(state))
        _finish_pending_effect(state, pending)
        return
    if card.id == "power_harassment":
        # 公開されたカードが『パワハラ』なら、譲渡先を選ぶ（入力待ち）。
        owner = int(data.get("target") or 0)
        pending["step"] = "choose_recipient"
        state.awaiting = [owner]
        return
    # それ以外のカードは元の手札に戻す（移動なし）。
    _log(state, "effect", {"effect": "labor", "seat": actor, "returned": True})
    _retire_if_empty(state, actor, reason="played")
    _finish_pending_effect(state, pending)


def _apply_labor_input(state: GameState, pending: dict[str, Any], action: Action) -> None:
    """労基の入力を処理する（対象 → 位置 → 公開 → 分岐）。"""
    actor = int(pending.get("actor") or 0)
    step = str(pending.get("step") or "")
    data = pending.get("data")
    if data is None:
        data = {}
        pending["data"] = data
    if step == "select_target":
        if action.type != "target" or action.target is None:
            raise IllegalAction("労基の対象選択が不正です")
        data["target"] = int(action.target)
        order = list(state.players[int(action.target)].hand)
        if state.shuffle_before_draw:
            state.rng.shuffle(order)
        data["order"] = [card_to_dict(card) for card in order]
        pending["step"] = "select_position"
        state.awaiting = [actor]
        _log(state, "effect", {"effect": "labor", "seat": actor, "step": "select_position", "target": data["target"]})
        return
    if step == "select_position":
        if action.type != "position" or action.position is None:
            raise IllegalAction("労基の位置選択が不正です")
        position = int(action.position)
        order = [card_from_dict(card) for card in data.get("order") or []]
        if not (0 <= position < len(order)):
            raise IllegalAction("労基の位置が範囲外です")
        card = order[position]
        data["revealed"] = card_to_dict(card)
        _log_reveal(state, frm=int(data.get("target") or 0), to="all", cards=[card], scope="all")
        pending["step"] = "resolved"
        _resolve_labor_after_position(state, pending)
        return
    if step == "choose_recipient":
        if action.type != "recipient" or action.target is None:
            raise IllegalAction("労基の譲渡先選択が不正です")
        data["recipient"] = int(action.target)
        _move_card(state, int(data.get("target") or 0), int(action.target), Card(id=str(data.get("revealed", {}).get("id") or ""), copy=int(data.get("revealed", {}).get("copy") or 1)))
        _retire_if_empty(state, int(data.get("target") or 0), reason="power_harassment")
        _retire_if_empty(state, actor, reason="played")
        _finish_pending_effect(state, pending)
        return
    raise IllegalAction(f"未知の労基ステップです: {step}")


def _apply_power_harassment_input(state: GameState, pending: dict[str, Any], action: Action) -> None:
    """パワハラの入力を処理する（対象 → 自分の手札から1枚 → 渡す）。"""
    actor = int(pending.get("actor") or 0)
    step = str(pending.get("step") or "")
    if step == "select_target":
        if action.type != "target" or action.target is None:
            raise IllegalAction("パワハラの対象選択が不正です")
        pending["data"]["target"] = int(action.target)
        # 発動者の手札が0枚なら、渡すカードが無いためここで効果を終了する（仮決め）。
        if state.players[actor].left or not state.players[actor].hand:
            _finish_pending_effect(state, pending)
            return
        pending["step"] = "select_position"
        state.awaiting = [actor]
        _log(state, "effect", {"effect": "power_harassment", "seat": actor, "step": "select_position", "target": int(action.target)})
        return
    if step == "select_position":
        if not _legal_effect_actions(state, actor):
            # 発動者の手札が無いため、渡すカードが無く即座に効果を終了する。
            _finish_pending_effect(state, pending)
            return
        if action.type != "position" or action.position is None:
            raise IllegalAction("パワハラのカード選択が不正です")
        position = int(action.position)
        hand = state.players[actor].hand
        if not (0 <= position < len(hand)):
            raise IllegalAction("パワハラのカード位置が範囲外です")
        card = hand[position]
        _move_card(state, actor, int((pending.get("data") or {}).get("target") or 0), card)
        _retire_if_empty(state, actor, reason="played")
        _finish_pending_effect(state, pending)
        return
    raise IllegalAction(f"未知のパワハラステップです: {step}")


def _apply_romance_input(state: GameState, pending: dict[str, Any], action: Action) -> None:
    """社内恋愛の入力を処理する（対象選択 → 見せ合い）。"""
    actor = int(pending.get("actor") or 0)
    step = str(pending.get("step") or "")
    if step != "select_target":
        raise IllegalAction(f"未知の社内恋愛ステップです: {step}")
    if action.type != "target" or action.target is None:
        raise IllegalAction("社内恋愛の対象選択が不正です")
    target = int(action.target)
    pending["data"]["target"] = target
    # お互いの全手札を見せる。手札は移動しない。第三者には見えない。
    _log_show(state, frm=actor, to=target, cards=list(state.players[actor].hand))
    _log_show(state, frm=target, to=actor, cards=list(state.players[target].hand))
    _finish_pending_effect(state, pending)


def _apply_training_input(state: GameState, pending: dict[str, Any], action: Action) -> None:
    """新人教育の入力を処理する（対象 → 2枚位置 → 任意の1枚）。"""
    actor = int(pending.get("actor") or 0)
    step = str(pending.get("step") or "")
    data = pending.get("data")
    if data is None:
        data = {}
        pending["data"] = data
    if step == "select_target":
        if action.type != "target" or action.target is None:
            raise IllegalAction("新人教育の対象選択が不正です")
        target = int(action.target)
        data["target"] = target
        order = list(state.players[target].hand)
        if state.shuffle_before_draw:
            state.rng.shuffle(order)
        data["order"] = [card_to_dict(card) for card in order]
        pending["step"] = "select_positions"
        state.awaiting = [actor]
        _log(state, "effect", {"effect": "training", "seat": actor, "step": "select_positions", "target": target})
        return
    if step == "select_positions":
        if action.type != "positions" or not action.positions:
            raise IllegalAction("新人教育の2枚選択が不正です")
        positions = [int(value) for value in action.positions]
        order = [card_from_dict(card) for card in data.get("order") or []]
        viewed = [order[index] for index in positions if 0 <= index < len(order)]
        data["viewed"] = [card_to_dict(card) for card in viewed]
        pending["step"] = "choose_take"
        _log(
            state,
            "reveal",
            {"from": int(data.get("target") or 0), "to": actor, "count": len(viewed), "cards": data.get("viewed") or []},
            scope="seats",
            seats=[actor],
        )
        return
    if step == "choose_take":
        if action.type == "skip":
            _finish_pending_effect(state, pending)
            return
        if action.type != "take" or action.position is None:
            raise IllegalAction("新人教育のtakeが不正です")
        index = int(action.position)
        viewed = [card_from_dict(card) for card in data.get("viewed") or []]
        if not (0 <= index < len(viewed)):
            raise IllegalAction("新人教育のtake位置が範囲外です")
        card = viewed[index]
        _move_card(state, int(data.get("target") or 0), actor, card, move="add")
        _finish_pending_effect(state, pending)
        return
    raise IllegalAction(f"未知の新人教育ステップです: {step}")


def _apply_info_share_input(state: GameState, pending: dict[str, Any], action: Action) -> None:
    """情報共有の入力を処理する（全員の入力が揃ったら一斉に左隣へ渡す）。"""
    step = str(pending.get("step") or "")
    if step != "collect":
        raise IllegalAction(f"未知の情報共有ステップです: {step}")
    responses = pending.setdefault("responses", {})
    responses[str(action.player)] = action.to_dict()
    waiting = [int(seat) for seat in (pending.get("targets") or state.awaiting)]
    if not all(str(seat) in responses for seat in waiting):
        # 全員の入力が揃うまで保留。まだ応答していない席だけを awaiting に残す。
        state.awaiting = [seat for seat in waiting if str(seat) not in responses]
        return
    # 全員の入力が揃ったので、一斉に左隣の在籍者へ渡す。
    data = pending.get("data") or {}
    for seat in waiting:
        action_for_seat = Action.from_dict(responses[str(seat)])
        card = state.players[seat].hand[action_for_seat.position or 0]
        recipient = next_alive(state, seat, 1)
        _move_card(state, seat, recipient, card)
    _finish_pending_effect(state, pending)


def _apply_trade_input(state: GameState, pending: dict[str, Any], action: Action) -> None:
    """取引の入力を処理する（2人の同時選択が揃ったら交換する）。"""
    step = str(pending.get("step") or "")
    data = pending.get("data")
    if data is None:
        data = {}
        pending["data"] = data
    if step == "select_target":
        if action.type != "target" or action.target is None:
            raise IllegalAction("取引の対象選択が不正です")
        data["target"] = int(action.target)
        # 発動者の手札が無ければ、ここで処理を終了する（仮決め: 渡せない場合は処理しない）。
        if state.players[int(pending.get("actor") or 0)].left or not state.players[int(pending.get("actor") or 0)].hand:
            _finish_pending_effect(state, pending)
            return
        pending["step"] = "collect"
        state.awaiting = [int(pending.get("actor") or 0), int(action.target)]
        pending["targets"] = list(state.awaiting)
        _log(state, "effect", {"effect": "trade", "seat": int(pending.get("actor") or 0), "step": "collect", "target": int(action.target)})
        return
    if step != "collect":
        raise IllegalAction(f"未知の取引ステップです: {step}")
    responses = pending.setdefault("responses", {})
    responses[str(action.player)] = action.to_dict()
    actor = int(pending.get("actor") or 0)
    target = int(data.get("target") or 0)
    responses = pending.get("responses") or {}
    awaiting = [int(seat) for seat in state.awaiting]
    if str(actor) in responses and str(target) in responses:
        actor_card = state.players[actor].hand[Action.from_dict(responses[str(actor)]).position or 0]
        target_card = state.players[target].hand[Action.from_dict(responses[str(target)]).position or 0]
        _move_card(state, actor, target, actor_card)
        _move_card(state, target, actor, target_card)
        _finish_pending_effect(state, pending)
        return
    # 2人分の入力が揃うまで保留。片方だけの入力では交換しない。
    state.awaiting = [seat for seat in awaiting if str(seat) not in responses]


def _apply_effect_input(state: GameState, action: Action) -> None:
    """保留中の効果への入力（target / position / positions / select_card / take / skip / recipient）を適用する。"""
    pending = state.pending
    if pending is None:
        raise IllegalAction("効果入力待ちの保留情報がありません")
    effect = str(pending.get("effect") or "")
    step = str(pending.get("step") or "")
    action_player = int(action.player)
    if effect == "labor":
        _apply_labor_input(state, pending, action)
        return
    if effect == "power_harassment":
        _apply_power_harassment_input(state, pending, action)
        return
    if effect == "romance":
        _apply_romance_input(state, pending, action)
        return
    if effect == "training":
        _apply_training_input(state, pending, action)
        return
    if effect == "info_share":
        _apply_info_share_input(state, pending, action)
        return
    if effect == "trade":
        _apply_trade_input(state, pending, action)
        return
    raise IllegalAction(f"未知の効果です: {effect}")


def _retire(state: GameState, seat: int, *, reason: str) -> None:
    """その席を退社させる。手札が0枚になったときだけ呼ぶ。"""
    player = state.players[seat]
    if player.left:
        return
    if player.hand:
        raise IllegalAction("手札が残っている席は退社できません")
    player.left = True
    # 退社順は履歴として残すが、順位はつけない（一人負け方式）。
    state.finish_order.append(seat)
    _log(state, "retire", {"seat": seat, "reason": reason})


def _end_turn(state: GameState) -> None:
    """手番を左隣の在籍者へ移し、draw フェーズから始めさせる。"""
    actor = state.current_player
    if actor is not None:
        state.current_player = next_alive(state, actor, 1)
    state.phase = PHASE_DRAW
    state.awaiting = []
    state.pending = None
    state.extra_plays = 0


def _normal_result(state: GameState) -> dict[str, Any]:
    """通常終了の結果を作る（勝者＝退社した全員、敗者＝残業保持者の一人負け）。"""
    # 席番号順に並べるが、これは順位を表さない。退社順の履歴は finish_order / イベントにある。
    winners = sorted(state.finish_order)
    alive = [player for player in state.players if not player.left]
    loser = next(
        (
            player.seat
            for player in alive
            if any(card.id == "overtime" for card in player.hand)
        ),
        None,
    )
    # 保険: 残業が見つからない場合は最後の在籍者を敗者とする。
    if loser is None and alive:
        loser = alive[-1].seat
    return {"type": "normal", "winners": winners, "loser": loser}


def _check_game_end(state: GameState) -> bool:
    """在籍者が1人以下ならゲームを終了する。終了したかを返す。"""
    alive = [player for player in state.players if not player.left]
    if len(alive) > 1 and state.phase != PHASE_GAME_OVER:
        return False
    if state.phase == PHASE_GAME_OVER:
        return True
    state.phase = PHASE_GAME_OVER
    state.end_reason = END_REASON_NORMAL
    state.current_player = None
    state.awaiting = []
    state.pending = None
    state.extra_plays = 0
    state.drinking_blocked = None
    outcome = _normal_result(state)
    _log(state, "game_over", outcome)
    return True


# ---------------------------------------------------------------------------
# 参照・判定
# ---------------------------------------------------------------------------


def is_game_over(state: GameState) -> bool:
    """ゲームが終了したか。"""
    return state.phase == PHASE_GAME_OVER


def result(state: GameState) -> Optional[dict[str, Any]]:
    """ゲーム結果を返す。ゲーム中は None。

    通常終了と労基終了の両方を表せる形にしてある。労基終了は 1-A では発火しない。
    """
    if not is_game_over(state):
        return None
    if state.end_reason == END_REASON_LABOR:
        info = state.labor_info or {}
        return {
            "type": "labor",
            "big_win": info.get("big_win"),
            "big_lose": info.get("big_lose"),
            "draws": list(info.get("draws") or []),
        }
    return _normal_result(state)


def visible_events(state: GameState, seat: int) -> list[dict[str, Any]]:
    """``seat`` から見えるイベントだけを返す。"""
    return [event for event in state.events if _visible(event, seat)]


def _pending_view(state: GameState, seat: int) -> Optional[dict[str, Any]]:
    """その席視点の pending。他人の入力内容と内部データ（data）は見せない。"""
    pending = state.pending
    if pending is None:
        return None
    view: dict[str, Any] = dict(pending)
    view.pop("data", None)
    responses = pending.get("responses") or {}
    own_key = str(seat)
    view["responses"] = {own_key: responses[own_key]} if own_key in responses else {}
    view["responded"] = [int(s) for s in state.awaiting if str(s) in responses]
    return view


def view_for(state: GameState, seat: int) -> dict[str, Any]:
    """``seat`` 視点の公開情報を返す。

    他人の手札は枚数のみ。中身は本人にだけ返す。各人の手札の枚数は全員に公開する。
    """
    if isinstance(seat, bool) or not isinstance(seat, int):
        raise InvalidSetup("席番号は整数で指定してください")
    if not (0 <= seat < len(state.players)):
        raise InvalidSetup(f"席番号が範囲外です: {seat}")

    players_view: list[dict[str, Any]] = []
    for player in state.players:
        entry: dict[str, Any] = {
            "seat": player.seat,
            "hand_count": len(player.hand),
            "left": player.left,
        }
        if player.seat == seat:
            entry["hand"] = [card_to_dict(card) for card in player.hand]
        players_view.append(entry)

    return {
        "viewer": seat,
        "phase": state.phase,
        "current_player": state.current_player,
        "awaiting": list(state.awaiting),
        "pending": _pending_view(state, seat),
        "extra_plays": state.extra_plays,
        "drinking_blocked": state.drinking_blocked,
        "meeting_declared": list(state.meeting_declared),
        "finish_order": list(state.finish_order),
        "discarded_count": len(state.discarded),
        "players": players_view,
        "events": visible_events(state, seat),
        "result": result(state),
    }


# ---------------------------------------------------------------------------
# JSON 変換
# ---------------------------------------------------------------------------


def _tuples_to_lists(value: Any) -> Any:
    """乱数状態の tuple を JSON 互換の list に変換する。"""
    if isinstance(value, tuple):
        return [_tuples_to_lists(item) for item in value]
    if isinstance(value, list):
        return [_tuples_to_lists(item) for item in value]
    return value


def _lists_to_tuples(value: Any) -> Any:
    """JSON から読み込んだ list を乱数状態の tuple に戻す。"""
    if isinstance(value, list):
        return tuple(_lists_to_tuples(item) for item in value)
    return value


def to_dict(state: GameState) -> dict[str, Any]:
    """JSON に流せる dict へ変換する（乱数状態も含む）。"""
    return {
        "players": [
            {
                "seat": player.seat,
                "hand": [card_to_dict(card) for card in player.hand],
                "left": player.left,
            }
            for player in state.players
        ],
        "discarded": [card_to_dict(card) for card in state.discarded],
        "current_player": state.current_player,
        "phase": state.phase,
        "awaiting": list(state.awaiting),
        "pending": None if state.pending is None else dict(state.pending),
        "extra_plays": state.extra_plays,
        "drinking_blocked": state.drinking_blocked,
        "meeting_declared": list(state.meeting_declared),
        "finish_order": list(state.finish_order),
        "end_reason": state.end_reason,
        "labor_info": None if state.labor_info is None else dict(state.labor_info),
        "shuffle_before_draw": state.shuffle_before_draw,
        "events": list(state.events),
        "rng_state": _tuples_to_lists(state.rng.getstate()),
    }


def from_dict(data: dict[str, Any]) -> GameState:
    """``to_dict`` の出力から状態を復元する。"""
    players = [
        PlayerState(
            seat=int(player["seat"]),
            hand=[card_from_dict(card) for card in player.get("hand", [])],
            left=bool(player.get("left", False)),
        )
        for player in data["players"]
    ]

    rng_state = data.get("rng_state")
    rng = random.Random()
    if rng_state is not None:
        rng.setstate(_lists_to_tuples(rng_state))

    current_player = data.get("current_player")
    pending = data.get("pending")
    labor_info = data.get("labor_info")

    return GameState(
        players=players,
        discarded=[card_from_dict(card) for card in data.get("discarded", [])],
        current_player=None if current_player is None else int(current_player),
        phase=str(data.get("phase", PHASE_DRAW)),
        awaiting=[int(seat) for seat in data.get("awaiting", [])],
        pending=None if pending is None else dict(pending),
        extra_plays=int(data.get("extra_plays", 0)),
        drinking_blocked=(
            None
            if data.get("drinking_blocked") is None
            else int(data["drinking_blocked"])
        ),
        meeting_declared=[int(seat) for seat in data.get("meeting_declared", [])],
        finish_order=[int(seat) for seat in data.get("finish_order", [])],
        end_reason=str(data.get("end_reason", "") or ""),
        labor_info=None if labor_info is None else dict(labor_info),
        shuffle_before_draw=bool(data.get("shuffle_before_draw", SHUFFLE_BEFORE_DRAW)),
        events=[dict(event) for event in data.get("events", [])],
        rng=rng,
    )
