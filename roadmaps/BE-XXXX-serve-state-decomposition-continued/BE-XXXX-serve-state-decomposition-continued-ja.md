[English](BE-XXXX-serve-state-decomposition-continued.md) · **日本語**

# BE-XXXX — ServeState の分解を継続し、認証系とプロバイダー設定系のマネージャに分離する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-state-decomposition-continued-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

`ServeState`（`bajutsu/serve/state.py:287`〜`627`）は、`serve` の中心にあるおよそ40個のフィールド
を持つ dataclass です。BE-0198 ではすでに、このなかで最も自己完結していた部分、すなわちジョブレジス
トリ（`jobs`、id の連番、両者を守るロック）を切り出し、独立した `JobRegistry` 型としました。
`ServeState` は現在この型を保持し、処理を委譲しています。続く BE-0206 では、状態コンテナ自体を
`bajutsu/serve/state.py` という専用モジュールに移し、ジョブ実行エンジンから切り離しました。本項目は
この分解を継続するものです。対象は、いまも `ServeState` に直接残っている次にまとまりの強い2つのクラ
スター、認証、セッション、OAuth のクラスターと、AI プロバイダー設定のクラスターです。それぞれを独立
した型とし、`ServeState` が現在 `job_registry` を保持しているのと同じ形で保持し、処理を委譲するよう
にします。`serve` は決定的な `run`/CI ゲートの外側にあるため（プライムディレクティブ 1）、これはアサ
ーションや pass/fail の判定に影響を与えない、可読性と保守性のためだけのリファクタリングです。

## 動機

config の結びつけ、認証、組織ごとの AI 設定、4 つのストレージのシーム、ジョブレジストリ、アップロー
ドのサンドボックス状態、evidence、object store の設定までを 1 つの dataclass に混在させると、見通し
が悪くなります。「リクエストはどう認証されるのか」や「プロバイダーの選択はどう永続化されるのか」を追
おうとする読み手は、まずそれ以外のすべてを頭の中で除外しなければなりません。BE-0198 の `JobRegistry`
切り出しが示したのは、必要なのは書き直しではなく、自己完結したフィールドとメソッドのまとまりを見つけ
て境界を与えることだという点でした。残っているまとまりは次の2つです。

1. **認証、セッション、OAuth。** フィールド `token`、`sessions`、`oauth`、`oauth_allowed_users`、
   `oauth_admins`、`oauth_viewers`（`state.py:387`〜`401`）と、メソッド `check_token`、
   `issue_session`、`valid_session`（`state.py:528`〜`538`）は、まとめて「このリクエストは認証済み
   か、誰としてか」という1つの問いに答えるものであり、`ServeState` の他の部分とは状態を共有しませ
   ん。`oauth_allowed_users`、`oauth_admins`、`oauth_viewers` は、role-based access control
   （RBAC、役割ベースのアクセス制御）のために認可層（`bajutsu/serve/authz.py`）から読み取られます。
   これらが認証のまとまりと行動をともにするのは、サーバー構築時に `token` や `oauth` と並んで設定さ
   れ、以後変化しないためです。
2. **プロバイダー設定。** フィールド `provider_settings`、`provider_settings_store`、両者を守る
   2つのロック `_provider_lock`（`state.py:451`）と `_persist_lock`（`state.py:456`）、そしてメソッ
   ド `org_provider_settings`、`put_org_provider_settings`、`set_org_provider_choice`
   （`state.py:540`〜`584`）は、組織ごとの AI プロバイダー選択（BE-0229）のうちインメモリ側を構成し
   ます。永続化側にあたる `_persist_provider_settings`（`bajutsu/serve/operations/config.py:631`）
   はすでに `state.py` の外にありますが、`ServeState` の内部に手を伸ばして両方のロックを取得し、
   `provider_settings` を直接読み書きしています。これは今日、`ServeState` の内部実装を import する
   ことでしか成り立っていません。名前を持つマネージャがあれば、このモジュールをまたぐ呼び出し元に、
   dataclass の内部へ手を伸ばす代わりに呼べる狭い境界を与えられます。

この2つのクラスターは合わせて、`ServeState` に残るロックのうち2つ（`_provider_lock` と
`_persist_lock`）を抱えます。ジョブレジストリ自身のロックはすでに BE-0198 で移動済みで、3つ目の
`ant_login_lock`（`state.py:428`）は無関係な `ant login` サブプロセスを守るもので、ここでは対象外です
（`sessions` クラスターは `ServeState` ではなく `InMemorySessionStore`（`sessions.py:37`）の内部に
独自のロックを持ちます）。またこの2つのクラスターは、およそ十数個のフィールドも抱えています。レジストリに次ぐ規
模のまとまりであり、BE-0198 が自らの**詳細設計**のなかで「あり得る後続作業」と名指ししながら、その
PR をレビュー可能な大きさに保つためにあえて対象外とした機会と同じ形のものです。本項目は、残るまとま
りのうち2つについてのその後続作業にあたります。ストレージのシームのまとまり（`artifacts`、
`scenarios`、`baselines`、`secrets`、`executor`、`repository`、`org_stores`/`StoreBundle`）は、それ
自体が独立した大きなクラスターであるため、別の項目に残します。

## 詳細設計

このリファクタリングは全体を通じて振る舞いを保存します。すべての `serve` エンドポイント、operations
層の関数、テストは、今日と同じ認証の判定、セッションの寿命、プロバイダー設定の読み書きを観測します。
作業は、BE-0198 が定めたパターンをなぞる形で、互いに排他的な2つの単位に分かれます。

- **1つ目のクラスターについて、認証、セッションの型を切り出します。** `token`、`oauth`、許可リスト
  という固定された設定を持つ `AuthConfig`、あるいは `sessions` と `issue_session`、
  `valid_session`、`check_token` メソッドもまとめて包む、より広い `SessionManager` のいずれかを導
  入します。`sessions` 自体がすでに差し替え可能な `SessionStore` のシームであり単なるフィールドで
  はないため、どちらの形がより読みやすいかは設計時に決めます。この型は `token`、`sessions`、
  `oauth`、`oauth_allowed_users`、`oauth_admins`、`oauth_viewers` を所有します。`ServeState` はこの
  型のインスタンスを1つ保持し、`check_token`、`issue_session`、`valid_session` を薄い委譲メソッド
  として転送するか（`job_registry` に対してすでに `register`、`try_register` が使っているパターン
  です）、型自体を公開して少数の呼び出し元（`serve/handler.py`、`serve/authz.py`）をそれ経由の読み
  取りに更新するかのいずれかを選びます。BE-0198 がそうしたように、どちらか一方を選び、一貫して適用
  します。
- **2つ目のクラスターについて、`ProviderSettingsManager` を切り出します。** この型は
  `provider_settings`、`provider_settings_store`、`_provider_lock`、`_persist_lock` を所有し、
  `org_provider_settings`、`put_org_provider_settings`、`set_org_provider_choice` を、既存の読み取
  り時コピー、書き込み時コピーの規律を寸分たがわず保ったまま公開します。すなわち、読み取りは常に独
  立した `OrgProviderSettings` のコピー（`slots` の dict も含めて）を返すため呼び出し元が生きたエ
  ントリを決してエイリアスできず、書き込みは常に呼び出し元自身のインスタンスではなく新しいコピーを
  格納します。これは（`JobRegistry` にとって「1つのロックのもとでの id 採番のアトミック性」が最も
  重要だったのと同じ意味で）ここで最も重要な不変条件であるため、メソッド本体をそのまま移すだけでな
  く、新しい型の境界によって保存されなければなりません。`operations/config.py` の
  `_persist_provider_settings`、すなわち今日 `ServeState` に直接手を伸ばして両方のロックを取得して
  いる唯一の呼び出し元は、この境界に手を伸ばす代わりにマネージャの公開された面を呼ぶようになりま
  す（マネージャ自身が `_persist_lock` を取得する `persist` 相当のメソッドを持つか、この1つの
  パッケージ外呼び出しのために両方のロックを公開するかは、マネージャ自身の不変条件を、この1つの
  必要なケースを超えてロックオブジェクトを漏らさずに守れる形で選びます）。
- **`ServeState` はコーディネーターとして残します。** 両方の切り出しのあと、`ServeState` は
  `job_registry`（既存）、新しい認証、セッションの型、新しい `ProviderSettingsManager` への参照を
  保持し、今日 `ServeState` 経由の読み取りを必要とする少数の呼び出し元へは引き続き転送します。認証
  とプロバイダー設定のフィールド、メソッド、ロックそのものを**定義**することはなくなります。
- **ロックの規律を寸分たがわず保ちます。** 切り出した各マネージャは自前のロックを持ちます
  （`_provider_lock` と `_persist_lock` はともにプロバイダー設定のクラスターと一緒に移動し、I/O が
  インメモリのロックの内側で決して走らないよう、今日 `_persist_lock` に文書化されているとおり2つの
  別々のロックのまま保ちます）。`org_provider_settings`（ロックを解放したあとにコピーを返す）と
  `set_org_provider_choice`（変更はロックの内側だけで完結し、ロックを保持したまま外部へ何も公開しな
  い）がすでに使っている、ロックの外側で公開するパターンはそのまま保ちます。新しいロックは導入せ
  ず、既存のどのロックの範囲も変えません。
- **テストとモジュール一覧のドキュメントを更新します。** 切り出した各型について、単体で構築した状
  態での単体テストを追加し（BE-0198 のレジストリのテストと同じく、完全な `ServeState` を組み立てず、
  モックも使いません）、`serve` のモジュール一覧が `state.py` の責務を表す形でこの分割の影響を受け
  るなら `docs/architecture.md`、`docs/ja/architecture.md` を更新します。

対象外であることを明示しておきます。ストレージのシームのまとまり（`artifacts`、`scenarios`、
`baselines`、`secrets`、`executor`、`repository`、`org_stores`/`StoreBundle`）、run の入出力先のまと
まり（`runs_dir`、`scenarios_dir`、`baselines_dir`、`uploads_dir`、`cwd`、`base_cwd`、`root`）、ア
ップロードのサンドボックス状態（`upload`、`upload_exec`、`bind_upload`/`release_upload`）、
evidence、object store の設定（`evidence`、`object_store`、`object_store_prefix`）は、いずれも
`ServeState` に残ります。本項目は `JobRegistry`、`Job`、`run_job`、`bajutsu/serve/jobs.py` 内のどの
コードにも触れません。

## 検討した代替案

- **残るすべてのまとまり（認証、プロバイダー設定、ストレージのシーム、run の入出力先、アップロー
  ドのサンドボックス状態、evidence、object store の設定）を一度に切り出す。** 本項目では見送りまし
  た。`ServeState` は operations 層全体から参照されており、残るまとまりすべてに触れる1つの PR は大
  きくなりすぎ、1回で見通せません。これは BE-0198 がジョブレジストリだけを先に切り出した理由と同じ
  です。認証とプロバイダー設定の2つのクラスターは、それぞれすでに専用のロックと、小さく名前の付いた
  メソッド群を持つ、次に自己完結度の高いまとまりであるため、これらをまとめて切り出すことを1回でレ
  ビュー可能な単位とします。ストレージのシームなどの残るまとまりの切り出しは、別項目として後続でき
  ます。
- **`ServeState` を1つの dataclass のまま残し、フィールドのまとまりを説明する既存のコメントに頼
  る。** 見送りました。コメントは（BE-0198 が切り出し前のジョブレジストリについて動機で述べたのと同
  じく）認証とプロバイダー設定のまとまりを正確に描写していますが、まとまり自体は暗黙のままです。ま
  た `provider_settings` の dict がエイリアスされないようにする読み取り時コピー、書き込み時コピー
  の規律は、3つのメソッドにまたがる慣習だけで守られており、新しい呼び出し元が容易に迂回できないよ
  うな型の境界では守られていません。
- **メソッドを移さず、フィールドだけを単なる入れ子の dataclass に移す。** 見送りました。メソッドを
  持たないフィールドの塊は、ロックの規律という不変条件を今日と同じく `ServeState` 自身のメソッド本
  体に散らばらせたままにし、責務を動かさずデータだけを動かすことになります。これは BE-0198 が共有
  dict に対する自由関数に対して述べたのと同じ異議です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `token`、`sessions`、`oauth`、`oauth_allowed_users`、`oauth_admins`、`oauth_viewers` と
      `check_token`、`issue_session`、`valid_session` メソッドを所有する認証、セッションの型
      （`AuthConfig`/`SessionManager` など）を切り出す
- [ ] `provider_settings`、`provider_settings_store`、`_provider_lock`、`_persist_lock` と
      `org_provider_settings`、`put_org_provider_settings`、`set_org_provider_choice` メソッドを
      所有する `ProviderSettingsManager` を、読み取り時コピー、書き込み時コピーの規律を寸分たがわ
      ず保ったまま切り出す
- [ ] `operations/config.py` の `_persist_provider_settings` を、`ServeState` のロックへ直接手を伸
      ばす代わりにマネージャの公開された面を呼ぶよう移行する
- [ ] `ServeState` を、既存の `job_registry` と並んで新しい2つのマネージャを保持するコーディネー
      ターとして残し、呼び出し元を一貫してそれら経由に揃える
- [ ] 切り出した各型について、単体で構築した状態での単体テストを追加する（完全な `ServeState` は
      組み立てない）
- [ ] `serve` のモジュール一覧が変わる場合は `docs/architecture.md`、`docs/ja/architecture.md` を
      更新する

## 参考

- `bajutsu/serve/state.py:287`〜`627`（`ServeState` dataclass）
- `bajutsu/serve/state.py:387`〜`401`（`token`、`sessions`、`oauth`、`oauth_allowed_users`、
  `oauth_admins`、`oauth_viewers`）
- `bajutsu/serve/state.py:528`〜`538`（`check_token`、`issue_session`、`valid_session`）
- `bajutsu/serve/state.py:414`〜`420`（`provider_settings`、`provider_settings_store`）
- `bajutsu/serve/state.py:451`〜`456`（`_provider_lock`、`_persist_lock`）
- `bajutsu/serve/state.py:540`〜`584`（`org_provider_settings`、`put_org_provider_settings`、
  `set_org_provider_choice`）
- `bajutsu/serve/operations/config.py:631`（`_persist_provider_settings`。今日 `state.py` の外から
  両方のロックへ直接手を伸ばしている唯一の呼び出し元）
- `bajutsu/serve/authz.py`（`oauth_allowed_users`、`oauth_admins`、`oauth_viewers` を読み取る RBAC
  の判定）
- BE-0198（`roadmaps/BE-0198-serve-state-job-registry-split/`）。本項目が継続する `JobRegistry` の
  切り出しで、その**詳細設計**が完全な分解を「後続項目」に委ねると述べていた点も含みます
- BE-0206（`roadmaps/BE-0206-serve-state-module-split/`）。状態コンテナを `bajutsu/serve/state.py`
  に移した項目で、本項目の切り出しはそのモジュールに対して行われます
