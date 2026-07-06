[English](BE-XXXX-run-behavior-flags-to-config.md) · **日本語**

# BE-XXXX — run のテスト動作を上書きするフラグを config に倒す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-run-behavior-flags-to-config-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | config の取得元 |
<!-- /BE-METADATA -->

## はじめに

「テスト動作の制御は、起動時の CLI フラグではなくチェックインされた config に置く」という方針を定め、それを `bajutsu run` の該当フラグに適用します。対象は、実行全体にわたって、実行中に何が起きるかを決める設定を上書きするフラグです。`--dismiss-alerts` / `--no-dismiss-alerts`、`--alert-instruction`、`--erase` / `--no-erase`、`--network` / `--no-network` の4種です。それぞれを `targets.<name>` の config に移し（すでにあるシナリオ単位の形は残します）、フラグは削除します。CLI には、選択、観測、出力、config の取得元に関するフラグだけを残します。

## 動機

`bajutsu run` は、現在二種類のフラグを混在させています。一方は実行を選択し観測するもので、どのターゲットとシナリオを走らせるか、ブラウザを表示するか、出力をどこに書くか、といったものです。もう一方は実行が何をするかを変えるものです。`--erase` / `--no-erase` は全シナリオの `preconditions.erase` を上書きし、`--dismiss-alerts` / `--no-dismiss-alerts` と `--alert-instruction` はシステムアラートガードを上書きし、`--network` / `--no-network` はアプリのネットワークのやり取りを収集するか（`request` アサーションが読むデータ）を切り替えます。この後者は、フラグとしては据わりが悪いものです。

実行全体を切り替えるフラグは再現性を損ないます。挙動が一時的なフラグで反転できると、「同じテスト」でも誰がどのフラグを渡したかで結果が変わります。prime directive #2（determinism first）は逆を指しています。実行はコミット済みの config とシナリオで決まるべきで、起動時のフラグで決まるべきではありません。

テスト動作は、しばしばアプリの性質でもあります。押すべきアラートのボタン（「Allow」「許可」）、状態を消去するかどうか、そもそもこのターゲットでネットワーク収集が可能かどうか。これらはテスト対象のアプリの性質です。prime directive #3（app-agnostic、アプリごとの差分は config に置く）は、これらを `targets.<name>` に置きます。

フラグが少ないほど、表面は薄くなります。run のフラグはそれぞれ serve にも mirror し（BE-0134）、文書化しなければなりません。4つ削れば、その表面が縮みます。

ここで引く線はこうです。テスト動作の制御は config に移します。選択（`--target`、`--scenario`、`--tag`、`--exclude`、`--backend`、`--udid`、`--workers`、`--browsers`）、観測と表示（`--headed`、`--browser`、`--progress`、`--log-predicate`、`--log-subsystem`）、出力（`--zip`、`--runs-dir`、`--evidence-store`）、config の取得元（`--config`、`--config-offline`、`--require-pinned-config`、ディレクトリの上書き）はフラグのまま残します。これらは何を走らせるか、どう観るか、出力をどこへ出すか、config 自体をどう読むかを決めるもので、いずれも実行の挙動ではないからです。とりわけ `--headed` は残します。これは `headless` の config フィールドに重なるデバッグの補助であって、テスト動作ではありません。

4つのフラグを削除しても、本質的に失うものはありません。「Claude-free の CI 実行のためにガードを強制的に無効にする」用途に `--no-dismiss-alerts` は要りません。ガードは AI の認証情報が無ければすでに no-op になります。本当に一度限りの変更は、ターゲット（またはシナリオ）を編集して行います。実行に関する他のすべてと同じ、コミット済みの状態です。これらはいずれも prime directive #1 には触れません。アラートガードは相変わらず AI プロバイダを `BlockedHandler` としてだけ呼び、`run` / CI の判定経路には関与しません。

## 詳細設計

1. **config スキーマ**：[`bajutsu/config.py`](../../bajutsu/config.py) の `TargetConfig` に次を追加します。
   - `dismissAlerts` … シナリオ側のフィールドと同じ形。真偽値の短縮形（`false` → `{ enabled: false }`）か、オブジェクト形 `{ enabled, instruction }`。シナリオの `DismissAlerts` モデル（[`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)）を再利用するか、形の互換を保った config 側のモデルを別に置きます（config から scenario への import 循環は避けます）。
   - `erase`（bool）… `preconditions.erase` のターゲットごとの既定値。
   - `network`（bool）… アプリのネットワークのやり取りを収集するか（ビルトイン既定は on。現在のフラグの既定と揃えます）。
2. **優先順位（実効値の解決）**：CLI の層が無いので、より具体的なものが勝つ順序で解決します。すなわち `シナリオ単位 ＞ ターゲット config ＞ ビルトイン既定` です。値を設定しないシナリオはターゲットの値を継承し、設定したシナリオは自分の値を保ちます。この処理は run がシナリオを準備してガードを組み立てる箇所、[`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) に置きます。（`network` には現在シナリオ単位の真偽値がありません。必要なら追加でき、なければ `ターゲット config ＞ ビルトイン既定` で解決します。）
3. **フラグとヘルパの削除**：`bajutsu run` から `--dismiss-alerts` / `--no-dismiss-alerts`、`--alert-instruction`、`--erase` / `--no-erase`、`--network` / `--no-network` を外し、実行全体の上書きヘルパ `_apply_dismiss_alerts` と `_apply_erase` を削除します。アラートの instruction は `_alert_guard_factory` の `シナリオ → ターゲット → ビルトイン default_instruction` の連なりに畳み込みます。これは CLI の破壊的変更です。
4. **serve Web UI**：serve は run の argv を、CLI のオプションのメタデータを introspect する汎用のフラグ mirror（[`bajutsu/serve/_cli_flags.py`](../../bajutsu/serve/_cli_flags.py)）で組み立てるので、フラグが無くなれば自動で emit されなくなります。ただし、Web UI にはハンドコードの部分もあり、あわせて削除する必要があります。**Replay / Record / Crawl** の各タブには、erase（`#erasedev`）と dismiss-alerts（`#nodismiss`）のチェックボックスがあり（[`bajutsu/templates/serve.html.j2`](../../bajutsu/templates/serve.html.j2) / [`serve.js`](../../bajutsu/templates/serve.js)）、[`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py) はリクエストボディから `erase` / `dismissAlerts` / `network` / `alertInstruction` を読みます。dead code が残らないよう、これらを削除します（`network` / `alertInstruction` には現在 UI コントロールがありません）。Web UI コントロールのトレードオフは *検討した代替案* を参照してください。
5. **ドキュメントと移行の注記**：新しい config フィールド（英語版と `docs/ja/`）と、フラグ削除を記載し、フラグから config への移行対応を添えます（例：`--alert-instruction "Allow"` → `targets.<name>` の `dismissAlerts: { instruction: "Allow" }`、`--no-erase` → `erase: false`）。これらの設定の取得元を説明している箇所があれば、[`DESIGN.md`](../../DESIGN.md) と [`docs/architecture.md`](../../docs/architecture.md) を更新します。AI プロバイダの認証情報の要件（BE-0047 / BE-0053）は変わりません。
6. **テスト**：新しいフィールドそれぞれの config パース（`dismissAlerts` の二つの on-disk 形を含む）、`シナリオ ＞ ターゲット ＞ 既定` の優先順位を検証するテストを追加し、削除した CLI フラグを検証していたテストは外すか付け替えます。

## 検討した代替案

- **フラグを config と併存させ、実行時の上書きとして残す**（`--headed` / `headless` の形。config が既定を持ち、フラグがその実行だけ上書きする）。採りません。実行の挙動を変える一時的なフラグは再現性に逆行し（prime directive #2）、CLI の表面と serve のフラグ mirror（BE-0134）の両方を増やします。デバッグや表示の補助である `--headed` と違い、この4つはテスト動作なので、その制御はチェックインされた config に置きます。
- **`--dismiss-alerts` だけに絞る**（この項目の当初の枠組み）。採りません。`--erase` は「override every scenario's X」というまったく同じ形で（ヘルパ `_apply_erase` は `_apply_dismiss_alerts` と同型）、`--alert-instruction` / `--network` も同じ種類です。まとめて直せば、フラグごとに同じ移動を繰り返すのではなく、方針を一度述べれば済みます。
- **まずフラグを非推奨にし、後で削除する**。検討しましたが、表面を一つに絞る即削除を選びました。移行は `targets.<name>` へ移す一行の作業です。
- **選択・観測のフラグ（`--headed`、`--browser`、ディレクトリの上書き）も一緒に倒す**。範囲外です。これらはテスト動作ではなく、何を走らせるか、どう観測するかを選ぶもので、config の対応を持つものはすでに設計どおり config と併存しています。
- **serve Web UI に実行ごとの動作トグルを残す**（erase / dismiss-alerts のチェックボックスを削除せずに）。採りません。これらのチェックボックスは、まさにこの方針が退けている実行ごとの一時的なトグルなので、Web UI もテスト動作を独自に上書きするのではなく target config から読むべきです。ただし影響は実在します。serve は現在 config を読み取り専用で開くので、チェックボックスが無くなると、serve のユーザは erase / dismiss-alerts を UI ではなく実行に使う target config で設定することになります。その config を Web UI で編集できるようにして UI からの制御を取り戻すことは、「serve Web UI への CLI 機能の取り込み」トピックの範疇であり、この項目ではありません。この項目は dead になるコントロールを削除するだけです。`network` / `alertInstruction` には現在 Web UI コントロールが無いので、失うものはありません。
- **ターゲット単位ではなく全体の `Defaults` フィールドを使う**。主たる置き場所としては採りません。これらの設定はアプリに固有なので、自然なキーはターゲットです。全アプリに共通する既定値が本当に必要になれば、あとから `Defaults` のフィールドを足せます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `TargetConfig` に `dismissAlerts`、`erase`、`network` を追加する（スキーマと `dismissAlerts` の変換）。
- [ ] run の経路で各設定の優先順位 `シナリオ ＞ ターゲット ＞ 既定` を解決する。
- [ ] 4つのフラグと `_apply_dismiss_alerts` / `_apply_erase` を削除し、instruction を既定の連なりに畳み込む。
- [ ] serve：erase / dismiss-alerts のチェックボックス（Replay/Record/Crawl）と `dispatch.py` のリクエストボディの読み取りを削除する。argv はフラグ mirror が自動で落とす（BE-0134）。
- [ ] config フィールドとフラグ削除を移行対応つきで文書化する（英語と日本語）。影響があれば DESIGN.md / architecture.md を更新する。
- [ ] パースと優先順位のテストを追加し、CLI フラグのテストは外すか付け替える。

## 参考

- run のフラグとガード：[`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py)（`_apply_dismiss_alerts`、`_apply_erase`、`_alert_guard_factory`）。
- シナリオモデル：[`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)（`DismissAlerts`、`Preconditions`）。
- config モデル：[`bajutsu/config.py`](../../bajutsu/config.py)（`TargetConfig`、`Defaults`）。
- フラグと config の前例：[`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py)（`_with_headed`、`TargetConfig.headless` に重なります）。観測系のフラグに残す形です。
- serve での露出：[`bajutsu/serve/_cli_flags.py`](../../bajutsu/serve/_cli_flags.py)（汎用のフラグ mirror）、[`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py)（リクエストボディの読み取り）、[`bajutsu/templates/serve.html.j2`](../../bajutsu/templates/serve.html.j2) / [`serve.js`](../../bajutsu/templates/serve.js)（erase / dismiss-alerts のチェックボックス）。
- 関連：BE-0134（serve と CLI のフラグ mirror のずれ）、BE-0047（AI データ主権 / redaction）、BE-0104 / BE-0053（ベンダー中立の AI プロバイダ）。
