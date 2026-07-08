[English](BE-XXXX-android-emulator-e2e-ci.md) · **日本語**

# BE-XXXX — Android の実機 e2e を CI に配線する（KVM 経由のエミュレータ）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-android-emulator-e2e-ci-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform expansion (Android / Web / Flutter) |
| 関連 | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md) |
<!-- /BE-METADATA -->

## はじめに

iOS には実機 e2e ワークフロー（`.github/workflows/e2e.yml`）があり、web バックエンドにも専用の
もの（`web-e2e.yml`）があります。しかし Android の e2e レーンはありません。Android バックエンド
（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)）はローカルの arm64
エミュレータで一度検証されましたが（2026-07-07）、その検証が黙って退行するのを防ぐものが CI に
ありません。本項目は Android エミュレータの e2e ワークフローを追加します。Linux ランナーで KVM の
もとに AVD を起動し、showcase シナリオを `--backend android` で駆動するもので、idb と web の e2e
レーンとまったく同じく、fast の `make check` ゲートの外に置きます。

## 動機

Android の実機アクチュエーションとデバイス制御の作業（この同じバッチで起票した、Android
アクチュエーション忠実度の項目とデバイス制御の項目）は、実機に対してのみ現れるので、Linux の
fast ゲートでは覆えません。CI にエミュレータのレーンがないと、これらの挙動は一度手で検証される
だけで、その後のどの変更でも気付かれずに退行しかねません。iOS も web もすでに e2e レーンを
持っています。Android の分を追加すれば、3 つ目のバックエンドについてもその安全網が戻り、実機の
スライスをローカルだけでなく CI で検証できるようになります。BE-0007 の phasing のノートも、これを
すでに見込んでいました。「エミュレータは KVM 経由の Linux CI（`android-emulator-runner`
アクション）で動く」のとおりです。

## 詳細設計

ワークフローは既存の e2e レーンをなぞります。専用のファイルを持ち、関連するパスでトリガーし、
`make check` には含めません。LLM は使わず、固定のシナリオに対する決定論的な `run` なので、prime
directive の枠内にとどまります。

### 作業分解（MECE）

1. **ワークフロー**（`.github/workflows/android-e2e.yml`）。KVM つきの
   `reactivecircus/android-emulator-runner` を使う Linux ランナーで、ローカル検証が使った API
   レベル（arm64 API 34）の AVD を起動します。他の e2e ワークフローと同じく、パスフィルタで
   ゲートします。
2. **showcase をビルドしてインストールする**。Android の showcase（Compose と Views の双子）を
   ビルドし、起動したエミュレータにインストールします。
3. **通るシナリオを実行する**。すでに実機で通っている中核の id/tap/type/value シナリオを
   `--backend android` で駆動し、決定論的な合否を検証します。
4. **visual／golden ベースラインの同等性**。2026-07-07 の検証で未確認のまま残った唯一の証跡の
   次元である、Android の visual／golden ベースラインのチェックを、このレーンの範囲に含めます。
5. **実機スライスとともに育てる**。アクチュエーション忠実度の項目とデバイス制御の項目が着地する
   につれて、それらが直すフロー（`notices`・`gestures`・`controls`・位置情報／クリップボード）まで
   シナリオ集合を広げ、レーンが増えていく実機の対象面を追随するようにします。

## 検討した代替案

- **self-hosted の macOS ランナー**。却下しました。Android エミュレータは KVM 経由の Linux で
  動き、そのほうが安価で、BE-0007 の phasing の選択（Android は Linux CI で lean な側を担う）に
  合います。macOS ランナーが要るのは idb レーンだけです。
- **エミュレータの実行を `make check` に畳み込む**。却下しました。エミュレータの起動は fast
  ゲートには重すぎます。fast ゲートは実機なしで Linux を含むどこでも走らなければなりません。e2e
  レーンは設計上分かれており、それは idb と web でも同じです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] ワークフロー（`.github/workflows/android-e2e.yml`）。`android-emulator-runner` ＋ KVM、パスでゲート。
- [ ] 起動したエミュレータへの Android showcase のビルドとインストール。
- [ ] 通る中核シナリオを `--backend android` で実行。
- [ ] visual／golden ベースラインの同等性チェック。
- [ ] アクチュエーション忠実度とデバイス制御のスライスの着地に合わせたシナリオ集合の拡張。

## 参考

[BE-0007 — Android バックエンド](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
`.github/workflows/e2e.yml`、`.github/workflows/web-e2e.yml`
