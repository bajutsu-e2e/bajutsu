[English](BE-0282-real-backend-network-coverage.md) · **日本語**

# BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0282](BE-0282-real-backend-network-coverage-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0282") |
| 実装 PR | [#1183](https://github.com/bajutsu-e2e/bajutsu/pull/1183)（web スライス）、[#1267](https://github.com/bajutsu-e2e/bajutsu/pull/1267)（web ジョブをゲートに昇格） |
| トピック | 検証とカバレッジ |
| 関連 | [BE-0020](../BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback-ja.md), [BE-0027](../BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md), [BE-0003](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci-ja.md) |
<!-- /BE-METADATA -->

## はじめに

ネットワークの経路（キャプチャ、リクエストとレスポンスのモック、そして request / event / sequence /
response-schema のアサーション）は、純粋なロジックとして完全にモデル化されユニットテストされており、その
実行経路は 2 つのバックエンドに配線されています。iOS はプロセス外でキャプチャし（アプリが各 exchange を
ループバック上の `NetworkCollector` へ POST する）、web はプロセス内でキャプチャします（Playwright ドライバが
`requestfinished` をフックし、`page.route` でモックのレスポンスを返す）。この実行経路を実デバイスや実ブラウザで
動かす CI レーンはありません。web の 2 つのジョブはどちらも `--no-network` を渡し、iOS のネットワークデモ
シナリオ（`network_live.yaml`、`network_mock.yaml`）はどの Makefile ターゲットからもどのワークフローからも
参照されていません。本項目では、この実ネットワーク経路を CI に配線します。Linux で安価な web を先に、続いて
iOS を対象にします。

## 動機

ネットワークの経路は、プロジェクトで最大の、実経路が未検証の面です。純粋なマッチャ、リクエストの一対一割り当て、
response-schema の検証、sequence のマッチングはユニットテストで検証され、collector の HTTP 受信部とトークン認証は
プロセス内の自己ループとして動かされています。どのテストも観測していないのが、実際の境界です。すなわち、アプリ側の
送信部（BajutsuKit の POST）、`page.route` による介入、ステップに対する `requestfinished` のタイミング、そして
モデルのデフォルト値ではなく実スタブから記録された `mocked` の来歴フラグです。

実ネットワークエビデンスの redaction が、そのもっとも鋭い実例です。redaction のアルゴリズムは十分にユニットテスト
されていますが、入力はすべて手で組み立てた exchange の辞書か、手で書いたログファイルです。*実際に
キャプチャされた* ヘッダやボディの中の秘密情報が、永続化されたエビデンスでマスクされるかは、どこでも
検証されていません。そうしたエビデンスを生むレーンがないからです。キャプチャした資格情報を漏らす退行が
あっても、スイート全体を通過してしまいます。

この実経路を動かすシナリオは iOS についてはすでに存在し、孤立しています。web 側は、既存のジョブが `--no-network`
で回避するのをやめるだけで足ります。したがってコストの大半は配線であり、新規の執筆ではありません。Android は
別の話でスコープ外にとどめます。Android にはネイティブのネットワークモニタがなく（`observes_network_via_driver`
は false で、adb ドライバは `NETWORK` capability を宣言しません）、まだそこにアクチュエーションの対象がないため
です。本項目では、その欠落を黙って飛ばすのではなく、Android の既知のギャップとして記録します。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **web ネットワークスモーク。** リクエストのモックとリクエストアサーションを持つ `demos/web` のシナリオを
  用意し（または既存を流用し）、`--no-network` なしで実行します。`page.route` がリクエストに介入すること、
  `requestfinished` のキャプチャが exchange を記録すること、モックされた exchange が `mocked` を立てて記録される
  ことをアサートします。
- **web の実エビデンス redaction。** そのシナリオを拡張し、キャプチャされる exchange がヘッダとボディに秘密情報を
  含むようにして、永続化されたエビデンスでそれがマスクされることをアサートします。「実ネットワークエビデンスの
  redaction」のギャップを、安価な Linux レーンで塞ぎます。
- **iOS collector の実経路。** `network_mock.yaml` と `network_live.yaml` をゲート対象外の `ios-e2e` ジョブとして
  つなぎ、BajutsuKit からループバック POST、アサーションと redaction までの連鎖を実 Simulator で走らせます。
- **Android のギャップを記録する。** Android のネットワークキャプチャはネイティブモニタが整うまでスコープ外で
  あることを、ワークフローとカバレッジの記述に明示します。欠落が見落としではなく意図した境界として読めるように
  します。
- **ゲート対象外から始める。** golden / visual の前例に従い、新しいジョブをまずシグナル（必須チェックではない）
  として着地させ、安定を確認してから必須に昇格させます。

## 検討した代替案

- **どこでも `--no-network` のままにしてユニットテストに頼る。** ユニットテストは純粋なロジックと collector の受信部を
  検証しますが、アプリ側の送信部、ブラウザの介入、実際にキャプチャしたエビデンスの redaction は一度も検証
  しません。最大の実経路のギャップが未観測のまま残ります。
- **ドライバレベルの介入ではなく外部モックサーバ（[BE-0027](../BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md)）を使う。**
  その手法は別の層（アプリが対話する独立したスタブ）であり、本項目を置き換えるのではなく補完します。本項目が
  対象とする `page.route` と collector の経路を動かすものではありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] web ネットワークスモーク: `--no-network` なしでモックとリクエストアサートを実行し、介入・キャプチャ・`mocked` フラグをアサートする。
- [x] web の実キャプチャエビデンスの redaction（ヘッダ／ボディの秘密情報が永続化エビデンスでマスクされる）。
- [ ] iOS collector の実経路: `network_mock.yaml` と `network_live.yaml` をゲート対象外ジョブとしてつなぐ。
- [x] Android のネットワークギャップを明示的に記録する（ネイティブモニタが整うまでスコープ外）。
- [x] まずシグナルとして着地させ、安定を確認してから必須に昇格させる。（web の `network (playwright)` ジョブは CI で安定を確認できたので `E2E (web)` ゲートに昇格。iOS レーンは今後。）

ログ:

- [#1183](https://github.com/bajutsu-e2e/bajutsu/pull/1183) — web スライス: デモアプリに Sync リクエストを追加し、`demos/web/scenarios/network.yaml`（モックされ、キャプチャされる、秘密情報を運ぶ `POST /api/sync`）、`fields: [password]` の redact ポリシー、`demos/web/network/assert_redaction.py`、`make -C demos/web e2e-network` ターゲット、ゲート対象外の `network (playwright)` CI ジョブを追加しました。Android のギャップはワークフローと `docs/architecture.md` に記録しました。iOS collector の実経路は後続に持ち越します。
- [#1267](https://github.com/bajutsu-e2e/bajutsu/pull/1267) — `network (playwright)` ジョブが CI で安定を確認できた（直近 50 回の実行で失敗 0）ので、シグナルから必須の `E2E (web)` ゲートへ昇格させました。android-e2e.yml で既にゲート入りしている `network (adb)` の web 版にあたります。`docs/architecture.md` と日本語ミラーも同じ変更で更新しました。

## 参考

- [BE-0020 — マルチバックエンドのエビデンスフォールバック](../BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback-ja.md)
- [BE-0027 — 外部モックサーバ](../BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md)
- [BE-0003 — codegen、トレース、ネットワークと CI（M3）](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci-ja.md)
- `bajutsu/network.py`、`bajutsu/web_network.py`、`demos/showcase/scenarios/network_live.yaml`、`demos/showcase/scenarios/network_mock.yaml`
