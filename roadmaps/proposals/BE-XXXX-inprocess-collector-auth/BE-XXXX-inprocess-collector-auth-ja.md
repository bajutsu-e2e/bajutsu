[English](BE-XXXX-inprocess-collector-auth.md) · **日本語**

# BE-XXXX — iOS 用インプロセスネットワークコレクタを認証する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-inprocess-collector-auth-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

iOS アプリのトラフィックを HTTP 経由で受け取るインプロセスのネットワークコレクタは、認証なしで
loopback ポートに届いた POST をすべて受け付けます。本提案は共有トークンを追加し、その run が
起動したアプリインスタンスだけが exchange を報告できるようにします。

## 動機

`NetworkCollector`（`bajutsu/network.py:63-138`）は `127.0.0.1` の空きポートに bind した
`ThreadingHTTPServer` を起動し（`start()`、108 行目）、そのハンドラの `do_POST`
（`_make_handler`、`bajutsu/network.py:145-160`）は送信元を一切確認せず、受け取った JSON
ボディをパースして保存します。同じマシン上で bind されたポートへ到達できるプロセスであれば
誰でも、実行中の scenario へ捏造した exchange を POST できてしまいます。パイプラインはそれを
`request` / `response` アサーションや `network.json` エビデンス成果物のための観測済み
ネットワークトラフィックとして扱います（`bajutsu/scenario/models/assertions.py:76`、
`bajutsu/runner/pipeline.py:189`）。

深刻度は Low です。コレクタは loopback 限定（`127.0.0.1` のみで `0.0.0.0` にはならない）なので、
影響範囲は Simulator と同じマシンですでに動いている別プロセスに限られます。これはネットワーク越しに
到達可能なエンドポイントに比べて明らかに小さい攻撃面であり、Simulator 自身の信頼モデル（アプリと
コレクタは設計上すでに同じホストの loopback を共有している。`bajutsu/network.py` のモジュール
docstring 参照）とも整合します。とはいえ、別のテスト run、無関係なツール、マルウェアなど
同じマシン上の任意のローカルプロセスが偽の exchange を注入すれば、`request` アサーションを
誤って成功・失敗させられ、runner が本来保証している決定性が損なわれます。

## 詳細設計

1. **コレクタ起動時に run ごとの共有トークンを生成する。** `start()` がすでに割り当てている
   一時ポートと並べて生成します。
2. **トークンをアプリへ注入する。** 現在ポートを注入している方法（`BAJUTSU_COLLECTOR` の
   起動時環境変数）と同じ経路で、例えば `BAJUTSU_COLLECTOR_TOKEN` のような環境変数として渡します。
   これにより Swift 側の送信処理（`BajutsuKit`）が各 POST にトークンを添付できます
   （ヘッダで渡す形とし、`serve` の `Authorization: Bearer` トークンチェックと同じ考え方に
   揃えます）。
3. **`do_POST` でトークンを検証する。** ボディをパース・保存する前に、定数時間比較
   （`secrets.compare_digest`。`serve` 自身のトークンチェックがすでに使っているパターンと
   同じ）で検証します。トークンが欠落・不一致の場合は黙って捨てるのではなく 401 / 403 で拒否
   し、設定ミスのあるクライアントが run の exchange をひそかに欠落させるのではなく、はっきり
   失敗するようにします。
4. **`BajutsuKit` 側の変更はコレクタの HTTP 送信部に限定する。** トークンはポートと同じ経路を
   通るため、scenario や CLI 側に新たな設定面を追加する必要はありません。

## 検討した代替案

- **影響範囲がすでにローカルに限られているため、loopback 限定のままトークンなしにする。**
  却下しました。共有 CI ランナーや複数ツールを動かす開発者マシンでは「別のローカルプロセス」は
  仮定の話ではなく、修正（`serve` 自身の認証パターンを踏襲した共有トークン）のコストは、
  run のエビデンスストリームへ認証なしで書き込めることによる決定性へのリスクに比べて安価です。
- **コレクタを TCP ポートではなく Unix ドメインソケットに bind する。** より大がかりなプラット
  フォームレベルの変更になるため却下しました。アプリ側の POST 経路は設計上 HTTP over TCP
  であり（`BajutsuKit` は Simulator のアプリプロセス内で動く）、トランスポートを切り替えると
  Swift 側 SDK と Python 側の受信部の両方に手を入れることになります。得られる利点
  （プロセスレベルの分離）は、共有トークンによってすでにアプリケーション層で達成できています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `BAJUTSU_COLLECTOR` と並べて run ごとの共有トークンを生成・注入する。
- [ ] `NetworkCollector` の `do_POST` でトークンを定数時間比較により検証する。
- [ ] `BajutsuKit` の HTTP 送信部を更新し、各 POST にトークンを添付する。
- [ ] 未認証・不一致なトークンの POST が拒否されることを検証する回帰テストを追加する。

まだ着手した PR はありません。

## 参考

`bajutsu/network.py:63-160`（`NetworkCollector`、`_make_handler`）、
`bajutsu/scenario/models/assertions.py:76`、`bajutsu/runner/pipeline.py:189`。関連: BE-0020
（マルチバックエンドのエビデンスフォールバック）。2026-07-02 のコードベース分析レポート
（セキュリティ）に基づきます。
