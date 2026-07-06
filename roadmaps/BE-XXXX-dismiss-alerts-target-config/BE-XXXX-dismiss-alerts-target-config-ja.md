[English](BE-XXXX-dismiss-alerts-target-config.md) · **日本語**

# BE-XXXX — dismissAlerts の制御を config に一本化し、CLI フラグを廃止する

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

システムアラートガードの設定の取得元を config に一本化します。`targets.<name>` にターゲットごとの `dismissAlerts` フィールドを追加し、実行全体を上書きする CLI フラグ（`--dismiss-alerts` / `--no-dismiss-alerts` と `--alert-instruction`）を**削除**します。これ以後、ガードはチェックインされた config とシナリオ単位の `dismissAlerts` だけで制御されます。CLI は薄くなり、実行中のアラート挙動はコミット済みのファイルだけで決まります。

## 動機

システムアラートガード（idb からは見えず操作もできない OS のプロンプトを、画面認識で閉じる仕組み）は、現在ちょうど二つの層でしか設定できません。

- **シナリオ単位**：シナリオ YAML の `dismissAlerts`。リッチな形（`false`、または `{ enabled, instruction }`）で書けます。
- **実行全体**：CLI の `--dismiss-alerts` / `--no-dismiss-alerts`（全シナリオを上書きする真偽値）と、`--alert-instruction`（既定のボタンラベル）。

ここには方向の揃った二つの問題があり、どちらも config へ寄せ、CLI から離す方向を指しています。

第一に、config の層が無く、しかもアラートの挙動はアプリの性質です。`targets.<name>` にも `Defaults` にも `dismissAlerts` はありません。App Tracking Transparency のプロンプトや通知の許可、「パスワードを保存しますか？」のダイアログを常に表示するアプリでは、すべてのシナリオで同じ扱い、同じボタンラベル（「Allow」「許可」「OK」）を使いたいはずです。その既定値は `targets.<name>` に置くのが自然で、prime directive #3（app-agnostic、アプリごとの差分は config に置く）が定めるとおりです。

第二に、実行全体を切り替える CLI フラグは再現性を損ないます。挙動が一時的なフラグで反転できると、「同じテスト」でも誰がどのフラグを渡したかで結果が変わります。prime directive #2（determinism first）は逆を指しています。実行はコミット済みの config とシナリオで決まるべきで、起動時のフラグで決まるべきではありません。CLI は薄いままにし、テスト挙動の制御（実行中に何が起きるか）は config に置きます。これは、実行をどう観察するかというデバッグや表示の補助（`--headed` など）とは区別され、そうした補助はフラグのまま残ってもかまいません。`dismissAlerts` は明確にテスト挙動なので、config に置きます。

フラグを削除しても、本質的に失うものはありません。「Claude-free の CI 実行のためにガードを強制的に無効にする」用途に `--no-dismiss-alerts` は要りません。ガードは AI の認証情報が無ければすでに no-op になるので、キーを与えないことで同じことが達成できます。本当に一度限りの変更は、ターゲット（またはシナリオ）を編集して行います。実行に関する他のすべてと同じ、コミット済みの状態です。

これは config の取得元の変更と、CLI の表面の削減です。ガードが AI プロバイダを呼ぶのは、割り込みプロンプトに対する `BlockedHandler` としてだけであり、`run` / CI の判定経路には関与しません。したがって prime directive #1 には触れず、必要な認証情報も変わりません。

## 詳細設計

1. **config スキーマ**：[`bajutsu/config.py`](../../bajutsu/config.py) の `TargetConfig` に `dismiss_alerts`（エイリアス `dismissAlerts`）を追加し、シナリオ側のフィールドと同じ形を受け付けます。真偽値の短縮形（`dismissAlerts: false`）は `{ enabled: <bool> }` に変換し、オブジェクト形 `{ enabled, instruction }` も受けます。シナリオの `DismissAlerts` モデル（[`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)）を再利用するか、config 側に小さなモデルを別に置くかは、config から scenario への import 循環を避けられるほうを選びます。どちらにせよ、両者は形の互換を保ちます。
2. **優先順位（実効値の解決）**：CLI の層が無くなるので、より具体的なものが勝つ順序で解決します。すなわち `シナリオ単位 ＞ ターゲット config ＞ ビルトイン既定（有効、既定の instruction）` です。`dismissAlerts` を持たないシナリオはターゲットの値を継承し、自分で設定したシナリオは自分の値を保ちます。この処理は run がシナリオを準備してガードを組み立てる箇所、[`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) の `_alert_guard_factory` の近くに置きます。
3. **CLI フラグの削除**：`bajutsu run` から `--dismiss-alerts` / `--no-dismiss-alerts` と `--alert-instruction` を外し、実行全体の上書きヘルパ `_apply_dismiss_alerts` を削除します。instruction の解決は `_alert_guard_factory` の `シナリオ → ターゲット → ビルトイン default_instruction` の連なりに畳み込みます。これは CLI の破壊的変更です。
4. **serve とフラグ mirror**：serve がこれらのフラグを出している場合（run のフラグを mirror します。BE-0134）、そちらからも削除し、二つの表面を揃えます。
5. **ドキュメントと移行の注記**：新しい config フィールド（英語版と `docs/ja/`）と、フラグ削除（代わりに `targets.<name>` やシナリオに何を書くか）を記載します。ガードの設定の取得元を説明している箇所があれば、[`DESIGN.md`](../../DESIGN.md) と [`docs/architecture.md`](../../docs/architecture.md) を更新します。認証情報の要件（BE-0047 / BE-0053 の AI プロバイダのキー）は変わりません。
6. **テスト**：二つの on-disk 形（真偽値の短縮形とオブジェクト形）の config パース、`enabled` と `instruction` の両方について `シナリオ ＞ ターゲット ＞ 既定` の優先順位を検証するテストを追加し、削除した CLI フラグを検証していたテストは外すか付け替えます。

## 検討した代替案

- **CLI フラグを config と併存させ、実行時の上書きとして残す**（`--headed` / `headless` の形。config が既定を持ち、フラグがその実行だけ上書きする）。採りません。実行の挙動を変える一時的なフラグは再現性に逆行し（prime directive #2）、CLI の表面と serve のフラグ mirror（BE-0134）の両方を増やします。デバッグや表示の補助である `--headed` と違い、`dismissAlerts` はテスト挙動なので、その制御は起動時のフラグではなくチェックインされた config に置きます。
- **まずフラグを非推奨にし、後で削除する**。検討しましたが、config を複製するだけのフラグを非推奨のまま抱えるより、表面を一つに絞る即削除を選びました。移行は `--alert-instruction "Allow"`（または `--no-dismiss-alerts`）を `targets.<name>` の `dismissAlerts` に移す一行の作業です。
- **ターゲット単位の代わりに、あるいはそれに加えて `Defaults.dismissAlerts` を置く**。主たる置き場所としては採りません。アラートのボタンラベルはアプリとロケールに固有なので、自然なキーは全アプリ共通の既定値ではなくターゲットです。全アプリに共通する既定値が本当に必要になれば、あとから `Defaults` のフィールドを足せますが、ここでは必要なく、優先順位の層をもう一つ増やすことになります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `TargetConfig` に `dismissAlerts` を追加する（スキーマと真偽値の変換）。
- [ ] run の経路で優先順位 `シナリオ ＞ ターゲット ＞ 既定` を解決する。
- [ ] `--dismiss-alerts` / `--no-dismiss-alerts` / `--alert-instruction` と `_apply_dismiss_alerts` を削除し、instruction を既定の連なりに畳み込む。
- [ ] serve に mirror されたフラグがあれば削除する（BE-0134）。
- [ ] フィールドとフラグ削除を文書化する（英語と日本語）。影響があれば DESIGN.md / architecture.md を更新する。
- [ ] パースと優先順位のテストを追加し、CLI フラグのテストは外すか付け替える。

## 参考

- CLI フラグとガード：[`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py)（`_apply_dismiss_alerts`、`_alert_guard_factory`）。
- シナリオモデル：[`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)（`DismissAlerts`）。
- config モデル：[`bajutsu/config.py`](../../bajutsu/config.py)（`TargetConfig`、`Defaults`）。
- フラグと config の前例：[`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py)（`_with_headed`、`TargetConfig.headless` に重なります）。
- 関連：BE-0134（serve と CLI のフラグ mirror のずれ）、BE-0047（AI データ主権 / redaction）、BE-0104 / BE-0053（ベンダー中立の AI プロバイダ）。
