[English](BE-XXXX-imap-pop3-http-wait-parse-json.md) · **日本語**

# BE-XXXX — IMAP と POP3 のメール受信、HTTP 状態コードの待機、JSON 文字列の解析

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-imap-pop3-http-wait-parse-json-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md)、[BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry-ja.md)、[BE-0440](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction-ja.md)、[BE-0472](../BE-0472-eval-step-value-operations/BE-0472-eval-step-value-operations-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`email` ステップは Hyper Text Transfer Protocol（HTTP）のエンドポイントからメールを読みます。応答の形式は Java Script Object Notation（JSON）です。本項目は、メールの取得元と、シナリオが HTTP の応答を読む方法を変えます。変更は次の 4 点です。

| 変更 | 内容 |
|---|---|
| メールの取得方式 | `email` は Internet Message Access Protocol（IMAP）か Post Office Protocol version 3（POP3）で受信箱を読みます。HTTP の受信箱は削除します。 |
| HTTP の待機 | `http` ステップは、期待する状態コードが返るまでリクエストを繰り返せるようになります。 |
| JSON の解析 | 新しい `parseJson` ステップが、任意の JSON 文字列からパス指定で値を取り出します。 |
| `extractBody` | `http` ステップから削除します。役割は `parseJson` が引き継ぎます。 |

4 点のうち 2 点は、既存のファイルを壊します。Uniform Resource Locator（URL）を指定した `mailbox:` の設定は読み込めなくなります。`extractBody` を使うシナリオも同様です。

## 動機

現在の受信箱は JSON しか受け付けません。ランナーはすべての応答を JSON として解析します。そのため、平文を返すサービスでは最初の取得でステップが失敗します。返す JSON もサービスごとに違います。設定の `messages` と `fields` はキー名の違いを吸収できます。しかし、応答の構造そのものの違いは吸収できません。

HTTP のサービスは、もともと `http` ステップで呼び出せます。`email` の内側に 2 つ目の HTTP 読み取りを持つと機能が重複し、しかも対応できるサービスは少なくなります。一方、メールの取得には IMAP と POP3 という広く提供されている方式があります。[BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md) は、IMAP を需要が出るまで見送りました。[BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry-ja.md) は、新しい取得方式を差し込むレジストリを用意しています。

現状の `http` ステップでは、HTTP の受信箱を置き換えられません。リクエストを 1 回送ると次へ進むためです。数秒後に届く値は取得できません。

JSON の読み取りにも関連する制約があります。[BE-0440](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction-ja.md) の項目抽出は、`http` ステップ内の応答本文にしか使えません。変数に入った文字列を読む手段はありません。`eval` ステップの `json` 操作は JSON 配列をリストに変えるだけで、オブジェクトの中は読めません。

変更の効果は、現在は成功させられない 2 つのシナリオで確認できます。1 つは、IMAP か POP3 の受信箱から認証コードを読むシナリオです。もう 1 つは、HTTP サービスの応答を待ち、保存した文字列から入れ子の値を読むシナリオです。

## 詳細設計

### 受信箱の設定

`targets.<name>.services.mailbox` にはメールサーバーを記述します。

```yaml
services:
  mailbox:
    kind: imap                          # 必須。imap か pop3
    host: "${secrets.MAIL_HOST}"
    port: 993                           # 省略可
    security: tls                       # tls（既定）か starttls
    username: "${secrets.MAIL_USER}"
    password: "${secrets.MAIL_PASSWORD}"
    folder: INBOX                       # imap のみ。デフォルトは INBOX
```

ポートのデフォルト値は `kind` と `security` で決まります。

| `kind` | `tls` | `starttls` |
|---|---|---|
| `imap` | 993 | 143 |
| `pop3` | 995 | 110 |

接続は常に Transport Layer Security（TLS）を使い、サーバー証明書を検証します。暗号化しない接続方式は用意しません。認証はユーザー名とパスワードで行います。POP3 の受信箱は 1 つだけなので、`kind: pop3` に `folder` を指定すると設定エラーになります。

設定項目の `url`、`headers`、`messages`、`fields` は削除します。いずれかが残った設定は読み込みに失敗します。エラーメッセージでは IMAP、POP3、`http` ステップを案内します。BE-0186 のレジストリは残し、組み込みのアダプターを `imap` と `pop3` に置き換えます。どちらも Python の標準ライブラリで実装するので、依存は増えません。

### `email` ステップ

シナリオの書き方は、`match`、`extract`、必須の `timeout` のままです。対象はステップ開始後に届いたメールで、複数が一致すれば最新を選びます。

読み取りのインタフェースは 2 つに分けます。問い合わせのたびに受信箱全体をダウンロードしないためです。

```python
class MailboxReader(Protocol):
    def list_ids(self, timeout: float) -> frozenset[str]: ...
    def fetch(self, ids: Collection[str], timeout: float) -> list[MailboxMessage]: ...
```

ハンドラーは、開始時点にあるメールの識別子を記録します。以降の問い合わせでは識別子の一覧を取り直し、未確認のメールだけを取得します。一致しなかったメールは確認済みに加えるので、ダウンロードは 1 回で済みます。接続は呼び出しごとに開き直します。POP3 のセッションは、開始時点の受信箱しか見せないためです。

| 観点 | IMAP | POP3 |
|---|---|---|
| メールの識別子 | `UIDVALIDITY` と 固有識別子の組 | `UIDL` が返す固有識別子 |
| 本文の取得 | 読み取り専用で開き `BODY.PEEK[]` を使用 | `RETR` を使用 |
| 受信時刻 | `INTERNALDATE` | `Date` ヘッダー |
| 識別子を返せないサーバー | 該当なし | ステップを失敗にする |

どちらのアダプターも、メールの削除と既読化は行いません。

生のメールは、`bajutsu/common/mailbox.py` の純粋な関数で `MailboxMessage` に変換します。

| 項目 | 変換の規則 |
|---|---|
| `to` | `To` ヘッダーの全アドレス。`match.to` は大文字と小文字を区別せず、いずれかと一致すれば成立 |
| `subject` | デコードしたヘッダーの文字列 |
| `body` | 添付ではない `text/plain` パートの連結。該当がなければ `text/html` パートのソース |
| `received_at` | 協定世界時の時刻。書式は `ISO 8601`。使える日付がなければ空文字列 |

接続エラー、ログインの拒否、証明書エラーは、ステップの失敗になります。エラーメッセージには、パスワードとメール本文を含めません。

### HTTP 状態コードの待機

`http` ステップに、秒単位の `timeout` を任意項目として追加します。

```yaml
- http:
    url: "${secrets.API}/codes/latest"
    status: 200
    timeout: 30
    saveBody: response
```

`timeout` を指定すると、応答が `status` と一致するまで約 1 秒間隔でリクエストを繰り返します。規則は次の表のとおりです。

| 状況 | 動作 |
|---|---|
| `status` のない `timeout` | シナリオの読み込みエラー |
| `GET` と `HEAD` 以外のメソッド | シナリオの読み込みエラー。再送でデータが重複して作られる恐れがあるため |
| 異なる状態コード | 次の試行へ進む |
| 接続エラー | 直ちに失敗 |
| 1 回のリクエストの上限 | 残り時間まで。最長 30 秒 |
| 成功 | 成功した応答の本文を `saveBody` に保存 |
| 期限切れ | 最後に受け取った状態コードを示して失敗 |
| 実行のキャンセル | ほかの条件待ちと同じく待機を中断 |

`timeout` を指定しない場合の動作は、現在と変わりません。成功の条件は状態コードだけです。データがそろう前から本文が空の 200 を返すサービスには、この待機を使えません。

また、`email` と違い、新しいデータかどうかは判定しません。古い値を拾わないためには、実行ごとに固有の対象を問い合わせます。たとえば `generate` ステップで作ったアドレスを使います。

### `parseJson` ステップ

```yaml
- parseJson:
    from: "${vars.response}"
    extract:
      - { var: code, path: "data.items[0].code" }
      - { var: userId, path: "data.user.id" }
```

`from` は、通常の `${...}` 置換が使える文字列です。`extract` は、`{ var, path }` を 1 件以上並べたリストです。次の規則は `extractBody` から引き継ぎます。

| 観点 | 規則 |
|---|---|
| パスの文法 | 変更なし。オブジェクトのキーと、0 始まりの `[n]` 添字 |
| 値の保存 | 文字列はそのまま。ほかの型は空白を詰めた JSON 文字列 |
| 失敗 | JSON でない入力、不正なパス、解決できないパス |
| 複数の指定 | すべて解決できたときに限り書き込む |
| `var` の重複 | 読み込みエラー。置換後にも再検査 |
| パスへの置換 | 値は 1 区間だけを埋め、`.`、`[`、`]` を含められない |

秘密値の扱いは `eval` ステップ（[BE-0472](../BE-0472-eval-step-value-operations/BE-0472-eval-step-value-operations-ja.md)）に合わせます。エラーメッセージには、入力の文字列も置換後の値も含めません。秘密値を含む入力から読んだ値は秘密値とみなし、以降の成果物でマスクします。`from` の文字数の上限は、`eval` の入力と同じ 1,048,576 文字です。

### `extractBody` の削除

`http` ステップから `extractBody` を削除します。`extractBody` を使うシナリオは読み込みに失敗し、エラーメッセージで `saveBody` と `parseJson` の組み合わせを案内します。

```yaml
- http: { url: "${secrets.API}/login", status: 200, saveBody: login }
- parseJson:
    from: "${vars.login}"
    extract:
      - { var: token, path: "data.token" }
```

### 最優先原則との関係

| 原則 | 本設計での守り方 |
|---|---|
| 合否の判定にモデルを使わない | メールの照合、状態コードの比較、パスの解決は固定の規則 |
| 決定性 | どちらの待機も、条件の成立か必須の期限で終了 |
| アプリ非依存 | サーバーと認証情報は `targets.<name>` に置き、シナリオには書かない |

### 作業手順

| # | 作業 | 主なファイル | 完了条件 | 前提 |
|---|---|---|---|---|
| 1 | `parseJson` のモデル、ハンドラー、読み込み検査、秘密値の記録を追加 | `bajutsu/common/scenario/`、`bajutsu/common/orchestrator/` | 上記の規則をユニットテストで確認 | なし |
| 2 | `extractBody` を削除し、テストを `parseJson` へ移行 | `http_request.py`、`_http_extract.py`、`handlers/http.py`、`substitution.py` | 旧シナリオが移行の案内付きで読み込みに失敗 | 1 |
| 3 | `http` に状態コードの待機を追加 | `http_request.py`、`orchestrator/loop/` のステップ振り分け | 代役のサーバーと時計で成功、期限切れ、キャンセルを確認 | なし |
| 4 | メールのモデルと読み取りインタフェースを置換 | `bajutsu/common/mailbox.py`、`mailbox_reader.py`、`_do_email` | 生メールの固定データで解析と選択のテストが成功 | なし |
| 5 | IMAP と POP3 のアダプターと新しい設定を追加し、HTTP の読み取りを削除 | `runner/mailbox.py`、`config/schema/mailbox.py` | `imaplib` と `poplib` の代役でテストが成功し、旧設定は読み込みに失敗 | 4 |
| 6 | 日英の文書を更新 | `docs/scenarios.md`、`docs/configuration.md`、`docs/dsl-grammar.md`、`docs/architecture.md` と日本語版 | 削除した設定を説明するページがない | 2、3、5 |
| 7 | 実際のテスト用メールサーバーで確認 | なし | IMAP と POP3 のそれぞれで `email` ステップが成功 | 5 |

品質ゲートはメールサーバーに接続できないため、最後の作業は手動の確認です。

## 検討した代替案

| 代替案 | 採用しなかった理由 |
|---|---|
| HTTP の受信箱を残し、平文形式を追加 | 応答の構造はサービスごとに違うため、形式の設定が増え続ける |
| `email` を廃止し、すべて `http` で受信 | IMAP か POP3 しか提供しない受信箱を読めない |
| IMAP、POP3 と `kind: http` の併存 | HTTP サービスを読む経路が 2 本になり、JSON の読み替え設定も残る |
| 複数ステップを囲む汎用の再試行構文 | 画面操作まで繰り返せてしまい、必要を大きく超えて言語が広がる |
| 本文がパターンに一致するまで待機 | 要望は状態コードだけ。条件は後続の項目で追加できる |
| `eval` にパス取得の操作を追加 | 複数の値を取るには専用ステップが読みやすく、`{ var, path }` の形も保てる |
| `extractBody` と `parseJson` の併存 | 同じ役割の仕組みが 2 つになり、規則を二重に保守する |
| メールのログインで OAuth 2.0 に対応 | トークンの発行と更新は独立した機能。OAuth 必須のサーバーは対象外 |
| 暗号化しないメール接続を許可 | 認証情報が平文でネットワークを流れる |

## 進捗

> 作業の進行に合わせて更新します。チェックリストは *詳細設計* の MECE な作業分解に対応し（作業単位ごとに
> 1 項目）、ログは変更内容と時期を（古い順に）記録して PR にリンクします。

- [ ] `parseJson` ステップ
- [ ] `extractBody` の削除
- [ ] `http` の状態コード待機
- [ ] メールのモデルと読み取りインタフェース
- [ ] IMAP と POP3 のアダプター、新しい設定、HTTP 読み取りの削除
- [ ] 日英の文書
- [ ] メールサーバーでの手動確認

## 参考

- [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md)：One Time Password（OTP）と `email` ステップと HTTP 受信箱の起点
- [BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry-ja.md)：新しいアダプターを差し込むレジストリ
- [BE-0440](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction-ja.md)：`parseJson` が引き継ぐパスの文法
- [BE-0472](../BE-0472-eval-step-value-operations/BE-0472-eval-step-value-operations-ja.md)：秘密値とエラーメッセージの規則
