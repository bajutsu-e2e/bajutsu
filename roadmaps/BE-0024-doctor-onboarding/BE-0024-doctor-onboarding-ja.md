[English](BE-0024-doctor-onboarding.md) · **日本語**

# BE-0024 — doctor / オンボーディング

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0024](BE-0024-doctor-onboarding-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0024") |
| 実装 PR | [#236](https://github.com/bajutsu-e2e/bajutsu/pull/236), [#337](https://github.com/bajutsu-e2e/bajutsu/pull/337), [#361](https://github.com/bajutsu-e2e/bajutsu/pull/361), [#405](https://github.com/bajutsu-e2e/bajutsu/pull/405), [#406](https://github.com/bajutsu-e2e/bajutsu/pull/406), [#407](https://github.com/bajutsu-e2e/bajutsu/pull/407), [#408](https://github.com/bajutsu-e2e/bajutsu/pull/408) |
| トピック | doctor / オンボーディング |
<!-- /BE-METADATA -->

## はじめに

doctor の実行可能性ゲート（CLI 群 + 起動済み Simulator の有無チェック）は実装済みです（[architecture.md](../../docs/ja/architecture.md#実装状況)）。本項目はもともと、doctor / オンボーディング周りの小さな改善を積み上げて受け入れるプレースホルダとして運用していました。その運用は終了しており、以後の新しいオンボーディング系の改善は本項目に追加せず、新規の BE 項目として提案します。下の「進捗」は、受け皿として運用していた間に出荷した候補の記録です。

## 動機

Bajutsu でうまくいくかどうかを最初に左右するのは、*セットアップ*が健全かどうかです。具体的には、適切な
CLI（command-line interface、コマンドラインツール）がインストールされ、Simulator が起動済みで、画面に
十分なアクセシビリティ id があって指し示せることです。Bajutsu はその両面にすでに答えています。
`preflight.runnability` が環境（Xcode の `xcrun`、バックエンドの CLI、起動済みの Simulator）をチェックし、
`doctor.score` が画面の規約準備度（id カバレッジ、名前空間の準拠、id の重複）を Ready / Partial / Blocked
に採点します。両者を合わせたものがオンボーディングのサーフェスであり、run が分かりにくい理由で失敗する
*前に*問題を捕まえるチェック群です。

ただしオンボーディングは一度きりの機能ではなく、積み上がっていくものです。新たなつまずき方が現れるたび
（依存の欠落、不親切な既定値、あれば誰かの一時間を救えたはずのチェック）、その修正はたいてい独立した機能では
なく `doctor`/`preflight` に属します。本項目は、こうした小さく関連した改善が、その都度使い捨ての提案を
生むことなく、居場所と紐づけ先の ID を持てるように存在します。

## 詳細設計

これは意図的な**プレースホルダ**トピックであり、単一の具体的な仕様ではありません。実装済みのオンボーディング
サーフェスは [architecture.md](../../docs/ja/architecture.md#実装状況) にすでに記載があります。`preflight.py`
（環境の実行可能性ゲート）と `doctor.py`（規約スコア）です。本項目の原則は、いま作る機能ではなく、*新しい*
候補をどう追加するかにあります。

- 新しいオンボーディング／doctor 候補とは、「run する前に診断する」サーフェスへの小さく的を絞った改善です。
  たとえば、新しい実行可能性チェック、より明確な対処策の文言、より鋭い `doctor` シグナル、より良い初回
  実行時の既定値などです。単独で BE 提案に昇格させるには小さすぎる場合に、独立した提案にせず、短い理由
  付きのサブ項目としてここに記録します。
- 候補が**範囲内**となるのは、run が分かりにくく失敗する*前に*、ユーザがセットアップや規約の問題を発見し
  修正できるよう助ける場合です。run ループを変える、ドライバの能力を追加する、その他の診断を超える変更は
  **範囲外**であり、独立した BE 項目にすべきです。
- どの候補もプライムディレクティブをそのまま継承します。チェックは決定的かつ LLM フリーに保ち
  （`doctor.score` は構造上「AI は関与しない」）、アプリ固有の事項（id 名前空間、バックエンド）は config から
  来るので、チェックはアプリ非依存のまま保たれます。

候補が独自の設計議論に値するほど大きく育ったら、専用の BE 項目に昇格させ、ここからは取り除く、という運用でした。
この受け皿としての運用自体は終了しており、以後のオンボーディング改善はどんな大きさでも新規の BE 項目として提案します。

### 出荷済みの候補

- **`doctor` が web（Playwright）バックエンドに対応。** 実行可能ゲート（`preflight.py`）は iOS 形でした。
  常に Xcode の `xcrun` と起動済みシミュレータを要求し、Playwright ランタイムは一切確認しなかったため、web
  ターゲットに対する `bajutsu doctor` は誤ったツールを要求し、その後 simctl の udid 解決でクラッシュしていました。
  ゲートはバックエンドの系統で分岐するようになりました。web バックエンドは Playwright パッケージとその Chromium
  ブラウザを確認し（対処策は `uv sync --extra web` / `playwright install chromium`、Xcode やシミュレータは不要）、
  `doctor` は新しいブラウザをターゲットの `baseUrl` へ遷移させてそのページを採点します。規約充足度スコア自体は
  もともとバックエンド非依存（各要素の id と trait を読むだけ）なので、web バックエンドの `data-testid` の id も
  そのまま採点されます。
- **actionable な要素が 1 つも無い画面は `Ready` ではなく `Blocked` と判定** ([#337](https://github.com/bajutsu-e2e/bajutsu/pull/337))。`doctor.score` は actionable な
  要素が無いとき id カバレッジを `1.0` として導出していたため、空画面、まだ読み込まれていない画面、想定外の画面が
  `Ready` と採点されていました。これは紛らわしい偽陽性で、`doctor` が実際にはテスト対象を 1 つも見つけていないのに、
  初回利用者へ「設定は健全だ」と伝えてしまいます。スコアは「actionable な要素が無い」状態を `Blocked` として扱い、
  `render` がその原因の見当（「アプリは想定した画面にあり、完全に読み込まれていますか」）を示すようになりました。
  この点検は決定的で、LLM は関与しません。
- **`doctor` がツールやデバイスを探る前に、ターゲットのバックエンド設定を点検** ([#361](https://github.com/bajutsu-e2e/bajutsu/pull/361))。
  `doctor` はターゲットを解決するとすぐツールやデバイスの探索に入っていたため、選択したバックエンドに対して
  誤ったフィールドを持つターゲット（`baseUrl` だけの iOS ターゲット、`bundleId` だけの web ターゲット）が、
  分かりにくい後続の起動や遷移の失敗として現れていました。`bundleId` を欠く iOS ターゲットは事前に点検すらされません
  でした。設定のパースは両方を欠くターゲットは弾きますが、実行するバックエンドにとって誤ったフィールドは弾きません。
  新しい `preflight.config_checks` が、選択したバックエンドに必要なフィールド（web は `baseUrl`、iOS は `bundleId`）の
  有無を、ターゲット名を含む対処法とともに点検し、`doctor` はこれを最初に走らせます。これにより、無駄な探索を始める前に
  修正可能なチェックリストで早く失敗します。この点検は決定的で、LLM は関与しません。
- **Web の trait/role マッピングを 6 から 20 エントリに拡充** ([#405](https://github.com/bajutsu-e2e/bajutsu/pull/405))。
  Playwright バックエンドの `_ROLE_MAP` は `a`、`button`、`input`、`textarea` しかカバーしておらず、テキスト入力を
  `ACTIONABLE_TRAITS` に含まれない `textbox` trait にマッピングしていたため、web のテキスト入力がすべて
  `doctor.score` から見えていませんでした。マップは checkbox、radio、switch、select/combobox/listbox、
  option/menuitem、searchbox、spinbutton、textarea（`textView`）をカバーするようになり、既存のテキスト入力
  マッピングも `textField`（`ACTIONABLE_TRAITS` に含まれる）に修正されました。スコアリングロジック自体は変更されていません。
- **`doctor` の id カバレッジしきい値が設定可能に** ([#406](https://github.com/bajutsu-e2e/bajutsu/pull/406))。
  `OK_COVERAGE`（0.9）と `FAIL_COVERAGE`（0.7）はハードコードされた定数で、テスト用 id を付ける必要のない装飾的
  要素が多いチームにはグレード判定を調整する手段がありませんでした。新しい `defaults.doctor` 設定セクション
  （`idCoverageOk` / `idCoverageFail`）が `score()` にスレッドされ、ロード時に検証されます（両方 [0, 1] の範囲で、
  ok >= fail）。既存の config は変更なしでそのまま動きます（ハードコード値がデフォルトとして維持されます）。
  この点検は決定的で、LLM は関与しません。
- **`doctor --scenario` が run 前にケイパビリティ互換性を点検** ([#407](https://github.com/bajutsu-e2e/bajutsu/pull/407))。
  すべての `doctor` チェックを通過しても、シナリオがバックエンドにないケイパビリティを使っていると run 中に失敗する
  可能性がありました（例: `multiTouch` のない idb 上での `pinch`）。CLI の `doctor` コマンドに新しい `--scenario`
  オプションを追加し、シナリオ YAML を読み込んでバックエンドの静的ケイパビリティに対して `capability_preflight` を
  実行します。非対応の構文は人間が読めるシナリオパス付きで報告されます
  （例: `step 3 > if > then[0]: pinch needs 'multiTouch'`）。この点検はデバイス不要の純粋関数です。
  なお、`use` コンポーネントの展開は適用されません（config コンテキストが必要なため）。展開によってのみ導入される
  ケイパビリティは検出されません。
- **Web UI が `POST /api/doctor` でプリフライトチェックを公開** ([#408](https://github.com/bajutsu-e2e/bajutsu/pull/408))。
  Web UI（`serve/`）にはオンボーディングサーフェスがなく、セットアップの問題を診断するには CLI か MCP ツールを使う
  必要がありました。新しい `POST /api/doctor` エンドポイントが、指定したターゲットに対して設定検証とツール実行可能性
  チェックを実行し、構造化 JSON（`{ok, checks[], target, backend}`）を返します。ライブスクリーンスコアは省略されて
  います（Web UI ユーザがまだデバイス接続を持っていない可能性があるため）。両トランスポート（stdlib ハンドラ + FastAPI）
  に配線されています。

## 検討した代替案

**各オンボーディング案を既存項目に折り込む。** 一部の候補には自然な居場所があります。新しいバックエンドの
実行可能性チェックは、そのバックエンドの作業に属します。しかし多くは横断的（より明確な対処策の文言、初回
実行時の既定値）で、単一の担い手がいません。受け皿がなければ抜け落ちます。独立したプレースホルダは、それらに
着地点を与えます。

**オンボーディングの微調整ごとに新しい BE 提案を立てる。** 重すぎるため却下しました。これらの多くは一行の
診断やメッセージ改善です。変更ごとに完全な提案を要求すると、小さく明らかに良い修正をプロセスで埋もれさせます。
本項目は、それらを低コストで蓄積し、本当に独自の設計に値するものだけを昇格させます。

**提案を書かず、`doctor` をその場しのぎで改善する。** 却下しました。ロードマップはプロジェクトの共有された
記憶です。ID がなければ、各オンボーディング改善の理由は失われ、何が検討されたかを一覧できる場所もありません。
可視化されたプレースホルダは、その履歴を保つ最も軽い方法です。

## 進捗

> 本項目は doctor / オンボーディングの小さな改善を受け入れる受け皿として運用していましたが、その運用は
> 終了しました。以下は運用期間中に出荷した候補の完了記録です。各候補の詳しい経緯は「詳細設計」の
> 「出荷済みの候補」を参照してください。

- [x] `doctor` が web（Playwright）バックエンドに対応（[#236](https://github.com/bajutsu-e2e/bajutsu/pull/236)）
- [x] actionable な要素が 1 つも無い画面を `Blocked` と判定（[#337](https://github.com/bajutsu-e2e/bajutsu/pull/337)）
- [x] `doctor` がターゲットのバックエンド設定を事前に点検（[#361](https://github.com/bajutsu-e2e/bajutsu/pull/361)）
- [x] Web の trait/role マッピングを 6 から 20 エントリに拡充（[#405](https://github.com/bajutsu-e2e/bajutsu/pull/405)）
- [x] `doctor` の id カバレッジしきい値を設定可能に（[#406](https://github.com/bajutsu-e2e/bajutsu/pull/406)）
- [x] `doctor --scenario` がケイパビリティ互換性を点検（[#407](https://github.com/bajutsu-e2e/bajutsu/pull/407)）
- [x] Web UI が `POST /api/doctor` を公開（[#408](https://github.com/bajutsu-e2e/bajutsu/pull/408)）

## 参考

[architecture.md](../../docs/ja/architecture.md#実装状況)
