[English](BE-0130-default-network-secret-redaction.md) · **日本語**

# BE-0130 — ネットワークの機密ヘッダーと Cookie を既定で redact する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0130](BE-0130-default-network-secret-redaction-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0130") |
| 実装 PR | [#626](https://github.com/bajutsu-e2e/bajutsu/pull/626) |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

ネットワーク証跡の redaction（`redact:`）はすでに存在しますが、完全にオプトイン方式であり、
ヘッダー名の照合も大文字小文字を区別する完全一致になっています。本項目は、標準的な機密ヘッダーの
集合を既定で redact 対象にし、`cookie` を指定したときに `set-cookie` も一緒に隠すようヘッダー照合を
修正します。

## 動機

`Redact`（`bajutsu/scenario/models/evidence.py:47`）は `labels`・`headers`・`fields` のすべてを
既定で空リストにしています。そのため `redact:` に一度も触れない scenario では、`active` プロパティが
`False` の `Redactor` が使われます。`_write_network`（`bajutsu/runner/pipeline.py:49`）は
`redactor.redact_exchange` を無条件に呼びますが、非アクティブな `Redactor` にとってこの呼び出しは
何もしないのと同じです（`bajutsu/redaction.py:88` の `if not self.active: return exchange` という
ガード）。結果として、捕捉されたすべての exchange（ヘッダー、Cookie、ボディ）がそのまま
`runs/<id>/<sid>/network.json` に書き出されます。`Authorization` トークン、セッションの
`Cookie`・`Set-Cookie` の値、リクエストやレスポンスのボディに埋め込まれた秘密情報は、著者が
明示的にオプトインしない限り、すべての run の証跡にプレーンテキストのまま残り続けます。

これは、証跡が日常的に共有されるツールにとって、静かに開いたままの既定構成です。run のレポートは
そもそも閲覧されること、バグ報告に添付されること、`record`・`enrich`・`triage` を通じて設定済みの
AI プロバイダーへ送られることを前提に設計されています（BE-0047「AI data sovereignty」を参照）。
失敗した run の後に `network.json` を読むレビュアーが、既定のまま有効なベアラートークンを
手渡されるべきではありません。

もう一つ、ヘッダーの照合は完全一致でしか行われません。`Redactor.__init__` の `_header_names`
（`bajutsu/redaction.py:104`、著者が指定したヘッダー名から作る `{h.lower() for h in
redact.headers}` という集合）は、指定された名前そのものにしか一致しません。セッション Cookie を
隠したいという意図を自然に単数形で `headers: [cookie]` と書いた著者は、レスポンス側の
`Set-Cookie` が隠されないままになります。`"set-cookie" != "cookie"` だからです。この 2 つの名前は
同じ秘密情報（クライアントが送る Cookie とサーバーが発行する Cookie）を指しており、
一つの関心事として扱うべきです。

## 詳細設計

1. **標準的な機密ヘッダーの集合を既定で redact する。** `authorization`・`cookie`・
   `set-cookie`・`x-api-key`、および他の一般的な認証情報系ヘッダー（`proxy-authorization`・
   `x-auth-token`）を含む組み込みの既定リストを用意し、scenario の `redact:` が未設定、または
   `headers` を含まない場合でも隠します。`Redactor` は、scenario 側で指定された `headers` を
   この既定集合とマージします。著者が基本的な保護にオプトインする必要はなく、`redact:` は
   scenario 固有のヘッダー・フィールド名や要素の `labels` を追加するためのものとして残します。
2. **明示的な、見える形の抜け道を用意する。**（認証失敗のデバッグなど）既定で隠されるヘッダーの
   生の値がどうしても必要な著者は、既定の一部を無効化する scenario オプションを名指しで
   要求できるようにします。単に `redact:` を書かないという暗黙の既定ではなく、保護を外すことを
   目に見える形の意図的な選択にします。
3. **ヘッダー名の照合を正規化する。** `_header_names` の比較を全体として大文字小文字を区別しない
   ものにし（本来の意図どおりですが、リクエスト・レスポンスのヘッダー辞書のキーが集合照合の前に
   一貫して小文字化されているか確認します）、`cookie` と `set-cookie` を連動させます。
   どちらか一方を指定すれば両方が隠されるようにします。両者は方向が逆なだけで同種の秘密情報を
   運ぶからです。
4. **テスト。** `redact:` がない scenario でも `network.json` の `Authorization`・`Cookie`・
   `Set-Cookie` が隠されること、`headers: [cookie]` を指定した scenario で `Set-Cookie` も
   隠されること、抜け道のオプションを明示的に設定したときだけ生の値が現れることを検証します。
5. **ドキュメント。** 既定で隠されるヘッダー集合と抜け道について、証跡・redaction に関する
   ドキュメント（`docs/` と `docs/ja/` の両方）に記載し、`cookie`・`set-cookie` を一つの関心事として
   扱うことを明記します。

エンコードを考慮したマッチング（パーセントエンコーディング、Basic 認証の base64、HTML・JSON
エスケープ）の欠落は、別の兄弟項目（encode-aware secret redaction）で扱います。本項目のスコープは
既定でどのヘッダーを隠すか、ヘッダー名をどう照合するかに限られ、値の照合方法には踏み込みません。
本項目は決定的な `run`・CI の合否判定には一切触れず、LLM も導入しません。redaction は証跡書き込み
経路の内部だけで完結します。

## 検討した代替案

- **redaction を完全にオプトインのままにし、リスクを文書化するだけにとどめる。** 却下しました。
  今回の指摘はまさに現状の挙動そのものであり、すでに既定でプレーンテキストの秘密情報が証跡に
  現れています。証跡が共有され AI プロバイダーに送られるツールには、より強い警告文ではなく
  安全な既定値が必要です。
- **既定集合にオプトインするために `redact: {}`（空だが存在する）を要求し、`redact:` が完全に
  ない場合は何も変えない。** 却下しました。「`redact:` キーがない」場合と「`redact:` はあるが
  空」の場合を区別する意味がなく、混乱を招きます。どちらの場合も同じ安全な既定値を適用すべきです。
- **明示的に許可されていないヘッダーはすべて隠す。** 既定として攻撃的すぎるため却下しました。
  ほとんどのヘッダー（`Content-Type`、`User-Agent`、アプリ固有のヘッダー）は秘密情報ではなく、
  それらまで既定で隠すと、対応するセキュリティ上の利点なしに `network.json` の証跡としての
  有用性を大きく損ないます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 組み込みの機密ヘッダー既定集合を無条件で隠す
- [x] 既定の一部を無効化する、明示的かつ見える形の抜け道（暗黙のオプトアウトなし）
- [x] ヘッダー名照合の正規化（大文字小文字を区別しない、`cookie` と `set-cookie` の連動）
- [x] テスト：`redact:` なしでの既定マスキング、`cookie` が `Set-Cookie` も隠すこと、
      抜け道によるオプトアウト
- [x] ドキュメント更新（日英両方）

- 2026-07-04: [#626](https://github.com/bajutsu-e2e/bajutsu/pull/626) — `Redactor` に組み込みの
  機密ヘッダー既定集合、`unmaskHeaders` の抜け道、`cookie`/`set-cookie` の連動を追加しました。
  `redact_exchange` で既定ヘッダーを無条件にマスクし、redaction のドキュメントを日英両方で
  更新しました。

## 参考

- `bajutsu/scenario/models/evidence.py:47` — `Redact`。すべてのリストを既定で空にしている。
- `bajutsu/runner/pipeline.py:49` — `_write_network`。`<sid>/network.json` を書き出す。
- `bajutsu/redaction.py:88` — `redact_exchange` 内の `active` による no-op ガード。
- `bajutsu/redaction.py:104` — `_header_names`。完全一致のヘッダー名集合。
- [BE-0047 — AI data sovereignty](../../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)
- [BE-0032 — Secret variables](../../BE-0032-secret-variables/BE-0032-secret-variables-ja.md)
- 2026-07-02 のコードベース分析レポート（セキュリティ）に由来します。
