[English](BE-0450-worker-capability.md) · **日本語**

# BE-0450 — worker が実行できる範囲の宣言：ホスト対応とデバイス台数の上限

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0450](BE-0450-worker-capability-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0450") |
| トピック | Driver & backend architecture |
<!-- /BE-METADATA -->

## はじめに

**worker capability** は、1 台の worker が何を実行できるかを表します。worker は、シナリオを実行するマシンまたはプロセスです。開発者のマシン、server の worker、デバイスクラウドへ投入する worker が含まれます。worker capability は、`worker.yaml` という小さな YAML ファイルに置きます。ファイルには、worker の環境、worker が同時に進行できるジョブの数、1 回のジョブが持てる target の数、worker が提供するドライバーを書きます。Bajutsu は、デバイスに触れる前にシナリオの要求をこのファイルと突き合わせます。この worker で動かせないシナリオは、満たされない行を理由に名指しして、その場で失敗します。worker capability は、server がジョブを worker へ振り分けるために使っている語彙（[BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md)）を拡張します。

現状、実行可否を決める事実は二つあり、どちらにも宣言がありません。一つは、worker のホストがそのプラットフォームを操作できるかどうかです。iOS のシナリオには macOS が必要で、Linux の worker では動きません。もう一つは、1 回のジョブが同時に操作できる target の数です。target は、`bajutsu.config.yaml` の `targets` の 1 項目です（[用語集](../../docs/ja/glossary.md#target-app-device)）。シナリオの実行中、各 target はデバイスを 1 台保持します。デバイスは、Simulator、エミュレーター、実機、ブラウザのコンテキストのいずれかです。AWS Device Farm の 1 回の実行が確保する物理端末は 1 台なので、2 つの target を操作するシナリオは動きません。ホストは、ドライバーとマシンの事実なので、本項目はドライバーとマシンから得ます。target の上限は、worker の環境の事実なので、運用者が `worker.yaml` に書きます。

## 動機

Bajutsu は、*ドライバー*ができることに対しては、すでにシナリオを検査しています。preflight の capability チェック（[BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md)）は、各構文をドライバーの capability トークンで判定します。デバイス操作のトークン（[BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities-ja.md)）は、その判定を操作ごとに分けました。この仕組みが答えるのは「このドライバーはこのステップを実行できるか」です。「この worker はこのドライバーを動かせるか、target を何個まで扱えるか」には答えられません。この二つの事実は、ステップにもドライバーのトークンにも属しません。

欠けた事実は、曖昧な失敗として現れます。`xcodebuild` のないホストでは、iOS のアクチュエーターが単に利用不可になり、`select_actuator` が汎用の `no available actuator` を送出します（[`bajutsu/common/backends.py:121`](../../bajutsu/common/backends.py)）。メッセージは iOS に macOS が必要だとは述べないため、Linux の worker へ iOS のジョブを送った運用者は、原因を自分で突き止めることになります。server の worker プール（[BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md)）は `platform:ios` のようなトークンでルーティングしますが、このトークンはホスト OS と結び付いていません。

target の数にも同じ欠落があります。複数 target のシナリオ（[BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md)）は、`targets` の各項目について 1 つの target を全体の実行中保持し、ローカルの実行は台数が足りないプールを拒否します。serve の Device Farm dispatch（[BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md)）は、シナリオごとに 1 件のリクエストを作り、リクエストは単一の `target` だけを持ちます。Device Farm の 1 回の実行が確保する端末は 1 台だけですが、そのことを示す宣言はありません。そのため dispatch には、2 つの target を名指すシナリオを拒否する根拠がなく、そのシナリオは課金される端末を確保してから失敗します。

実装後に確認できる違いは 3 つあります。

- Linux ホストで iOS のシナリオを実行すると、`no available actuator` ではなく、デバイス操作の前に、満たされない要件を名指しした理由（`xcuitest runs only on macOS`）で失敗します。
- Device Farm の worker が lease した、2 つの target を持つシナリオは、端末の確保より前に、target 1 個の上限を名指しした理由で失敗します。
- 同じシナリオのスイートは、Mac では変更なしで動きます。

## 詳細設計

### ファイルが述べるもの

**worker** はシナリオを実行するマシンであり、**target** はシナリオが操作するアプリを指します。この 2 つは、書き手も寿命も異なります。target は、アプリのチームが `bajutsu.config.yaml` に書きます。マシンが何を扱えるかを知っているのは、運用者です。したがって worker capability は、`bajutsu.config.yaml` には置かず、専用のファイル `worker.yaml` に置きます。プロジェクトの設定は、server の worker がジョブを lease したあとでしか届かないので、lease を受けるために広告する内容を、その設定で述べることもできません。

1 つの `worker.yaml` は、1 台の worker を述べます。worker のホストも環境も 1 つなので、どちらにも一覧は要りません。ファイルの最上位キーは 5 つです。

| キー | 型 | 意味 |
|---|---|---|
| `version` | 整数 | ファイルのスキーマのバージョン。現在、ローダーは `1` を受け付け、それ以外の値は拒否します。 |
| `environment` | 文字列 | この worker がジョブを実行する場所です。`local`（開発者のマシン、または自分のホストでデバイスを操作する worker）か、`devicefarm` のようなデバイスクラウドのプロバイダー名です。省略は `local` を表します |
| `maxJobConcurrency` | 正の整数（省略可） | この worker が同時に進行できるジョブの数です。省略は 1 を表します。満杯のあいだ、worker は lease の要求を送りません |
| `maxTargetsPerJob` | 正の整数（省略可） | 1 つのシナリオが宣言できる target の数で、つまり同時に保持するデバイス数です。ドライバーをまたいで数え、Simulator、エミュレーター、実機、ブラウザの target がそれぞれ 1 と数えられます。省略は上限なしを表します |
| `drivers` | ドライバー名の一覧 | この worker が提供するドライバーです。一覧から省いたドライバーは、この worker では利用できません |

`maxTargetsPerJob` は、すべてのドライバーの target を、シナリオごとにまとめて数えます。別々のシナリオを並べて動かす `--workers` のレーンは、足し合わせません。`2` なら、1 回のジョブが Simulator とブラウザを同時に操作できます。`1` なら、どちらか一方だけです。

ファイルは、不変の値として読み込み、スキーマに照らして検証します。未知のドライバー名、未知の環境、正でない数は、読み込み時にエラーになります。`local` 以外の環境は、Bajutsu が登録できる batch provider の種類（[`serve/batch_provider`](../../bajutsu/serve/batch_provider/_functions.py)。現在は `devicefarm`）を名指ししなければなりません。そのプロバイダーを実際に登録できるか（たとえば `DEVICEFARM_PROJECT_ARN` が設定されているか）は、Device Farm の dispatch の項目が述べるとおり、worker の起動時に確かめます。クラウドを加えるとは、そのプロバイダーの種類を加えることなので、ファイルは環境を名指すだけで、定義はしません。`local` の worker が受け付ける `maxJobConcurrency` は 1 だけです。ローカルの worker で複数のジョブを同時に動かすには、ジョブごとにデバイスを割り当てる仕組みが要り、それは別の作業です。ローダーは、それより大きい値を、その旨を添えて拒否します。`bajutsu run` の `--workers` のレーンには影響しません。

### ドライバーとマシンから得るもの

worker capability のファイルは、ホストの一覧を持ちません。ホストは、worker のプロセスが動いている OS であり、worker がマシンから読みます。ファイルからは読みません。ドライバーがそのホストで動くかどうかは、ドライバーの固定された事実なので、各ドライバーが `CAPABILITIES` の隣に宣言します（`xcuitest` は macOS を要し、他の 3 つは Bajutsu が動く場所ならどこでも動きます）。環境が `local` の worker では、ファイルに載せていても、ホストが動かせないドライバーは、`xcuitest runs only on macOS; this worker runs on linux` のような理由で、利用できません。環境がデバイスクラウドの worker では、ドライバーはクラウドのホストで動くので、ホストの規則は適用しません。

パッケージに同梱する既定のファイルは、すべてのドライバーを target の上限なしで提供するローカルの worker を述べます。Linux のマシンでは、`xcuitest` は載っていても、ホストの理由で利用できません。

```yaml
# bajutsu/common/capability/worker.yaml（既定。パッケージに同梱）
version: 1
environment: local
drivers: [xcuitest, adb, playwright, fake]
```

### ファイルを置き換える

運用者は、起動時に `--worker-config` で別のファイルを渡して、そのプロセスの既定を置き換えます。ファイルは既定と統合されず、既定の代わりに使われ、ローダーがスキーマに照らして検証します。以下に、Mac、Android のエミュレーターとブラウザを担当する Linux マシン、Device Farm へ投入する worker の、3 つのファイルを示します。

```yaml
# mac-ci.worker.yaml
version: 1
environment: local
maxTargetsPerJob: 2                # 1 回のジョブで Simulator とブラウザを併用できる
drivers: [xcuitest, playwright]
```

```yaml
# linux-android.worker.yaml
version: 1
environment: local
maxTargetsPerJob: 1                # 1 回のジョブはエミュレーターかブラウザの一方だけ
drivers: [adb, playwright]
```

```yaml
# devicefarm.worker.yaml
version: 1
environment: devicefarm
maxJobConcurrency: 2               # 進行中の Device Farm のジョブは最大 2 つ
maxTargetsPerJob: 1                # Device Farm の 1 回の実行は端末 1 台
drivers: [adb, xcuitest]
```

3 つ目のファイルは `playwright` を省いているので、ブラウザのシナリオは、その worker では利用できません。

ファイルは、ルーティング用トークンを宣言しません。worker が広告する runtime と機種のトークン（`ios18`、`ipad`）は、現在と同じく、Simulator の一覧（[`serve/capabilities.py`](../../bajutsu/serve/capabilities.py)）から、マシンが実際に持つものとして導きます。宣言は lease の成功を約束できず、手書きの在庫は、実際の在庫からずれていく 2 つ目の情報源になるからです。target の上限は事情が違います。実行前にそれを答えるプローブがないので、宣言します。

`bajutsu worker`（`bajutsu worker --once` を含む）と `bajutsu run` は、`--worker-config <path>` を受け取ります。開発者のマシンも worker だからです。`bajutsu run` は、server がジョブを実行しないようにする項目が述べるとおり、1 回実行の worker の薄いラッパーで、`environment` が `local` でないファイルを拒否します。`run` は自分のホストのデバイスを操作するコマンドであり、クラウドへは投入しないからです。`--worker-config` の指定がなければ既定のファイルを使います。環境変数も、作業ディレクトリの暗黙の探索もないので、有効なファイルは常にコマンドラインで名指ししたものです。server は、worker ではないので、このファイルを読みません。

### `--capabilities` と `requires` の廃止

現在、自由なトークンをルーティングに入れる設定は 2 つあります。server の worker は `--capabilities` または `$BAJUTSU_WORKER_CAPABILITIES`（たとえば `ios18,ipad`）を受け取り、それをそのまま広告する集合に加えます（[`serve/cli/worker.py`](../../bajutsu/serve/cli/worker.py)）。target、または `defaults` は `requires` のトークンを列挙し、それがジョブの必須集合に入ります（[`serve/helpers.py`](../../bajutsu/serve/helpers.py)）。この 2 つは 1 つの仕組みの両端であり、本項目は両方を廃止します。

これらの目的のうち、広告する側は、worker capability が担います。runtime と機種のトークンは、フラグなしで Simulator の一覧から導かれます。環境と target の上限は、`worker.yaml` から得られます。`--capabilities` がそれ以上にできるのは、マシンが持たないトークンを広告することで、それは取り除くべき欠陥です。`ios18` を名乗りながら runtime を持たない worker は、実行できないジョブを引き寄せます。BE-0166 が防ごうとした誤ったルーティングそのものです。フラグをなくすと、在庫からは作れない `requires` のトークンは、どの worker も広告できなくなり、そのジョブは永久に待つことになります。したがって設定はフラグと一緒に廃止します。

ジョブの必須集合は、`platform:<p>` と、単一のホスト OS でしか動かないドライバーの `host:<os>` になります。iOS の runtime や機種を要求することは `requires` で可能でしたが、`requires` がなくなると、新しい供給源が要ります。target の `device` フィールドから要求を導く作業は別の項目であり、それまでは、ジョブが iOS の runtime や機種を要求することはできません。

worker が広告するのは、次の情報源の和集合です。`--platform` の `platform:*` トークン、Simulator の一覧から導くトークン、動いている OS の `host:<os>`、環境が `local` でないときの `environment:<name>` です。環境が `local` でない worker は、自分のホストではデバイスを操作しないので、`platform:*` トークンを広告せず、`--platform` の既定値を使わず、明示された `--platform` は環境を名指ししたメッセージで拒否します。`local` の worker は、起動時に `--platform` をファイルとホストに照らして検査します。ファイルが省いているか、ホストが動かせないときは、提供できない platform を広告せず、ドライバーを名指ししたメッセージで終了します。既定の `--platform ios` のまま起動した Linux のマシンは、何もせず待ち続ける状態から起動時のエラーに変わります。これは正しい失敗です。

`--capabilities`、`$BAJUTSU_WORKER_CAPABILITIES`、`requires` は、1 リリースのあいだ非推奨とします。`--capabilities` と環境変数は次のリリースで削除し、`requires` は、target の `device` から要求を導く項目が入ってから削除します。それより前に削除すると、`ios18` や `ipad` を要求するジョブが、対応する worker に届く手段を失うからです。非推奨の期間は、引き続き受け付け、通知を一度だけ出します（`warn_once`、[`deprecations.py`](../../bajutsu/common/deprecations.py)）。フラグと環境変数の通知は、トークンが今後、一覧とファイルから得られると伝えます。`requires` の通知は、iOS の runtime や機種を要求することが、target の `device` から要求を導けるようになるまでサポートされないと伝えます。削除後の `requires` も同じ理由で失敗しますが、`requires` には置き換え先のキーがないので、置き換え先を名指ししない削除のエラーを使います。これらが有効なあいだ、3 つのどれかに予約された接頭辞 `environment:` のトークンがあれば、その予約を名指ししたメッセージで、起動時または config の読み込み時に拒否します。この接頭辞を広告できるのは、登録済みのプロバイダーだけだからです。`--platform` は残します。ドライバーを選ぶ指定であり、capability トークンではないからです。

### シナリオの要求の導出

シナリオの要求は、人が書かずに計算します。要求は、シナリオと有効な target 設定だけから決まる純粋関数です。

- 必要なドライバー：宣言された各 target、または解決された単一の target について、下のホストと `drivers` の絞り込みのあとで選ばれたアクチュエーターです。
- target の数：宣言された target の数、または 1 です。BE-0428 はプールごとに数えますが、この数はすべてのプールの合計です。したがって Simulator の target 1 つとブラウザの target 1 つは、2 と数えます。

シナリオに新しいフィールドは加えません。プライムディレクティブ 3 は守られます。アプリごとの差は `targets.<name>` に残り、worker の事実は `worker.yaml` に残ります。

### 検査

[`capability_preflight.py`](../../bajutsu/common/capability/capability_preflight.py) の `unsupported()` の隣に、純粋関数 `worker_capability_unsupported()` を加えます。引数は、シナリオ、有効な target 設定、worker の capability、ホストです。`unsupported()` は capability トークンしか受け取らず、他の呼び出し元（ランナーの 2 つの経路、アクチュエーターの格上げ、`doctor`）には渡せる worker のファイルがないため、シグネチャを変えません。新しい関数は、シナリオごとに 1 回、target ごとのトークン検査より前に実行します。起動できないドライバーには、ステップ単位の検査は意味を持たないからです。この関数は、シナリオが必要とするドライバーごとに規則 1 と 2 を評価し、シナリオごとに 1 回、規則 3 を評価します。

1. `local` の worker では、ホストがそのドライバーを動かせます。
2. ドライバーが worker の `drivers` に載っています。
3. シナリオの target の数が `maxTargetsPerJob` 以下です。

違反は、シナリオごとに、固定の接頭辞 `worker-capability-unsupported:` で始まる preflight の理由文字列として報告します。スキーマを変えなくても、読み手とテストが capability の理由と区別できます。メッセージには、ドライバーと満たされない要件を書きます。失敗するのは該当のシナリオだけで、残りは BE-0082 と同じく実行されます。検査はデバイスにも時計にも触れない純粋関数なので、決定的な経路に留まり（プライムディレクティブ 1）、テストに Simulator を必要としません。

検査は 2 か所に置きます。どちらも、ファイルを持つのが worker だけなので、worker の側です。

- **`bajutsu run`**：規則 1 と 2 は、`select_actuator` を呼ぶ前に、`_select_actuator_or_exit`（[`cli/_shared.py`](../../bajutsu/cli/_shared.py)）の中で実行します。この関数は、要求されたアクチュエーターのうち、ホストが動かせないドライバーと、worker の `drivers` が省いたドライバーを候補から外し、外したものを除いたバックエンドの一覧を返します。したがって `runner/pool.py` のシナリオごとの選択が、それを再び選ぶことはありません。候補が残らないときにだけ、名指しした理由で終了コード 2 になります。したがって `[ios, web]` のようなフォールバックの一覧は、Linux でも `playwright` に解決されます。`record`、`crawl`、`audit`、`repl` も同じ関数を共有するので、同じメッセージを得ます。この位置に置かないと、Linux ホストは、ランナーの preflight が始まる前に、汎用の `no available actuator` で終了コード 2 のまま終わります。`run` は、`_resolve_target_effs` より前に、読み込んだシナリオに対して `worker_capability_unsupported()` を評価し、違反したシナリオを接頭辞付きの理由で報告して、以降に渡す一覧から外します。したがって、違反したシナリオだけが宣言する target にはデバイスを取得せず、`_pool_demand` もそれを数えません。この検査は、各シナリオが宣言する target を、デバイスを取得せずに設定から読んで判定します。そのため、推論した target の一括実行で、最初のファイルと target が重ならない後続のシナリオも、そのシナリオ自身の target で判定されます。`_select_actuator_or_exit` が終了コード 2 になるのは、実行できるシナリオが 1 つも残らないときだけです。`_acquire_targets` を分け、宣言されたすべての target のアクチュエーターを、最初の `acquire_device` の呼び出しより前に選んで検査します。
- **lease したジョブ、または `bajutsu worker --once`**：クラウドへの依頼を持つジョブ（1 ジョブに 1 シナリオ）では、worker は何かを投入する前に同じ関数を実行し、既存の結果の経路で、名指しした理由を付けてジョブを失敗させます。ローカルのジョブでは、検査は上の `run` の経路の中でシナリオごとに実行されるので、失敗するのは該当のシナリオだけです。worker は、起動する内部の run の入口に、`--worker-config` のパスを絶対パスにして渡します。したがって、その子プロセスの検査も同じファイルを読みます。

server は、ルーティングにとどまります。単一のホスト OS だけで動くドライバーは、対応する `host:<os>` トークンをジョブの必須集合に加えます。ルーティングの判定は全要素を要求する部分集合の検査（[`serve/capabilities.py`](../../bajutsu/serve/capabilities.py)）なので、複数のホストで動くドライバーは `host:` 要件を加えません。`environment:*` トークンを広告する worker は、それを要求するジョブだけを担当します。この規則は Device Farm の投入の項目が `can_serve` に加え、クラウドのジョブの必須集合もそちらが作ります。クラウドへの依頼（`Job.batch`）を持つジョブは例外で、`environment:<name>` だけを要求し、`platform:*` も `host:*` も要求しません。端末とそのホストは、プロバイダーのものだからです。クラウドへの依頼を持たないジョブは、`cloudBatch` を設定した target のものでも、ローカルの要求のままです。接続中のどの worker にも合致しないジョブは、BE-0166 と同じく待機します。worker の構成は時間とともに変わるため、worker がまだ揃っていない起動直後に現在の構成で拒否すると、有効なジョブまで拒否してしまうからです。

### target の設定を再構成する項目との関係

提案中の、target の設定を再構成する項目があります（slug `target-config-restructure`）。この項目は、どこで実行するかの選択を target から外し、その受け皿を本項目に求め、本項目が入ってから着手します。本項目の次の6点に影響します。

- **`environment` が `appium` を受け付けます。** 再構成の項目は、target の `deviceProvider` を `worker.yaml` へ移します。移った先は、グリッドの `endpoint` を持つ `appium` という environment です。これで `environment` の語彙は、batch provider の種類の外へ広がります。ローダーは `endpoint` を必須として `appium` を受け付け、ほかの environment では `endpoint` を拒否します。`appium` の environment は、グリッドが受け持つプラットフォーム（現在は `ios`）を書きます。そのプラットフォームの target はグリッドへ行き、ほかの target はローカルに残ります。この environment は device cloud のものと同じく振る舞い、`maxJobConcurrency` は1を超えてもよく、`drivers` にはローカルの target が使うドライバとグリッドのドライバを並べ、ホストの規則はローカルの target にだけ当てはめます。端末を動かすコマンド（`run`、`record`、`crawl`、`repl`、`audit`、`doctor`）と MCP のサーバーはどれも、`--worker-config` でこの environment を受け付け、今の URL の udid を置き換えます。本項目がこのフラグを与えるのは `worker` と `run` だけなので、再構成の項目がそれを広げ、これらのコマンドでほかのローカル以外の environment を拒否します。`environment:appium` だけを広告し、それを求めるジョブは、Device Farm のジョブと同じく `platform:*` や `host:*` のトークンを持ちません。
- **ランタイムと端末の種類の出どころが変わります。** 本項目は、後続の項目が target の `device` から iOS のランタイムと端末の種類を導くまで、`requires` を残します。再構成の項目は、このフィールドを `runsOn.model` と `runsOn.os` に置き換え、シナリオがどちらも上書きできるようにします。後続の導出が読むのは、各シナリオの有効な `runsOn`、つまり target とシナリオの値を合わせたものです。
- **`requires` が早く消えます。** 本項目は通知つきで `requires` を非推奨にしますが、再構成の項目はその期間を途中で打ち切り、新しい target のスキーマとともに `requires` を取り除きます。導出が入るまで、hosted のジョブは iOS のランタイムや端末の種類を要件にできません。再構成の項目はこの空白を受け入れます。本項目は、導出まで `requires` を残すことで、この空白を避けています。
- **「対象外」の状態が生まれます。** `>=18` のような片側が開いた条件を満たす端末が手元にないシナリオは、走らせずに「対象外」と記録されます。リストや両側に境界のある範囲が求める回を引き受けられる端末がない場合は、対象外ではなく失敗になります。本項目の「境界」は、設定を誤った worker がすべてを飛ばしても成功に見えることを理由に、スキップの状態を退けています。再構成の項目は、この失敗と、1回も走らなかった invocation を非ゼロで終了させる規則で、この懸念に応えます。worker capability の検査は、worker が実行できないシナリオを引き続き失敗させるので、2つの結果は混ざりません。
- **プラットフォームをまたぐフォールバックのリストがなくなります。** 再構成の項目は `backend` を取り除くので、「検査」で `bajutsu run` について述べている、Linux のホストで `[ios, web]` が `playwright` に解決される挙動はなくなります。target は1つのプラットフォームを名指しし、Web も回したい run は2つ目の target を使います。
- **ホストはマシンの事実のままです。** 再構成の項目は、target 側の `runsOn.host.os` を検討して取り下げました。ホストの制約は `host:<os>` だけのままです。

### 境界

本項目が決めるのは、ステップの capability ではなく実行可否です。したがって既存のトークンによる判定は変えません。skipped という状態も足しません。実行できないシナリオは失敗として扱います。黙ってスキップすると、設定を誤った worker が緑のまま報告されてしまうからです。Device Farm のクォータも調べません。target の上限は運用者が述べる事実であり、クォータは Device Farm の dispatch の項目のジョブ同時実行の予算が引き続き扱います。server は、dispatch のときに、worker の上限に対してジョブを検査しません。worker が lease のときにそのようなジョブを失敗させます。worker が自分の上限を広告して、server がより早く拒否できるようにする作業は、後の項目で行えます。`requires` がなくなったあと、target の `device` から iOS の runtime や機種の要求を導く作業も、後の項目です。server は、単一のホスト OS でしか動かないドライバーのジョブに `host:<os>` を加えるので、server より先に worker を更新します。

### プライムディレクティブへの適合

- **AI による判定の排除。** 検査は、シナリオ、worker のファイル、ホストだけから決まる決定的な関数で、モデル呼び出しを含みません。
- **決定性優先。** ゲートは早く失敗し、満たされない要件を名指しします。遅い失敗や曖昧な失敗を置き換えます。
- **アプリ非依存。** worker の事実は 1 つの YAML ファイルのデータであり、ツール、ランナー、シナリオは target をまたいで変わりません。

### 作業分解（MECE）

1. **worker のファイルとローダー。** 既定の `worker.yaml`、worker capability を返すスキーマとローダー、`worker` と `run` の `--worker-config` を加えます。
2. **ドライバーのホスト要件。** 各ドライバーのホスト要件を `CAPABILITIES` の隣に宣言し、ホストをマシンから読みます。
3. **要求の導出。** シナリオが必要とするドライバーと target の数を計算します。
4. **検査。** `worker_capability_unsupported()` を加え、メッセージの接頭辞を定め、`bajutsu run`（デバイスの lease の前。`_acquire_targets` を分け、すべての target を先に検査する）、ホストやファイルが除くアクチュエーターを候補から外す共有のアクチュエーター選択、worker の lease の経路と `--once` に接続し、内部の run の入口へファイルのパスを渡します。
5. **広告とルーティング。** `host:<os>` を広告し、非推奨のトークンの入力で `environment:` の予約を強制し、`local` の worker の `--platform` の規則を適用し、ローカルのジョブの必須集合を作り、`--capabilities`、`$BAJUTSU_WORKER_CAPABILITIES`、`requires` を非推奨にします。
6. **ドキュメント。** `docs/drivers.md`、`docs/scenarios.md`、`docs/architecture.md`、`docs/configuration.md`、`docs/self-hosting.md`、`docs/cli.md` と、その日本語ミラー、および運用者に `requires` で runtime を固定するよう案内している `deploy/self-host/README.md` を更新します。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| すべてのドライバーとすべての環境の表 | 共有の 1 つのファイルに、各ドライバーのホストと動かせる環境を列挙します。 | worker のホストも環境も 1 つなので、表は、運用者に決して当てはまらない行を読ませます。ホストはドライバーとマシンの事実であり、環境は worker の事実です。 |
| ドライバーごとの target の上限 | 各ドライバーが `drivers` の下に自分の上限を持ちます。 | ジョブの target はドライバーをまたぐので、「Simulator とブラウザの併用」には、どのドライバーの上限が効くかを決める規則が要ります。最上位の 1 つの数なら、直接言い表せます。 |
| `--capabilities` をファイルと併存させる | フラグを上書きとして残します。 | マシンが持たないトークンを運用者が広告でき、worker が実行できないジョブを引き寄せます。トークンは在庫がすでに供給しています。 |
| `requires` を残し、`worker.yaml` でラベルを宣言する | ファイルに自由なラベルを足し、`requires` がそれを名指しします。 | 手書きのトークンに戻ります。在庫から導く設計は、それを避けるために選びました。 |
| 在庫をファイルで宣言する | `iosRuntimes`、`apiLevels`、`browsers` のような型付きフィールドを、ドライバーごとに置きます。 | 宣言は lease の成功を約束できず、worker が調べられる在庫からずれていく 2 つ目の情報源になります。ファイルに置くのは、プローブでは答えられない事実だけです。 |
| 運用者のファイルを既定に統合する | 運用者のファイルで個々のエントリーを上書きし、狭めることだけを許します。 | 統合には、省略と広げる指定の規則が要り、有効な内容を知るために 2 つのファイルを読み合わせる必要があります。ファイル全体を置き換える形なら、読むファイルは 1 つです。 |
| `bajutsu.config.yaml` に置く | プロジェクトの設定に `workerCapabilities` キーを加えます。 | このファイルは target を記述し、アプリのチームが書きます。server の worker はジョブを lease したあとでしか受け取れないので、worker が何を広告するかを、このファイルでは決められません。 |
| ドライバーの capability トークンだけを増やす | `hostOS:darwin` や `singleDevice` のようなトークンを `capabilities()` に足します。 | 同じ `xcuitest` クラスが、Mac と Device Farm で異なる上限のまま動きます。静的なトークン集合では、両方を表せません。 |
| 実行時のプローブだけ | OS と target の数を検出して、実行時に拒否します。 | Device Farm の target 1 個という上限は検出できず、「宣言する」という要望も満たせません。 |
| 失敗ではなくスキップ | 実行できないシナリオを skipped として報告します。 | 設定を誤った worker がすべてをスキップしても CI が通ってしまい、決定性を優先する原則に反します。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] worker のファイルとローダー
- [ ] ドライバーのホスト要件
- [ ] 要求の導出
- [ ] 検査
- [ ] 広告とルーティング
- [ ] ドキュメントと日本語ミラー

## 参考

- [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md)：本項目が拡張する preflight。
- [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities-ja.md)：操作ごとの capability トークン。
- [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md)：worker の capability ルーティング。
- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md)：Device Farm の dispatch とデバイス予算。
- [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md)：複数 target のシナリオとデバイスプールの規則。
- [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)：デバイスクラウドのプロバイダー。
- target の設定を再構成する項目（slug `target-config-restructure`）：`worker.yaml` に `appium` の environment を加えます。また、target の `device` を `runsOn.model` と `runsOn.os` に置き換え、新しい target のスキーマとともに `requires` を取り除きます。
