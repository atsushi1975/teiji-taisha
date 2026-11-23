"""テスト用の状態操作ヘルパ（ルール判定はエンジンに任せる）。"""

from teiji.engine import Action, Card, GameState, PHASE_PLAY, apply


def cards(*card_ids: str) -> list[Card]:
    """カードIDの列から手札を作る（同種ごとに copy を 1 始まりで振る）。"""
    counts: dict[str, int] = {}
    result: list[Card] = []
    for card_id in card_ids:
        copy_number = counts.get(card_id, 0) + 1
        counts[card_id] = copy_number
        result.append(Card(id=card_id, copy=copy_number))
    return result


def set_hands(state: GameState, hands: dict[int, list[str]]) -> GameState:
    """指定した席の手札を差し替える（テスト用）。"""
    for seat, card_ids in hands.items():
        state.players[seat].hand = cards(*card_ids)
    return state


def no_shuffle(state: GameState) -> GameState:
    """引く位置をテストで固定するため、シャッフルを無効にする。"""
    state.shuffle_before_draw = False
    return state


def ready_to_play(state: GameState) -> GameState:
    """「引く」を飛ばして「出す」フェーズからテストする（テスト専用）。"""
    state.phase = PHASE_PLAY
    return state


def draw_at(state: GameState, seat: int, position: int = 0) -> GameState:
    """その席が指定位置のカードを引く。"""
    return apply(state, Action(type="draw", player=seat, position=position))


def play_pair(state: GameState, seat: int, card_id: str) -> GameState:
    """その席が指定種類のペアを出す。"""
    return apply(state, Action(type="play", player=seat, card=card_id))


def pass_turn(state: GameState, seat: int) -> GameState:
    """その席がペアを出さずに手番を終える。"""
    return apply(state, Action(type="pass", player=seat))


def card_total(state: GameState) -> int:
    """手札＋場に出したカードの総数（49 で保存されるべき）。"""
    return sum(len(player.hand) for player in state.players) + len(state.discarded)
