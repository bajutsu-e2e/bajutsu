[English](BE-XXXX-idb-input-text-via-stdin.md) · **日本語**

# BE-XXXX — idb への入力テキストを argv でなく stdin 経由で渡す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-idb-input-text-via-stdin-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

idb バックエンドは `idb ui text` コマンドの引数にテキストをそのまま渡すことで文字入力を実現しています。その文字列がシークレット変数（`${secrets.*}`）や認証時に解決される OTP だった場合、値はプロセスの argv に載ったまま、その `idb` プロセスが生きている間はホスト上の他プロセスから読める状態になります。

## 動機

`bajutsu/drivers/idb.py:78` の `text_cmd` は次のように実装されています。

```python
def text_cmd(udid: str, text: str) -> list[str]:
    """The `idb` argv that types text into the focused field."""
    return ["idb", "ui", "text", "--udid", udid, text]
```

`text` はステップの `type_text` アクションから未解決のまま渡ってくるため、`${secrets.password}` や実行中に取得した OTP を入力するシナリオでは、その値がそのまま `subprocess.run` の argv 要素になります。Linux・macOS ではプロセスの argv は `ps aux` や `/proc/<pid>/cmdline` などを通じて同一ホスト上の他ユーザーから見えます。共有 CI ランナーのように「同じホスト上で別ジョブが動いている」状況は十分に起こり得るため、深刻度は中程度です。Bajutsu はシークレットの保護にすでに力を入れており（BE-0032 はログや証跡でシークレットをマスクします）、このパスだけが生の値を漏らしている点が問題です。

## 詳細設計

値の解決方法や扱いは変えず、`idb` への渡し方だけを変えます。

- `idb ui text` への入力を argv ではなく stdin 経由で渡し、プロセスのコマンドラインに値が一切現れないようにします。この設計は、位置引数のテキストを渡さない場合に `idb ui text` が stdin を読む（stdin が空のときのみ argv にフォールバックする）ことを前提とするため、実装時に idb の実際の挙動を確認します。これは idb 側の新しい機能を必要としない、呼び出し方だけの変更です。
- 変更は idb バックエンドに閉じます。`text_cmd` は `text` を argv 要素として受け取るのをやめ、ドライバの `_run` 呼び出し（または idb 専用の小さなラッパー）がテキストを subprocess の `input=` として渡すようにします。ランナーや各バックエンドが使う `Driver.type_text` インターフェース自体は変わりません。変わるのは idb によるその実装だけです。
- シークレットの解決・マスキングやシナリオのスキーマには手を加えません。今回塞ぐのは argv への漏えいであり、`${secrets.*}` の置換やログ出力の仕組みは対象外です。

## 検討した代替案

- **argv のまま残し、CI 環境の分離に委ねる。** 却下しました。あらゆる CI 環境がシングルテナントであることをドライバ層から保証・検証する手段はなく、修正自体は idb バックエンドに閉じた安価な変更だからです。
- **Bajutsu 自身の subprocess ログだけをマスクする。** 却下しました。Bajutsu はすでに生のシークレットをログに出さないようにしており、ここで漏れているのは OS レベルの argv テーブルです。アプリケーション側のログマスキングでは塞げません。
- **設定・シークレット層で対処する（例：ターゲットがシークレットを入力しようとしたら警告する）。** 却下しました。根本的な露出は解消されず、単に author の手間が増えるだけです。stdin 経由という直接的な修正であれば、author 側の挙動を変えずに漏えいそのものをなくせます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `text_cmd`／idb ドライバのテキスト入力パスを、argv ではなく stdin 経由に切り替える。
- [ ] 入力したテキストが構築後の argv に一切現れないことを検証する、ドライバ層のテストを追加・調整する。

まだ着手した PR はありません。

## 参考

- `bajutsu/drivers/idb.py:78`（`text_cmd`）
- 関連: [BE-0032](../../implemented/BE-0032-secret-variables/BE-0032-secret-variables-ja.md)
  （シークレット変数）、[BE-0035](../../implemented/BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md)
  （デバイス制御ステップ）
- 2026-07-02 のコードベース分析レポート（セキュリティ）に由来します。
