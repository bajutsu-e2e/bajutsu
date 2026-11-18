[English](BE-0115-inprocess-collector-auth.md) · **日本語**

# BE-0115 — iOS 用インプロセスネットワークコレクタを認証する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0115](BE-0115-inprocess-collector-auth-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0115") |
| 実装 PR | [#690](https://github.com/bajutsu-e2e/bajutsu/pull/690) |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

iOS アプリのトラフィックを HTTP 経由で受け取るインプロセスのネットワークコレクタは、認証なしで
loopback ポートに届いた POST をすべて受け付けます。本提案は共有トークンを追加し、その run が起動した
アプリインスタンスだけが、コレクタへ exchange を報告できるようにします。

## 動機

`NetworkCollector`（`bajutsu/network.py:63-138`）は、`127.0.0.1` の空きポートに bind した
`ThreadingHTTPServer` を起動します（`start()`、108行目）。そのハンドラの `do_POST`
（`_make_handler`、`bajutsu/network.py:145-160`）は、受け取った JSON ボディの送信元を一切確認せず、
パースして保存します。同じマシン上で、bind されたポートへ到達できるプロセスであれば、誰でも実行中の
scenario へ捏造した exchange を POST できます。パイプラインは、その exchange を `request` / `response`
アサーションや `network.json` エビデンス成果物のための、観測済みのネットワークトラフィックとして扱います
（`bajutsu/scenario/models/assertions.py:76`、`bajutsu/runner/pipeline.py:189`）。

深刻度は Low です。コレクタは loopback 限定（`127.0.0.1` のみで `0.0.0.0` にはならない）なので、影響範囲は、
Simulator と同じマシンですでに動いている別プロセスに限られます。この攻撃面は、ネットワーク越しに到達可能な
エンドポイントに比べて、明らかに小さいものです。Simulator 自身の信頼モデルとも整合します。設計上、アプリと
コレクタはすでに同じホストの loopback を共有しています（`bajutsu/network.py` のモジュール docstring 参照）。

とはいえ、同じマシン上の任意のローカルプロセスは、偽の exchange を注入できます。別のテスト run、無関係な
ツール、マルウェアなどです。偽の exchange は、`request` アサーションを誤って成功または失敗させ、runner が
本来保証している決定性を損ないます。

## 詳細設計

1. **コレクタ起動時に run ごとの共有トークンを生成する。** `start()` がすでに割り当てている一時ポートと
   並べて生成します。
2. **トークンをアプリへ注入する。** 現在ポートを注入している方法（`BAJUTSU_COLLECTOR` の起動時環境変数）と
   同じ経路で、たとえば `BAJUTSU_COLLECTOR_TOKEN` のような環境変数として渡します。これにより Swift 側の
   送信処理（`BajutsuKit`）は、各 POST にトークンをヘッダとして添付できます。`serve` の
   `Authorization: Bearer` トークンチェックと同じ考え方に揃えます。
3. **`do_POST` でトークンを検証する。** ボディをパース・保存する前に、定数時間比較
   （`secrets.compare_digest`）で検証します。`serve` 自身のトークンチェックがすでに使っているパターンと
   同じです。トークンが欠落または不一致の場合は、黙って捨てずに 401 / 403 で拒否します。設定ミスの
   あるクライアントは、run の exchange をひそかに欠落させるのではなく、はっきり失敗します。
4. **`BajutsuKit` 側の変更は、コレクタの HTTP 送信部に限定する。** トークンはポートと同じ経路を通るため、
   scenario や CLI 側に新たな設定面は要りません。

## 検討した代替案

- **影響範囲がすでにローカルに限られているため、loopback 限定のままトークンなしにする。**
  却下しました。共有 CI ランナーや、複数ツールを動かす開発者マシンでは、「別のローカルプロセス」は仮定の
  話ではありません。修正は、`serve` 自身の認証パターンを踏襲した共有トークンです。run のエビデンス
  ストリームへ認証なしで書き込めることによる、決定性へのリスクに比べて、修正のコストは安価です。
- **コレクタを TCP ポートではなく Unix ドメインソケットに bind する。** より大がかりなプラットフォーム
  レベルの変更になるため、却下しました。アプリ側の POST 経路は、設計上 HTTP over TCP です
  （`BajutsuKit` は Simulator のアプリプロセス内で動きます）。トランスポートを切り替えると、Swift 側 SDK と
  Python 側の受信部の両方に手を入れることになります。得られる利点（プロセスレベルの分離）は、共有トークンが
  すでにアプリケーション層で達成しています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `BAJUTSU_COLLECTOR` と並べて run ごとの共有トークンを生成・注入する。
- [x] `NetworkCollector` の `do_POST` でトークンを定数時間比較により検証する。
- [x] `BajutsuKit` の HTTP 送信部を更新し、各 POST にトークンを添付する。
- [x] 未認証・不一致なトークンの POST が拒否されることを検証する回帰テストを追加する。

- [#690](https://github.com/bajutsu-e2e/bajutsu/pull/690) — 共有トークン認証を一通り実装（コレクタが `start()` でトークンを生成し、`pool` が
  `BAJUTSU_COLLECTOR_TOKEN` を注入、`do_POST` が `secrets.compare_digest` で検証、`BajutsuKit`
  が `Authorization: Bearer` として送信）し、受理・拒否の両経路に回帰テストを追加。

## 参考

`bajutsu/network.py:63-160`（`NetworkCollector`、`_make_handler`）、
`bajutsu/scenario/models/assertions.py:76`、`bajutsu/runner/pipeline.py:189`。関連: BE-0020
（マルチバックエンドのエビデンスフォールバック）。2026-07-02 のコードベース分析レポート
（セキュリティ）に基づきます。
