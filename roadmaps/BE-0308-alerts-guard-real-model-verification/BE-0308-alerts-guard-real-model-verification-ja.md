[English](BE-0308-alerts-guard-real-model-verification.md) · **日本語**

# BE-0308 — システムアラートガードの実モデル検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0308](BE-0308-alerts-guard-real-model-verification-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0308") |
| トピック | 検証とカバレッジ |
| 関連 | [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md), [BE-0295](../BE-0295-record-crawl-real-model-verification/BE-0295-record-crawl-real-model-verification-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`agents/alerts.py` のシステムアラートガードは、ライブの AI 操作が実デバイス上で予期しない
システムダイアログ（権限プロンプト、クラッシュ時のシート）へ盲目的に作用してしまうのを止めるために
存在します。`record` と `crawl` は同じ `_build_alert_guard`（`bajutsu/cli/_shared.py`）を共有しており、
決定的な `run --dismiss-alerts` 経路（`bajutsu/cli/commands/run.py`）も、このガードを利用します。
これに触れるテストはすべて、手組みの `AlertDecision` か、テスト作者が
座標を打ち込んだだけの `FakeBlock` による tool-use レスポンスを与えており、実モデルが実際の
スクリーンショットを見て判断したものではありません。本項目は、このガードが本来果たすべき役割、
すなわち本物のアラートが表示された本物の画面に対して、Claude が dismiss（閉じる）コントロールを
正しく特定できるかどうかの実モデル検証を追加します。

## 動機

`tests/test_alerts.py` の `StubLocator` と `FakeBackend(FakeBlock("resolve_alert", ...))` は、
このガードのコードが、受け取った `AlertDecision` を正しくアクションへ配線していることを証明します。
これは配線に対する実質的で有用な検証です。しかし、このガードが本来主張する安全性は別の話です。
実デバイスから捕捉した本物のアラートダイアログを実際に視覚モデルへ与えたとき、その判断が、隣にある
破壊的な「削除」ボタンなどではなく、正しい dismiss の座標へ確実に着地するかどうかは何も証明しません。
ここで実モデルが誤った判断を返すことは些細なバグではなく、このガードが本来防ぐべき当のものに失敗する
ことを意味します。しかも現行のテストスイートはそれを一切検出しません。実モデルに本物のアラートを
見せて判断させるテストが1つもないからです。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **本物のアラートによるフィクスチャ一式を用意する**：ショーケースアプリ上の実際のシステム
  アラートのスクリーンショットを捕捉します（最低限、権限プロンプトを1つ。再現できるならクラッシュ
  /エラーシートも）。少なくとも1つは、近くに破壊的なコントロールがあるダイアログを含め、
  フィクスチャ一式が「*何らかの*ボタンを見つけた」と「*正しい*ボタンを見つけた」を区別できる
  ようにします。
- **API キーでゲートしたライブ検証テスト**：各フィクスチャに対して、実際の認証情報でガードの
  実際の視覚処理経路を呼び出し、返ってきた座標が正しい dismiss コントロールの frame 内に収まって
  いることを検証します。単に何らかの決定が返ってきたことだけを確認するのではありません。
- **まずゲート対象外のシグナルとして着地させる**：
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例に従い、このガードの呼び出し元に触れる CI レーンへシグナルとしてまず組み込み、必須
  チェックにするのはそのあとで検討します。本項目が検証するのはガードの実際の精度のみであり、
  ガードの位置づけ（Tier 1、ライブの AI 操作）を変えたり、決定的な `run` の判定にガードを近づけたり
  することはありません（prime directive 1）。

## 検討した代替案

- **ガードの配線が正しいことを根拠に、ユニットテストを信頼する**：正しい配線は、ガードが渡された
  `AlertDecision` に従って行動することを保証しますが、実モデルが本物のアラートを見たときに常に
  *正しい*決定を出すかどうかについては何も語りません。それこそがここで問われている安全性の
  性質です。
- **`record` の一般的なライブ利用テストでカバーされているとみなす**：そのようなテストは現状
  存在しません。`record` 自体に実モデルによる CI カバレッジがないためです。したがって本項目が
  乗れる既存の安全網はありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] ショーケースアプリから本物のアラートによるフィクスチャ一式を捕捉する（破壊的コントロールを
  含むケースを1つ以上含める）。
- [ ] ガードが正しい dismiss コントロールを特定できることを検証する、API キーでゲートしたライブ
  テストを追加する。
- [ ] ゲート対象外のシグナルとして CI に組み込む。

## 参考

- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- [BE-0295 — record と crawl の propose ループに対する実モデル検証](../BE-0295-record-crawl-real-model-verification/BE-0295-record-crawl-real-model-verification-ja.md)
- `bajutsu/agents/alerts.py`、`tests/test_alerts.py`（`StubLocator`、`FakeBackend`/`FakeBlock`）
