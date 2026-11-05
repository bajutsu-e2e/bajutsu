# XCUITestバンドルRunnerの自動更新

> ステータス: 実装完了
> 対象: `bajutsu/common/platform_lifecycle/environments/xcuitest/`、`bajutsu/common/platform_lifecycle/environments/_bundled_runner.py`、`scripts/serve.sh`、`Makefile`
> 関連: [BE-0292](../../roadmaps/BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md)、[docs/architecture.md](../architecture.md)

BajutsuKitのソースが存在するチェックアウトでは、wheel同梱のXCUITest Simulator Runnerを、呼び出し経路によらず常にソースと同じ状態に保ちます。鮮度判定と、差分があるときの再ビルドを、`_resolve_runner`側の1箇所に集約します。`scripts/serve.sh`が独自に持つbash実装は、そこへ委譲します。

## 1. なにをつくるのか

Simulatorターゲットが`xcuitest.testRunner`を指定していないとき（bundled tier）を考えます。`_resolve_runner`は、BajutsuKitソースの内容ハッシュを計算します。この値を、バンドル済みRunnerの`build-info.json`が記録する`sourceHash`と比較します。両者が一致しなければ、`make runner-bundle`と同等の再ビルドを実行し、その結果を使います。

この判定と再ビルドは、bundled tierを実際に解決するあらゆる呼び出し経路で共通して働きます。対象には`bajutsu run`、pytest経由のテスト、`bajutsu serve`が含まれます。`bajutsu doctor`は別枠です。後述のとおり、判定結果の開示だけを行い、再ビルドはしません。

再ビルドが必要なのに実行できない場合は、古いバンドルへフォールバックせず、その場でエラーにします。実行できない場合とは、`xcodebuild`または`xcodegen`がない場合と、`xcodebuild`自体が失敗する場合を指します。

`doctor`の`xcuitest_runner_summary`は、ビルドを走らせずにハッシュ比較だけを行います。バンドルが最新か失効しているかを、1行追加で開示します。

### やらないこと

- **explicit tier（`xcuitest.testRunner`を指定したターゲット）には触れません。** ユーザーは`build`フィールドで、すでに再ビルドコマンドを制御できます。
- **device tierには触れません。** 実機Runnerは署名が要るため、引き続き明示設定を必須とします（BE-0288）。
- **BajutsuKitソースを含まないインストール（wheelインストール）では、鮮度判定自体をしません。** ハッシュを取る対象のソースが存在しないため、現状どおり同梱バンドルをそのまま使います。
- **呼び出しのたびに無条件で`xcodebuild`を実行する仕組みにはしません。** ハッシュが一致する限り、再ビルドは走らせません。
- **再ビルド失敗時に古いバンドルへフォールバックする経路は作りません。** 失敗はその場でエラーにします。
- **新しいコマンドライン引数は追加しません。** 既存の`BAJUTSU_SKIP_RUNNER_BUNDLE`環境変数を流用します。`serve.sh`だけでなく`_resolve_runner`側の判定にも、この環境変数を適用します。

## 2. なぜつくるのか

`bajutsu/_xcuitest_runner/`（wheel同梱のbundled Runner）の鮮度判定と自動再ビルドは、現在`scripts/serve.sh:76-101`にしかありません。この仕組みは、`serve_uses_xcuitest`とハッシュ比較（`scripts/xcuitest-runner-hash.sh`）を経て、`make runner-bundle`を呼びます。`make serve`から`bajutsu serve`を起動する経路だけが、この仕組みを発火させます。

一方、`_resolve_runner`（`_functions.py:512-554`）は、bundled tierに落ちたとき、`bundled_products_dir()`の存在だけを見ます。その中身が現在のBajutsuKitソースと一致しているかは、確認しません。次の経路は、すべてこちらを通ります。

- `bajutsu run`を直接叩く経路
- pytestからxcuitestバックエンドのテストを実行する経路
- `bajutsu doctor`で疎通を確認する経路

存在確認だけで鮮度を見ない設計は、BajutsuKit/Runnerの開発中に、次の事故を招きえます。Runner側のソースを変更したあと、`make serve`を経由せず`bajutsu run`やpytestを直接実行すると、`_xcuitest_runner/`に残っている古いバンドルをそのまま掴みます。テストは通常どおり実行され、失敗しません。変更した挙動が実際には反映されていないまま、「動いた」という誤った確認が残ります。この不整合は、`make runner-bundle`を手動で叩くまで気づけません。

デバイス経由の`demos/showcase/`のターゲット（`run-swiftui`や`e2e-*`など）は、`runner-build`をmakeの前提条件にしています。CI（`ios-e2e.yml`）も、明示的に`make runner-bundle`を都度実行しています。したがって、この事故はbundled tierを頼る経路に限られます。つまり、BajutsuKit開発者がserve以外の経路で素朴にxcuitestバックエンドを動かす場面です。裏を返せば、その1箇所（`_resolve_runner`）に鮮度判定を集約すれば、経路ごとに個別対応する必要がなくなります。

## 3. どう実現するか

### ハッシュ計算をPythonに移す

`scripts/xcuitest-runner-hash.sh`は、次の5つを対象にハッシュを計算しています。

- `BajutsuKit/Package.swift`
- `BajutsuKit/Sources`
- `BajutsuKit/Runner/Host`
- `BajutsuKit/Runner/Sources`
- `BajutsuKit/Runner/project.yml`

このハッシュ算出（sha256の入れ子）を、`_bundled_runner.py`に`source_hash() -> str`として移植します。アルゴリズムはシェル版と一致させます。既存の`build-info.json`が記録済みの`sourceHash`と食い違うと、移行直後のすべてのバンドルが不必要に「失効」と判定されてしまうからです。

実装時の補足を記します。実装の途中で、`Package.swift`がリポジトリルートへ移動しました（コミット「`chore(swift): move Package.swift to the repo root`」）。この移動を受けて、`_HASH_SOURCE_PATHS`の対象は`BajutsuKit/Package.swift`ではなく`Package.swift`にしました。上の5項目は、移植元のシェルスクリプトが対象にしていたものをそのまま記録しています。以後の対象は、`_bundled_runner.py`の`_HASH_SOURCE_PATHS`を正とします。

`Makefile`の`runner-bundle`ターゲットも、`build-info.json`への書き込みでこの関数を呼ぶよう変更します（`uv run python -c "..."`経由）。ハッシュの算出箇所を1つに保ち、シェル版とPython版が別々に変化してずれることを防ぎます。この時点で`scripts/xcuitest-runner-hash.sh`の呼び出し元がなくなるため、削除します。

### ソース有無の判定

`_bundled_runner.py`に`runner_source_present() -> bool`を追加します。`_BUNDLE_DIR`は、`Path(__file__).resolve()`を起点に`bajutsu/_xcuitest_runner`を指します。この判定も同じ起点の1階層上（`parents[4]`、リポジトリルート）を基準にし、そこから`BajutsuKit/Runner/project.yml`の存在を見ます。`pyproject.toml`の`packages = ["bajutsu"]`が示すとおり、wheelインストールには`BajutsuKit/`が含まれません。したがって、この判定はGitチェックアウトかどうかを確実に見分けます。

### 鮮度判定と再ビルド

`_bundled_runner.py`に`ensure_bundled_runner_fresh() -> None`を追加します。処理の流れは次のとおりです。

1. `runner_source_present()`が`False`なら、何もせず戻ります。
2. 環境変数`BAJUTSU_SKIP_RUNNER_BUNDLE`が`"1"`なら、何もせず戻ります。
3. `source_hash()`を計算し、`bundled_runner_build_info()`が返す`sourceHash`と比較します。一致し、かつ`bundled_products_dir()`が値を返すなら、何もせず戻ります（最新）。
4. 一致しない、またはバンドルが存在しない場合は、モジュールレベルのロックを取得します。このロックは、`materialize()`の`_digest_lock`と同じ位置づけを持ちます。SimulatorのDevice Poolは、複数レーンから並行して`_resolve_runner`を呼びます。ロックがなければ、同時に複数の`xcodebuild`が走りかねません。
5. ロック内で、手順3を再確認します（二重チェックロック）。別レーンが直前に再ビルドを終えていれば、そのまま戻ります。
6. `xcodebuild`と`xcodegen`がどちらも実行できれば、`make runner-bundle`相当のビルドをサブプロセスで実行します。
7. いずれかのツールがない、またはビルドが失敗したら、`simctl.DeviceError`を送出します。メッセージは`scripts/serve.sh:94-98`と同じ形式にし、欠けているツールを名指しします（例: 「Xcode (xcodebuild) — install Xcode」）。ビルド自体の失敗では、`xcodebuild`の失敗理由を添えます。
8. サブプロセスの終了コードが0でも、手順3の一致判定を取り直します。`make`は、`printf`への値渡しに使うコマンド置換の失敗を拾いません。そのため、`sourceHash`が空文字列のまま`build-info.json`に書かれても、`make runner-bundle`は成功として終了しえます。一致しなければ、そのビルドを「見た目は成功したが結果が一致しない」ものとして、`simctl.DeviceError`を送出します。再確認せずに戻ると、以後の呼び出しがすべて同じ不一致を検出しては、黙って再ビルドを繰り返します。

`_resolve_runner`のbundled tier（`_functions.py:543`の直前）に、呼び出しを追加します。`bundled_products_dir()`を呼ぶ前に、`ensure_bundled_runner_fresh()`を呼びます。

### `scripts/serve.sh`の変更

現在bashで書かれている鮮度判定と再ビルド呼び出し（`scripts/serve.sh:73-101`）を、共通関数の呼び出しに置き換えます。この呼び出しは、`ensure_bundled_runner_fresh()`を呼ぶだけにとどめます。`BAJUTSU_SKIP_RUNNER_BUNDLE`のチェックはPython側にも入りますが、bashのガードは残します。スキップ時に、`uv run`の起動コストを払わずに済むからです。

`serve_uses_xcuitest`によるバックエンド判定と、起動前に「staging…」を表示するUXは維持します。`ensure_bundled_runner_fresh()`が`DeviceError`を送出したら、serveの起動を中断し、そのエラーメッセージを表示します。

### `doctor`の開示

`xcuitest_runner_summary`（`doctor.py:135-149`）に、ビルドを伴わない鮮度チェックの結果をもう1行加えます。`runner_source_present()`が`True`で、かつハッシュが不一致のとき、バンドルが失効していて次回実行時に再ビルドされる旨を注記します。ビルドや`materialize()`は呼びません。開示だけを行うという既存の方針は、変えません。

## 4. 検討した代替案と、採らなかった理由

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| `serve.sh`と同じbashロジックを他の入口（`bajutsu run`、pytestのfixtureなど）にも複製する | 各エントリポイントの前段に、同種のシェル処理を追加します | 実装が経路の数だけ増え、シェルとPythonの二重実装がドリフトします |
| ハッシュ比較を省き、bundled tierを解決するたびに無条件で`xcodebuild`を実行する | 常に最新を保証する、もっとも単純な方式です | Simulatorのdevice poolは複数レーンを並行起動するため、レーンごとに数十秒単位の待ちが乗り、テスト実行全体が大幅に遅くなります |
| 自動発火はさせず、`--rebuild-runner`のような明示フラグでのみ再ビルドする | ユーザーが必要と判断したときだけ実行します | 「常に最新」という動機を満たしません。フラグを付け忘れれば、従来どおり古いバンドルを掴みます |
| 再ビルドに失敗したら既存バンドルへフォールバックし、警告だけ出す（`serve.sh`の現行挙動を踏襲） | ビルド環境が整っていないホストでも、動作を止めません | 「常に最新」を守れないまま実行を続けてしまい、古いRunnerでの実行に気づけない事故を再発させます |

## 5. 作業手順

| # | 状態 | やること | 触るファイル | 完了条件 | 前提 |
|---|---|---|---|---|---|
| 1 | ✅ | `source_hash()`を実装します。既存の`scripts/xcuitest-runner-hash.sh`と同じ入力に対して同じ値を返すことを、テストで確認します | `bajutsu/common/platform_lifecycle/environments/_bundled_runner.py`、対応するテスト | 固定したフィクスチャに対するユニットテストが通ります（`pytest`） | なし |
| 2 | ✅ | `runner_source_present()`を実装します。`BajutsuKit/`がないディレクトリとあるディレクトリの両方を、テストで確認します | 同上 | ユニットテストが通ります | なし |
| 3 | ✅ | `Makefile`の`runner-bundle`ターゲットの呼び出しをPython版（`source_hash()`）に置き換えます。`scripts/xcuitest-runner-hash.sh`本体の削除は、`scripts/serve.sh`がまだ参照しているため、手順8へ後ろ倒しします | `Makefile` | macOS上で`make runner-bundle`を実行すると、`build-info.json`の`sourceHash`が従来と同じ値になります | 1 |
| 4 | ✅ | `ensure_bundled_runner_fresh()`を実装します。`subprocess.run`をモックし、次の3経路をテストします。鮮度一致時は何もしません。不一致時はビルドを呼びます。ツール欠如時はエラーになります | `bajutsu/common/platform_lifecycle/environments/_bundled_runner.py` | ユニットテストが通ります | 1, 2 |
| 5 | ✅ | 並行呼び出しのテストを追加します。2スレッドから同時に`ensure_bundled_runner_fresh()`を呼び、モックした`subprocess.run`の呼び出し回数が1回であることを確認します | 同上 | ユニットテストが通ります | 4 |
| 6 | ✅ | `_resolve_runner`のbundled tierから`ensure_bundled_runner_fresh()`を呼びます。既存の`_resolve_runner`のテストに、鮮度判定が呼ばれることの確認を足します | `bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py`、対応するテスト | ユニットテストが通ります | 4 |
| 7 | ✅ | `xcuitest_runner_summary`に鮮度の注記を追加します | `bajutsu/cli/commands/doctor.py`、対応するテスト | ユニットテストが通ります | 4 |
| 8 | ✅ | `scripts/serve.sh`のbashロジックを`ensure_bundled_runner_fresh()`の呼び出しに置き換えます。これで`scripts/xcuitest-runner-hash.sh`の呼び出し元がなくなるため、本体を削除します | `scripts/serve.sh`、`scripts/xcuitest-runner-hash.sh` | macOS上で`make serve`を実行し、初回はビルドが走り、2回目以降は走らないことを目視で確認します | 6 |
| 9 | ✅ | `docs/architecture.md`とそのja版のRunner解決の記述を更新します。`docs/ai-development.md`はbundled runnerに触れていないため、対象外です | `docs/architecture.md`、`docs/ja/architecture.md` | 記述が現在の挙動と一致します | 8 |
| 10 | ✅ | `make check`を実行します。E2E CI（`ios-e2e.yml`）がすでに`make runner-bundle`を明示実行しているため、この変更が追加の待ちを生まないことも確認します | なし | `make check`が通ります | 9 |
