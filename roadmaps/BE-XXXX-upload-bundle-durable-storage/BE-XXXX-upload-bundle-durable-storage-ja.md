[English](BE-XXXX-upload-bundle-durable-storage.md) · **日本語**

# BE-XXXX — アップロードした zip config バンドルをオブジェクトストレージへ永続化する（ホスト型 serve 向け）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-upload-bundle-durable-storage-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | config の取得元 |
| 関連 | [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)、[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)、[BE-0204](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support-ja.md)、[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)、[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)、[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) は、ブラウザ
の利用者が `.zip`（config、シナリオ、ビルド済みアプリバイナリ）をアップロードし、それを `serve`
の稼働中の config としてバインドできるようにしました。このとき生成される config source レコード
は `upload` 種別です（`{kind: "upload", filename, sha256, size}`、`bind_upload_config`、
`bajutsu/serve/operations/upload.py`）。
[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)（**実装済み**）は、この
レコードをそのまま基盤にしています。登録した**プロジェクト**は `git` や `file` と同じように
`upload` 種別の source にもバインドでき、`{kind, filename, sha256, size}` というレコード自体を
（ホスト型なら `projects` 行、そうでなければローカルの JSON ストアへ）耐久性を持って永続化しま
す。

どちらの項目も永続化していないのは、`sha256` が指す**バイト列そのもの**です。`POST /api/upload`
は zip を `state.uploads_dir` 配下の serve 専有ディレクトリへ展開するだけであり、これは一時的、
プロセス単位、ローカルディスク限りの場所です。BE-0073 自身の「スコープ外」節にも「アップロードは
一時的なものであり、永続化やバージョン管理は Git source の役割である」と明記されています。
BE-0225 は、この結果生じる壁にすでに出荷済みのコードでぶつかっています。`activate_project`
（`bajutsu/serve/operations/projects.py`）は、`upload` 種別のプロジェクトを再起動する操作を
`409`（「アップロード済みバンドルのプロジェクトへは切り替えられません。設定を再アップロードして
バインドしてください」）で拒否します。アップロードを受け付けたレプリカが失われた時点で、再展開
できる対象が何も残っていないからです。

この隙間は、BE-0073 が対象とする単一 Mac の Tier A 向け `serve` では問題になりません。プロセスも
ローカルディスクも 1 つだけで、セッションの途中で再起動が起きないからです。問題になるのは、ホス
ト型でマルチテナントの `server` バックエンド（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）
です。BE-0015 は、ロードバランサーの背後で**ステートレスかつオートスケールするレプリカ**として
動くことを前提に設計されており、まさにこの理由からセッションとジョブを Postgres へ移しています
（「セッションは再起動を越えて維持され、レプリカをまたいで有効である」）。ところが、アップロー
ドされたバンドルの*内容*は、いまだにアップロードを受け付けたレプリカのローカルディスクにしか存
在しません。本項目は、この隙間を塞ぎます。`upload` 種別の source が持つ `sha256` の指すバイト列
を耐久性のあるものにし、どのレプリカからでも取得できるようにします。BE-0063 の Git source が
commit SHA によってすでに実現している性質と同じです。これにより、アップロードにバインドされた
BE-0225 のプロジェクトは、`409` にぶつかる代わりに、レプリカの再作成を乗り越えられるようになりま
す。

## 動機

BE-0073 の「設計上一時的である」という判断には、デプロイ環境に関する 2 つの事実が異なる角度から
効いてきます。

1. **オートスケールとレプリカの再作成。** `server` バックエンドでは、レプリカが（再デプロイ、ク
   ラッシュ、スケールダウンによって）再作成されることがあります。また、アップロードを受け付けた
   レプリカとは別のレプリカにリクエストが届くこともあります（これは BE-0015 がセッションとジョ
   ブをデータベースへ移した理由そのものです）。現在、展開済みのバンドルは、それを展開したレプリ
   カの `state.uploads_dir`（`ServeState` のフィールドであり、プロセス内のローカルディスク上の実
   体です）にしか存在しません。別のレプリカや、再起動後の同じレプリカに届いた run リクエスト
   は、materialize する対象を何も見つけられません。
2. **BE-0225 は、この壁にすでに出荷済みのコードでぶつかっています。** BE-0225 の
   `ProjectRegistry`（`bajutsu/serve/project_registry.py`）は、プロジェクトの config source レコ
   ードを耐久性を持って永続化します。`upload` 種別のプロジェクトなら `{kind: "upload", filename,
   sha256, size}` を、`projects` 行（ホスト型の場合）かローカルの JSON ストア（データベースがな
   い場合）へ保存します。Git にバインドされたプロジェクトを後で開き直すと、必要なレプリカ上で保
   存済みの commit SHA から再度 clone します。一方、アップロードにバインドされたプロジェクトを
   開き直そうとしても再 materialize できる対象が何もないため、`activate_project` はこれを頭から
   拒否します。`kind == "upload"` は常に `409` を返します。BE-0225 がすでに耐久性を持って保存し
   ている `sha256` は、その指すバイト列を取得するための鍵として、まさに正しい内容アドレス方式の
   鍵です。レコードの形は何も変える必要がありません。ただ、そのバイト列を今のところどこにも耐久
   性を持って書き込んでおらず、鍵が解決する先が存在しないだけです。

ここで、関連する運用上の注意点を1つ添えておきます。本項目で直接修正するものではありません。
BE-0063 自身のキャッシュルート解決（`bajutsu/config_source.py` の `_default_cache_root()`）は
`Path.home()` にフォールバックしており、これは、外部から与えられた UID で動き、その UID に対応
する `/etc/passwd` のエントリも `HOME` も無いコンテナでは、実行時に例外を投げます。`HOME` を書き
込み可能な任意のパスへ明示的に設定しておけば、この問題は完全に避けられます。キャッシュディレクト
リの木は必要に応じて作成されるため（`mkdir(parents=True, exist_ok=True)`）、あらかじめ存在してい
る必要はありません。Git source を有効にするホスト型デプロイは、この理由から `HOME` を明示的に設
定すべきです。本項目自身がローカルで materialize に使うディレクトリ（後述の単位 2）は
`state.uploads_dir` であり、これはもともと `HOME` から導かれるものではなく、`--runs` を基準にし
た明示的なパスです。したがって本項目はこの前提を引き継ぎません。

この修正は、コードベースがすでに 2 度採用している形をそのままなぞるため、新しい仕組みを何も追加
しません。

- **内容アドレス方式。** BE-0073 は、証跡のために zip の sha256 をすでに計算しており（`manifest.json`
  に記録されます）、BE-0225 はそれを `upload` 種別の source レコードの一部としてすでに永続化して
  います。このハッシュは、Git source にとっての解決済み commit SHA と同じ役割を果たす、耐久性が
  あり衝突しにくい鍵として、そのまま使えます。
- **オブジェクトストレージの seam。** [BE-0204](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support-ja.md)
  は、すでに `server` バックエンドに認証済みの `ObjectStore`（`BAJUTSU_SERVER_STORE`、`s3://` /
  `gs://`）を与えています。これは、「レプリカより長生きし、どのレプリカからも読み取れる必要があ
  る」という、まさにこの種類のデータのためのものであり、今日は run のアーティファクト、シナリ
  オ、visual baseline に使われています。アップロードされたバンドルの内容もこれと同じ種類のデー
  タであり、4 つ目の独自ストアではなく、同じ seam の背後に置くべきものです。

pass/fail、runner、ドライバ、シナリオスキーマのいずれにも触れません。決定的な `run` より前の取得
段階の配線にすぎないため、prime directive 1〜3（[CLAUDE.md](../../CLAUDE.md)）はそのまま成り立
ちます。また、AI provider・モデル設定の永続化（別に追跡されている、
[BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings-ja.md)
の保留中の hosted DB バックエンド化）にも触れません。

## 詳細設計

作業は、アップロード時の永続化、`activate_project` の無条件な `409` を置き換えるオンデマンドの
取得・展開フォールバック、オブジェクトストレージを設定していない場合の段階的縮退という 3 つの単
位に MECE に分かれます。config source の形を新設する必要はありません。BE-0225 はすでに
`{kind: "upload", filename, sha256, size}` を耐久性を持って保存しています。本項目はその
`sha256` の指すバイト列を解決できるようにするだけです。

### 1. アップロード時に生の zip を内容アドレス方式で org 単位に永続化する

`bind_upload_config`（`bajutsu/serve/operations/upload.py`）が `POST /api/upload` を扱う現在の検
証と展開の挙動（一時ファイルへストリームし、検証し、`state.uploads_dir` 配下の新規ディレクトリへ
展開する）はそのまま変えません。加えて、**`BAJUTSU_SERVER_STORE` が設定されている場合**には、
`serve` の稼働中 config を切り替える `state.bind_upload(upload)` の**前**に、新しいステップを 1
つ挟みます。すでに計算している sha256 から導いた鍵のもとに、生の（圧縮されたままの）zip バイト
をそのストアへ書き込みます。この鍵は、この seam が保存する他のオブジェクトがすべて使ってい
る同じ per-org プレフィックスの下にネストします。`artifact_prefix`・`scenario_prefix`・
`baseline_prefix` はいずれも `xxx_prefix(org_prefix(base, org))` という形で合成されており
（`bajutsu/serve/server/object_store.py`、`_build_server_state` で配線）、これと同じ形で
`upload_prefix` を定義すれば、鍵は `upload_prefix(org_prefix(base, org)) + f"{sha256}.zip"` にな
ります。`org_prefix` の下にネストすることで、重複排除（および解決）は 1 テナントの範囲に留まり、
他の兄弟ストアがすでに強制している分離境界と一致します。org を問わない共有の名前空間にすると、
ある org のアップロードが、内容が同一の別 org のアップロードと重複排除の対象になり、fetch 経路
の認可実装次第では内容の存在を推測されうる余地が生まれます。展開済みツリーではなく生の zip を
書き込むのは、オブジェクトを小さく保つためであり、zip-slip・zip-bomb の検証を、一度だけ信頼して再利用
するものではなく、各回の materialize（後述）で毎回繰り返すステップとして保つためであり、BE-0060
のエクスポートが run の束をすでに単一の zip アーティファクトとして扱っている形にも合わせるため
です。内容アドレス方式にすることで、同一バンドルの再アップロードは、同一 org 内であれば書き込み
を伴わない no-op になります。鍵はすでに存在しているため、そのリクエストを処理したレプリカでの
ローカル展開だけが今日と同じように行われます。

このオブジェクトストレージへの書き込みは同期的かつブロッキングであり、バインドより**前**に置く
理由もそこにあります。`bind_upload_config` は、この書き込みが成功するまで `state.bind_upload(upload)`
を一切呼びません。書き込みが（ネットワークエラー、バケットへの到達不可、権限拒否などで）失敗し
た場合、既存の検証失敗の経路と同じように、展開済みのローカルディレクトリを削除してエラーを返し
ます。この時点で共有状態には何も触れていないため、`serve` の稼働中 config はリクエスト前のまま
であり、ロールバックすべきものが何もありません。バインドを先にしてから後で失敗した場合にロール
バックするのではなく、書き込みをバインドより前に順序づけておくことで、この失敗経路が単純なまま
保たれます。クライアントが受け取るのは「耐久性のあるバイト列に裏付けられたバインド済み config」
か「エラーで状態は一切変わっていない」のどちらかであり、両者が食い違う状態にはなりません。これ
は、run 完了後のエビデンスアップロードの best-effort な失敗処理（`bajutsu/object_store.py` の
`upload_tree`。すでに確定した run の verdict がアーティファクトアップロードの成否に依存してはな
らないため best-effort になっています）とは意図的に異なります。ここではまだ何も確定していませ
ん。best-effort な書き込みにしてしまうと、BE-0225 がすでに耐久性を持って保存している
`{kind: "upload", sha256, ...}` というプロジェクトレコードが、実際には何も書き込まれていない鍵
を指したまま登録されてしまい、そのバンドル 1 つに限って、本項目が塞ごうとしている隙間をそのまま
再現してしまいます。耐久性を偽るプロジェクトレコードや、それを追い越して進んでしまう稼働中
config より、明確なアップロード失敗の方が安全です。

### 2. `activate_project` の upload 種別に対する `409` を取得・展開フォールバックに置き換える

`activate_project`（`bajutsu/serve/operations/projects.py`）に、`kind == "upload"` のときに
（諦める前に）試す 2 番目の解決経路を追加します。source レコードの `sha256`（プロジェクトの org
に絞って）でオブジェクトストレージから zip バイトを取得し、新規アップロードのときとまったく同じ
ように `state.uploads_dir` 配下の新規ディレクトリへローカル展開し、`bind_upload_config` がすでに
適用している zip-slip・zip-bomb・パス封じ込めの検証を同じく走らせ、`bind_upload_config` が今日
行っているのと同じ方法で結果をバインドします。オブジェクトストレージが設定されていない場合、あ
るいは鍵がそこに無い場合にだけ、今日の `409` がそのまま残ります。これにより、ローカルの
`uploads_dir` は耐久性のあるオブジェクトストレージの手前に立つキャッシュになります。これは、
[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) のローカルで内容アドレス
方式の Git checkout キャッシュ（`~/.cache/bajutsu/gitsrc/...`）が、リモートの origin に対してす
でに持っている関係と同じです。耐久性を持つのは、解決済みの SHA（Git の場合）や sha256（zip の場
合）であり、ローカルディスク上の checkout や展開済みツリーは常に破棄可能で、そこから再現できま
す。この仕組みによって、`activate_project` は、自分自身が受け取っていないバンドルについても、新
しく起動した、あるいはこれまでと異なるレプリカ上で成功するようになります。

### 3. オブジェクトストレージを設定していない場合の段階的縮退

`BAJUTSU_SERVER_STORE` を設定していないローカル `serve`（デフォルトであり、ホスト型以外のすべて
の Tier A・セルフホストのデプロイ）では、挙動はまったく変わりません。アップロードは、BE-0073 と
BE-0225 が今日出荷している通りに一時的なままであり、`activate_project` は `upload` 種別のプロジ
ェクトを同じ `409` で拒否し続けます。これは、任意のストア
（[BE-0204](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support-ja.md)）や
設定（[BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings-ja.md)）
についてすでに確立されているゼロコンフィグの前例と一致します。耐久性を持ち、レプリカをまたいで
解決できるようにする仕組みは加算的であり、運用者がアーティファクト・シナリオ・baseline のために
オブジェクトストレージの seam をすでに選んでいる場合にだけ働きます。

### 保持期間

内容アドレス方式にすることで、org 単位の自然な重複排除が得られます（同じバイト列の再アップロー
ドは書き込みを伴わない no-op です）。実際の削除は、バケット自身の lifecycle policy に委ねます。これは、
`bajutsu/object_store.py` のモジュール docstring が、この seam が支えるすべての URI アドレス方式
のストアを統べる保持機構として、すでに明記しているものと同じです。新しい保持や garbage
collection のコードは追加しません。

## 検討した代替案

- **zip（または展開済みツリー）を Postgres の BLOB として保存する。** 見送りました。BE-0015 のス
  キーマは、大きなバイナリのペイロードをすでにデータベースの外に置いています（アーティファクト、
  シナリオ、baseline はいずれも BE-0204 でオブジェクトストレージへ移り、テーブルには入れていませ
  ん）。数メガバイトのアプリバイナリは、まさに Postgres が不向きなペイロードの形です。もう一つの
  インフラ依存を避けることより、既存のストレージ seam との一貫性の方を重視しました。
- **生の zip ではなく展開済みツリーを永続化する。** 見送りました。展開済みツリーはより大きなオブ
  ジェクトになります（1 つではなく多数の小さな鍵になります）。また、将来の materialize が、展開
  時にしか走らない zip-slip・zip-bomb の検証をスキップしてしまう可能性があります。materialize の
  たびに生の zip から再展開すれば、その検証を一度だけ信頼するのではなく、常に効かせ続けられます。
- **永続的なシナリオ保存を、BE-0073 が当初意図していたとおり Git source だけに限定する。** 再利
  用可能でバージョン管理されたスイートを望むチームにとっては、これが今も正しい答えです。この点は
  本項目でも変わりません。しかし、BE-0225 がすでに登録している場合（Git リポジトリを背後に持たな
  い一度限りのアップロードされたバンドルにバインドされたプロジェクトが、それでもレプリカの再起動
  を越えて生き残る必要がある場合）には力になりません。2 つの source は別々の問題を解いており、補
  完的な関係のままです。
- **出荷済みの `upload` 種別とは別に、新しく `zip` という config source 種別を導入する。** 見送
  りました。BE-0225 の `_KIND_TO_SOURCE` とフロントエンド（`serve.core.js` の `source.kind ===
  'upload'`）は、すでに `upload` を、この形の source を表す唯一の識別子として、allowlist・UI・
  レジストリの全経路に通しています。同じ形のデータに二つ目の名前を作れば、すでに配線済みのその
  一本の経路を分断するだけで、得るものがありません。
- **BE-0225 のプロジェクトレジストリの永続化まで本項目の責務にする。** スコープの広げすぎとして
  見送りました。BE-0225 はすでに「どのプロジェクトがどの source レコードを指すか」と「そのレコー
  ドをどこに保存するか（データベース行かローカル JSON か）」を所有しています。本項目の役割はもっ
  と狭く、すでに耐久性を持つ `sha256` が指すバイト列を、どのレプリカからでも解決できるようにする
  ことだけです。両者を束ねると、BE-0225 がすでに単独で実装しているレジストリの関心事に、オブジェ
  クトストレージの関心事を結合してしまいます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 1 — `BAJUTSU_SERVER_STORE` が設定されているとき、`bind_upload_config` が稼働中の config を
      切り替える**前**に、アップロードされた生の zip をプロジェクトの org プレフィックス配下に
      sha256 で鍵付けしてオブジェクトストレージへ永続化する。書き込みが失敗した場合は、バインド
      する前に展開済みローカルディレクトリを削除してリクエストを失敗させ、バインドしてから
      ロールバックする経路は作らない。
- [ ] 2 — `activate_project` に、`kind == "upload"` のときの既存の `409` より先に試す、オブジェク
      トストレージからの取得・展開フォールバックを追加し、materialize のたびに BE-0073 の
      zip-slip・zip-bomb・パス封じ込めの検証を再実行する。
- [ ] 3 — `BAJUTSU_SERVER_STORE` を設定していない場合の挙動（既存の `409` を含む）が変わらないこ
      とを確認する。

## 参考

- [BE-0073 — config・シナリオ・アプリバイナリを zip でまとめてアップロードし Web UI から実行す
  る](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) — 本項目が耐久性
  を持たせる、一時的なアップロード経路。内容アドレス方式の鍵として再利用する sha256 の証跡ハッ
  シュ。
- [BE-0063 — Git リポジトリ + ref から config（とシナリオ一式）を読み込
  む](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) — 本項目がアップロードに対し
  てなぞる、耐久性のある origin の手前にローカルキャッシュを置く形。
- [BE-0204 — サーバー側オブジェクトストレージの GCS 対
  応](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support-ja.md) — 本項目の永
  続化と materialize が読み書きする `BAJUTSU_SERVER_STORE` / `ObjectStore` の seam。
- [BE-0108 — ホスティング時は config の取得元をアップロードと Git だけに絞
  る](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
  — アップロードが、ホスト型デプロイでしっかり支えるべき 2 つの config source のうち 1 つである
  理由。
- [BE-0225 — serve の config プロジェクトハブ（登録・一覧・切替・実
  行）](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)（実装済み） — その
  `upload` 種別のプロジェクト登録が、本項目が塞ぐ隙間（`activate_project` の `409`）にすでにぶつ
  かっている利用者。
- [BE-0015 — Web UI の公開ホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)
  — 本項目のレプリカ間解決が対象とする、ステートレスでオートスケールするレプリカのトポロジ。
- [BE-0184 — serve の AI プロバイダー設定を再起動をまたいで永続化す
  る](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings-ja.md)
  — 設定（シナリオの内容ではなく）のための、別に追跡されている姉妹の隙間。本項目では明示的にスコ
  ープ外です。
- `bajutsu/object_store.py`（`ObjectStore`、`object_store_from_uri`、`upload_tree`）、
  `bajutsu/serve/operations/upload.py`（`bind_upload_config`）、
  `bajutsu/serve/operations/projects.py`（`activate_project`、`_KIND_TO_SOURCE`）、
  `bajutsu/serve/project_registry.py`（`ProjectRegistry`）、
  `bajutsu/serve/server/object_store.py`（`org_prefix`、`artifact_prefix`/`scenario_prefix`/`baseline_prefix`）、
  `bajutsu/serve/uploads.py`（`extract_bundle`）、
  `bajutsu/serve/state.py`（`uploads_dir`、`bind_upload`/`release_upload`）。
