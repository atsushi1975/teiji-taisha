# 定時退社（非公式・個人用実装）

MottainaiGames 製ボードゲーム **『定時退社』**（ルールブック第4版）のオンライン対戦版を、
身内で遊ぶための**非公式な個人用実装**です。**公開予定はありません。**

現在は **フェーズ1-A（ゲームエンジン基盤）** のみです。Flask・Web UI・Docker・CLI には手を付けていません。

## 必要なもの

- Python 3.10 以上
- 依存パッケージなし（エンジンは標準ライブラリのみ。要件は空です）

## 構成

```
teiji-taisha/
  teiji/
    __init__.py   公開 API の再エクスポート
    engine.py     ルール判定のみの純粋なゲームエンジン
  tests/          unittest によるエンジンのテスト
  init.md         ルール・設計・進捗・未確定事項の引き継ぎ文書
```

## テスト

```bash
cd teiji-taisha
python3 -m unittest discover
```

## エンジンの使い方（1-A）

```python
from teiji import engine

state = engine.new_game(4, seed=1, first_player=0)
actions = engine.legal_actions(state)
state = engine.apply(state, actions[0])
view = engine.view_for(state, seat=0)
outcome = engine.result(state)  # ゲーム中は None
```

- 乱数は状態が持つ `random.Random` のみ。`seed` を指定すれば同じ展開を再現できます。
- `apply` は元の状態を壊さず、新しい状態を返します。
- `to_dict` / `from_dict` で乱数状態を含めて JSON 往復できます。
- 手番の人以外が入力する場面に備え、状態に `awaiting`（入力待ちの席）と
  `pending`（保留中の効果）の枠を持たせてあります（1-B で使用）。

## 実装済み（1-A）

- 49枚のデッキ生成と全員への配布（端数は先頭の席から）
- 手番進行（引く → 出す／出さない）、ペア判定（種類で判定、1組だけ）
- 退社（出す／引かれる）、通常終了（退社順の順位と残業保持者の敗北）
- `view_for`、構造化イベントログ（公開範囲付き）、JSON 変換
- ノルマ・飲み会・エナドリ（連鎖可）・会議の効果
- 労基・パワハラ・社内恋愛・新人教育・情報共有・取引・タバコ休憩は
  `STUB_EFFECTS = True` の間、ペアを出せるだけで効果なし（1-B で実装）

## 今後の予定

| フェーズ | 内容 |
| --- | --- |
| 1-B | 残り7効果と CLI |
| 2 | Flask + 素の JavaScript の Web UI |
| 3 | Docker 化・VPS 組み込み |
| 4 | CPU プレイヤー |

詳細は [init.md](init.md) を参照してください。

## 原作

- 『定時退社』 MottainaiGames（ルールブック第4版）
- 原作のカード画像・ルールブックの著作権は权利人に帰属します。
  本リポジトリはルールを実装した非公式の個人用ソフトウェアであり、原作とは関係ありません。
