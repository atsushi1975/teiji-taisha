"""定時退社（MottainaiGames 製）のゲームエンジン。

ルール判定だけを持つ純粋なロジック。画面・通信・I/O には一切依存しない。
乱数は GameState が持つ ``random.Random`` に限定し、seed で再現できる。

設計の要点
----------
* 49枚を全員に配り切る。山札はなく、出したカードは ``discarded`` に積む。
* 手番は「引く（強制）→ 出す（任意）」の順。ペアはカードの種類（id）で判定する。
* ``legal_actions`` で合法手を列挙し、``apply`` で適用する。
* ``apply`` は状態を破壊せず、新しい ``GameState`` を返す（内部で deepcopy）。
* ``awaiting`` / ``pending`` は 1-B の同時選択用の枠。1-A では空のまま。
* 公開情報と非公開情報は ``view_for`` で分ける。
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

# STUB_EFFECTS: 1-B で実装する7効果をスタブ（効果なし）にするフラグ。
# スタブの対象は下の STUB_EFFECT_IDS。1-B で False にして実装に差し替える。
STUB_EFFECTS = True
STUB_EFFECT_IDS: frozenset[str] = frozenset(
    {
        "labor",
        "romance",
        "training",
        "info_share",
        "trade",
        "power_harassment",
        "smoke_break",
    }
)

PHASE_DRAW = "draw"
PHASE_PLAY = "play"
PHASE_EFFECT_INPUT = "effect_input"
PHASE_GAME_OVER = "game_over"

END_REASON_NORMAL = "normal"
END_REASON_LABOR = "labor"  # 枠のみ。1-A では発火しない。


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
    left: bool = False  # 退社したか
    finish_rank: Optional[int] = None  # 退社順（1始まり）。未退社は None


@dataclass
class GameState:
    players: list[PlayerState]
    discarded: list[Card]  # 場に出したカード（末尾が直近）
    current_player: Optional[int]
    phase: str = PHASE_DRAW  # draw | play | effect_input | game_over
    awaiting: list[int] = field(default_factory=list)  # 今入力を待っている席（1-B 用）
    pending: Optional[dict[str, Any]] = None  # 保留中の効果（1-B 用）
    extra_plays: int = 0  # エナドリによる追加出し回数
    drinking_blocked: Optional[int] = None  # 飲み会で次に出せない席
    meeting_declared: list[int] = field(default_factory=list)  # 会議で宣言した席（全員公開）
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

    def to_dict(self) -> dict[str, Any]:
        """アクションを JSON 互換 dict に変換する。"""
        return {
            "type": self.type,
            "player": self.player,
            "position": self.position,
            "card": self.card,
            "target": self.target,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Action":
        """``to_dict`` の出力からアクションを復元する。"""
        return Action(
            type=str(data["type"]),
            player=int(data["player"]),
            position=None if data.get("position") is None else int(data["position"]),
            card=None if data.get("card") is None else str(data["card"]),
            target=None if data.get("target") is None else int(data["target"]),
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


def legal_actions(state: GameState) -> list[Action]:
    """現在入力待ちの席が選べる手をすべて返す。終了時は空リスト。

    ``awaiting`` が空なら手番の席、非空ならその席の入力として扱う
    （1-B の同時選択用の分岐。1-A では ``awaiting`` は常に空）。
    """
    if state.phase == PHASE_GAME_OVER:
        return []
    actor = _actor(state)
    if actor is None:
        return []
    if state.phase == PHASE_EFFECT_INPUT:
        # 1-A では effect_input には遷移しない。1-B で pending に基づき列挙する。
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


def _validate(state: GameState, action: Action) -> None:
    """アクションの共通検証。不正なら TeijiError 系の例外を投げる。"""
    if not isinstance(action, Action):
        raise IllegalAction("Action を指定してください")
    if state.phase == PHASE_GAME_OVER:
        raise IllegalAction("ゲームは終了しています")
    actor = _actor(state)
    if actor is None:
        raise IllegalAction("操作を待っている席がいません")
    if action.player != actor:
        raise NotYourTurn(f"入力待ちは席{actor} です（操作: 席{action.player}）")
    if state.awaiting and action.player not in state.awaiting:
        raise NotYourTurn(f"席{action.player} は入力待ちの席ではありません")

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
    _log(state, "draw", {"from": source, "to": actor, "position": action.position})

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
    _log(
        state,
        "play",
        {
            "seat": actor,
            "card": action.card,
            "copies": [card.copy for card in cards],
        },
    )

    _apply_effect(state, actor, action.card)

    if not player.hand:
        # 出して0枚になった人は即退社。退社した人が発動した効果は既に処理済み。
        _retire(state, actor, reason="played")
    if _check_game_end(state):
        return

    # エナドリ: このターン、追加でもう1回ペアを出してもよい（連鎖可）。
    # 出して退社した人は追加出しを行使しない（手札が無いため）。
    if player.left:
        _end_turn(state)
        return
    if action.card == "energy":
        state.extra_plays += 1
    elif state.extra_plays > 0:
        state.extra_plays -= 1

    if state.extra_plays > 0:
        # 追加出しがある場合は同じ席で play フェーズを継続する。
        state.phase = PHASE_PLAY
    else:
        _end_turn(state)


def _apply_effect(state: GameState, actor: int, card_id: str) -> None:
    """ペアを出した効果を処理する（1-A で実装する4種＋スタブ）。"""
    if card_id == "quota":
        # ノルマ: 効果なし。
        _log(state, "effect", {"effect": "quota", "seat": actor})
        return
    if card_id == "drinking":
        # 飲み会: 次の手番の人はカードを出せない（退社済みは飛ばす）。
        target = next_alive(state, actor, 1)
        state.drinking_blocked = target
        _log(state, "effect", {"effect": "drinking", "seat": actor, "target": target})
        return
    if card_id == "energy":
        # エナドリ: 追加出し回数を増やす（解決は _apply_play 側）。
        _log(state, "effect", {"effect": "energy", "seat": actor})
        return
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
        return

    if card_id in STUB_EFFECT_IDS:
        # STUB_EFFECTS: 1-B で実装する効果。現状はペアを出せるだけで効果なし。
        if not STUB_EFFECTS:
            raise NotImplementedError(f"{card_id} の効果は 1-B で実装します")
        _log(
            state,
            "effect",
            {"effect": card_id, "seat": actor, "stub": True},
        )
        return

    raise IllegalAction(f"未知のカード種別です: {card_id}")


def _retire(state: GameState, seat: int, *, reason: str) -> None:
    """その席を退社させる。手札が0枚になったときだけ呼ぶ。"""
    player = state.players[seat]
    if player.left:
        return
    if player.hand:
        raise IllegalAction("手札が残っている席は退社できません")
    player.left = True
    player.finish_rank = len(state.finish_order) + 1
    state.finish_order.append(seat)
    _log(
        state,
        "retire",
        {"seat": seat, "reason": reason, "rank": player.finish_rank},
    )


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
    """通常終了の結果を作る（勝者＝退社順、敗者＝残業保持者）。"""
    winners = list(state.finish_order)
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
    standings = [
        {"seat": seat, "rank": index + 1, "status": "retired"}
        for index, seat in enumerate(winners)
    ]
    if loser is not None:
        standings.append(
            {"seat": loser, "rank": len(standings) + 1, "status": "loser"}
        )
    return {
        "type": "normal",
        "winners": winners,
        "loser": loser,
        "standings": standings,
    }


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
            "finish_rank": player.finish_rank,
        }
        if player.seat == seat:
            entry["hand"] = [card_to_dict(card) for card in player.hand]
        players_view.append(entry)

    return {
        "viewer": seat,
        "phase": state.phase,
        "current_player": state.current_player,
        "awaiting": list(state.awaiting),
        "pending": None if state.pending is None else dict(state.pending),
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
                "finish_rank": player.finish_rank,
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
            finish_rank=(
                None
                if player.get("finish_rank") is None
                else int(player["finish_rank"])
            ),
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
