[English](BE-0182-record-human-value-prompt.md) · **日本語**

# BE-0182 — record 中の人による値入力（OTP・ランダム値・一度きりの値）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0182](BE-0182-record-human-value-prompt-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0182") |
| 実装 PR | [#1207](https://github.com/bajutsu-e2e/bajutsu/pull/1207) |
| トピック | オーサリング体験 |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md), [BE-0044](../BE-0044-scenario-provenance/BE-0044-scenario-provenance-ja.md), [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md), [BE-0120](../BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization-ja.md), [BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff-ja.md) |
<!-- /BE-METADATA -->

## はじめに

record の human-in-the-loop ハンドオフの土台
（[BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff-ja.md)）の上に乗る項目です。本項目が
扱うのは、AI が入力欄は特定できるものの、その値を知り得ないケースです。ワンタイムパスワード（OTP）、
二要素認証（2FA）のコード、ランダムな文字列、外部が発行する一度きりの値などが該当します。`record` は
一時停止し、人が値を供給し、記録が続行します。記録される成果物は、その値を再実行時に決定論的に解決
します。解決は、[BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md) の `totp` /
`email` ステップ、または `secret` / `var` への橋渡しによります。

## 動機

側方チャネルから来る値は、実際のログインや本人確認、パスワード再設定の flow を AI が記録するとき、
最も多く記録を止める要因です。エージェントには「コードを入力」という欄が見えていても、値は認証
アプリや受信箱、あるいは人の頭の中にしか存在しません。現状の `record` では、ループがその欄で停止する
か、エージェントがもっともらしいが誤った値を作り出すかのいずれかで、どちらも残りを著者が手書きする
ことになります。

[BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md) は、実行時の側をすでに解決して
います。`totp` は seed から OTP をローカルに計算し、`email` はテスト用の受信箱をポーリングします。
ただし BE-0046 は、著者がその欄を OTP 欄だと既に知っていて、seed や受信箱を前もって配線してある
ことを前提とします。その知識が発見されるのは、まさに record ループの中で、flow の途中で対話的に、
です。本項目はこの隙間を埋めます。record の最中に人が値を一度だけ供給できるようにして flow を端から
端まで捉え、なおかつ BE-0046 や宣言済みの secret を指し示すことで、人が居なくても再実行できる成果物
を出力します。

## 詳細設計

土台の要求と応答の契約の上に構築し、本項目では値に固有の挙動を定めます。

**詰まりの検出と明示的な要求。** エージェントは、自らの知識では埋められないと判断した欄に対して、
土台の「人が必要」という結果を立ち上げます。判断はヒューリスティック（OTP / code / verification と
ラベル付けされた欄、あるいは著者が印を付けた欄）によります。そしてツールは、欄を埋めるための値を
決して推測しません。著者があらかじめ、その欄を人が供給するものとして印を付けることもできます。

**プロンプトの内容。** ハンドオフの要求は、対象の欄を指し示し（簡潔なセレクタの要約）、値を求めます。
人は土台のインターフェース（CLI の標準入力、または `serve` の入力欄）を通じてそれを入力します。

**実際の実行と、記録される成果物。** 供給された値は、記録が次の画面へ進むよう、実際のアプリへ入力
されます。しかしシナリオには書き込みません。値はランダムまたは秘密だからです。ここでは
[BE-0120](../BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization-ja.md)
のトークン化とマスキングを再利用し、リテラルが YAML やマニフェスト、実行中の進捗ストリームに一切
残らないようにします。

**決定論的な出力、すなわち橋渡し。** 記録されるステップは、`${vars.*}` / `${secrets.*}` の
プレースホルダに、値の出所を分類したラベル付きの TODO を添えたものになります。TODO は著者が配線
できるよう、「`totp` で解決（BE-0046）」「`email` で解決（BE-0046）」「`secret` として宣言」の
いずれかを示します。prime directive 1 に沿い、AI はもっともらしい分類を「提案」してよく（判定では
なくオーサリングです）、著者が確認して配線します。配線を終えれば、再実行は完全に決定論的で AI を
含みません。

**来歴。** ステップには `from:` の来歴
（[BE-0044](../BE-0044-scenario-provenance/BE-0044-scenario-provenance-ja.md)）を持たせ、人による
値のハンドオフが出所であることを記録します。これにより、レポートや GUI エディタが、なぜまだ配線が
必要なのかを示せます。

**CLI と `serve`。** 両インターフェースは土台から得ます。本項目が加えるのは、値のプロンプトと、
その上での分類と TODO の出力だけです。`serve` では、ハンドオフのペインが待っている欄（要求の
スクリーンショット上で強調表示）と、値のための入力欄を一つ示します。著者は認証アプリや受信箱で
コードを読み取り、ブラウザに入力します。値は**ブラウザの中だけ**で供給され、デバイスへのアクセスを
必要としません。ですからこのパターンは、シミュレータが著者の目の前に無いリモートやセルフホストの
`serve`（BE-0015 / BE-0016）でも、そのまま機能します。ここが、著者がデバイスに触れる必要のある
操作引き取りのパターンとの、はっきりした対比です。

## 検討した代替案

- **人が入力したリテラルの値を記録する。** 却下します。値はランダムまたは秘密なので、次の実行では
  再現できず、しかも秘密を成果物へ漏らします。これは
  [BE-0120](../BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization-ja.md)
  と [BE-0152](../BE-0152-totp-seed-artifact-leak/BE-0152-totp-seed-artifact-leak-ja.md) が防いでいる、
  まさにその問題です。
- **ハンドオフを設けず、記録の前に BE-0046 の事前設定を必須にする。** 唯一の経路としては却下します。
  著者は、flow がその欄に達するまで、そこが OTP 欄だと気付いていないことがよくあります。ハンドオフは
  それを対話的に発見し、そのうえで BE-0046 へ誘導します。両者は代替ではなく補完の関係です。
- **実行時に「毎回値を尋ねる」ステップを置く。** 却下します。それは決定論的な `run` と CI のゲートに
  人を置くことです（directive 1）。本項目の狙いは、値を決定論的な出所へ解決し、記録された flow を
  無人で再生できるようにすることにあります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 値の詰まりの検出（ヒューリスティックな印付け）。値の推測入力は行わない。
- [x] 土台の要求と応答の契約に載せた、値のプロンプトの内容。
- [x] 供給された値の実際の入力と、成果物に対する BE-0120 のトークン化とマスキング。
- [x] 決定論的な出力の橋渡し: `${vars.*}` / `${secrets.*}` のプレースホルダと、分類済みの TODO（totp / email / secret）。
- [x] 人による値の出所を示す `from:` 来歴（BE-0044）。

後続へ先送り（この最初のスライスの対象外）: 値の詰まりの検出のうち*著者による事前の印付け*——flow が
その欄に達する前に、著者が欄を人供給と宣言できるようにする経路で、ここで出荷したエージェント主導の
ヒューリスティックとは別物です。これには設定（`targets.<name>`）の面が要り、土台
（[BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff-ja.md)）が著者主導の操作
引き取りのトリガを先送りしたのと同じ流儀です。

**ログ**

- BE-0179 の土台の上に値パターンを実装しました。エージェントの `ask_human` ツールは、値の入る先の
  フィールドを名指しし、`classify`（`totp` / `email` / `secret`）とプレースホルダの `name` を提案する
  ようになり（[`bajutsu/agents/claude.py`](../../bajutsu/agents/claude.py)）、それらを `Proposal`
  （[`bajutsu/agents/protocols.py`](../../bajutsu/agents/protocols.py)）が運びます。フィールドを名指し
  した値応答では、record ループが実際の値をライブのアプリに入力し、プレースホルダの `type` ステップを
  記録します——`${vars.*}`（実行時の橋渡しである `totp` / `email`、BE-0046）か `${secrets.*}`（宣言
  済みの secret）で、リテラルではありません（BE-0120）。そのステップの `from:` 来歴（BE-0044）には
  分類済みの TODO を載せます（[`bajutsu/record.py`](../../bajutsu/record.py)）。フィールドを名指し
  しないハンドオフは、これまでどおり観測し直します。高速スイートのテストが、プレースホルダの形、ライブ
  入力、漏らさない保証、フィールド無しの退避を網羅します。ドキュメントは両言語で更新しました。

## 参考

土台: [BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff-ja.md)。姉妹パターン:
`record-human-takeover-step`（操作）。関連する既存項目:
[BE-0046 — OTP・メールの側方チャネルステップ](../BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md)
（実行時の橋渡し先）、
[BE-0120 — 記録された scenario の YAML でシークレットをトークン化する](../BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization-ja.md)、
[BE-0044 — シナリオの来歴](../BE-0044-scenario-provenance/BE-0044-scenario-provenance-ja.md)。
