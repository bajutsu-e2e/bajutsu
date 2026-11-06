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

doctor の実行可能性ゲートは実装済みです（[architecture.md](../../docs/ja/architecture.md#実装状況)）。ゲートは、コマンドラインツール（CLI）群と、起動済み Simulator の有無のチェックからなります。

本項目はもともと、doctor とオンボーディング周りの小さな改善を、出てくるたびに受け入れるプレースホルダとして運用していました。その運用は終了しています。以後の新しいオンボーディング系の改善は、大きさを問わず、新規の BE 項目として提案します。下の「進捗」は、受け皿として運用していた間に出荷した候補の記録です。

## 動機

Bajutsu の成否をまず左右するのは、*セットアップ*が健全かどうかです。セットアップが健全であるための条件は 3 つあります。

- 適切なコマンドラインツールがインストールされている。
- Simulator が起動している。
- 画面に十分なアクセシビリティ id があり、要素を指し示せる。

Bajutsu は、この 3 つの条件にすでに答えています。

- `preflight.runnability` は、環境（Xcode の `xcrun`、バックエンドのツール、起動済みの Simulator）をチェックします。
- `doctor.score` は、画面の規約準備度（id カバレッジ、名前空間の準拠、id の重複）を Ready、Partial、Blocked に採点します。

両者を合わせたものがオンボーディングの窓口であり、run が分かりにくい理由で失敗する*前に*問題を捕まえるチェック群です。

ただしオンボーディングは一度きりの機能ではなく、積み上がっていくものです。新たなつまずき方は、折に触れて現れます。依存の欠落、不親切な既定値、あれば誰かの一時間を救えたはずのチェックなどです。その修正は、たいてい独立した機能ではなく `doctor` や `preflight` に属します。本項目は、こうした小さく関連した改善に、居場所と紐づけ先の ID を与えます。これがなければ、改善のたびに使い捨ての提案が生まれます。

## 詳細設計

これは意図的な**プレースホルダ**トピックであり、単一の具体的な仕様ではありません。実装済みのオンボーディングの窓口は、[architecture.md](../../docs/ja/architecture.md#実装状況) にすでに記載があります。窓口は 2 つの部品からなります。`preflight.py`（環境の実行可能性ゲート）と `doctor.py`（規約スコア）です。本項目の原則は、*新しい*候補をどう追加するかです。いま作る機能ではありません。

- 新しいオンボーディングまたは doctor の候補とは、「run する前に診断する」窓口への、小さく的を絞った改善です。たとえば、新しい実行可能性チェック、より明確な対処策の文言、より鋭い `doctor` シグナル、より良い初回実行時の既定値があります。単独で BE 提案にするには小さすぎる候補は、短い理由を付けたサブ項目として、ここに記録します。
- 候補が**範囲内**となるのは、run が分かりにくく失敗する*前に*、ユーザがセットアップや規約の問題を発見して修正できるよう助ける場合です。診断を超える候補は**範囲外**です。run ループを変える、ドライバの能力を追加する、といった変更が当たります。範囲外の候補は、独立した BE 項目にします。
- どの候補も、プライムディレクティブをそのまま継承します。チェックは決定的かつ LLM フリーに保ちます（`doctor.score` は構造上「AI は関与しない」）。アプリ固有の事項（id 名前空間、バックエンド）は config から来るので、チェックはアプリ非依存のままです。

この項目が受け皿だった間は、次の運用でした。独自の設計議論に値するほど大きく育った候補は、専用の BE 項目に昇格させ、ここからは取り除きます。受け皿としての運用自体は、すでに終了しています。以後のオンボーディング改善は、大きさを問わず、最初から新規の BE 項目として提案します。

### 出荷済みの候補

- **`doctor` が web（Playwright）バックエンドに対応。** 実行可能性ゲート（`preflight.py`）は iOS 向けの形でした。常に Xcode の `xcrun` と起動済みのシミュレータを要求し、Playwright ランタイムは確認しませんでした。そのため、web ターゲットに対する `bajutsu doctor` は、誤ったツールを要求したうえで、simctl の udid を解決する段階でクラッシュしていました。

  ゲートは、バックエンドの系統で分岐するようになりました。web バックエンドは、Playwright パッケージとその Chromium ブラウザを確認します。対処策は `uv sync --extra web` と `playwright install chromium` で、Xcode もシミュレータも不要です。`doctor` は、新しいブラウザをターゲットの `baseUrl` に遷移させて、そのページを採点します。規約スコア自体は、もともとバックエンド非依存です。各要素の id と trait を読むだけだからです。そのため、web バックエンドの `data-testid` の id は、そのまま採点されます。
- **actionable な要素が 1 つもない画面は `Ready` ではなく `Blocked` と判定** ([#337](https://github.com/bajutsu-e2e/bajutsu/pull/337))。`doctor.score` は、actionable な要素がないとき、id カバレッジを `1.0` として導出していました。そのため、空の画面、まだ読み込まれていない画面、想定外の画面が `Ready` と採点されました。この紛らわしい偽陽性は、`doctor` が実際にはテスト対象を 1 つも見つけていないのに、初回利用者へ「設定は健全だ」と伝えてしまいます。

  スコアは、「actionable な要素がない」状態を `Blocked` として扱うようになりました。`render` は、その原因の見当（「アプリは想定した画面にあり、完全に読み込まれていますか」）を示します。これで、シグナルが本当の問題を指します。この点検は決定的で、LLM は関与しません。
- **`doctor` がツールやデバイスを探る前に、ターゲットのバックエンド設定を点検** ([#361](https://github.com/bajutsu-e2e/bajutsu/pull/361))。`doctor` は、ターゲットを解決するとすぐ、ツールやデバイスの探索に入っていました。そのため、選択したバックエンドに対して誤ったフィールドを持つターゲットが、分かりにくい後続の起動や遷移の失敗として現れました。たとえば、`baseUrl` だけを持つ iOS ターゲットや、`bundleId` だけを持つ web ターゲットです。`bundleId` を欠く iOS ターゲットは、事前に点検されることすらありませんでした。設定のパースは、両方のフィールドを欠くターゲットは弾きます。しかし、実行するバックエンドにとって誤ったフィールドは弾きません。

  新しい `preflight.config_checks` が、選択したバックエンドに必要なフィールド（web は `baseUrl`、iOS は `bundleId`）の有無を点検します。対処法には、ターゲット名を含めます。`doctor` はこの点検を最初に走らせるので、無駄な探索を始める前に、修正できるチェックリストで早く失敗します。この点検は決定的で、LLM は関与しません。
- **Web の trait/role マッピングを 6 から 20 エントリに拡充** ([#405](https://github.com/bajutsu-e2e/bajutsu/pull/405))。Playwright バックエンドの `_ROLE_MAP` がカバーしていたのは、`a`、`button`、`input`、`textarea` だけでした。さらに、テキスト入力を `textbox` trait にマッピングしていました。`textbox` は `ACTIONABLE_TRAITS` に含まれません。そのため、`doctor.score` は web のテキスト入力をすべて見落としていました。

  マップは、checkbox、radio、switch、select/combobox/listbox、option/menuitem、searchbox、spinbutton、textarea（`textView`）をカバーするようになりました。既存のテキスト入力のマッピングは、`textField` に修正しました。`textField` は `ACTIONABLE_TRAITS` に含まれます。スコアリングのロジック自体は変更していません。
- **`doctor` の id カバレッジのしきい値が設定可能に** ([#406](https://github.com/bajutsu-e2e/bajutsu/pull/406))。`OK_COVERAGE`（0.9）と `FAIL_COVERAGE`（0.7）は、ハードコードされた定数でした。テスト用 id を付ける必要のない装飾的な要素が多いチームには、グレード判定を調整する手段がありませんでした。

  新しい `defaults.doctor` 設定セクション（`idCoverageOk` と `idCoverageFail`）が、`score()` まで渡されます。値はロード時に検証します。どちらも [0, 1] の範囲で、ok >= fail でなければなりません。ハードコードされた値が既定値として残るので、既存の config は変更なしで動きます。この点検は決定的で、LLM は関与しません。
- **`doctor --scenario` が run の前にケイパビリティの互換性を点検** ([#407](https://github.com/bajutsu-e2e/bajutsu/pull/407))。すべての `doctor` チェックを通過しても、シナリオがバックエンドにないケイパビリティを使っていると、run の途中で失敗する可能性がありました（例: `multiTouch` のない idb 上での `pinch`）。

  CLI の `doctor` コマンドに、新しい `--scenario` オプションを追加しました。シナリオ YAML を読み込み、バックエンドの静的ケイパビリティに対して `capability_preflight` を実行します。非対応の構文は、人間が読めるシナリオパスを付けて報告します（例: `step 3 > if > then[0]: pinch needs 'multiTouch'`）。この点検はデバイス不要の純粋関数です。`use` コンポーネントの展開は適用しません。展開には config コンテキストが必要だからです。そのため、展開によってのみ導入されるケイパビリティは検出されません。
- **Web UI が `POST /api/doctor` でプリフライトチェックを公開** ([#408](https://github.com/bajutsu-e2e/bajutsu/pull/408))。Web UI（`serve/`）にはオンボーディングの窓口がありませんでした。セットアップの問題を診断するには、CLI か MCP ツールを使う必要がありました。

  新しい `POST /api/doctor` エンドポイントは、指定したターゲットに対して、設定の検証とツールの実行可能性のチェックを実行します。結果は、構造化 JSON（`{ok, checks[], target, backend}`）で返します。ライブの画面スコアは省略します。Web UI のユーザが、まだデバイスに接続していない可能性があるからです。このエンドポイントの目的は、run を試みる前にセットアップの問題を診断することにあります。両方のトランスポート（stdlib のハンドラと FastAPI）で公開しています。

## 検討した代替案

**各オンボーディング案を既存の項目に折り込む案。** 一部の候補には、自然な居場所があります。新しいバックエンドの実行可能性チェックは、そのバックエンドの作業に属します。しかし多くの候補は横断的で、単一の担い手がいません。より明確な対処策の文言や、初回実行時の既定値がその例です。受け皿がなければ、そうした候補は抜け落ちます。独立したプレースホルダが、着地点を与えます。

**オンボーディングの微調整ごとに、新しい BE 提案を立てる案。** 重すぎるため却下しました。こうした微調整の多くは、一行の診断やメッセージの改善です。変更ごとに完全な提案を要求すると、小さく明らかに良い修正が、プロセスに埋もれます。本項目なら、微調整を低コストで蓄積できます。昇格させるのは、独自の設計に値するものだけです。

**提案を書かず、`doctor` をその場しのぎで改善する案。** 却下しました。ロードマップは、プロジェクトが共有する記憶です。ID がなければ、各オンボーディング改善の理由が失われます。何を検討したかを一覧できる場所もなくなります。可視化されたプレースホルダは、その履歴を保つ最も軽い方法です。

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
