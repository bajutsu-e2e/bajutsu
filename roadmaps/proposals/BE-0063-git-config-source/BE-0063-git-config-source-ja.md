[English](BE-0063-git-config-source.md) · **日本語**

# BE-0063 — Git リポジトリ + ref から config（とシナリオ一式）を読み込む

* 提案: [BE-0063](BE-0063-git-config-source-ja.md)
* Author: [@0x0c](https://github.com/0x0c)
* 状態: **提案**
* トラック: [提案](../../README-ja.md#提案)
* トピック: config の取得元

## はじめに

現状、各コマンドの `--config <path>` はローカルファイルシステム上のパス（既定は `bajutsu.config.yaml`）で、config 内の `scenarios` / `baselines` / `setup` / `appPath` / `build` は作業ディレクトリ基準の相対パスである。本提案では、その `--config` フラグ（`run` / `record` / `doctor` / `crawl` のいずれでも）と serve の config ピッカーに、代わりに **ある ref を指す Git リポジトリ**を `github:<owner>/<repo>@<ref>:<path>` の形で指定できるようにする。Bajutsu はその ref におけるリポジトリ部分木を実体化し、そこから config を読み込み、config の相対パスを、チェックアウトした木に対して解決する。変わるのは config とシナリオ一式の取得方法だけで、スキーマ、ランナー、ドライバ、決定的ゲートはそのままである。

関連項目は、ホスティングの対をなす [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)（`ScenarioStore` seam）と [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)（セルフホスト）、および [BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)（serve の堅牢化）である。

## 動機

チームの config とシナリオは、すでに Git リポジトリに置かれている。[DESIGN §6.5](../../../DESIGN.md) はこれを意図的に定めている（「シナリオはリポジトリ内のただのファイルであり、履歴は git が持ち、Bajutsu は独自のストアを持たない」）。ところが、それらを実行するには、まずそのリポジトリをローカルにチェックアウトし、その中から実行しなければならない。継続的インテグレーション（CI）にとっても、ホストされた、あるいはセルフホストの `serve` にとっても、このローカルチェックアウトは手間になるか、そもそも不可能である。

- **セルフホストの serve**：Web UI は薄いランチャである。現在、セルフホストの serve（[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) の Tier A）の運用者は、チームの config とシナリオを Mac に手で配置し、手作業で同期し続けなければならない。serve を `github:acme/mobile-tests@main` に向ければ、UI がチームのテストリポジトリを直接取得し、ブランチの切り替えは再デプロイではなく UI 上のフィールドになる。
- **ホストされたコントロールプレーン**：マルチテナントのサービス（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）では、Git を供給元とする実装が、`ScenarioStore` seam の問い（「プロジェクトの config とシナリオはどこから来るのか」）に対する、いま動く答えになる。オブジェクトストア経由の経路と併存する。
- **チェックアウト不要の CI**：`bajutsu run --config github:acme/mobile-tests@<sha>:e2e/bajutsu.config.yaml --app sample` とすれば、汎用のランナーが、別のテストリポジトリの作業コピーを持たずに、そのスイートを実行できる。

設計全体を左右するのは、**config だけでは足りない**という事実だ。[`demos/features/demo.config.yaml`](../../../demos/features/demo.config.yaml) は `scenarios: demos/features/app/scenarios`、`appPath:`、`build: make -C demos/features sample-build` を設定しており、いずれも run の作業ディレクトリ基準の相対パスである。YAML だけを取得しても、これらのパスは行き先を失う。したがって「Git から config を読む」は、「config が置かれたリポジトリ部分木を、選んだ ref で実体化し、そこから読み込む」ことを意味せざるをえない。

## 詳細設計

### spec の構文

`--config` はこれまでどおりローカルパスを受け付ける。加えて Git ソースを受け付ける。

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

汎用の Git URL は、将来の GitHub Enterprise、GitLab、セルフホストのホストに道を残す（本項目が実装するホストは GitHub だけである）:

```
git+https://<host>/<owner>/<repo>.git@<ref>#<path>
```

認識できるスキームを持たない値は、これまでどおりローカルパスとして扱う。したがって既存の呼び出しはすべて従来どおり動作する。

### 解決と「git ソース」seam

新しい resolver を、既存の `_load_effective`（`bajutsu/cli/_shared.py`）の背後に置く。こうすることで、各コマンドが個別に Git spec を解釈するのではなく、すべてのコマンドと serve が一か所からこの能力を得る。`_load_effective(config, app)` は次のようになる。

1. **`config` 文字列を解析する。** ローカルパスなら現状の挙動を変えず、Git spec なら `(host, owner, repo, ref, path)` を得る。
2. **ref を不変の commit SHA へ解決する。** GitHub では `GET /repos/{owner}/{repo}/commits/{ref}` が `sha` を返す。branch、tag、SHA のいずれに対しても効く、1 回の軽い要求である。この commit SHA が、以下すべての決定性の拠り所になる。
3. **その SHA における木を実体化する。** commit SHA をキーとするキャッシュディレクトリ `~/.cache/bajutsu/gitsrc/<host>/<owner>/<repo>/<sha>/` へ展開する（内容アドレス方式）。ディレクトリが不変の SHA でキー付けされるため、キャッシュヒットは常に妥当であり、SHA を固定した run は初回取得後は完全にオフラインで動く。取得には GitHub の tarball エンドポイント（`GET /repos/{owner}/{repo}/tarball/{sha}`）を使う。1 回の要求で済み、`git` バイナリを要さず、任意の ref を受け、キャッシュディレクトリへアトミックに展開する。
4. **config を `<cache>/<path>` から読み込む。** その相対エントリ（`scenarios`、`baselines`、`setup`、`appPath`、`build`、およびシナリオの `mocks`）を、呼び出し側のカレントディレクトリではなく、チェックアウトの根に対して解決する。これがパス処理に対する唯一の横断的変更である。パスの基準が、暗黙のプロセス作業ディレクトリではなく、明示的な値（実体化した根）になる。
5. **来歴を記録する。** 解決された `<host>/<owner>/<repo>@<sha>` を、元の ref とともに run の `manifest.json` に書き、serve で提示する。これにより、branch を指定した run でも、どの commit を実行したのかを正確に述べられる。

この resolver は、小さくテスト可能な seam である。`ConfigSource` Protocol に、`LocalSource`（現状の挙動）と `GitHubSource` の実装、環境変数由来のトークン、遅延 import を持たせる。[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) が serve のために確立したのと同じ seam パターンである。そのテストは、ネットワークも Simulator も使わず、偽のトランスポートを相手に Linux ゲート上で走る。

### 決定性と、可変な ref の境界

これは [DESIGN §2](../../../DESIGN.md)（決定性優先）に触れるので、はっきり述べておく。裸の branch ref は**可変**である。`…@main` は、今日解決した結果と 1 週間後に解決した結果とで、別の commit になりうる。Bajutsu はオーサリングの利便のためにこれを許すが、可変性が隠れないように境界を引く。

- branch は読み込み時に具体的な commit SHA へ解決し、**その SHA を記録する**。これにより run は事後に再現できる。記録された SHA に対して再実行すれば、同じ木を再生する。
- resolver はモデル呼び出しを一切行わず、SHA が与えられれば完全に決定的である。ここは Tier-2 の判定経路には入らない。合否の判断ではなく config の取得だからである。
- ビット単位の再現性が要るゲートは、tag か commit SHA を固定する（`@v1.4.0`、`@9f3c1ab`）。`run` は、ゲートの文脈で裸の branch を渡されたとき警告できる。オプトインの `--require-pinned-config` のもとでは失敗にできる。

こうして設計は「固定 sleep を持たない、推測せず失敗する」精神を保つ。Bajutsu が素性の知れないリビジョンを黙って実行することはなく、何を実行したかは常に manifest から復元できる。

### 認証（プライベートリポジトリ）

公開リポジトリには資格情報が要らない。プライベートなものには `GITHUB_TOKEN` か `GH_TOKEN` のトークンを使い、`gh` CLI がログイン済みなら `gh auth token` にフォールバックして、API と tarball の要求に `Authorization: Bearer` ヘッダとして付す。トークンはログに出さず、証跡へ漏れないように redaction の既定へ加える。合う箇所では、既存の `bajutsu/github.py` のヘルパを複製せず再利用する。

### キャッシュ、オフライン利用、鮮度

- キャッシュは commit SHA をキーとするため不変であり、無効化を要さない。並行する run も安全に共有する（一時ディレクトリへ展開してから、所定の場所へ rename する）。
- branch や tag の ref は、読み込みのたびに SHA を再解決する（前述の軽い commits API 呼び出し 1 回）。`--config …@<sha>` はそれすら省き、キャッシュヒット時はオフラインで動く。`--config-offline` スイッチ（キャッシュを使い、ネットワークに触れない）は、隔離環境での再実行を支える。
- キャッシュのガベージコレクションは、キャッシュディレクトリに対する LRU（least-recently-used、最近最も使われていないものから捨てる方式）か TTL（time-to-live、保持期限）による刈り込みである。具体的な方針は実装に委ねる。

### 変わらないもの（アプリ非依存、スキーマ不変）

config スキーマ（`bajutsu/config.py`）、`resolve()`、ランナー、ドライバ、アサーション評価器、決定的ゲートは手を入れない。Git ソースは、同じ config と木を取得する新しい手段にすぎない。これはまさに [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) の「動かすのは呼び出しと配管だけ」であり、[DESIGN §6.5](../../../DESIGN.md) の「履歴は git が持つ」である。アプリ固有の差分は config に留まり、ツールがリポジトリごとに分岐することはない。

### CLI からの利用（run / doctor / record / crawl）

resolver は `_load_effective` の背後にあるため、`--config` を取るすべてのコマンドが、コマンドごとの変更なしに Git ソースを受け付ける。これは CI やスクリプトからの経路であり、後述の serve GUI とは別の面である。コマンドは Git ソースの扱いで 2 種類に分かれる。

- **読み取り専用（`run` と `doctor`）**：Git ソースの本来の利用者であり、CI 用途の主眼である。実体化したチェックアウトから config を（`run` ではシナリオも）読み、実行または採点するだけで、書き戻しはしない。
  - `bajutsu run --config github:acme/mobile-tests@v1.4.0:e2e/bajutsu.config.yaml --app checkout`
  - `bajutsu doctor --config github:acme/mobile-tests@main:e2e/bajutsu.config.yaml --app checkout`
  - `--app <name>` は、ローカル config のときと同様に、取得した config の `apps:` からエントリを選ぶ。`--backend` / `--udid` / `--workers` / `--scenario` は不変である。
- **オーサリング（`record` と `crawl`）**：これらは新しいファイル（シナリオ、`screenmap.json`）を生成するが、SHA をキーとする読み取り専用のキャッシュはそれを受け取れない。したがって Git ソースは**読み取り専用の入力**である。アプリの config と既存シナリオをそこから解決してよい（エージェントの文脈になる）が、生成物は**ローカルのパス**（`--out`、既定はカレントディレクトリの下）へ書き、キャッシュへは書かない。著者はそのファイルをレビューし、通常の git でリポジトリへコミットする。これはまさに [DESIGN §6.5](../../../DESIGN.md) の「AI の出力は人間がコミットするレビュー可能な差分」であり、変わらない。ローカルで完結するオーサリングループには、これまでどおり `--config` をローカルのチェックアウトに向ける。

すべてのコマンドに付随するフラグが 2 つある。`--config-offline`（キャッシュを使い、ネットワークに触れない）と、`--require-pinned-config`（ゲート向けに、裸の branch を警告ではなく失敗にする。前述の「決定性」を参照）である。

### serve からの利用（GUI）

serve の config ピッカーは、現在は `--root` に限定したローカルのファイルブラウザだが、ここに「Git から」モードを加える。リポジトリ、ref、パスのフィールド、あるいは単一の `github:…` 文字列を受ける。開くと、serve は上記のとおり自分のキャッシュへ解決して実体化し、そのチェックアウトに対して既存の run と record の経路を駆動する。`serve --config github:…` は、`--config <path>` がローカルソースを束ねるのと同じ要領で、起動時に Git ソースを束ねる。これが [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) の Tier A の利得である。運用者は Mac の serve をチームのリポジトリに向けるだけでよく、ファイルを手で同期しなくて済む。[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) のパス限定の堅牢化は、現在 `--root` に効いているのと同じく、チェックアウトの根に効く。

## 検討した代替案

- **config の YAML だけを取得し、木は取得しない。** 主たる設計としては不採用とする。config の `scenarios` / `appPath` / `build` / `baselines` は相対パスなので、木を伴わない config では `run` できない。部分木ごと実体化することが、この機能を有用にする。
- **tarball エンドポイントではなく `git clone`。** `git clone --depth 1 --branch <ref>`（あるいは `--filter=blob:none` の部分クローン）は素直な手段だが、ホストに `git` バイナリを要し、`--branch` は任意の commit SHA を受け付けない（SHA の浅い取得には、サーバ側の `uploadpack.allowReachableSHA1InWant` が要るが、これは普遍的に有効化されてはいない）。tarball エンドポイントは HTTP 要求 1 回で済み、`git` を要さず、branch、tag、SHA を一様に受ける。浅いクローンは、GitHub 以外の Git ホスト向けのフォールバックとして残す。
- **Contents API でファイルごとに取得する。** 不採用とする。シナリオの**ディレクトリ**を 1 ファイルずつ列挙して取得するのは通信が多く、競合しやすい。一方、ある SHA の tarball 1 つはアトミックで完結している。
- **branch のみ（常に最新）、あるいは固定 ref のみ（branch を拒否）。** どちらの極も不採用とし、「任意の ref を受け、解決した SHA を記録する」を採る。branch 追従はオーサリングに便利で、固定は再現可能なゲートに要る。SHA の記録が、この両者を両立させる。
- **Bajutsu 独自の config ストア（config をサービスへアップロードする）。** 不採用とする。[DESIGN §6.5](../../../DESIGN.md)（独自ストアを持たない）に反する。Git がすでに、バージョン管理された真実の供給元である。
- **Git ソースでも、相対パスを呼び出し側のカレントディレクトリに対して解決する。** 不採用とする。呼び出し側のディレクトリは取得した木とは無関係なので、パスは実体化したチェックアウトの根に対して解決しなければならない。だからこそ、パスの基準を明示にする。
- **`record` / `crawl` に Git ソースへ書き戻させる。** 不採用とする。キャッシュは不変の commit SHA で内容アドレス付けされているため、そこへ書くと不変条件が壊れ、変更も一過性になる（後の刈り込みで失われうる）。オーサリングは代わりに、人間が git でコミットするローカルのパスへ出力する。これはこれまでと同じ「レビューしてからコミット」の流れであり、Git ソースを読み取り専用の入力として扱う方針と一貫する。

## 参考

- [DESIGN §6.5](../../../DESIGN.md)（シナリオは git 管理下のファイル、独自ストアを持たない）、[DESIGN §8](../../../DESIGN.md)（CLI とアプリ別 config）。
- `bajutsu/cli/_shared.py`（`_load_effective`）、`bajutsu/config.py`（相対パスのフィールド）、`bajutsu/cli/commands/serve.py`（config ピッカー）、`bajutsu/github.py`。
- [`demos/features/demo.config.yaml`](../../../demos/features/demo.config.yaml)：`scenarios` / `appPath` / `build` が作業ディレクトリ基準の相対パスである config（要点）。
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)（Git ソースが実装する `ScenarioStore` seam）、[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)（セルフホスト。リポジトリから serve するのが Tier A の利得）、[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)（serve の堅牢化。Git ソースが守る、トークン認証とパス限定）。
- [docs/configuration.md](../../../docs/ja/configuration.md)。
