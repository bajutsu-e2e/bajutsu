[English](BE-0514-email-imap-pop3.md) · **日本語**

# BE-0514 — `email` ステップのメール受信を IMAP と POP3 にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0514](BE-0514-email-imap-pop3-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0514") |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md)、[BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`email` ステップは Hypertext Transfer Protocol（`HTTP`）のエンドポイントからメールを読みます。応答の形式は JavaScript Object Notation（`JSON`）です。本項目は、この取得方式を置き換えます。受信箱は Internet Message Access Protocol（IMAP）か Post Office Protocol version 3（POP3）で読みます。`HTTP` の受信箱は削除します。

本変更は既存のファイルを壊します。Uniform Resource Locator（URL）を指定した `mailbox:` の設定は読み込めなくなります。

## 動機

現在の受信箱は `JSON` しか受け付けません。ランナーはすべての応答を `JSON` として解析します。そのため、平文を返すサービスでは最初の取得でステップが失敗します。

返す `JSON` もサービスごとに違います。設定の `messages` と `fields` はキー名の違いを吸収できます。しかし、応答の構造そのものの違いは吸収できません。

`HTTP` のサービスは、もともと `http` ステップで呼び出せます。`email` の内側に 2 つ目の `HTTP` 読み取りを持つと機能が重複し、しかも対応できるサービスは少なくなります。一方、メールの取得には IMAP と POP3 という広く提供されている方式があります。

[BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md) は、IMAP を需要が出るまで見送りました。[BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry-ja.md) は、新しい取得方式を差し込むレジストリを用意しています。

変更の効果は、IMAP か POP3 の受信箱から認証コードを読むシナリオが成功することで確認できます。

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

接続は常に Transport Layer Security（TLS）を使います。サーバー証明書は、ホスト名を含めて検証します。暗号化しない接続方式は用意しません。

`security: starttls` では、暗号化への切り替えが成功するまで認証情報を送りません。サーバーが `STARTTLS`（IMAP）や `STLS`（POP3）に対応しない場合は、ステップを失敗にします。平文のまま接続を続けることはありません。

認証はユーザー名とパスワードで行います。POP3 の受信箱は 1 つだけなので、`kind: pop3` に `folder` を指定すると設定エラーになります。

設定項目の `url`、`headers`、`messages`、`fields` は削除します。いずれかが残った設定は読み込みに失敗します。エラーメッセージでは IMAP、POP3、`http` ステップを案内します。

`HTTP` の受信箱を使っている場合は、`http` ステップへ移行します。遅れて届くメールを扱うには、`http` の状態コード待機も必要です。待機は別の提案で追加します。待機の提案は、本項目と同時か先にリリースします。BE-0186 のレジストリは残し、組み込みのアダプターを `imap` と `pop3` に置き換えます。どちらも Python の標準ライブラリで実装するので、依存は増えません。

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

一致したメールが 2 通以上あり、日付のないメールが含まれる場合は、ステップを失敗にします。最新のメールを決められないためです。識別子の順で選ぶことはしません。

接続エラー、ログインの拒否、証明書エラーは、ステップの失敗になります。エラーメッセージには、パスワードとメール本文を含めません。

### 最優先原則との関係

| 原則 | 本設計での守り方 |
|---|---|
| 合否の判定にモデルを使わない | メールの照合と値の抽出は固定の規則 |
| 決定性 | 待機は、一致するメールの到着か必須の期限で終了 |
| アプリ非依存 | サーバーと認証情報は `targets.<name>` に置き、シナリオには書かない |

### 作業手順

| # | 作業 | 主なファイル | 完了条件 | 前提 |
|---|---|---|---|---|
| 1 | メールのモデル、読み取りインタフェース、選択規則を置換 | `bajutsu/common/mailbox.py`、`mailbox_reader.py`、`_do_email` | 日付のないメールを含む固定データで解析と選択のテストが成功 | なし |
| 2 | IMAP と POP3 のアダプターと新しい設定を追加し、`HTTP` の読み取りを削除 | `runner/mailbox.py`、`config/schema/mailbox.py` | `imaplib` と `poplib` の代役でテストが成功し、旧設定は読み込みに失敗 | 1 |
| 3 | 日英の文書を更新 | `docs/scenarios.md`、`docs/configuration.md`、`docs/architecture.md` と日本語版 | 削除した設定を説明するページがない | 2 |
| 4 | 実際のテスト用メールサーバーで確認 | なし | IMAP と POP3 のそれぞれで `email` ステップが成功 | 2 |

品質ゲートはメールサーバーに接続できないため、最後の作業は手動の確認です。

## 検討した代替案

| 代替案 | 採用しなかった理由 |
|---|---|
| `HTTP` の受信箱を残し、平文形式を追加 | 応答の構造はサービスごとに違うため、形式の設定が増え続ける |
| `email` を廃止し、すべて `http` で受信 | IMAP か POP3 しか提供しない受信箱を読めない |
| IMAP、POP3 と `kind: http` の併存 | `HTTP` サービスを読む経路が 2 本になり、`JSON` の読み替え設定も残る |
| メールのログインで OAuth 2.0 に対応 | トークンの発行と更新は独立した機能。OAuth 必須のサーバーは対象外 |
| 暗号化しないメール接続を許可 | 認証情報が平文でネットワークを流れる |

## 進捗

> 作業の進行に合わせて更新します。チェックリストは *詳細設計* の MECE な作業分解に対応し（作業単位ごとに
> 1 項目）、ログは変更内容と時期を（古い順に）記録して PR にリンクします。

- [ ] メールのモデルと読み取りインタフェース
- [ ] IMAP と POP3 のアダプター、新しい設定、`HTTP` 読み取りの削除
- [ ] 日英の文書
- [ ] メールサーバーでの手動確認

## 参考

- [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md)：One Time Password（OTP）と `email` ステップと `HTTP` 受信箱の起点
- [BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry-ja.md)：新しいアダプターを差し込むレジストリ
