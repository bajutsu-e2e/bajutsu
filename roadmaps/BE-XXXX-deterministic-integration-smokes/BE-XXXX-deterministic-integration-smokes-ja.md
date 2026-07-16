[English](BE-XXXX-deterministic-integration-smokes.md) · **日本語**

# BE-XXXX — AI と外部サービスのサブパスに対する決定論的な実機スモーク

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-deterministic-integration-smokes-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | 検証とカバレッジ |
| 関連 | [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md), [BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry-ja.md), [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md), [BE-0017](../BE-0017-mcp-server/BE-0017-mcp-server-ja.md) |
<!-- /BE-METADATA -->

## はじめに

3 つの機能領域は、全体のスタックが AI 駆動か外部サービス依存であるため、正しく CI ゲートに一度も乗りません
（プライムディレクティブ 1 が LLM を判定の経路から外します）。メールボックスと 2 要素認証のメールステップ、
自律クロール、そしてデバイスを駆動する MCP サーバです。それぞれには、LLM や生きた外部アカウントなしに実
バックエンドで走らせられる決定論的なサブパスがあり、いまはどれもどのレーンにも配線されていません。本項目では、
それらのサブパスに対する決定論的な実機スモークを追加し、AI が判定する経路は意図的に CI の外に保ちます。

## 動機

メールボックスステップの唯一の外部依存は HTTP の取得であり、いまはどのテストでも注入されています
（[BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry-ja.md) がこれをレジストリに
しました）。ループバックのスタブメールサーバがあれば、取得から抽出、アサートまでの連鎖を、生きたプロバイダも
鍵もなしに決定論的に走らせられます。したがって OTP とメールステップ
（[BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md)）を、プライムディレクティブ 1 に触れずに
端から端まで実証できます。

自律クロール（[BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）は
LLM のガイドを使いますが、AI を使わないガイドの上のクロールエンジンは決定論的です。その実デバイスの契約、
すなわち画面変化の検出、クラッシュの検出、そして `recover` の継ぎ目は、いまは fake の `react` フックで完全に
スクリプトされているため、実アプリの画面が操作に応じて実際に変化するか、`recover` が動けなくなったブラウザを
癒やすかは未観測です。web ショーケースに対する AI 抜きのクロールが、モデルなしでその契約を動かします。

MCP サーバ（[BE-0017](../BE-0017-mcp-server/BE-0017-mcp-server-ja.md)）の `bajutsu_run` ツールは、決定論的な
`run` CLI を起動するだけですが、MCP の層を通してデバイスを端から端まで駆動するテストはありません。MCP から
web への往復を 1 回行えば、ツールが正しい引数で外部コマンドを呼ぶことだけでなく、実バックエンドを
アクチュエートすることを実証できます。

いずれも LLM を判定の経路に置きません。それぞれが、本来は除外される機能の決定論的な一片であり、その境界は
決定論と AI の切り分けが読み取れるように書き留める価値があります。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **メールボックスの実 HTTP スモーク。** ループバックのスタブメールサーバを立て、OTP とメールステップの
  シナリオ（秘密情報や TOTP を含む）を 1 つの実バックエンド（web）で走らせ、取得、抽出、後続のアサートを
  アサートします。生きたプロバイダも鍵もなしです。
- **AI 抜きのクロールスモーク。** クロールエンジンを決定論的な（モデルでない）ガイドで web ショーケースに対して
  走らせ、実ブラウザで画面変化の検出と `recover` の継ぎ目をアサートします。
- **MCP 統合スモーク。** MCP サーバを通して `bajutsu_run`（と `bajutsu_doctor`）を web バックエンドに対して 1 回
  往復させ、MCP の層を通した実アクチュエーションを実証します。
- **AI の境界を文書化する。** どの経路（record、モデルを使うクロール、enrich）が決定論的 CI の外にとどまるか、
  そしてなぜかを明示し、決定論と AI の切り分けが読み取れるようにします。

## 検討した代替案

* **3 つとも fake だけのカバレッジにとどめる。** 決定論的なサブパスは実バックエンドで走らせられ、その実デバイスの
  挙動（メールボックスの取得、クロールの画面変化検出と `recover`、MCP のアクチュエーション）は未観測です。
  fake だけでは走らせられる経路を未検証のまま残します。
* **AI の経路そのものをゲートにする。** モデル駆動のクロール、record、enrich を CI ゲートに乗せると、LLM を
  判定の経路に置くことになり、プライムディレクティブ 1 に反します。スコープに入るのは決定論的なサブパスだけです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] メールボックスの実 HTTP スモーク: ループバックのスタブメールサーバと OTP / TOTP シナリオを web で。
- [ ] AI 抜きのクロールスモーク: web ショーケースに対する決定論的ガイドで、画面変化検出と `recover` をアサートする。
- [ ] MCP 統合スモーク: MCP サーバ経由の `bajutsu_run` / `bajutsu_doctor` を web に対して。
- [ ] どの AI 経路が決定論的 CI の外にとどまるか、そしてなぜかを文書化する。

## 参考

- [BE-0046 — OTP / メールステップ](../BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md)
- [BE-0186 — メールステップのためのメールボックスプロバイダレジストリ](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry-ja.md)
- [BE-0038 — 自律クロール探索](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)
- [BE-0017 — MCP サーバ](../BE-0017-mcp-server/BE-0017-mcp-server-ja.md)
