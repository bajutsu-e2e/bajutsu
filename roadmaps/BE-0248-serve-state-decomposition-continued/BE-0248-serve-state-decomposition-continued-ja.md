[English](BE-0248-serve-state-decomposition-continued.md) · **日本語**

# BE-0248 — ServeState の分解を継続し、認証系とプロバイダー設定系のマネージャに分離する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0248](BE-0248-serve-state-decomposition-continued-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0248") |
| 実装 PR | [#1092](https://github.com/bajutsu-e2e/bajutsu/pull/1092) |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

`ServeState`（`bajutsu/serve/state.py:287`〜`627`）は、`serve` の中心にある、およそ40個のフィールドを持つ
dataclass です。この分解は、すでに2つの項目で始まっています。

- BE-0198 は、最も自己完結した部分であるジョブレジストリ（`jobs`、id の連番、両者を守るロック）を切り出し、
  独立した `JobRegistry` 型にしました。`ServeState` は現在この型を保持し、処理を委譲しています。
- BE-0206 は、状態コンテナ自体を `bajutsu/serve/state.py` という専用モジュールに移し、ジョブ実行エンジンから
  切り離しました。

本項目は、同じ分解を継続します。対象は、いまも `ServeState` に直接残っている、次にまとまりの強い2つの
クラスターです。認証、セッション、OAuth のクラスターと、AI プロバイダー設定のクラスターです。それぞれを
独立した型にします。`ServeState` は、現在 `job_registry` を保持しているのとまったく同じ形で、その型を保持し、
処理を委譲します。

`serve` は決定的な `run`/CI ゲートの外側にあります（プライムディレクティブ 1）。したがってこのリファクタリングは、
可読性と保守性のためだけに行います。アサーションや pass/fail の判定には影響しません。

## 動機

config の結びつけ、認証、組織ごとの AI 設定、4 つのストレージのシーム、ジョブレジストリ、アップロードの
サンドボックス状態、evidence と object store の設定が、1 つの dataclass に混在しています。この型は見通しが
悪くなっています。「リクエストはどう認証されるのか」や「プロバイダーの選択はどう永続化されるのか」を追う
読み手は、まずそれ以外のすべてを頭の中で除外しなければなりません。

BE-0198 の `JobRegistry` 切り出しが示したのは、必要なのは書き直しではないということです。必要なのは、
自己完結したフィールドとメソッドのまとまりを見つけて、境界を与えることです。そのようなまとまりが、
2つ残っています。

1. **認証、セッション、OAuth。** このクラスターには、フィールド `token`、`sessions`、`oauth`、
   `oauth_allowed_users`、`oauth_admins`、`oauth_viewers`（`state.py:387`〜`401`）が属します。メソッド
   `check_token`、`issue_session`、`valid_session`（`state.py:528`〜`538`）も属します。これらはまとめて、
   「このリクエストは認証済みか、誰としてか」という1つの問いに答えます。`ServeState` の他の部分とは、
   状態を共有しません。

   認可層（`bajutsu/serve/authz.py`）は、ロールベースアクセス制御（RBAC）のために、
   `oauth_allowed_users`、`oauth_admins`、`oauth_viewers` を読み取ります。これらのフィールドは、認証の
   まとまりと行動をともにします。サーバー構築時に `token` や `oauth` と並んで設定され、以後変化しないためです。
2. **プロバイダー設定。** このクラスターの構成要素は次のとおりです。
   - フィールド `provider_settings` と `provider_settings_store`
   - 両者を守る2つのロック `_provider_lock`（`state.py:451`）と `_persist_lock`（`state.py:456`）
   - メソッド `org_provider_settings`、`put_org_provider_settings`、`set_org_provider_choice`
     （`state.py:540`〜`584`）

   これらは合わせて、組織ごとの AI プロバイダー選択（BE-0229）のインメモリ側を構成します。

   永続化側にあたる `_persist_provider_settings`（`bajutsu/serve/operations/config.py:631`）は、すでに
   `state.py` の外にあります。ただし `ServeState` の内部に手を伸ばして両方のロックを取得し、
   `provider_settings` を直接読み書きしています。これは今日、`ServeState` の内部実装を import することで
   しか成り立っていません。名前を持つマネージャがあれば、このモジュールをまたぐ呼び出し元に、狭い境界を
   与えられます。呼び出し元は、dataclass の内部へ手を伸ばす必要がなくなります。

この2つのクラスターは、`ServeState` に残るロックのうち2つ（`_provider_lock` と `_persist_lock`）を抱えます。
ジョブレジストリ自身のロックは、すでに BE-0198 で移動済みです。3つ目の `ant_login_lock`（`state.py:428`）は、
無関係な `ant login` サブプロセスを守るもので、ここでは対象外です。`sessions` クラスターは、`ServeState` では
なく `InMemorySessionStore`（`sessions.py:37`）の内部に、独自のロックを持ちます。

この2つのクラスターは、およそ十数個のフィールドも抱えています。レジストリに次ぐ規模のまとまりです。
BE-0198 は、自らの**詳細設計**でこの機会を「あり得る後続作業」と名指ししました。その PR をレビュー可能な
大きさに保つために、あえて対象外としました。本項目は、残るまとまりのうち2つについての、その後続作業です。
ストレージのシームのまとまりは、それ自体が独立した大きなクラスターなので、別の項目に残します。該当するのは、
`artifacts`、`scenarios`、`baselines`、`secrets`、`executor`、`repository`、`org_stores`/`StoreBundle` です。

## 詳細設計

このリファクタリングは、全体を通じて振る舞いを保存します。すべての `serve` エンドポイント、operations 層の
関数、テストは、今日と同じ認証の判定、セッションの寿命、プロバイダー設定の読み書きを観測します。作業は、
BE-0198 が定めたパターンをなぞる形で、互いに排他的な2つの単位に分かれます。続く手順は、その支えとなる作業です。

- **1つ目のクラスターについて、認証、セッションの型を切り出します。** `token`、`sessions`、`oauth`、
  `oauth_allowed_users`、`oauth_admins`、`oauth_viewers` を所有する型を導入します。形は2通り考えられます。
  `AuthConfig` は、`token`、`oauth`、許可リストという固定された設定を持ちます。より広い `SessionManager` は、
  `sessions` と `issue_session`、`valid_session`、`check_token` メソッドもまとめて包みます。どちらの形が
  読みやすいかは、設計時に決めます。`sessions` 自体が、単なるフィールドではなく、すでに差し替え可能な
  `SessionStore` のシームだからです。

  `ServeState` は、この型のインスタンスを1つ保持します。公開のしかたは2通りです。1つ目は、
  `check_token`、`issue_session`、`valid_session` を薄い委譲メソッドとして転送する方法です。これは、
  `job_registry` に対して `register`、`try_register` がすでに使っているパターンです。2つ目は、型自体を公開し、
  少数の呼び出し元（`serve/handler.py`、`serve/authz.py`）をそれ経由の読み取りに更新する方法です。
  BE-0198 がそうしたように、どちらか一方を選び、一貫して適用します。
- **2つ目のクラスターについて、`ProviderSettingsManager` を切り出します。** マネージャは、
  `provider_settings`、`provider_settings_store`、`_provider_lock`、`_persist_lock` を所有します。
  `org_provider_settings`、`put_org_provider_settings`、`set_org_provider_choice` を公開し、既存の読み取り時
  コピー、書き込み時コピーの規律は寸分たがわず保ちます。
  - 読み取りは常に、独立した `OrgProviderSettings` のコピー（`slots` の dict も含む）を返します。呼び出し元は、
    生きたエントリを決してエイリアスできません。
  - 書き込みは常に、呼び出し元自身のインスタンスではなく、新しいコピーを格納します。

  これは、ここで最も重要な不変条件です。`JobRegistry` では、1つのロックのもとでの id 採番のアトミック性が
  最も重要でした。同じ意味です。メソッド本体をそのまま移すだけでなく、新しい型の境界によって、この不変条件を
  保存しなければなりません。

  `operations/config.py` の `_persist_provider_settings` は、今日、`ServeState` に直接手を伸ばして両方の
  ロックを取得している唯一の呼び出し元です。これからは、マネージャの公開された面を呼ぶようになります。
  方法は2通りです。マネージャ自身が `_persist_lock` を取得する `persist` 相当のメソッドを持つか、このパッケージ
  外の呼び出し元のために両方のロックを公開するかです。マネージャ自身の不変条件を守れて、この1つの必要な
  ケースを超えてロックオブジェクトを漏らさない形を選びます。
- **`ServeState` はコーディネーターとして残します。** 両方の切り出しのあと、`ServeState` は `job_registry`
  （既存）、新しい認証、セッションの型、新しい `ProviderSettingsManager` への参照を保持します。今日
  `ServeState` 経由の読み取りを必要とする少数の呼び出し元へは、引き続き転送します。認証とプロバイダー設定の
  フィールド、メソッド、ロックそのものを**定義**することは、なくなります。
- **ロックの規律を寸分たがわず保ちます。** 切り出した各マネージャは、自前のロックを持ちます。
  `_provider_lock` と `_persist_lock` は、ともにプロバイダー設定のクラスターと一緒に移動します。I/O がインメモリの
  ロックの内側で走らないよう、今日 `_persist_lock` に文書化されているとおり、2つの別々のロックのまま保ちます。
  次の2つのパターンは、そのまま保ちます。
  - `org_provider_settings` は、ロックを解放したあとにコピーを返します（ロックの外側での公開）。
  - `set_org_provider_choice` は、変更をロックの内側だけで完結させ、ロックを保持したまま外部へ何も公開しません。

  新しいロックは導入せず、既存のどのロックの範囲も変えません。
- **テストとモジュール一覧のドキュメントを更新します。** 切り出した各型について、単体で構築した状態での
  単体テストを追加します。BE-0198 のレジストリのテストと同じく、完全な `ServeState` を組み立てず、モックも
  使いません。`serve` のモジュール一覧が、`state.py` の責務をこの分割の影響を受ける形で表しているなら、
  `docs/architecture.md` と `docs/ja/architecture.md` を更新します。

対象外であることを明示しておきます。次のまとまりは、いずれも `ServeState` に残ります。

- ストレージのシームのまとまり（`artifacts`、`scenarios`、`baselines`、`secrets`、`executor`、`repository`、
  `org_stores`/`StoreBundle`）
- run の入出力先のまとまり（`runs_dir`、`scenarios_dir`、`baselines_dir`、`uploads_dir`、`cwd`、`base_cwd`、
  `root`）
- アップロードのサンドボックス状態（`upload`、`upload_exec`、`bind_upload`/`release_upload`）
- evidence と object store の設定（`evidence`、`object_store`、`object_store_prefix`）

本項目は、`JobRegistry`、`Job`、`run_job`、`bajutsu/serve/jobs.py` 内のどのコードにも触れません。

## 検討した代替案

- **残るすべてのまとまりを一度に切り出す。** 対象は、認証、プロバイダー設定、ストレージのシーム、run の
  入出力先、アップロードのサンドボックス状態、evidence と object store の設定です。本項目では見送りました。
  `ServeState` は operations 層全体から参照されています。残るまとまりすべてに触れる1つの PR は大きくなりすぎ、
  1回で見通せません。BE-0198 も、同じ理由でジョブレジストリだけを先に切り出しました。認証とプロバイダー設定の
  2つのクラスターは、次に自己完結度の高いまとまりです。それぞれ、すでに専用のロックと、小さく名前の付いた
  メソッド群を持っています。この2つをまとめて切り出すことを、1回でレビュー可能な単位とします。ストレージの
  シームなどの残るまとまりの切り出しは、別項目として後続できます。
- **`ServeState` を1つの dataclass のまま残し、フィールドのまとまりを説明する既存のコメントに頼る。**
  見送りました。コメントは、認証とプロバイダー設定のまとまりを正確に描写しています。BE-0198 も、切り出し前の
  ジョブレジストリについて、動機で同じことを述べました。しかし、まとまり自体は暗黙のままです。
  `provider_settings` の dict がエイリアスされないようにする読み取り時コピー、書き込み時コピーの規律は、
  3つのメソッドにまたがる慣習だけで守られています。新しい呼び出し元が容易に迂回できない型の境界で守る
  ほうが、確実です。
- **メソッドを移さず、フィールドだけを単なる入れ子の dataclass に移す。** 見送りました。メソッドを持たない
  フィールドの塊は、ロックの規律という不変条件を、今日と同じく `ServeState` 自身のメソッド本体に散らばらせた
  ままにします。責務を動かさず、データだけを動かすことになります。BE-0198 も、共有 dict に対する自由関数に
  対して、同じ異議を述べました。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `token`、`sessions`、`oauth`、`oauth_allowed_users`、`oauth_admins`、`oauth_viewers` と
      `check_token`、`issue_session`、`valid_session` メソッドを所有する `SessionManager` を切り出す
- [x] `settings`（組織ごとの辞書）、`store`、`_provider_lock`、`_persist_lock` と
      `org_provider_settings`、`put_org_provider_settings`、`set_org_provider_choice` メソッドを
      所有する `ProviderSettingsManager` を、読み取り時コピー、書き込み時コピーの規律を寸分たがわ
      ず保ったまま切り出す
- [x] `operations/config.py` の `_persist_provider_settings` を、`ServeState` のロックへ直接手を伸
      ばす代わりにマネージャの公開メソッド `persist` を呼ぶよう移行する
- [x] `ServeState` を、既存の `job_registry` と並んで新しい2つのマネージャ（`auth`／`providers`）を
      保持するコーディネーターとして残し、呼び出し元を一貫してそれら経由に揃える
- [x] 切り出した各型について、単体で構築した状態での単体テストを追加する（完全な `ServeState` は
      組み立てない）
- [x] `docs/architecture.md`、`docs/ja/architecture.md` は変更不要であることを確認する。`serve` の
      モジュール一覧は `serve/` を大づかみに述べるだけで、`state.py` 内部の責務には触れていない

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
