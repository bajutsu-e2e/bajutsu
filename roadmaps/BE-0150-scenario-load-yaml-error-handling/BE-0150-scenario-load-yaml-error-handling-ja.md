[English](BE-0150-scenario-load-yaml-error-handling.md) · **日本語**

# BE-0150 — `trace --explain` と `audit` で壊れたシナリオを綺麗に失敗させる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0150](BE-0150-scenario-load-yaml-error-handling-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0150") |
| 実装 PR | [#603](https://github.com/bajutsu-e2e/bajutsu/pull/603) |
| トピック | コードベース品質・技術的負債 |
| 由来 | BE-0117（CLI コマンド層のカバレッジ）の作業中に発見 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu trace --explain <シナリオ>` と `bajutsu audit <シナリオ>` は、どちらも共通ヘルパー
`load_expanded_scenarios`（`bajutsu/cli/_shared.py`）でシナリオを読み込みます。どちらも、その呼び出しを
`except (OSError, ValueError)` で囲んで、不正な入力を分かりやすいメッセージ付きの `exit 2` に変換します。

しかし、このガードは不完全です。**YAML の構文エラー**は `yaml.YAMLError`（`ParserError` や
`ScannerError` など）を送出します。これは `ValueError` のサブクラスではありません。そのためエラーは
ガードをすり抜け、`exit 1` で Python のトレースバックがそのまま表に出てしまいます。本項目は、壊れた
シナリオファイルが、構造的に不正なシナリオと同じように、きれいに失敗するようにします。

## 動機

この 2 つのコマンドは、「入力が見つからない、読み取れない、またはシナリオが不正なら、メッセージ付きで
`exit 2` する」という契約を掲げています。隣り合うコードパスは、この契約を守っています。構造的に不正な
シナリオとは、YAML としては妥当でも、内容がスキーマ検証に通らないものです。これは pydantic の
`ValidationError` を送出します。`ValidationError` は `ValueError` のサブクラスなので、ガードに捕捉され、
`failed to load scenario: …` として `exit 2` で表示されます。

ところが、オーサリングで最もよくある間違いは、別の、より悪いパスをたどります。その間違いとは、ファイルが
そもそも YAML として解析できなくなるタイプミスです。閉じ忘れた波括弧や、崩れたインデントがその例です。
生の `yaml` 例外がそのまま伝播し、利用者は 1 行のメッセージではなくスタックトレースを見せられます。終了
コードも、契約上の `2` ではなく `1` になります。

これは小さな正しさの欠落ですが、利用者への影響があります。人が最も間違えやすい入力に対してこそ、ツールは
トレースバックではなく、決定的で読みやすいエラーを返すべきです。

この欠落は、[BE-0117](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet-ja.md) の CLI 層
テストを書いている最中に見つかりました。BE-0117 のテストは、意図的に到達可能な `ValueError` 分岐を使い、
この欠落は独立した修正に委ねています。欠落を塞ぐには、新しい例外を捕捉するという挙動の変更が必要で、
テストの追加だけでは済まないからです。

## 詳細設計

作業は、ローダーとその 2 つの呼び出し元にわたって、MECE（漏れなく重複なく）に分解できます。

- **境界をどこに置くか決める。** `load_expanded_scenarios` は `Raises: OSError` / `ValueError`
  （「ファイルは解析できるが、内容が不正」）を掲げています。YAML の*構文*エラーは、ファイルが
  **解析できない**ことを意味します。そのため、今の契約の外だとも言えます。筋の通った選択肢は次の 2 つです。
  どちらか 1 つを選び、一貫して適用します。
  1. **ローダーで正規化する。** `load_expanded_scenarios` 内の `load_scenario_file(path.read_text(...))` の
     解析を囲みます。`yaml.YAMLError` は、`from e` で連鎖させて `ValueError` として送出し直します。これは
     ヘルパーがすでに掲げる `ValueError` の契約に沿います。`trace --explain` と `audit`、および将来の
     呼び出し元を含む**すべての**呼び出し元が、きれいな挙動をそのまま得られます。解析できない内容も
     `ValueError` を送出すると、docstring を更新します。
  2. **各呼び出し元で捕捉を広げる。** 2 か所の `except (OSError, ValueError)` に `yaml.YAMLError`
     も加えます。局所的ですが、コマンドモジュールに `yaml` の import が漏れ出します。将来の呼び出し元
     ごとに繰り返す必要もあります。

  選択肢 1 を推奨します。1 か所で済み、掲げた契約に一致し、呼び出し元ごとの繰り返しがないからです。
- **選んだ修正を実装する。** 選択肢 1 なら `bajutsu/cli/_shared.py` に入れます。`yaml.YAMLError` が
  生じるのは、解析の 1 か所だけだからです。エラー文言は、行動につながる内容（ファイル名と解析の問題）を
  保ちます。
- **リグレッションテストを追加する。** 本当に壊れた YAML シナリオ（閉じ忘れたフロー写像など）で、両方の
  入口をテストします。トレースバックではなく、`exit 2` と `failed to load scenario` のメッセージが出ることを
  確認します。既存の `ValueError`（構造的に不正）のテストは残します。両方の分岐をカバーし続けるためです。
- **どの呼び出し元も退行していないことを確認する。** `run` は独自のローダーを使い、
  `load_expanded_scenarios` は使わないので対象外です。`trace` / `audit` の正常系と、ほかのエラー分岐が
  変わっていないこと、そしてゲートが緑のままであることを確認します。

## 検討した代替案

- **現状のままにする。** 却下します。掲げた契約は、これらの入力を「メッセージ付きで `exit 2`」としています。
  最もよくあるオーサリングのタイプミス 1 つでトレースバックが出るのは、小さくとも実在する正しさのバグです。
- **呼び出し元で広い `Exception` を捕捉する。** 却下します。広い捕捉は、無関係なバグ（プログラミング
  エラー）まで「failed to load scenario」の陰に飲み込みます。本当の失敗を隠すのは、プロジェクトの「失敗は
  大きく鳴らす」姿勢と逆行します。修正は、解析エラーの系統だけを正確に捕捉すべきです。
- **読み込み前に YAML を別途検証する。** 冗長なので却下します。ローダーはすでに一度解析しています。
  事前解析をもう 1 回行うのは無駄な処理であり、真実の源も二重になります。既存の解析 1 回から出る例外を
  正規化するほうが簡潔です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 境界（ローダーでの正規化か、呼び出し元での捕捉か）を選び、その判断を記録する
- [x] `yaml.YAMLError` が綺麗な `exit 2` になるよう修正を実装する（docstring も更新する）
- [x] `trace --explain` と `audit` の壊れた YAML に対するリグレッションテストを追加する（既存の
      `ValueError` 分岐のテストは残す）
- [x] `trace` / `audit` の正常系と他の分岐が変わっていないこと、ゲートが緑であることを確認する

- **選択肢 1（ローダーで正規化）**を採用しました。`bajutsu/cli/_shared.py` の
  `load_expanded_scenarios` が、解析と展開の本体を囲みます。そして `yaml.YAMLError` を
  `ValueError(...) from e` として送出し直します。これにより、壊れたシナリオ（または参照コンポーネント）は、
  トレースバックではなく、掲げた `exit 2` と `failed to load scenario` メッセージで失敗します。
  `trace --explain` と `audit` の両方にリグレッションテストを追加し、既存の `ValueError`（構造的に不正）の
  テストは残しました。副次的に、同じローダーと同じ `except (OSError, ValueError)` を共有する `coverage` も、
  きれいな挙動をそのまま得られます。これは、ローダー 1 か所で直すことの根拠でもあります。

## 参考

- `bajutsu/cli/_shared.py:75`（`load_expanded_scenarios`）：`Raises: OSError` / `ValueError` を
  掲げています。`yaml.YAMLError` を送出しうる `yaml` 解析はここにあります。
- `bajutsu/cli/commands/trace.py:49-53`（`_explain`）：`yaml.YAMLError` がすり抜ける
  `except (OSError, ValueError)` です。
- `bajutsu/cli/commands/audit.py:87-91`（`audit`）：同じガード、同じ欠落です。
- [BE-0117 — CLI コマンド層の残りをテストしてから、カバレッジフロアをラチェットする](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet-ja.md)
  ：この欠落が見つかった、テスト作業中の項目です。
