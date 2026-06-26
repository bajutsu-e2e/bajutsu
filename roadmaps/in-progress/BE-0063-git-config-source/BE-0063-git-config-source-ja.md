[English](BE-0063-git-config-source.md) · **日本語**

# BE-0063 — Git リポジトリ + ref から config（とシナリオ一式）を読み込む

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0063](BE-0063-git-config-source-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トピック | config の取得元 |
<!-- /BE-METADATA -->

## はじめに

現状、各コマンドの `--config <path>` はローカルファイルシステム上のパス（既定は `bajutsu.config.yaml`）で、config 内の `scenarios` / `baselines` / `setup` / `appPath` / `build` は作業ディレクトリ基準の相対パスです。本提案では、その `--config` フラグ（`run` / `record` / `doctor` / `crawl` のいずれでも）と serve の config ピッカーに、代わりに **ある ref を指す Git リポジトリ**を `github:<owner>/<repo>@<ref>:<path>` の形で指定できるようにします。Bajutsu はその ref におけるリポジトリ部分木を実体化し、そこから config を読み込み、config の相対パスを、チェックアウトした木に対して解決します。変わるのは config とシナリオ一式の取得方法だけで、スキーマ、ランナー、ドライバ、決定的ゲートはそのままです。

関連項目は、ホスティングの対をなす [BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)（`ScenarioStore` seam）と [BE-0016](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)（セルフホスト）、および [BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)（serve の堅牢化）です。

## 動機

チームの config とシナリオは、すでに Git リポジトリに置かれています。[DESIGN §6.5](../../../DESIGN.md) はこれを意図的に定めています（「シナリオはリポジトリ内のただのファイルであり、履歴は git が持ち、Bajutsu は独自のストアを持たない」）。ところが、それらを実行するには、まずそのリポジトリをローカルにチェックアウトし、その中から実行しなければなりません。継続的インテグレーション（CI）にとっても、ホストされた、あるいはセルフホストの `serve` にとっても、このローカルチェックアウトは手間になるか、そもそも不可能です。

- **セルフホストの serve**：Web UI は薄いランチャです。現在、セルフホストの serve（[BE-0016](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) の Tier A）の運用者は、チームの config とシナリオを Mac に手で配置し、手作業で同期し続けなければなりません。serve を `github:acme/mobile-tests@main` に向ければ、UI がチームのテストリポジトリを直接取得し、ブランチの切り替えは再デプロイではなく UI 上のフィールドになります。
- **ホストされたコントロールプレーン**：マルチテナントのサービス（[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）では、Git を供給元とする実装が、`ScenarioStore` seam の問い（「プロジェクトの config とシナリオはどこから来るのか」）に対する、いま動く答えになります。オブジェクトストア経由の経路と併存します。
- **チェックアウト不要の CI**：`bajutsu run --config github:acme/mobile-tests@<sha>:e2e/bajutsu.config.yaml --app sample` とすれば、汎用のランナーが、別のテストリポジトリの作業コピーを持たずに、そのスイートを実行できます。

設計全体を左右するのは、**config だけでは足りない**という事実です。[`demos/features/demo.config.yaml`](../../../demos/features/demo.config.yaml) は `scenarios: demos/features/app/scenarios`、`appPath:`、`build: make -C demos/features sample-build` を設定しており、いずれも run の作業ディレクトリ基準の相対パスです。YAML だけを取得しても、これらのパスは行き先を失います。したがって「Git から config を読む」は、「config が置かれたリポジトリ部分木を、選んだ ref で実体化し、そこから読み込む」ことを意味せざるをえません。

## 詳細設計

### spec の構文

`--config` はこれまでどおりローカルパスを受け付けます。加えて Git ソースを受け付けます。

GitHub 短縮形（第一級の主たる形）:

```
github:<owner>/<repo>[@<ref>][:<path>]
```

- `<ref>`：branch、tag、commit SHA のいずれか。既定はリポジトリの既定ブランチ。
- `<path>`：リポジトリ内の config ファイルへのパス。既定はリポジトリ直下の `bajutsu.config.yaml`（`DEFAULT_CONFIG` のファイル名に一致）。
- 例:
  - `github:acme/mobile-tests`（既定ブランチ、直下の config）
  - `github:acme/mobile-tests@main:e2e/bajutsu.config.yaml`
  - `github:acme/mobile-tests@v1.4.0:e2e/bajutsu.config.yaml`
  - `github:acme/mobile-tests@9f3c1ab:e2e/bajutsu.config.yaml`（固定、再現可能）

汎用の Git URL は、将来の GitHub Enterprise、GitLab、セルフホストのホストに道を残します（本項目が実装するホストは GitHub だけです）:

```
git+https://<host>/<owner>/<repo>.git@<ref>#<path>
```

認識できるスキームを持たない値は、これまでどおりローカルパスとして扱います。したがって既存の呼び出しはすべて従来どおり動作します。

### 解決と「git ソース」seam

新しい resolver を、既存の `_load_effective`（`bajutsu/cli/_shared.py`）の背後に置きます。こうすることで、各コマンドが個別に Git spec を解釈するのではなく、すべてのコマンドと serve が一か所からこの能力を得ます。`_load_effective(config, app)` は次のようになります。

1. **`config` 文字列を解析する。** ローカルパスなら現状の挙動を変えず、Git spec なら `(host, owner, repo, ref, path)` を得ます。
2. **ref を不変の commit SHA へ解決する。** GitHub では `GET /repos/{owner}/{repo}/commits/{ref}` が `sha` を返します。branch、tag、SHA のいずれに対しても効く、1 回の軽い要求です。この commit SHA が、以下すべての決定性の拠り所になります。
3. **その SHA における木を実体化する。** commit SHA をキーとするキャッシュディレクトリ `~/.cache/bajutsu/gitsrc/<host>/<owner>/<repo>/<sha>/` へ展開します（内容アドレス方式）。ディレクトリが不変の SHA でキー付けされるため、キャッシュヒットは常に妥当であり、SHA を固定した run は初回取得後は完全にオフラインで動きます。取得には GitHub の tarball エンドポイント（`GET /repos/{owner}/{repo}/tarball/{sha}`）を使います。1 回の要求で済み、`git` バイナリを要さず、任意の ref を受け、キャッシュディレクトリへアトミックに展開します。
4. **config を `<cache>/<path>` から読み込む。** その相対エントリ（`scenarios`、`baselines`、`setup`、`appPath`、`build`、およびシナリオの `mocks`）を、呼び出し側のカレントディレクトリではなく、チェックアウトの根に対して解決します。これがパス処理に対する唯一の横断的変更です。パスの基準が、暗黙のプロセス作業ディレクトリではなく、明示的な値（実体化した根）になります。
5. **来歴を記録する。** 解決された `<host>/<owner>/<repo>@<sha>` を、元の ref とともに run の `manifest.json` に書き、serve で提示します。これにより、branch を指定した run でも、どの commit を実行したのかを正確に述べられます。

この resolver は、小さくテスト可能な seam です。`ConfigSource` Protocol に、`LocalSource`（現状の挙動）と `GitHubSource` の実装、環境変数由来のトークン、遅延 import を持たせます。[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) が serve のために確立したのと同じ seam パターンです。そのテストは、ネットワークも Simulator も使わず、偽のトランスポートを相手に Linux ゲート上で走ります。

### 決定性と、可変な ref の境界

これは [DESIGN §2](../../../DESIGN.md)（決定性優先）に触れるので、はっきり述べておきます。裸の branch ref は**可変**です。`…@main` は、今日解決した結果と 1 週間後に解決した結果とで、別の commit になりえます。Bajutsu はオーサリングの利便のためにこれを許しますが、可変性が隠れないように境界を引きます。

- branch は読み込み時に具体的な commit SHA へ解決し、**その SHA を記録します**。これにより run は事後に再現できます。記録された SHA に対して再実行すれば、同じ木を再生します。
- resolver はモデル呼び出しを一切行わず、SHA が与えられれば完全に決定的です。ここは Tier-2 の判定経路には入りません。合否の判断ではなく config の取得だからです。
- ビット単位の再現性が要るゲートは、tag か commit SHA を固定します（`@v1.4.0`、`@9f3c1ab`）。`run` は、ゲートの文脈で裸の branch を渡されたとき警告できます。オプトインの `--require-pinned-config` のもとでは失敗にできます。

こうして設計は「固定 sleep を持たない、推測せず失敗する」精神を保ちます。Bajutsu が素性の知れないリビジョンを黙って実行することはなく、何を実行したかは常に manifest から復元できます。

### 認証（プライベートリポジトリ）

公開リポジトリには資格情報が要りません。プライベートなものには `GITHUB_TOKEN` か `GH_TOKEN` のトークンを使い、`gh` CLI がログイン済みなら `gh auth token` にフォールバックして、API と tarball の要求に `Authorization: Bearer` ヘッダとして付します。トークンはログに出さず、証跡へ漏れないように redaction の既定へ加えます。合う箇所では、既存の `bajutsu/github.py` のヘルパを複製せず再利用します。

### キャッシュ、オフライン利用、鮮度

- キャッシュは commit SHA をキーとするため不変であり、無効化を要しません。並行する run も安全に共有します（一時ディレクトリへ展開してから、所定の場所へ rename します）。
- branch や tag の ref は、読み込みのたびに SHA を再解決します（前述の軽い commits API 呼び出し 1 回）。`--config …@<sha>` はそれすら省き、キャッシュヒット時はオフラインで動きます。`--config-offline` スイッチ（キャッシュを使い、ネットワークに触れない）は、隔離環境での再実行を支えます。
- キャッシュのガベージコレクションは、キャッシュディレクトリに対する LRU（least-recently-used、最近最も使われていないものから捨てる方式）か TTL（time-to-live、保持期限）による刈り込みです。具体的な方針は実装に委ねます。

### 変わらないもの（アプリ非依存、スキーマ不変）

config スキーマ（`bajutsu/config.py`）、`resolve()`、ランナー、ドライバ、アサーション評価器、決定的ゲートは手を入れません。Git ソースは、同じ config と木を取得する新しい手段にすぎません。これはまさに [BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) の「動かすのは呼び出しと配管だけ」であり、[DESIGN §6.5](../../../DESIGN.md) の「履歴は git が持つ」です。アプリ固有の差分は config に留まり、ツールがリポジトリごとに分岐することはありません。

### CLI からの利用（run / doctor / record / crawl）

resolver は `_load_effective` の背後にあるため、`--config` を取るすべてのコマンドが、コマンドごとの変更なしに Git ソースを受け付けます。これは CI やスクリプトからの経路であり、後述の serve GUI とは別の面です。コマンドは Git ソースの扱いで 2 種類に分かれます。

- **読み取り専用（`run` と `doctor`）**：Git ソースの本来の利用者であり、CI 用途の主眼です。実体化したチェックアウトから config を（`run` ではシナリオも）読み、実行または採点するだけで、書き戻しはしません。
  - `bajutsu run --config github:acme/mobile-tests@v1.4.0:e2e/bajutsu.config.yaml --app checkout`
  - `bajutsu doctor --config github:acme/mobile-tests@main:e2e/bajutsu.config.yaml --app checkout`
  - `--app <name>` は、ローカル config のときと同様に、取得した config の `apps:` からエントリを選びます。`--backend` / `--udid` / `--workers` / `--scenario` は不変です。
- **オーサリング（`record` と `crawl`）**：これらは新しいファイル（シナリオ、`screenmap.json`）を生成しますが、SHA をキーとする読み取り専用のキャッシュはそれを受け取れません。したがって Git ソースは**読み取り専用の入力**です。アプリの config と既存シナリオをそこから解決してよい（エージェントの文脈になる）のですが、生成物は**ローカルのパス**（`--out`、既定はカレントディレクトリの下）へ書き、キャッシュへは書きません。著者はそのファイルをレビューし、通常の git でリポジトリへコミットします。これはまさに [DESIGN §6.5](../../../DESIGN.md) の「AI の出力は人間がコミットするレビュー可能な差分」であり、変わりません。ローカルで完結するオーサリングループには、これまでどおり `--config` をローカルのチェックアウトに向けます。

すべてのコマンドに付随するフラグが 2 つあります。`--config-offline`（キャッシュを使い、ネットワークに触れない）と、`--require-pinned-config`（ゲート向けに、裸の branch を警告ではなく失敗にする。前述の「決定性」を参照）です。

### serve からの利用（GUI）

serve の config ピッカーは、現在は `--root` に限定したローカルのファイルブラウザですが、ここに「Git から」モードを加えます。リポジトリ、ref、パスのフィールド、あるいは単一の `github:…` 文字列を受けます。開くと、serve は上記のとおり自分のキャッシュへ解決して実体化し、そのチェックアウトに対して既存の run と record の経路を駆動します。`serve --config github:…` は、`--config <path>` がローカルソースを束ねるのと同じ要領で、起動時に Git ソースを束ねます。これが [BE-0016](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) の Tier A の利得です。運用者は Mac の serve をチームのリポジトリに向けるだけでよく、ファイルを手で同期しなくて済みます。[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) のパス限定の堅牢化は、現在 `--root` に効いているのと同じく、チェックアウトの根に効きます。

### 実装状況

**CLI の取得コア**を出荷しました（`bajutsu/config_source.py`）。`parse_config_spec`（`github:` ショートハンドと `git+https://…` 形式。spec でない値はローカルパスのまま）と、`materialize`（ref を SHA に解決し、GitHub の tarball を取得し、ラッパーディレクトリを剥がしつつ tar のパストラバーサルを拒否して content-addressed なキャッシュへ展開、temp+rename で原子的に、SHA キーのキャッシュヒットで再ダウンロードを省略）です。`_load_effective` の背後に配線したので、`run` / `doctor` は Git の `--config` を受け付け、config の `scenarios` / `baselines` / `schemas` / `appPath` はチェックアウトのルートを基準に再解決されます。GitHub トランスポートは注入可能なシームで、fake を相手にオフラインでテストします。トークンは `GITHUB_TOKEN` / `GH_TOKEN` / `gh auth token` から取ります。

Git ソースからの `run` は、解決したコミットも **run の来歴**として記録します。`manifest.json` の `provenance.configSource` が `{ host, owner, repo, ref, sha }` を持つので、ブランチ指定の run でも実際に実行した正確なコミットが分かります（BE-0049 の provenance ブロックの拡張。純粋なメタデータで、判定には入りません）。

ゲート向けスイッチ **`--config-offline` と `--require-pinned-config`** を `bajutsu run` に出荷しました。`--config-offline` はネットワークに触れずキャッシュから実体化します（オフラインでは解決できないので固定 `@<sha>` が必要）。`--require-pinned-config` は Git config がフル commit SHA を固定していなければ失敗します。提案の「タグか SHA」より厳格にしたのは、タグは force-move されうるので、不変でオフラインに検証できる pin は SHA だけだからです。

残り: Git ソースでの `build` の作業ディレクトリ、serve の「Git から」ピッカー、Git ソースを `record` / `crawl` の **読み取り専用入力**として扱うこと（生成物は SHA キーのキャッシュではなくローカルの `--out` に書く）、そして config のパス項目をチェックアウトのルートに**閉じ込める**こと（ルートを抜ける絶対パスや `../` を拒否。[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) に倣う）です。

## 検討した代替案

- **config の YAML だけを取得し、木は取得しない。** 主たる設計としては不採用とします。config の `scenarios` / `appPath` / `build` / `baselines` は相対パスなので、木を伴わない config では `run` できません。部分木ごと実体化することが、この機能を有用にします。
- **tarball エンドポイントではなく `git clone`。** `git clone --depth 1 --branch <ref>`（あるいは `--filter=blob:none` の部分クローン）は素直な手段ですが、ホストに `git` バイナリを要し、`--branch` は任意の commit SHA を受け付けません（SHA の浅い取得には、サーバ側の `uploadpack.allowReachableSHA1InWant` が要りますが、これは普遍的に有効化されてはいません）。tarball エンドポイントは HTTP 要求 1 回で済み、`git` を要さず、branch、tag、SHA を一様に受けます。浅いクローンは、GitHub 以外の Git ホスト向けのフォールバックとして残します。
- **Contents API でファイルごとに取得する。** 不採用とします。シナリオの**ディレクトリ**を 1 ファイルずつ列挙して取得するのは通信が多く、競合しやすいです。一方、ある SHA の tarball 1 つはアトミックで完結しています。
- **branch のみ（常に最新）、あるいは固定 ref のみ（branch を拒否）。** どちらの極も不採用とし、「任意の ref を受け、解決した SHA を記録する」を採ります。branch 追従はオーサリングに便利で、固定は再現可能なゲートに要ります。SHA の記録が、この両者を両立させます。
- **Bajutsu 独自の config ストア（config をサービスへアップロードする）。** 不採用とします。[DESIGN §6.5](../../../DESIGN.md)（独自ストアを持たない）に反します。Git がすでに、バージョン管理された真実の供給元です。
- **Git ソースでも、相対パスを呼び出し側のカレントディレクトリに対して解決する。** 不採用とします。呼び出し側のディレクトリは取得した木とは無関係なので、パスは実体化したチェックアウトの根に対して解決しなければなりません。だからこそ、パスの基準を明示にします。
- **`record` / `crawl` に Git ソースへ書き戻させる。** 不採用とします。キャッシュは不変の commit SHA で内容アドレス付けされているため、そこへ書くと不変条件が壊れ、変更も一過性になります（後の刈り込みで失われうる）。オーサリングは代わりに、人間が git でコミットするローカルのパスへ出力します。これはこれまでと同じ「レビューしてからコミット」の流れであり、Git ソースを読み取り専用の入力として扱う方針と一貫します。

## 参考

- [DESIGN §6.5](../../../DESIGN.md)（シナリオは git 管理下のファイル、独自ストアを持たない）、[DESIGN §8](../../../DESIGN.md)（CLI とアプリ別 config）。
- `bajutsu/cli/_shared.py`（`_load_effective`）、`bajutsu/config.py`（相対パスのフィールド）、`bajutsu/cli/commands/serve.py`（config ピッカー）、`bajutsu/github.py`。
- [`demos/features/demo.config.yaml`](../../../demos/features/demo.config.yaml)：`scenarios` / `appPath` / `build` が作業ディレクトリ基準の相対パスである config（要点）。
- [BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)（Git ソースが実装する `ScenarioStore` seam）、[BE-0016](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)（セルフホスト。リポジトリから serve するのが Tier A の利得）、[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)（serve の堅牢化。Git ソースが守る、トークン認証とパス限定）。
- [docs/ja/configuration.md](../../../docs/ja/configuration.md)。
