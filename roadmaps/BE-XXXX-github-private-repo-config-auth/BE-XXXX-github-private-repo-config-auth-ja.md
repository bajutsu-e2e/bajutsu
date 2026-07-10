[English](BE-XXXX-github-private-repo-config-auth.md) · **日本語**

# BE-XXXX — GitHub を取得元とする config での private リポジトリへのアクセス権限の付与

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-github-private-repo-config-auth-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | config の取得元 |
| 関連 | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md), [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md), [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md), [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) により、`--config`（と serve
の config ピッカー）で GitHub リポジトリを ref 指定で名指しできるようになりました。`github:<owner>/<repo>@<ref>:<path>`
と書けば、Bajutsu がそのサブツリーを実体化して config を読み込みます。**public** リポジトリには認証情報が
要りませんが、**private** リポジトリには要ります。現状その認証情報は 1 つのヘルパー `github_token()`
（[`bajutsu/config_source.py:100`](../../bajutsu/config_source.py)）が解決します。`GITHUB_TOKEN` と
`GH_TOKEN`、次に `gh auth token` へのフォールバック、いずれも無ければ匿名という順で、プロセス全体で共通の
1 つのトークンを `Authorization: Bearer` として `api.github.com` へ送ります。

これは、開発者が自分のマシンで自分の private リポジトリを読む分には十分ですが、この機能が本来ねらう場面には
不十分です。すなわち、**無人で稼働し、利用者が複数いることもあるセルフホストの `serve`**
（[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）が、1 つ以上の private テスト
リポジトリを読む場合です。本項目は **github.com** に限定し、問いを 1 つに絞ります。*private リポジトリへの
アクセス権限を、GitHub を取得元とする config にどう付与するか*、です。受け入れる認証情報の種類、それぞれの
供給経路とスコープ、それぞれが必要とする最小権限、そして認証情報が欠けているか不足しているときの報告の仕方を
整理します。変わるのは認証情報の取得だけで、スキーマ、runner、ドライバ、決定的なゲートは手つかずであり、
大規模言語モデル (LLM) の呼び出しはどこにも加えません。

## 動機

現在の単一トークン方式には、config の取得元を BE-0016 の意図どおり使ったときにだけ現れる欠落が 3 つあります。

- **無人ホストのためのサービス識別子が無い。** セルフホストの `serve` デーモン（launchd や systemd）には
  対話的な `gh` セッションが無いため、`gh auth token` は使えず、運用者はデーモンのレベルで個人アクセス
  トークン (personal access token; PAT) を注入するしかありません。PAT は*人*に紐づきます。その人のアクセス
  権限をそのまま帯び、その人がローテーションしたり離任したりすると使えなくなります。private リポジトリを
  無人で読むサービスは、**それ自身として**認証すべきです。GitHub でそれにあたるのが **GitHub App の
  インストール**（短命で、インストール単位に絞られ、監査可能なトークン）ですが、Bajutsu にはそれを発行して
  使う経路がありません。
- **スコープが無い。すべてのリポジトリとすべての利用者で 1 つのトークン。** `github_token()` はプロセス全体で
  1 つのトークンを返し、どのリポジトリの要求に対してもそれを送ります。マルチ org のセルフホスト（BE-0016 は
  マルチ org 分離を出荷済み）では、どのテナントの config 取得も運用者の単一の識別子とそのアクセス権限を借りて
  しまいます。認証情報を特定の取得元（owner/repo）や org に束ねる手段がありません。
- **最小権限が、文書化も誘導もされていない。** `repo` スコープの classic PAT は、その利用者の*すべての*
  private リポジトリへの読み書きを許します。「このテストリポジトリを 1 つ読む」よりはるかに広い権限です。
  GitHub にはより狭い付与（特定リポジトリに **Contents: read** で絞った fine-grained PAT や App の
  インストール）がありますが、それを説明するものも、運用者にそちらを選ばせるものもありません。結果として、
  最も抵抗の少ない道が最も過剰な権限の認証情報になります。

4 つ目の小さな欠落が、以上のすべてを運用しづらくします。呼び出し元から見えない private リポジトリは 403 では
なく **404** を返し、`_get` はその `HTTPError` をそのまま伝播させる（[`config_source.py:127`](../../bajutsu/config_source.py)）
ため、「アクセス権限を付与していない」が「リポジトリが存在しない」に見えます。

Web UI は、これらすべてをいっそう際立たせます。serve の「Open config」ダイアログは既に「**From a Git
repository**」の取得元を提供しますが（BE-0063。`ops.bind_config` 経由でバインドします。
`bajutsu/serve/handler.py:318`）、**認証情報の欄がありません**。トークンは依然として serve プロセスの環境から
しか来ません。そのため、デーモンにトークンが無いマシンで UI から private リポジトリを指した利用者は、目の前の
画面からアクセスを渡す手段が無いまま、不透明な 404 を受け取ります。これはまさにセルフホスト運用者の経路で
あり、認証情報をデーモンへ事前注入するのではなく*入力して保存できる*唯一の場所です。

これらはいずれも prime directive に触れません。認証情報の解決は決定的でモデルを介さず（config を*取得*する
だけで、解決された SHA が決定性のアンカーであり続けます）、認証情報の差はツールやドライバや runner ではなく
環境か config に置かれます。

## 詳細設計

private リポジトリへのアクセスを、互いに排他で全体を覆う (MECE) 4 つに整理します。いずれも github.com 限定
です。#4 は単独で出せます。

### 1. 受け入れる認証情報の種類（定義された文書化済みの集合）

private リポジトリの読み取りを許す認証情報を、無人サービスへの適性が低いものから高いものへ、明示します。

- **`gh auth token`（対話的／開発者向け）。** 自分のマシンで作業する開発者向けの、現状のフォールバックとして
  残します。
- **`GITHUB_TOKEN` / `GH_TOKEN` 経由の個人アクセストークン。** 残します。classic の広い `repo` スコープの
  PAT よりも、対象リポジトリに **Contents: read** で絞った **fine-grained** な PAT を強く推奨する形で
  文書化します。
- **GitHub App のインストールトークン（セルフホストのサービスに推奨）。** 新規です。App の id、秘密鍵、対象の
  インストール（またはリポジトリから解決したインストール）を与えると、Bajutsu が短命なインストールアクセス
  トークンを発行し、それを Bearer として使います。これがサービス識別子への答えです。トークンは短命で、
  インストールのリポジトリに限られ、人に紐づきません。実質的なロジックが増えるのはこの部分だけです（JSON Web
  Token (JWT) の署名と、インストールトークンのエンドポイント呼び出し）。同じ解決の継ぎ目の背後に置く任意の
  認証情報プロバイダとして設計するので、PAT だけを使う構成が余分なものを引き込むことはありません。

### 2. 供給経路と優先順位

各認証情報が**どこから**来て**どの順で**解決されるかを定め、挙動を予測可能にし、無人デーモンに明確な入り口を
与えます。

- 文書化された優先順位（たとえば、明示的な App 認証情報、次に `GITHUB_TOKEN` / `GH_TOKEN`、次に
  `gh auth token`）。
- デーモンの供給経路を明記します（PAT なら launchd の `EnvironmentVariables` / systemd の `Environment` /
  秘密情報ファイル、App の経路なら App の秘密鍵ファイル）。
- 起動時に一度取り込むのではなく、**取得のたびに**解決します（トランスポートは `materialize` ごとに生成される
  ため、環境変数の経路については既にそうなっています）。これによりローテーションした秘密情報が再起動なしで
  効きます。

### 3. 認証情報を取得元に束ねる（プロセス全体で共通にしない）

認証情報を、プロセス全体で共通の 1 つのトークンではなく、**config の取得元に束ねられる**ようにします。特定の
owner/repo、または org 単位です。これにより、マルチ org のセルフホストが 1 つの識別子をテナント間で共有せず
に済みます。これが、config の取得元を BE-0016 のマルチ org 分離と整合させる部分であり、*ホスト*された
デプロイでの表出は
[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
（ホストされた UI がそもそもどの config 取得元を提供するか）と
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) の既存の認証で
境界づけられます。束ねを config で表すか、環境で表すか、serve の認証済み API を通すかが、ここで詰めるべき
主要な設計上の選択です。認証情報はログに出さず、BE-0063 が既に求めるとおり秘匿化の既定対象に加えます。

### 4. 最小権限のガイダンスと認証の診断（単独で出せる）

- 各種類の**最小の付与を文書化**します。fine-grained PAT や App のインストールなら対象リポジトリに
  Contents: read です。[docs/configuration.md](../../docs/configuration.md) と
  [docs/self-hosting.md](../../docs/self-hosting.md) に日英両方で書き、運用者を狭い付与へ誘導します。
- **認証の失敗を読み取れるようにします。** トランスポートの `HTTPError` を包みます。リポジトリに対する
  **404 / 403** は「リポジトリが見つからない、*または*アクセス権限が付与されていません。`<owner>/<repo>` に
  Contents: read を持つ認証情報を渡してください」に、**401** は「渡されたトークンが拒否されました」になります。
  最も不足していそうな付与を名指しすることが要点です。これは認証情報プロバイダの変更を必要とせず、最初に
  出せます。

### 5. serve（Web UI）の面

セルフホスト運用者が実際にアクセスを付与するのは Web UI なので、上記の認証情報モデルは画面まで届かねば
なりません。並行する仕組みを新設せず、serve の既存の継ぎ目を再利用します。

- **「From a Git repository」ダイアログの認証情報の受け口。** private な Git ソースをバインドするとき、
  ダイアログで運用者は**保存済みの認証情報を選ぶ**か、**新しい認証情報を入力する**（fine-grained PAT、または
  #1 の App 認証情報）かを選べます。値は送信後にブラウザに保持されず、読み返されもしません。serve が既に
  秘密情報を扱うのと同じく、ダイアログはマスク済みのプレビューを表示します。
- **新しいストアではなく既存の `SecretStore` の継ぎ目に保存する。** serve には、これに合う継ぎ目が既に
  あります。local の単一利用者向けの `EnvSecretStore` と、hosted バックエンド向けの org 単位で保存時暗号化
  される `DbSecretStore` です（[BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)。
  `bajutsu/serve/secrets.py`、`bajutsu/serve/server/secrets.py`）。UI で入力した Git 認証情報はそこへ
  保存されます。マスク済みプレビューのみで、HTTP ハンドラが平文を読み返せません。これが #3 の**取得元ごと／
  org ごとのスコープ**を具体化する部分でもあります。hosted バックエンドでは認証情報は既に `org_id` で
  スコープされるので、各テナントの保存済み Git 認証情報は自然にそれぞれのものになります（AI プロバイダ設定を
  保存する読み取り可能な `provider_store`、
  [BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings-ja.md)
  は、serve の設定保存の面としての先例ですが、Git 認証情報は読み取り可能なストアではなく*書き込み一度きり*の
  `SecretStore` に属します）。
- **[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
  に沿ってデプロイを意識する。** 認証情報の欄は Git ソースにのみ付随します。Git ソースは local と hosted の
  両バックエンドで提供されるため、Git ソースを提供しないバックエンドには何も新たに露出しません。hosted
  バックエンドでは認証情報は上記のとおり org ごとで、local serve では現状どおり既定でプロセス環境なので、
  local の単一利用者の実行は変わりません。
- **診断をダイアログに表示する。** #4 の認証メッセージ（404/403 →「アクセス権限が付与されていません。
  `<owner>/<repo>` に Contents: read を持つ認証情報を渡してください」）を、バインドが失敗したときに**バインド
  ダイアログにインラインで**表示し、運用者がサーバのログを読まずにその場で直せるようにします。

### 変わらないもの

spec の構文、SHA でキー付けしたキャッシュ、`_load_effective` の継ぎ目、config のスキーマ、`resolve()`、
runner、ドライバ、アサーション評価器、決定的なゲートは、いずれも手つかずです。変わるのは、*private な
github.com リポジトリ向けの認証情報を、どう選び、どう束ね、どう診断するか*だけであり、BE-0063 の「取得だけが
変わる」を private アクセスの問いに絞ったものです。

## 検討した代替案

- **SSH の deploy key。** 却下します。BE-0063 のトランスポートは git-over-SSH ではなく HTTPS の REST tarball
  エンドポイントなので、deploy key は呼び出しを認証しません。対応するには `git clone` へ切り替えることになり、
  それは BE-0063 が既に却下しています（`git` バイナリが要り、任意の SHA の浅いフェッチが一様でない）。
- **classic PAT を推奨経路にする。** *推奨*としては却下します（入力としては残します）。classic の `repo`
  スコープの PAT は過剰に付与し（すべての private リポジトリへの読み書き）、人に紐づきます。ガイダンスは
  fine-grained PAT か App のインストールへ誘導します。
- **プロセス全体で共通の単一トークンのままにする。** 却下します。単一チームのセルフホストには十分ですが、
  BE-0016 のマルチ org 分離には合いません。1 つの共有識別子がテナント境界を越えるからです。
- **認証情報を config ファイルやリポジトリに保存する。** 却下します。秘密情報はバージョン管理に置くべきでは
  ありません。認証情報は環境、鍵ファイル、または serve の秘密情報ストアに置きます。
- **serve 内での OAuth デバイスフロー。** 見送ります。より重く、環境や App の経路が無人の場合を既に覆います。
  対話的なセルフホストログインが欲しくなればデバイスフローは後から足せます。
- **App のみにして PAT の経路を落とす。** 却下します。App はサービスにとって正しい*既定*ですが、単一の開発者
  には過剰です。両方を残し、無人ホストには App を推奨します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] #4 最小権限の文書（configuration と self-hosting、日英両方）を書き、`config_source.py` に `HTTPError` 401/403/404 の認証診断を加えます（単独で出せます）。
- [ ] #1 GitHub App のインストールトークンプロバイダ（JWT ＋インストールトークンのエンドポイント）を、既存の PAT / `gh` の経路と並べて認証情報の継ぎ目の背後に置きます。
- [ ] #2 認証情報の優先順位を文書化し、無人デーモンの供給経路を用意します。
- [ ] #3 取得元ごと（owner/repo または org）に認証情報をスコープします。ホストされたデプロイでは BE-0108 / BE-0051 で境界づけます。
- [ ] #5 serve の「From a Git repository」ダイアログに認証情報欄を設け、`SecretStore` の継ぎ目（BE-0136）に保存し、デプロイを意識し（BE-0108）、認証診断をインラインで表示します。

## 参考

- [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) — 本項目が拡張する Git を
  取得元とする経路。[`bajutsu/config_source.py`](../../bajutsu/config_source.py)（`:100` の
  `github_token()`、`:123` の `_GitHubTransport` の bearer ヘッダ、`:133,137` の `api.github.com` への
  リクエスト、`:127` の `_get`）。
- [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)（セルフホストとそのマルチ org
  分離。取得元ごとのスコープが要る理由）、
  [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)（serve の
  ハードニング。認証情報の経路が守るトークン認証、秘匿化、パスの封じ込め）、
  [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
  （ホストされたデプロイが提供する config 取得元）、
  [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)（UI で入力した
  認証情報を保存する `SecretStore` の継ぎ目）、
  [BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings-ja.md)
  （serve の設定保存の先例）。
- serve：`bajutsu/serve/handler.py`（`bind_config`）、`bajutsu/serve/secrets.py` /
  `bajutsu/serve/server/secrets.py`（`SecretStore` の継ぎ目）、`bajutsu/serve/state.py`（アクティブな
  config の Git ソース来歴）。
- GitHub ドキュメント：個人アクセストークン（fine-grained と classic）、GitHub App とインストールアクセス
  トークン、リポジトリの `Contents` 権限。
- [docs/configuration.md](../../docs/configuration.md)、[docs/self-hosting.md](../../docs/self-hosting.md)。
