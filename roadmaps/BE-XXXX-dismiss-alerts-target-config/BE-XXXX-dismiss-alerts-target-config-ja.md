[English](BE-XXXX-dismiss-alerts-target-config.md) · **日本語**

# BE-XXXX — dismissAlerts をターゲットごとに config で指定する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-dismiss-alerts-target-config-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Configuration sourcing |
<!-- /BE-METADATA -->

## はじめに

`targets.<name>` の config にターゲットごとの `dismissAlerts` フィールドを追加します。これにより、システムアラートガードの既定値、つまりガードを有効にするかどうかと、どのボタンを押すかを、アプリごとに一度だけ config で設定できるようになります。シナリオ単位の `dismissAlerts` と、実行全体を上書きする CLI の `--dismiss-alerts` のあいだに欠けている中間の層を埋めます。

## 動機

システムアラートガード（idb からは見えず操作もできない OS のプロンプトを、画面認識で閉じる仕組み）は、現在ちょうど二つの層でしか設定できません。

- **シナリオ単位**：シナリオ YAML の `dismissAlerts`。リッチな形（`false`、または `{ enabled, instruction }`）で書けます。
- **実行全体**：CLI の `--dismiss-alerts` / `--no-dismiss-alerts`（全シナリオを上書きする真偽値）と、`--alert-instruction`（既定のボタンラベル）。

config の層がまったく存在しません。`targets.<name>` にも `Defaults` にも `dismissAlerts` はありません。これはデータの持ち方として適切ではありません。

アラートの挙動は、シナリオの性質ではなくアプリの性質です。App Tracking Transparency のプロンプトや通知の許可、「パスワードを保存しますか？」のダイアログを常に表示するアプリでは、すべてのシナリオで同じ扱い、同じボタンラベル（「Allow」「許可」「OK」）を使いたいはずです。現在の設計では、そのために各シナリオファイルに `dismissAlerts: { instruction: "Allow" }` を書き写すか、実行のたびに `--alert-instruction "Allow"` を渡すことを覚えておくしかありません。これはアプリごとの重複そのものであり、prime directive #3（app-agnostic、アプリごとの差分は config に置く）が `targets.<name>` に置くべきだと定めている対象です。

既存のフラグと config の関係とも非対称です。`--headed` は `TargetConfig.headless` の config フィールドに重なる形で働き、ブラウザ表示の既定値は config に置いたうえで、フラグがその実行だけ上書きします。`--dismiss-alerts` にはこれに対応する config がありません。この隙間を埋めれば、両者の振る舞いが揃います。

これは config の取得元だけの変更です。ガードが AI プロバイダを呼ぶのは、割り込みプロンプトに対する `BlockedHandler` としてだけであり、`run` / CI の判定経路には関与しません。したがって prime directive #1 には触れず、必要な認証情報も変わりません。

## 詳細設計

1. **config スキーマ**：[`bajutsu/config.py`](../../bajutsu/config.py) の `TargetConfig` に `dismiss_alerts`（エイリアス `dismissAlerts`）を追加し、シナリオ側のフィールドと同じ形を受け付けます。真偽値の短縮形（`dismissAlerts: false`）は `{ enabled: <bool> }` に変換し、オブジェクト形 `{ enabled, instruction }` も受けます。シナリオの `DismissAlerts` モデル（[`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)）を再利用するか、config 側に小さなモデルを別に置くかは、config から scenario への import 循環を避けられるほうを選びます。どちらにせよ、両者は形の互換を保ちます。
2. **優先順位（実効値の解決）**：run がシナリオを準備してガードを組み立てるとき、より具体的なものが勝つ順序で解決します。すなわち `CLI ＞ シナリオ単位 ＞ ターゲット config ＞ ビルトイン既定（有効、既定の instruction）` です。`dismissAlerts` を持たないシナリオはターゲットの値を継承し、自分で設定したシナリオは自分の値を保ち、CLI の `--dismiss-alerts` / `--no-dismiss-alerts` は実行全体の `enabled` を上書きします。この処理は [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) の `_apply_dismiss_alerts` / `_alert_guard_factory` の隣に置き、順序が明示され、[`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py) の `--headed` / `headless` の前例（`_with_headed`）と同じように読めるようにします。
3. **instruction の既定値**：ターゲットの `instruction` は、アプリごとの既定のボタンラベルになります。`_alert_guard_factory` にある既存の `default_instruction` の連なりに、シナリオ単位の instruction と CLI の `--alert-instruction` の下位として組み込みます。
4. **ドキュメント**：新しいフィールドを config リファレンス（英語版と `docs/ja/`）に記載します。ガードの設定の取得元を説明している箇所があれば、[`DESIGN.md`](../../DESIGN.md) と [`docs/architecture.md`](../../docs/architecture.md) を更新します。認証情報の要件（BE-0047 / BE-0053 の AI プロバイダのキー）は変わらないことを明記します。
5. **テスト**：二つの on-disk 形（真偽値の短縮形とオブジェクト形）の config パース、そして `enabled` と `instruction` の両方について `CLI ＞ シナリオ ＞ ターゲット ＞ 既定` の優先順位を検証するテストを追加します。

## 検討した代替案

- **ターゲット単位の代わりに、あるいはそれに加えて `Defaults.dismissAlerts` を置く**。主たる置き場所としては採りません。アラートのボタンラベルはアプリとロケールに固有なので、自然なキーは全アプリ共通の既定値ではなくターゲットです。全アプリに共通する既定値が本当に必要になれば、あとから `Defaults` のフィールドを足せますが、この隙間を埋めるのに必要はなく、わずかな利得のために優先順位の層を四つ目として増やすことになります。
- **CLI 専用のままにする**。採りません。本来アプリの性質であるものに対して、実行ごとのフラグやシナリオごとの重複を強いることになり、config がまさに取り除くための重複です。
- **run オプション向けの汎用的な優先順位の仕組みを新設する**。過剰です。本提案は仕組みを新たに作らず、確立済みの `--headed` / `headless` の重ね方をそのまま再利用します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `TargetConfig` に `dismissAlerts` を追加する（スキーマと真偽値の変換）。
- [ ] run の経路で優先順位 `CLI ＞ シナリオ ＞ ターゲット ＞ 既定` を解決する。
- [ ] ターゲットの `instruction` を `default_instruction` の連なりに組み込む。
- [ ] フィールドを文書化する（英語と日本語）。影響があれば DESIGN.md / architecture.md を更新する。
- [ ] パースと優先順位のテストを追加する。

## 参考

- CLI フラグとガード：[`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py)（`_apply_dismiss_alerts`、`_alert_guard_factory`）。
- シナリオモデル：[`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)（`DismissAlerts`）。
- config モデル：[`bajutsu/config.py`](../../bajutsu/config.py)（`TargetConfig`、`Defaults`）。
- フラグと config の前例：[`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py)（`_with_headed`、`TargetConfig.headless` に重なります）。
- 関連：BE-0134（serve と CLI のフラグ mirror のずれ）、BE-0047（AI データ主権 / redaction）、BE-0104 / BE-0053（ベンダー中立の AI プロバイダ）。
