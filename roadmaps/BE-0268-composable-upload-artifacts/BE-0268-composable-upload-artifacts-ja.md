[English](BE-0268-composable-upload-artifacts.md) · **日本語**

# BE-0268 — config・シナリオ・アプリバイナリを独立した content-addressed な成果物として個別にアップロードし run ごとに合成する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0268](BE-0268-composable-upload-artifacts-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0268") |
| トピック | config の取得元 |
| 関連 | [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md), [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) |
<!-- /BE-METADATA -->

## はじめに

いまのホスト型 `bajutsu serve` は、実行可能なテスト一式を **1 本の合体 `.zip`** として取得します。`bajutsu.config.yaml` とそのシナリオツリー、ビルド済みアプリバイナリを一緒にまとめ、単一の `POST /api/upload` でアップロードし、ひとまとまりとして展開する形です（[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)）。この 1 つの成果物は、変更頻度がまったく異なる 3 つのものを結合しています。**バイナリ**は大きく（数十から数百 MB）ビルドのたびに変わり、**シナリオツリー**はテキストで小さくオーサリングの編集のたびに変わり、**config** はテキストで小さくほとんど変わりません。

本提案は、この単一のアップロードを **個別にアップロードできる 3 つの content-addressed な成果物**（`config`・`scenarios`・`binary`）に分解し、run が選んだ三つ組を、決定論的なランナーがすでに消費しているとおりの一貫したツリーへ **合成** するようにします。各成果物は、[BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md) がすでに追加したオブジェクトストアに sha256 で格納します。そのため、変わっていない成果物の再アップロードは no-op になり（変わったものだけをアップロードすればよい）、`(config, scenarios, binary)` の任意の組み合わせを新規アップロードなしで組み立てられます。これはあくまで config とシナリオとバイナリのツリーを **取得** する手段にとどまり、シナリオスキーマ、ランナー、ドライバ、決定論ゲートには一切手を触れず、LLM もどこにも足しません。

本項目は、実装済みの 3 項目を素直に延長したものです。[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) の合体バンドルを分解し、[BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md) の content-addressed なオブジェクトストアを成果物ごとの格納先として再利用し、[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) の `upload` 種別の config-source レコードを「1 本の zip」から「合成された三つ組」へ広げます。

## 動機

合体バンドルは正しい *下限* です。実行できる最小のものになっています。しかし、チームの規模とともに大きくなる 2 つのコストを強います。

1. **変更のたびにバイナリごとバンドル全体を送り直す。** いまは 1 つのシナリオ YAML を 1 行直すにも、変わっていない大きなビルド済みバイナリを含め、一式を再 zip して再アップロードします。実ネットワーク越しのホスト型 serve では、このアップロードをバイナリが支配します。テキストだけの編集にこれを再び払うのは純粋な無駄です。3 つはそれぞれ独立したライフサイクルを持つので、個別にアップロードできるべきです。クライアントは実際に変わったものだけを送り、変わっていないバイナリ（同じ sha256）はそもそも回線に載せるべきではありません。

2. **再アップロードなしに組み合わせられない。** テストでよくある要求に **組み合わせマトリクス** があります。同じ回帰シナリオを 2 つのバイナリビルド（A/B、あるいは last-known-good とリリース候補）に対して走らせたり、複数のシナリオセットを 1 つのバイナリに対して走らせたりする形です。不透明な 1 本の zip では、多くのマスがバイナリやシナリオセットを共有しているのに、マトリクスのマスごとに個別のフルアップロードになります。成果物が content-addressed で run 時に合成されるなら、新しいマスはすでに格納済みの成果物に対する新しい *三つ組* にすぎず、アップロードは発生しません。

この 2 つが可能にする、3 つ目の組織上の利点があります。3 つのアップロードは **誰が何を持つか** にきれいに対応します。CI ジョブはビルドしたばかりのバイナリを push し、テスト作成者は自分のブランチからシナリオツリーを push し、運用者は config を固定します。アップロードを分離すると、各担当がそれぞれのタイミングで、それぞれの資格情報を通じて、一枚岩の再 zip を協調させずに push できます。

[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) の *検討した代替案* は、「config の YAML だけをアップロードしツリーは上げない」を正しく却下しています。`scenarios` と `appPath` が **相対パス** であり、ツリーもバイナリも伴わない config は実行できないからです。本提案はこの却下を蒸し返しません。むしろ *解決* します。3 つの部分は run 時にはやはりすべて存在しており、ただ **別々に到着して別々に格納され、run の前に 1 つのツリーへ合成し戻される** だけです。BE-0073 が守る相対パスの不変条件は、弱めるのではなく合成のステップで保たれます（*詳細設計* を参照）。

これは prime directive の内側にとどまります。合成は決定論的な配管です。content-addressed な 3 成果物を confined なツリーへ材料化し、config の相対パスをそのツリーに対して解決するだけで、合否は機械的なアサーションだけが計算し、`run`/CI の経路のどこにも LLM は入りません（directive 1）。一貫したツリーを組めない合成（どのバイナリ成果物も埋めない `appPath` を持つ config など）は、推測せずに **決定論的に失敗** します（directive 2）。そして仕組みは app-agnostic です。config が各成果物の配置先の唯一の真実なので、合成のステップはアプリごとに分岐しません（directive 3）。

## 詳細設計

作業は MECE に 5 つの単位へ分かれます。成果物ごとのモデル、合成のステップ、アップロード API、プロジェクトの束ね方、UI です。

### 1. content-addressed な 3 つの成果物の種別

**成果物** は 3 つの種別のいずれかで、それぞれ自身のバイトの sha256 で、[BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md) が導入したオブジェクトストア（`bajutsu/object_store.py`）に格納します。格納先は、あらゆる兄弟ストアがすでに使っている org ごとの prefix 方式（`bajutsu/serve/server/object_store.py` の `org_prefix` / `upload_prefix`）の下に、3 つが衝突しないよう種別ごとのサブ prefix を足したものです。

- **`config`**：単一の `bajutsu.config.yaml`（そのバイト。小さい）。
- **`scenarios`**：シナリオのサブツリー。YAML ツリー（`scenarios/…`、config が参照する場合は `baselines/` や `setup/` も）を `.zip` として運びます（テキストで小さい）。
- **`binary`**：ビルド済みアプリ成果物（`.app.zip` / `.ipa`。大きい）。BE-0073 のバンドルが運んでいたものそのもので、いまはそれ単体で格納します。

content-addressing により、「変わったものだけをアップロードする」と「変わっていないバイナリでは no-op」の性質がそのまま得られます。ストアはすでにキーで重複排除しており、`ObjectStore.exists(key)` でクライアント（や UI）が「この sha はもう格納済みか」を確かめ、格納済みならアップロードを省けます。これは BE-0243 と worker アップロード経路（`bajutsu/serve/operations/worker_uploads.py`、`presign.py`）がすでに頼っている `exists` / `put_bytes` / `presigned_put_url` の seam と同じものです。

### 2. 合成：3 成果物を 1 つのツリーへ材料化する（BE-0073 の不変条件を保つ）

BE-0073 と [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) がともに守る要点は変わりません。ランナーは、config の相対パス `scenarios` / `appPath` / `baselines` / `setup` が解決される **単一の自己完結したツリー** を必要とします（`bajutsu/serve/operations/upload.py` の `_validate_bundle_config` と `Effective.rebased`）。そこで **合成** は成果物 sha の三つ組 `(config_sha, scenarios_sha, binary_sha)` とし、新しい `materialize_composition` ステップがそれらを、fresh で serve 所有の confined なディレクトリへ組み立てます。BE-0073 の `materialize_bundle` の兄弟にあたります。

1. `config` 成果物のバイトを、ツリーのルートに `bajutsu.config.yaml` として書き出す。**config がレイアウトの唯一の真実** であり、自身の相対的な `scenarios` と `appPath` フィールドによって、他の 2 成果物がどこに置かれるべきかをちょうど名指しします。
2. `scenarios` の zip を、config の `scenarios` パスが解決されるように展開する（例：`./scenarios`）。BE-0073 が展開に課すのと同じ zip-slip / zip-bomb の上限を課します。
3. `binary` 成果物を config の `appPath` に置く（`.app.zip` からの `.app` ディレクトリ、または `.ipa` をそのまま）。「バイナリはどこか」にすでに答えている唯一のフィールドです。

そのうえで、組み立てたルートに対して BE-0073 の既存の `_validate_bundle_config` を走らせます。各ターゲットのパスフィールドはツリー内に confined でなければならず、さらに本設計が加える新しい整合性チェックとして、config の `appPath` と `scenarios` が **供給された成果物によって実際に埋まっている** ことを確かめます。どのバイナリ成果物も埋めない `appPath`、あるいはどのシナリオ成果物も埋めない `scenarios` を名指す config を持つ三つ組は、bind 時に具体的なエラーで拒否し、半分だけ材料化されたツリーに対して走らせることはしません（directive 2）。下流のすべて（`resolve()`、ランナー、ドライバ、アサーション評価器、レポート）は、BE-0073 のバンドルやローカルの `--config` run が通るのとバイト単位で同じ経路です。新しいのは *組み立て* だけです。

**来歴（provenance）。** run の `manifest.json` には **3 つの sha の三つ組**（と各成果物の表示用ファイル名）を記録し、BE-0073 の単一 zip sha の来歴を拡張します。「この run は何を実行したか」が 3 つの部分それぞれのバイトまで答えられるようになり、合体 zip が与えたものより強い保証であり、組み合わせマトリクスの監査の起点になります。

### 3. アップロード API（種別ごと、追加的。合体 zip は糖衣として残す）

認証付きの 3 つのアップロード経路です。それぞれ生のボディをメモリ上限付きで一時ファイルへストリームし（BE-0073 のパターン）、格納した **sha256** を返します。

- `POST /api/artifacts/config`：ボディは YAML のバイト。
- `POST /api/artifacts/scenarios`：ボディはシナリオツリーの zip。
- `POST /api/artifacts/binary`：ボディは `.app.zip` / `.ipa`。

それぞれ [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) のトークン認証の背後にある admin か editor のアクションで、upload をすでに統べているのと同じ [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md) のホスト型ソース許可リストで審査します。付随する `HEAD`/exists チェック（あるいは worker アップロード経路がするように `presigned_put_url`）により、クライアントは格納済みのバイトの再送を省けます。これが「変わっていないバイナリを再アップロードしない」の裏づけです。既存の合体 `POST /api/upload` は **残し**、糖衣として読み替えます。サーバ側で zip を同じ 3 つの content-addressed な成果物へ分解して合成を組むので、1 回のドロップのフローはそのまま動き、合体でも分割でもすべてのアップロードが 1 つの内部表現に着地します。

### 4. プロジェクトの束ね方（BE-0225 の `upload` ソースレコードを広げる）

[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) はプロジェクトを、判別可能な config-source レコード（`kind` とロケータ）に束ねます。その `upload` 種別は今日、単一のバンドル sha を指しています（`activate_uploaded_project` がその 1 本の zip を fetch して展開する。`bajutsu/serve/operations/upload.py`）。本項目は `upload` のロケータを、単一 sha から **三つ組** `(config_sha, scenarios_sha, binary_sha)` へ広げます。すると `activate`/`run` は `materialize_bundle` の代わりに `materialize_composition` を呼びます。後方互換は形の判定です。従来の単一 sha の `upload` レコードは引き続き `materialize_bundle` を通って解決し、三つ組は新しいステップを通ります。束ねたプロジェクトの一脚だけ（新しいバイナリ sha、同じシナリオと config）の更新はレコードの編集であってアップロードではありません。組み合わせマトリクスと差分更新の利点がプロジェクトの水準で立ち現れるのはここです。

### 5. UI

**Open config → Upload** の面に **3 つのドロップゾーン**（Config・Scenarios・Binary）を足します。それぞれ、いま選ばれている成果物（ファイル名と短い sha）を **前回のを再利用** の導線とともに表示するので、ユーザは変わったものだけを再ドロップし、sha がすでに格納済みのゾーンについてはクライアントがアップロードを省きます。小さな **合成ピッカー** で、すでにアップロード済みの成果物から三つ組を組み立て（config vX、scenarios vY、binary vZ を選ぶ）て実行できます。組み合わせマトリクスを N 回のフルアップロードではなく数クリックにする面です。run は引き続き同じジョブ機構（`bajutsu/serve/jobs.py`）を流れて History に出て、[BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) のエクスポートが往復をそのまま閉じます。

### 決定論・ゲート・app-agnostic

- **LLM なし、合否に影響なし。** 合成は決定論的な `run` の前の取得と組み立てで、合否は機械のみのままです。directive 1 と 2 は構成上成り立ちます。
- **Linux でテスト可能。** 成果物ごとの格納、exists による重複排除チェック、シナリオとバイナリの成果物への zip-slip / zip-bomb の上限、合成の組み立て、整合性の検証は、すべて純粋なパッケージングと配管であり、fixture の成果物に対して既存の Linux ゲートで単体テストできます。Simulator は不要です。実際のアプリの *インストールと実行* だけが Mac を要し、これは任意の iOS run と同じです。
- **app-agnostic。** config がレイアウトの唯一の権威です。合成ステップは config から `appPath` / `scenarios` を読み、アプリごとに分岐しません。アプリごとの違いは合成された config の `targets.<name>` にとどまります。

### バックエンドのスコープ（iOS を先行、web は注記）

主役は **iOS** です。`binary` 成果物は config の `appPath` に置かれ Simulator にインストールされる `.app.zip` / `.ipa` で、BE-0073 とちょうど同じです。web（Playwright）バックエンドには「アプリバイナリ」がありません。その対応物は [BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md) 経由で配信する束ねた **静的サイト** で、BE-0073 がすでに先送りしたのと同じ後続です。3 成果物への分割は形として backend-neutral であり（依然として「部分に分けて届ける config ツリー」です）、仕組みは iOS を決め打ちしません。web の変種（`binary` の代わりに `site` 成果物）は後のスライスです。

### スコープ外

- **ソースからのアプリのビルド。** BE-0073 と [DESIGN §1](../../DESIGN.md) のとおり、Bajutsu はビルド済み成果物を受け取ります。`binary` 成果物はビルドの成果物を運ぶのであって、ビルドはしません。
- **成果物のバージョン管理ライブラリや保持ポリシー。** content-addressed な格納は自然に重複排除して再利用を可能にしますが、名前つき成果物バージョンの *カタログ化* とその保持は、Git ソース（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）と [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) の領分です。本項目は成果物を格納し三つ組を合成するのであって、閲覧できるバージョンレジストリは足しません。
- **マルチテナントの実行分離。** テナントごとの Simulator や egress 制御は、引き続き [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) と [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) の領分です。

## 検討した代替案

- **単一の合体 zip を維持する（現状、[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)）。** *唯一の* 選択肢としては却下します。変更頻度がまったく異なる 3 成果物を 1 回のアップロードへ結合し、1 行のシナリオ編集に大きなバイナリの再送を強い、バイナリ×シナリオの組み合わせマトリクスのマスごとに個別のフルアップロードにしてしまいます。合体 zip は、3 成果物へ分解する糖衣として残しますが、もはや唯一の形ではありません。
- **バンドル zip のクライアント側差分/パッチ。** 前回のバンドルに対する差分をアップロードする案。却下します。バイナリの差分は壊れやすく形式依存で、独立したライフサイクルを持つ 3 つではなく依然 1 つの不透明な成果物をモデル化し、組み合わせマトリクスへのきれいな道もありません（差分は 1 つのベースに対する相対で、三つ組の自由な選択ではありません）。content-addressed な成果物まるごとのほうが単純で、しかも重複排除がただで効きます。
- **config だけをアップロードしツリーは上げない**（BE-0073 の元の却下）。蒸し返しません。本設計は 3 つの部分を run 時にすべて存在させて合成するので、BE-0073 の相対パスの異論は退けるのではなく満たします。
- **レイアウトを記述する独自のマルチパート manifest 形式。** BE-0073 が独自形式を却下したのと同じ理由で却下します。config が *そのまま* レイアウトの記述です。自身の `scenarios` / `appPath` がすでに各成果物の配置先を言っています。config を合成の権威として、[BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md) のオブジェクトストアの上で再利用すれば、新しい形式は何も要りません。
- **BE-0073 か BE-0243 に畳み込む。** 却下します。BE-0073 は合体バンドルの取得で、BE-0243 はそのバンドルの durable な格納で、どちらも実装済みで安定しています。分解と run 時合成は、独自の API、プロジェクトレコードの形、UI を持つ別個の機能であり、閉じた項目の後追い編集ではなく `関連` として素直につなぐのが妥当です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 1 — 成果物ごとのモデル：BE-0243 のオブジェクトストア上の 3 つの content-addressed な種別（`config` / `scenarios` / `binary`）、種別ごとの prefix、`exists` ベースの重複排除チェック。
- [x] 2 — 合成：config をレイアウトの権威として三つ組を confined なツリーへ組み立てる `materialize_composition`。BE-0073 の zip-slip/zip-bomb の上限と `validate_bundle_config` を再利用し、整合性チェック（`appPath`/`scenarios` が供給された成果物で埋まること）を加える。`manifest.json` の三つ組 sha の来歴。
- [x] 3（一部）— アップロード API：sha を返す `POST /api/artifacts/{config,scenarios,binary}` と、重複排除のための `GET /api/artifacts/exists` を実装しました。合体 `POST /api/upload` を 3 分解の糖衣として読み替える部分は今回のPRに**含みません**。すでにリリース済みでテストも充実したコードへの純粋な内部表現の変更であり、新しい利用者向けの機能を追加しないため、後続のPRへ切り出しています。
- [x] 4 — プロジェクトの束ね方：BE-0225 の `upload` ソースレコードを単一 sha から三つ組（`{"artifacts": {"config", "scenarios", "binary"}}`。従来の単一 `sha256` 形式とは判別可能）へ広げ、従来のレコードもそのまま解決できるようにしました。一脚ごとの取得・キャッシュに対応しています。
- [ ] 5 — UI：前回再利用とクライアント側の重複排除省略つきの 3 ドロップゾーン、組み合わせマトリクス用の合成ピッカー。上のバックエンド中核とは切り離し、別途レビュー可能なPRへ先送りします。

### ログ

- 2026-07-14 — 単位 1、2、4、および単位 3 の追加部分（バックエンドの中核）を実装しました。単位 5 の
  UI と単位 3 の残り部分は、上のチェックリストのとおり後続PRへ先送りしています。本文書の *詳細設計*
  から外れた点が 2 つあります。1 点目は、`scenarios` 成果物を config の `scenarios` フィールドごとに
  個別展開するのではなく、合成のルート直下へそのまま展開する点です。これは、従来の合体バンドルが
  すでに `scenarios`／`baselines`／`setup` のツリーを config と並べて運んでいるやり方と一致し、
  `extract_bundle` をそのまま再利用できるぶん単純です。2 点目は、合成でバインドした場合の
  `manifest.json` の来歴が、単一の `sha256` ではなく `compositionId` と、供給された成果物ごとの
  `<種別>Sha` を報告する点です。合成 ID を単一の `sha256` として上書きすると、1 つの成果物のバイト列
  に対して検証可能なハッシュであるかのように誤解を招くため、そうしませんでした。レビューで見つかった
  2 点も直しています。`scenarios` 成果物のzipに `bajutsu.config.yaml` という名前のエントリが紛れて
  いた場合、展開の順序次第では信頼済みの config を上書きしかねなかったため、config の書き込みを
  scenarios の展開より後に行うよう順序を入れ替えました。また `GET /api/artifacts/exists` の管理者
  権限ゲートが、GET リクエストには効かない実装になっていたため、`GET /api/config/content` と同じ
  早期リターンの形で正しくゲートされるようにしました。

## 参考

- [CLAUDE.md](../../CLAUDE.md)、[DESIGN §1](../../DESIGN.md)（Bajutsu はビルド済みアプリを受け取り、ビルドはしない）、[DESIGN §2](../../DESIGN.md)（AI は判定しない。決定論優先。テストごとにクリーンな環境）。
- [BE-0073 — config・シナリオ・アプリバイナリを zip でまとめてアップロード](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)：本項目が分解する合体バンドル。再利用する相対パスの不変条件と展開・検証のガード。
- [BE-0243 — アップロードした zip の config バンドルをオブジェクトストレージへ永続化](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md)：各成果物を格納する content-addressed な `ObjectStore`（`bajutsu/object_store.py`）。
- [BE-0225 — serve の config プロジェクトハブ](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)：`upload` 種別の config-source レコードを 1 本の zip sha から三つ組へ広げる。
- [BE-0063 — Git リポジトリと ref から config（とそのシナリオツリー）を読み込む](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)：「ツリーを材料化し config をそのルートに対して解決する」兄弟の seam。
- [BE-0051 — ホスティングのための serve ハードニング](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)、[BE-0108 — ホスト型 config ソースの制限](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)：すべてのアップロード経路が背後に置くトークン認証とソース許可リスト。
- [BE-0060 — run レポートを zip でダウンロード/エクスポート](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md)：往復を閉じるエクスポート側。
- `bajutsu/object_store.py`（content-addressed なストア）、`bajutsu/serve/operations/upload.py`（`bind_upload_config` / `materialize_bundle` / `_validate_bundle_config` / `activate_uploaded_project`。合成ステップはこれらを結ぶ）、`bajutsu/serve/uploads.py`（`materialize_bundle` / `find_bundle_config`）、`bajutsu/serve/server/object_store.py`（`org_prefix` / `upload_prefix`）、`bajutsu/serve/operations/worker_uploads.py` と `presign.py`（`presigned_put_url` のアップロード seam）、`bajutsu/config.py`（`AppConfig.appPath` / `scenarios`）：本項目が触れる面。
- [docs/ja/configuration.md](../../docs/ja/configuration.md)、[docs/ja/cli.md](../../docs/ja/cli.md#serve)、[docs/ja/architecture.md](../../docs/ja/architecture.md)。
