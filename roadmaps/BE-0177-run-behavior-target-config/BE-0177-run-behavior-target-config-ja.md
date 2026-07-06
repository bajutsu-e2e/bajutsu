[English](BE-0177-run-behavior-target-config.md) · **日本語**

# BE-0177 — run のテスト動作設定にターゲット config の既定値を持たせる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0177](BE-0177-run-behavior-target-config-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0177") |
| 実装 PR | [#715](https://github.com/bajutsu-e2e/bajutsu/pull/715) |
| トピック | config の取得元 |
<!-- /BE-METADATA -->

## はじめに

現在はシナリオ単位で設定するか、実行全体を CLI フラグで上書きするしかない run のテスト動作設定に、ターゲットごとの config の層を追加します。対象はシステムアラートガード（`dismissAlerts` とその instruction）、`erase`、`network` の収集です。それぞれに `targets.<name>` のフィールドを設け、アプリ単位の既定値を与えます。すでにあるシナリオ単位の形も CLI フラグも、どちらも残します。設定は `CLI フラグ ＞ シナリオ単位 ＞ ターゲット config ＞ ビルトイン既定` の順で解決します。これは、ブラウザ表示についてコードベースがすでに使っている `--headed` / `headless` の重ね方とまったく同じです。欠けていた config の層を足す、非破壊的な追加です。

## 動機

実行中に何が起きるかを決める `bajutsu run` のいくつかの設定、すなわちアラートガード（`--dismiss-alerts` / `--no-dismiss-alerts`、`--alert-instruction`）、状態の消去（`--erase` / `--no-erase`）、ネットワークの収集（`--network` / `--no-network`）は、シナリオ単位で設定するか、実行全体をフラグで上書きできますが、**config にアプリ単位の既定値がありません**。`targets.<name>` にも `Defaults` にもこれらはありません。

これらの既定値はアプリの性質です。押すべきアラートのボタン（「Allow」「許可」）、実行前に状態を消去するか、そもそもこのターゲットでネットワーク収集が可能か。これらはテスト対象のアプリの性質で、どのシナリオでも同じです。prime directive #3（app-agnostic、アプリごとの差分は config に置く）は、これらを `targets.<name>` に置きます。この層が無いと、設定を各シナリオに書き写すか、実行のたびにフラグを渡すことになります。

これは非対称も解消します。`--headed` と `--browser` はすでに `TargetConfig.headless` / `browser` の config フィールドに重なっています。config がアプリの既定値を持ち、フラグがその実行だけ上書きします。`--dismiss-alerts` / `--erase` / `--network` にはこれに対応する config がありません。追加すれば、run のフラグ全体が揃います。

CLI フラグは意図して残します。一度限りの CI 実行、デバッグ、そしてこれらのフラグに紐づく serve Web UI の Replay / Record / Crawl のチェックボックスのための、実行時の上書きとして残ります。両方を持つのは確立された `--headed` のモデルなので、変更は非破壊的で、serve Web UI もそのまま動きます（後述の *serve Web UI* を参照）。再現性（prime directive #2）は保たれます。実行はその入力、すなわち config、シナリオ、渡されたフラグを与えれば決定的で、serve も CI もそれらを記録します。prime directive #1 には触れません。アラートガードは相変わらず AI プロバイダを `BlockedHandler` としてだけ呼び、`run` / CI の判定経路には関与しません。

## 詳細設計

1. **config スキーマ**：[`bajutsu/config.py`](../../bajutsu/config.py) の `TargetConfig` に次を追加します。
   - `dismissAlerts` … シナリオ側のフィールドと同じ形。真偽値の短縮形（`false` → `{ enabled: false }`）か、オブジェクト形 `{ enabled, instruction }`。シナリオの `DismissAlerts` モデル（[`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)）を再利用するか、形の互換を保った config 側のモデルを別に置きます（config から scenario への import 循環は避けます）。
   - `erase`（bool）… `preconditions.erase` のターゲットごとの既定値。
   - `network`（bool）… アプリのネットワークのやり取りを収集するか（ビルトイン既定は on。現在のフラグの既定と揃えます）。
2. **優先順位（実効値の解決）**：より具体的なものが勝つ順序で解決します。すなわち `CLI フラグ ＞ シナリオ単位 ＞ ターゲット config ＞ ビルトイン既定` です。ターゲット config は、シナリオが何も設定しないときに継承される既定値を与えます。設定したシナリオは自分の値を保ち、CLI フラグは実行全体を上書きします。この処理は run がシナリオを準備してガードを組み立てる箇所、[`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) の `_apply_dismiss_alerts` / `_apply_erase` / `_alert_guard_factory` の隣に置き、[`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py) の `--headed` / `headless` の前例（`_with_headed`）と同じように読めるようにします。（`network` には現在シナリオ単位の真偽値がありません。`CLI フラグ ＞ ターゲット config ＞ ビルトイン既定` で解決します。）
3. **instruction の既定値**：ターゲットの `instruction` は、`_alert_guard_factory` の既存の `default_instruction` の連なりに、シナリオ単位の instruction と CLI の `--alert-instruction` の下位として組み込みます。
4. **serve Web UI**：削除は不要です。フラグと、erase（`#erasedev`）/ dismiss-alerts（`#nodismiss`）の Replay / Record / Crawl のチェックボックスはそのまま動きます（[`bajutsu/serve/_cli_flags.py`](../../bajutsu/serve/_cli_flags.py) がフラグを mirror し、[`bajutsu/templates/serve.html.j2`](../../bajutsu/templates/serve.html.j2) / [`serve.js`](../../bajutsu/templates/serve.js) がコントロールを持ちます）。ターゲット config の既定値は、チェックが入っていないチェックボックスの*下位*に適用されるので、`dismissAlerts` を設定したターゲットのバンドルを serve で実行すると、ユーザが何も切り替えなくてもその既定値が効きます。この層が serve を通しても成り立つことを確認します。削除するものはありません。
5. **ドキュメント**：新しい config フィールド（英語版と `docs/ja/`）と、優先順位の全体を記載します。これらの設定の取得元を説明している箇所があれば、[`DESIGN.md`](../../DESIGN.md) と [`docs/architecture.md`](../../docs/architecture.md) を更新します。AI プロバイダの認証情報の要件（BE-0047 / BE-0053）は変わりません。
6. **テスト**：新しいフィールドそれぞれの config パース（`dismissAlerts` の二つの on-disk 形を含む）、各設定について `CLI ＞ シナリオ ＞ ターゲット ＞ 既定` の優先順位を検証するテストを追加します。

## 検討した代替案

- **制御を config に完全に倒し、CLI フラグを削除する**（この項目の以前の枠組み）。採りません。CLI の破壊的変更であり、serve Web UI の erase / dismiss-alerts のチェックボックスを機能させなくします。serve は config を読み取り専用で開くので、別途 config 編集の UI を作るまで、serve のユーザはこれらをまったく設定できなくなります。さらに、フラグと config の両方を持つ `--headed` / `--browser` との一貫性も崩れます。再現性の利得はわずかです。実行はその記録された入力（config、シナリオ、フラグ）を与えれば既に決定的なので、config の既定値と明示的なフラグの上書きという組み合わせは、`--headed` が使うのと同じ、クリーンでよく理解されたモデルです。
- **ターゲット単位ではなく全体の `Defaults.dismissAlerts` / `erase` / `network` を使う**。主たる置き場所としては採りません。これらはアプリに固有なので、自然なキーはターゲットです。全アプリに共通する既定値が本当に必要になれば、あとから `Defaults` のフィールドを足せます。
- **`--dismiss-alerts` だけに絞る**。`--erase` は「override every scenario's X」という同じ形で、`--network` も同じ種類です。三つまとめて config の層を足せば、ほぼ同じ変更を三度行うのではなく、一つのまとまった変更になります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `TargetConfig` に `dismissAlerts`、`erase`、`network` を追加し、`Effective` に解決する（スキーマと `dismissAlerts` の変換）。
- [x] run の経路で各設定の優先順位 `CLI ＞ シナリオ ＞ ターゲット ＞ 既定` を解決する（`erase` は `_filter_scenarios`、ガードの `enabled` は `_alert_guard_factory`、`network` は `run`）。「未設定」が下位に落ちるよう `Preconditions.erase` と `--network` を `bool | None` にした。
- [x] ターゲットの `instruction` を `default_instruction` の連なりに組み込む。
- [x] この層が serve を通しても成り立つことを確認する（フラグとチェックボックスは変更せず、config の既定値が下位に効く。serve のコード変更は不要）。
- [x] config フィールドと優先順位を文書化する（英語と日本語の `configuration.md` / `cli.md`）。DESIGN.md / architecture.md への影響はなし。
- [x] パースと優先順位のテストを追加する。

**ログ**

- [#715](https://github.com/bajutsu-e2e/bajutsu/pull/715) — `dismissAlerts` / `erase` / `network` のターゲットごとの config 層を、優先順位 `フラグ ＞ シナリオ ＞ ターゲット ＞ 既定` で実装。config パースと優先順位のテスト、日英のドキュメント更新を含む。

## 参考

- run のフラグとガード：[`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py)（`_apply_dismiss_alerts`、`_apply_erase`、`_alert_guard_factory`）。
- シナリオモデル：[`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)（`DismissAlerts`、`Preconditions`）。
- config モデル：[`bajutsu/config.py`](../../bajutsu/config.py)（`TargetConfig`、`Defaults`）。
- フラグと config の前例：[`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py)（`_with_headed`、`TargetConfig.headless` に重なります）。この項目が足すのと同じ重ね方です。
- serve での露出：[`bajutsu/serve/_cli_flags.py`](../../bajutsu/serve/_cli_flags.py)（フラグ mirror）、[`bajutsu/templates/serve.html.j2`](../../bajutsu/templates/serve.html.j2) / [`serve.js`](../../bajutsu/templates/serve.js)（erase / dismiss-alerts のチェックボックス）。
- 関連：BE-0134（serve と CLI のフラグ mirror）、BE-0047（AI データ主権 / redaction）、BE-0104 / BE-0053（ベンダー中立の AI プロバイダ）。
