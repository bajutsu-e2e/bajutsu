[English](BE-XXXX-worker-command-execution-policy.md) · **日本語**

# BE-XXXX — 設定が宣言したコマンドを実行するかを worker が判断する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-worker-command-execution-policy-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | セキュリティ強化 |
| 関連 | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)、[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)、[BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)、[BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md)、[BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs-ja.md)、[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本提案では、設定が宣言したコマンドを実行するかどうかの判断を、サーバーから worker に移します。
設定は、ランを実行するマシンの上で Bajutsu に走らせるコマンドを宣言できます。現在は
`services.server.cmd` と `subject.build` がそれにあたります。今は `serve` の `--upload-exec`
オプションが、アップロードされたバンドルに限って、そうしたコマンドを実行するかを決めています。
本提案は、このオプションを worker の capability ファイル `worker.yaml` の `commands` ブロックで
置き換えます。このブロックは、設定の出どころ（アップロードされたバンドル、Git リポジトリ、
ローカルのファイルシステム上の設定）ごとに1つずつモードを決めます。

worker は受け付ける出どころを申告し、`serve` はコマンドを宣言したジョブを、そのジョブの出どころを
受け付ける worker に振り分けます。ランは、実行したすべてのコマンドを、実行を許した判断とともに
記録します。運用者は、実行されるべきでなかったコマンドを後から見つけられます。

## 動機

[BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)
は、`serve` がまだ自分でジョブを実行していた時期に、コマンドの方針を `serve` に置きました。
[BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs-ja.md) 以降、`serve` は
すべてのジョブをキューに積み、`bajutsu worker` が実行します。方針とリスクが、別々のマシンに
分かれています。

- **リスクを負うのは worker です。** 設定のコマンドは worker のホストの上で動き、worker の
  ファイル、ネットワーク、資格情報に触れられます。署名鍵を持つ worker には、使い捨ての Linux
  コンテナより厳しい方針が要ります。サーバー全体で1つの値では、この違いを表せません。
- **サーバーからは worker のホストが見えません。** `sandbox` モードには、コンテナを動かすマシンに
  Docker が必要です。`serve` は、ある worker に Docker があるかを知らないままモードを決めています。
- **worker の運用者は断れません。** worker は実行できるジョブを取り、そのコマンドをサーバーの方針の
  下で実行します。マシンを運用する人には、断るための設定がありません。

方針の外に残っている出どころもあります。BE-0090 は、アップロード以外から来た設定をすべて信頼済みと
して扱います。Git リポジトリから読み込んだ設定
（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）も、この扱いに入ります。
そのため、多くのリポジトリのジョブを実行する worker は、どの設定が宣言したコマンドも実行します。

さらに、ランがどのコマンドを実行したかは、どこにも記録されません。リポジトリ内のスクリプトが
書き換えられた場合のような意図しないコマンドは、ランの証跡に痕跡を残しません。

本提案が入った後は、次の2つの違いを観察して確かめられます。`worker.yaml` で `uploaded: deny` を
指定した worker は、アップロードされた設定がコマンドを宣言しているジョブを取りません。各ランの
`manifest.json` には、ランが実行または拒否したコマンドが、出どころ、モード、実行した内容の
ハッシュとともに並びます。

## 詳細設計

### `worker.yaml` の `commands` ブロック

[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md) の worker の capability
ファイルに、省略できるブロックを1つ加えます。

```yaml
version: 1
environment: local
drivers: [playwright]
commands:
  uploaded: sandbox
  git: allow
  local: allow
```

各キーは設定の出どころを、各値はモードを表します。

| モード | その出どころのコマンドの扱い |
|---|---|
| `allow` | worker のホスト上で実行します。 |
| `sandbox` | worker のホスト上の使い捨ての Docker コンテナで実行します。現在の BE-0090 の `sandbox` モードと同じです。 |
| `deny` | その出どころのコマンドは実行しません。worker は、その出どころの設定がコマンドを宣言しているジョブを取りません。 |

ブロックやキーを省略すると、現在と同じ扱いになります。`uploaded: sandbox`、`git: allow`、
`local: allow` です。どれかのキーを `sandbox` にした worker は、起動時に Docker の有無を確かめます。
Docker が無ければ、そのキーを示すメッセージを出して終了します。自分のファイルを守れない worker は
起動しないので、後で失敗するジョブを取ることもありません。

### 統制の対象になるコマンド

このブロックは、設定が Bajutsu に実行させるために宣言するコマンドを、すべて統制します。

| コマンド | `allow` | `sandbox` | `deny` |
|---|---|---|---|
| `services.server.cmd` | ホスト上で実行します | 現在と同じくコンテナで実行します | 実行しません |
| `subject.build` | バイナリが無いとき、ホスト上で実行します | 実行しません | 実行しません |

`sandbox` で `subject.build` を実行しないのは、iOS のビルドに macOS のツールチェーンが必要で、
Linux のコンテナには無いからです。この扱いは、
[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) が
アップロードに適用している規則とも一致します。アップロードされたバンドルは、ビルド済みのバイナリを
同梱しているからです。バイナリが無いランは失敗し、ビルドを止めたモードを示します。

姉妹提案「ターゲット設定から、ランと各シナリオの前後に外部コマンドを実行する」は、
`targets.<name>.hooks` に4つのフックのリストを加え、`services.server` を削除します。姉妹提案が
入った後は、上の表の `services.server.cmd` の位置に、フックのエントリが入ります。

### 設定の出どころ

`serve` は、各ジョブに、そのジョブが実行する設定の出どころを示すラベルを付けます。

| 出どころ | 設定の出元 |
|---|---|
| `uploaded` | ブラウザの利用者がアップロードしたバンドル（BE-0073） |
| `git` | Git リポジトリと ref（BE-0063） |
| `local` | ランを始めたマシンのファイルシステム上のファイル |

ラベルは、現在の `upload_exec` フィールドに代わってジョブの intent に載ります。設定の側から自分の
ラベルを指定する手段はありません。端末や CI から始めた `bajutsu run` は、Git の出どころから
読み込んだ設定には `git` を、それ以外には `local` を付けます。

### 振り分け

worker は、受け付ける出どころごとに capability のトークンを1つ申告します。モードが `deny` でない
出どころについて、`commands:uploaded`、`commands:git`、`commands:local` を申告します。`serve` は、
統制の対象になるコマンドを1つ以上宣言した設定のジョブについて、必要なトークンの集合に
`commands:<source>` を加えます。
[BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md) の振り分けは、
必要なトークンがすべてそろっているかを確かめる部分集合の判定です。そのため、コマンドを宣言した
ジョブは、その出どころを受け付ける worker にだけ届きます。接続中のどの worker とも合わないジョブは
待ち続け、利用できる worker がないという既存の通知が付きます。

`subject.build` は、必要なトークンを加えません。worker がビルド済みのバイナリをすでに持っているかを、
`serve` は知り得ないからです。ビルドするかどうかは、上の表に従って worker がランの中で決めます。

### ランの中での確認

`bajutsu run` は、デバイスの作業より前に、読み込んだ `worker.yaml` に照らして方針をもう一度
確かめます。出どころが `deny` に当たる統制対象のコマンドがあれば、ランは終了コード2で失敗します。
メッセージは、コマンド、出どころ、モードを示します。この2度目の確認は、`serve` を通さずに始めた
ランと、申告がファイルとずれた worker の両方を受け止めます。

### コマンドの記録

各ランは、`manifest.json` に `commands` のリストを加えます。各エントリは、ランが出会った統制対象の
コマンドを1つずつ記録します。

| フィールド | 意味 |
|---|---|
| `field` | 設定がコマンドを宣言した場所（`services.server.cmd` など） |
| `cmd` | 宣言どおりのコマンドライン。`${secrets.*}` の値はマスクします。 |
| `source`、`mode` | 設定の出どころと、適用したモード |
| `decision` | `ran`、`sandboxed`、`refused` のいずれか |
| `exitCode` | 実行した場合の終了コード |
| `sha256` | コマンドラインのハッシュ。実行ファイルが設定のディレクトリ内にあれば、そのファイルのハッシュも記録します。 |

レポートは、このリストをランのほかの来歴と並べて示します。コマンドラインが同じでも、2つのランの
あいだでスクリプトが書き換えられていれば、ハッシュの違いとして表れます。

### `--upload-exec` の削除

`serve --upload-exec` と `$BAJUTSU_UPLOAD_EXEC` は削除します。どちらかを受け取った `serve` は
起動を拒み、メッセージは `worker.yaml` の `commands` ブロックを示します。
`serve --emit-launchagent` が出力する launchd の plist も、このオプションを渡さなくなります。
`serve` がランに渡していた `run`、`record`、`crawl` の `--upload-exec` オプションも、あわせて
削除します。

`reuse` モードは引き継ぎません。`reuse` の下では、アップロードされた `services.server.cmd` は
実行されず、ランは準備完了の URL にすでに応答しているサーバーを使っていました。姉妹提案のフックは
その URL を廃止するので、URL を前提にしたモードには確かめるものが残りません。`reuse` を使っていた
運用者は、`sandbox` か `deny` を選びます。

### 移行

- `bajutsu/common/capability/` は、`commands` ブロックを読み、検証し、申告します。
- `bajutsu/serve/server/worker_job.py` と `bajutsu/serve/commands.py` は、`upload_exec` に代えて
  出どころのラベルを運びます。
- `bajutsu/common/runner/launch_server.py` と `sandbox.py` は、`--upload-exec` オプションではなく、
  ランの方針の確認からモードを受け取ります。
- `docs/self-hosting.md`、`docs/cli.md`、`docs/drivers.md`、`DESIGN.md`、`docs/architecture.md` を、
  振る舞いの変更にあわせて日本語版とともに更新します。

### プライムディレクティブとの関係

- **AI は判定しません。** 方針は worker 上の表の参照であり、振り分け、ラン、ゲートのどれにも
  大規模言語モデル（LLM）の呼び出しは入りません。
- **決定性を優先します。** ファイル、出どころ、コマンドが同じなら、判断も常に同じです。
- **アプリに依存しません。** 方針はマシンの運用者が持つ `worker.yaml` に置き、アプリの設定は
  変わりません。

## 検討した代替案

- **方針を `serve` に残す**：採りません。`serve` はジョブを実行せず、worker のホストも見えず、
  worker の運用者に断る手段を与えません。
- **両方に置き、厳しいほうを採る**：採りません。サーバー全体の上限があると方針の置き場所が2つになり、
  片方だけを読んだ運用者には、実際に効くモードが分かりません。
- **コマンドのパスの許可リスト**：採りません。パスはファイルの中身を保証しません。バンドルは、
  許可された名前で有害なスクリプトを同梱できます。
- **承認済みハッシュの許可リスト**：見送ります。書き換えられたスクリプトの実行は止められますが、
  新しいハッシュを誰かが確認して承認する流れが必要です。上のコマンドの記録が、その流れの出発点となる
  ハッシュを用意します。
- **`reuse` モードを残す**：採りません。`reuse` が前提とする準備完了の URL は、姉妹提案のフックで
  Bajutsu から無くなります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `worker.yaml`：`commands` ブロック、デフォルト値、検証、起動時の Docker の確認。
- [ ] 出どころのラベル：`upload_exec` に代えて `uploaded` / `git` / `local` をジョブの intent で運び、CLI のランにもラベルを付ける。
- [ ] 振り分け：`commands:<source>` トークンを申告し、コマンドを宣言したジョブに必要なトークンを加える。
- [ ] ランの中での確認：出どころが `deny` に当たる統制対象のコマンドを、デバイスの作業より前に拒否する。
- [ ] コマンドの記録：`manifest.json` の `commands` のリストと、レポートでの表示。
- [ ] `serve --upload-exec`、`$BAJUTSU_UPLOAD_EXEC`、`run`・`record`・`crawl` の `--upload-exec` オプションを削除する。
- [ ] ドキュメント、`DESIGN.md`、`docs/architecture.md` を両言語で更新する。

## 参考

- [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)：Git リポジトリからの設定の
  読み込み。
- [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)：アップロード
  されたバンドル。その `build` は実行しません。
- [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)：
  本提案が置き換える `serve --upload-exec` の方針。
- [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md)：capability の
  トークンによるジョブの振り分け。
- [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs-ja.md)：`serve` はすべての
  ジョブをキューに積み、worker が実行します。
- [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)：worker の capability
  ファイル。
- [`bajutsu/common/runner/launch_server.py`](../../bajutsu/common/runner/launch_server.py) と
  [`sandbox.py`](../../bajutsu/common/runner/sandbox.py)：現在の統制されたサーバーの起動。
- [`bajutsu/serve/server/worker_job.py`](../../bajutsu/serve/server/worker_job.py)：worker がジョブの
  intent からランのコマンドを組み立てる箇所。
