[English](BE-0155-idb-input-text-via-stdin.md) · **日本語**

# BE-0155 — idb への入力テキストを argv でなく stdin 経由で渡す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0155](BE-0155-idb-input-text-via-stdin-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0155") |
| 実装 PR | [#615](https://github.com/bajutsu-e2e/bajutsu/pull/615), [#737](https://github.com/bajutsu-e2e/bajutsu/pull/737) |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

idb バックエンドは、`idb ui text` コマンドの引数にテキストをそのまま渡して文字入力を実現しています。
その文字列が、シークレット変数（`${secrets.*}`）や、authoring または run の最中に解決される OTP
だった場合、値はプロセスの argv に載ります。その `idb` プロセスが生きている間は、ホスト上の他の
プロセスから値を読めます。

## 動機

`bajutsu/drivers/idb.py:78` の `text_cmd` は、次のように実装されています。

```python
def text_cmd(udid: str, text: str) -> list[str]:
    """The `idb` argv that types text into the focused field."""
    return ["idb", "ui", "text", "--udid", udid, text]
```

`text` は、ステップの `type_text` アクションから未解決のまま渡ってきます。`${secrets.password}` や
実行中に取得した OTP を入力するシナリオでは、その値がそのまま `subprocess.run` の argv 要素に
なります。Linux と macOS では、プロセスの argv は `ps aux` や `/proc/<pid>/cmdline` などを通じて、
同一ホスト上の他ユーザーから見えます。共有 CI ランナー上の他のジョブも含まれます。

深刻度は中程度です。悪用には、`idb` プロセスが生きている間に同じホストへローカルアクセスできる
ことが必要です。しかし CI ランナーは、「同じホスト上で別ジョブが動いている」状況が現実に起こり得る、
まさにマルチテナントの環境です。シークレットは、Bajutsu がすでに保護に力を入れている資産でもあります。
BE-0032 は、ログと証跡でシークレットをマスクします。このパスだけが、生の値を漏らしています。

## 詳細設計

> **この節は当初の提案であり、実際に実装されたものとは異なります。** 以下の stdin 方式は実現でき
> ませんでした。fb-idb の `idb ui text` はテキストを位置引数として要求し、stdin を読まないためです。
> 実装版は同じく値を argv に載せませんが、その手段として fb-idb の gRPC クライアント（`client.text`）
> を用います。実際に採用した設計は *進捗* を参照してください。

値の解決方法や扱いは変えず、`idb` への渡し方だけを変えます。

- `idb ui text` への入力を argv ではなく stdin 経由で渡し、プロセスのコマンドラインに値が一切
  現れないようにします。この設計は、位置引数のテキストを渡さない場合に `idb ui text` が stdin を
  読み、stdin が空のときは argv にフォールバックすることを前提とします。その前提が正しければ、
  idb 側の新しい機能を必要としない、サブプロセスの呼び出し方だけの変更になります。実装時に、
  idb の実際の挙動を確認します。
- 変更は idb バックエンドに閉じます。`text_cmd` は、`text` を argv 要素として受け取るのをやめます。
  代わりに、ドライバの `_run` 呼び出し（または idb 専用の小さなラッパー）が、テキストを subprocess の
  `input=` として渡します。ランナーと各バックエンドが使う `Driver.type_text` インターフェース自体は
  変わりません。変わるのは、idb によるその実装だけです。
- シークレットの解決・マスキングとシナリオのスキーマには、手を加えません。塞ぐのは argv への
  漏えいです。`${secrets.*}` の置換やログ出力の仕組みは、対象外です。

## 検討した代替案

- **argv のまま残し、CI 環境の分離に委ねる。** 却下しました。あらゆる CI 環境がシングルテナントで
  あることを、ドライバ層から保証・検証する手段はありません。修正自体は、idb バックエンドに閉じた
  安価な変更です。
- **Bajutsu 自身の subprocess ログだけをマスクする。** 却下しました。Bajutsu は、すでに生の
  シークレットをログに出さないようにしています。ここで漏れているのは OS レベルの argv テーブルで、
  アプリケーション側のログマスキングでは塞げません。
- **設定・シークレット層で対処する（例：ターゲットがシークレットを入力しようとしたら警告する）。**
  却下しました。根本的な露出は解消されず、author の手間が増えるだけです。stdin 経由という直接的な
  修正であれば、author 側の挙動を変えずに漏えいそのものをなくせます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 入力テキストを `idb` の argv から外す。CLI ではなく fb-idb の gRPC クライアントで実現しました。
- [x] 入力した値が subprocess の argv に一切現れないことを検証する、ドライバ層のテストを追加・調整しました。

- 最初は stdin 経由を試み（#615）、その後壊れていることが判明しました。この設計は、位置引数の
  テキストを渡さなければ `idb ui text` が stdin を読むという前提に立っていました。実際は、そう
  なっていません。fb-idb 1.1.7 は、`text` を必須の位置引数（`type=str`）として宣言し、stdin から
  読む経路を持ちません。値を stdin に載せて `idb ui text --udid <udid>` を実行すると、
  `error: the following arguments are required: text`（exit 2）で失敗します。stdin 化は、idb の
  テキスト入力そのものを壊していました。あらゆる `type` アクションが `CalledProcessError` として
  表面化しました（crawl のデモ中に発覚）。決定的ゲートは、これを見落としました。テストが fake の
  `_run_text` を注入するだけで、実際の `idb ui text` の stdin 挙動を検証していなかったためです。
- テキスト入力について CLI の利用をやめ、fb-idb の gRPC クライアントを直接呼ぶ形に修正しました。
  `idb ui text` は `client.text()` の薄いラッパーにすぎません。`IdbDriver.type_text` は
  `_type_text_via_companion` を経由し、`ClientManager.from_udid` でセッションを開いて
  `await client.text(text=…)` を呼びます。値は gRPC で `idb_companion` に送られ、コマンドラインには
  一切現れません。本項目が本来目指していた状態を、これで実際に達成しました。実機（起動中のシミュレータ）
  で確認済みです。`Driver.type_text` インターフェースは不変です。ドライバのテストは、`_type_text`
  クラス属性を差し替えます。値がコンパニオン経路に渡ること、およびその値のために subprocess の argv が
  組み立てられないことを検証します。

## 参考

- `bajutsu/drivers/idb.py`（`_type_text_via_companion`、`IdbDriver.type_text`）
- 関連: [BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables-ja.md)
  （シークレット変数）、[BE-0035](../BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md)
  （デバイス制御ステップ）
- 2026-07-02 のコードベース分析レポート（セキュリティ）に由来します。
