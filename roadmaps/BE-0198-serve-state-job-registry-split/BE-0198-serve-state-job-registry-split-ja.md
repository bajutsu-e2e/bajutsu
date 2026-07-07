[English](BE-0198-serve-state-job-registry-split.md) · **日本語**

# BE-0198 — ServeState から JobRegistry を切り出す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0198](BE-0198-serve-state-job-registry-split-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0198") |
| トピック | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## はじめに

`serve` の中心にある dataclass `ServeState` は、あらゆる関心事を抱え込む型に育ってしまいました。1
つの型のなかに、run の成果物ディレクトリ、差し替え可能な 6 つのストレージ・ログ・セッションの seam、
認証ポリシー、AI プロバイダ設定、監視用のカウンタ、そしてジョブレジストリ（`jobs` の dict と、その連番
カウンタ `_seq`、ロック `_lock`）が同居しています。この項目では、そのうちジョブレジストリの責務、すなわ
ちジョブの登録、id の採番、同時実行数の上限の適用を、`ServeState` が保持する専用の `JobRegistry` オブ
ジェクトへ切り出します。`ServeState` のなかで最も独立している振る舞いを、どのエンドポイントの挙動も変え
ずに、単独で読めて単独でテストできる形にするのが狙いです。`serve` は決定的な `run` や CI のゲートの外に
あるため、これは純粋に可読性と保守性を高めるリファクタリングです。

## 動機

`ServeState`（`bajutsu/serve/jobs.py:174`〜`447`）は、互いに無関係な少なくとも 6 つの責務のまとまりを
1 つの dataclass で背負っています。

1. **run の入出力の場所**：`runs_dir`、`scenarios_dir`、`baselines_dir`、`uploads_dir`、`cwd`、
   `base_cwd`、`root`。
2. **差し替え可能なストレージや配信の seam**：`artifacts`、`scenarios`、`baselines`、`secrets`、
   `executor`、`logbus`、`sessions`、`repository` に加え、org ごとのストア生成関数 `org_stores` と
   `for_org` / `StoreBundle` の仕組み（BE-0015 のマルチテナント対応）。
3. **認証と認可**：`token` / `check_token`、`issue_session` / `valid_session`、OAuth クライアント、
   そして `oauth_allowed_users` / `oauth_admins` / `oauth_viewers` の許可リスト（BE-0051、BE-0015
   の 7b・7c）。
4. **AI プロバイダ設定**：`provider_settings` と `provider_settings_snapshot` /
   `set_provider_setting`、そしてサインインの状態を持つ `ant_login_proc` / `ant_login_lock`
   （BE-0183、BE-0175）。
5. **監視**：`/metrics` エンドポイント向けの `active_jobs` / `in_flight_by_org`（BE-0169）。
6. **ジョブレジストリ**：`jobs`、`_seq`、`_lock` と、各ジョブに id を採番し、全体・ユーザーごと・org
   ごとの同時実行数の上限を適用するメソッド `_register` / `register` / `try_register`（BE-0051、
   BE-0015 の 7c-3、BE-0016 の Tier B）。

6 番目のまとまりが最も切り離しやすい部分です。`jobs` / `_seq` / `_lock` に触れるのは登録と集計のメソッ
ド（`_register`、`register`、`try_register`、`active_jobs`、`in_flight_by_org`）だけであり、それらの
メソッドは `ServeState` のほかのフィールドをほとんど読みません。上限値（`max_concurrent`、
`max_concurrent_per_user`、`max_concurrent_per_org`）はこれらのメソッドと一緒に動き、登録したジョブが
必要とする `logbus` が唯一の外部依存です。それにもかかわらず、これらがほかのすべてと同じ dataclass 上に
あるために、ジョブレジストリで最も重要な不変条件、すなわち「id の採番と上限の判定を 1 つのロックのもとで
アトミックに行い、同時に届いた 2 つのディスパッチが両方とも上限をすり抜けないようにする」（`try_register`、
`bajutsu/serve/jobs.py:409`）という約束が、その約束そのものを表す型ではなく、docstring の散文として述べ
られているにとどまっています。今の構造で上限のロジックをテストするには、上限とは無関係なストア・シークレ
ット・起動ディレクトリを `__post_init__` で解決する `ServeState` を丸ごと組み立てる必要があります。

より低レベルの 2 つの臭いがこれを深めています。`_lock` という名前のロックは、`jobs` の dict と
`provider_settings` の dict という**無関係な 2 つのもの**を守っています（`provider_settings_snapshot` /
`set_provider_setting` が同じ `_lock` を取ります。`bajutsu/serve/jobs.py:364`〜`375`）。名前からはこ
のロックが何を守るのか読み取れず、2 つの責務が理由なく 1 つのロックで競合します。また `_seq` は可変のカ
ウンタで、正しく変更してよい箇所は `_register` だけですが、`JobRegistry` にすればこのカウンタの所有者を
構造として 1 つに定められます。

規模は M です。切り出し自体はほぼ機械的で、5 つのメソッドと 3 つのフィールドを新しいクラスへ移し、あとは
委譲するだけですが、広く参照される型に触れるため `ServeState` の公開面は保たなければなりません。`serve`
は意図して決定的なゲートの外に置かれており（`run` の判定ではなく Tier‑2 のツールです）、プライムディレク
ティブには抵触しません。得られるのは、複数の BE 項目（BE-0051、BE-0015、BE-0016）が 1 つの dataclass に
積み上げてきた、希少なデバイスをめぐる同時実行のロジックが、その不変条件をまさに名指しして所有する型に、
ようやく収まることです。

## 詳細設計

このリファクタリングは挙動を保ちます。`serve` の各エンドポイント、executor、`run_job` から見える振る舞い
は変わらず、id は引き続き単調増加の連番から採番され、同時実行数の上限も同じ閾値で拒否します。作業は次の互
いに排他的な単位に分かれます。

- **`JobRegistry` 型を導入する**。この型が `jobs`、id の連番、そして自前のロックを所有し、登録と集計の
  面だけを公開します。`register(job)`、`try_register(job)`（上限を受け取り、いずれかの上限に達したら
  `None` を返す）、`active_jobs()`、`in_flight_by_org()` です。「1 つのロックのもとで数えてから挿入す
  る」というアトミックな不変条件はこの型のなかに完結するので、その保証は共有 dataclass の docstring では
  なくクラスの境界で表現されます。id のカウンタはこの型が唯一の所有者となり、`_seq` は `ServeState` 上を
  漂うフィールドではなくなります。
- **同時実行数の上限をどこに置くかを決める**。3 つの上限（`max_concurrent`、
  `max_concurrent_per_user`、`max_concurrent_per_org`）はジョブレジストリの状態ではなく設定です。
  `try_register` に渡す（レジストリを純粋な仕組みに保ち、上限は呼び出しごとに与える）か、上限を渡してレジ
  ストリを構築する（`serve()` のフラグから構築時に上限を固定する）か、どちらかを選んで明記します。どちらが
  読みやすいかは operations 層の呼び出し箇所で判断します。
- **`ServeState` に `JobRegistry` を持たせて委譲する**。`ServeState` は登録と集計の面（`register` /
  `try_register` / `active_jobs` / `in_flight_by_org`）を薄く転送するメソッドを残してレジストリへ委譲
  する**か**、呼び出し側が `state.job_registry` へ直接触れるかのいずれかにします。これもどちらかを選び、
  operations 層が一様に読めるよう一貫して適用します。いずれにせよ `ServeState` はジョブレジストリのフィー
  ルドとロックを**定義**しなくなります。
- **`provider_settings` に専用のロックを与える**。`jobs` が移れば、共有していた `_lock` が守るのは
  `provider_settings` だけになります。守る対象を名前で表すロックへ改名する（あるいは provider 設定のまと
  まりと一緒に移す）ことで、1 つのロックが無関係な 2 つの dict を覆っていたと読者が気付かなくてよいように
  します。これでジョブ登録と設定パネルの書き込みのあいだにあった見せかけの競合が消えます。
- **`Job` dataclass はそのままにする**。`Job` はジョブ自身の記録で、executor と `run_job` が使います。
  移すのはその**レジストリ**（dict と id の採番と上限）だけです。この項目では `Job`、`run_job`、
  `_boot_devices`、`_build_app`、永続化ヘルパには触れません。
- **レジストリの単体テストを絞って追加する**。上限のロジックが自前の型に収まれば、id の単調増加と、各上限
  （全体、識別済み actor に対するユーザーごと、org ごと）を、`ServeState` を丸ごと組み立てずに
  `JobRegistry` へ直接テストできます。モックは使わず、プレーンな `Job` の値と fake の `logbus`（インメ
  モリの bus はすでに存在します）を組み立てる、というプロジェクトのモック不使用の方針に従います。

範囲外（境界を明示するために挙げます）：ストレージ seam のまとまり、認証のまとまり、AI 設定のまとまりは、
いずれも `ServeState` に残します。それらをさらに分けるのは妥当な後続作業ですが、この項目には**含めません**。
ジョブレジストリは最初に切り出すのに最もきれいで独立した seam であり、これだけを行うことで PR がレビューし
やすい大きさに収まります。

## 検討した代替案

- **`ServeState` を 1 つの dataclass のまま残し、各フィールドのまとまりを説明する既存の docstring に頼
  る**。却下します。まとまりは実在しますが暗黙のままで、最も重要な不変条件（アトミックな上限判定）が、共有
  される型の散文であってクラスの境界ではありません。切り出せばこの不変条件を構造として表せ、`serve` 全体を
  立ち上げずに上限のロジックをテストできます。
- **すべての責務のまとまり（ストレージ、認証、AI 設定、レジストリ）を一度に別々の協調オブジェクトへ分け
  る**。この項目では却下します。全面的な分解は、operations 層のいたるところで参照される型への大きな横断的
  変更となり、一度のレビューでは追いにくくなります。ジョブレジストリは最も切り離しやすい部分で、ほかとほと
  んど状態を共有しないため、これを最初に切り出すと最も低いリスクで可読性の利得の大半が得られます。次のまと
  まりは、価値があると分かった時点で後続の項目が剥がせます。
- **フィールドは `ServeState` に残し、メソッドだけを dict を受け取る自由関数へ移す**。却下します。共有
  される dict を扱う自由関数では不変条件に居場所を与えられず、`_seq` に唯一の所有者を与えることもできませ
  ん。可変のカウンタとそのロックは今と同じように `ServeState` 上に散らばったままで、責務を動かさずコードだ
  けを動かすことになります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `jobs` / id の連番 / 自前のロックを所有する `JobRegistry` 型を導入し、`register` /
      `try_register` / `active_jobs` / `in_flight_by_org` を公開する
- [ ] 同時実行数の上限をどこに置くか（呼び出しごとに渡すか、レジストリの構築時に固定するか）を決めて適用する
- [ ] `ServeState` に `JobRegistry` を持たせ、呼び出し箇所を一貫してそこへ通す（薄い委譲か直接アクセスか）
- [ ] `provider_settings` に専用の名前付きロックを与え、`_lock` が 2 つの関心事を守る状態を解消する
- [ ] id の単調増加と各同時実行数の上限を `JobRegistry` へ直接検証する単体テストを追加する

## 参考

- `bajutsu/serve/jobs.py:174`〜`447`（`ServeState` dataclass）
- `bajutsu/serve/jobs.py:249`、`:311`、`:312`（`jobs`、`_seq`、`_lock`）
- `bajutsu/serve/jobs.py:388`〜`426`（`_register` / `register` / `try_register`。レジストリの振る舞い
  とアトミックな上限判定）
- `bajutsu/serve/jobs.py:364`〜`386`（`provider_settings_snapshot` / `set_provider_setting` /
  `active_jobs` / `in_flight_by_org`。`_lock` を使うそのほかの箇所）
- 同時実行数の上限を導入した BE-0051（全体・共有トークン）、BE-0015（7c-3 のユーザーごと）、BE-0016
  （Tier B の org ごと）
- 同じトピックの姉妹分解：BE-0143（run コマンドの分解）、BE-0172（run ループのステップ分解）、BE-0092
  （crawl coordinator の抽出）
