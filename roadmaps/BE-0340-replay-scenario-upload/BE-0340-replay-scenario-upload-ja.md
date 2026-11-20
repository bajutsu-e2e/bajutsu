[English](BE-0340-replay-scenario-upload.md) · **日本語**

# BE-0340 — シナリオファイルを Replay へ直接アップロードする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0340](BE-0340-replay-scenario-upload-ja.md) |
| 提案者 | [@akira-matsuda](https://github.com/akira-matsuda) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0340") |
| 実装 PR | [#1653](https://github.com/bajutsu-e2e/bajutsu/pull/1653) |
| トピック | serve Web UI への CLI 機能の取り込み |
| 関連 | [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)、[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)、[BE-0273](../BE-0273-serve-replay-scenario-viewer/BE-0273-serve-replay-scenario-viewer-ja.md) |
<!-- /BE-METADATA -->

## はじめに

serve Web UI の **Replay** タブは、束縛中の config が持つターゲットの scenario scope にあるシナリオ
を実行します。scenario scope は、ローカルでは scenarios ディレクトリを指します。ホスト型デプロイでは、
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) のプロジェクトごとの
ストレージを指します。Replay には、この scope へシナリオを足す手段がありません。

この提案は、Replay にアップロード操作を追加します。手元の `.yaml` シナリオファイル、または複数をまと
めた `.zip` を選ぶと、そのまま scope へ配置されます。配置したシナリオは、その場で選択・実行できます。

アップロードの対象は、常にすでに開いている config です。config を新しく束縛する手段は増やしません。
同名のファイルはその場で上書きし、UI には上書きが起きたことを表示します。

## 動機

Replay のシナリオ一覧は、常に `GET /api/scenarios` から届きます（`bajutsu/serve/operations/reads.py`）。
このエンドポイントは、内部で `state.for_org(org).scenarios.scope(target)` を呼びます。config が束縛
されていなければ、この呼び出しは scope として `None` を返します。そのため一覧はエラーにならず、黙っ
て空になります。

`"open a config first"` という明示的なエラーを返すのは、`start_run` と `start_record` です。この 2
つは、シナリオを実行・作成する操作です（`bajutsu/serve/operations/dispatch.py`）。

現状、この scope にシナリオを足す経路は 3 つあります。

- サーバのファイルシステムへ手作業で置く経路です。ホスト型デプロイでは使えない、ローカル限定の手段で
  す（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）。
- `record` を実行し、AI にシナリオを書かせる経路です。
- zip／compose のアップロード一式を使って、config 全体を差し替える経路です（[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)
  ／ [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)）。

3 つ目の経路は、よくある場面には大がかりすぎます。ローカル、Git 由来、以前のアップロードのいずれかで、
config がすでに開いているとします。そこへ、手で書いたシナリオファイルや他所から持ち込んだシナリオフ
ァイルを、1 つか数個だけ足す、あるいは差し替える場面です。

compose 経路（BE-0268）は、config を束縛し直す意図がなくても `config` アーティファクトを求め続けます。
`bind_composition` は、それを持たないリクエストを受け付けません（`bajutsu/serve/operations/upload.py`）。
旧来の bundle アップロード（BE-0073）は、稼働中の設定をまるごと捨てます。どちらの経路も、「開いてい
るものにシナリオを足す」操作ではなく、「別のプロジェクトを開く」操作です。

このギャップは、ホスト型デプロイで最も大きくなります。オペレータがサーバのファイルシステムに触れられ
ず、ファイルを直接置けないためです。`record` による AI オーサリングも、あらゆるシナリオに向くわけで
はありません。Bajutsu の外ですでにレビュー済み・調整済みのシナリオを、Replay に届けるためだけに AIで
書き直させる理由はありません。すでに開いている config に絞ったアップロード操作は、config の束縛には
一切触れずに、このギャップを埋めます。

## 詳細設計

どちらのアップロード経路も、既存の `ScenarioScope`／`ScenarioStore` の seam（`bajutsu/serve/scenarios.py`）
を通ります。`save_scenario`、`start_run`、`start_record` は、すでにこの seam を共有しています。その
ため、[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) の
パス閉じ込めと BE-0015 の org スコープは、そのまま効き続けます。

どちらの経路も、新しい信頼境界を開きません。すでに束縛済みの config が開いた scope へ、新しい配置手
段を足すだけです。どちらも `state.config` が束縛済みで、有効な `target` があることを必要とします。
scope の解決に失敗したとき、単体ファイルの経路は `save_scenario` のエラーをそのまま受け継ぎます。エ
ラーの文言は `"path must be a *.yaml under the scenarios dir"` です。新しい zip エンドポイントも、同
じ場面で自前の同等なエラーを返します。この提案は、config を束縛・差し替える手段を増やしません。

ロールベースアクセス制御（RBAC、BE-0015 7c-2）は、`ScenarioScope` の seam とは別の仕組みです。RBACに
は、それ自体の更新が要ります。`bajutsu/serve/authz.py` は、状態を変える `POST` を明示的な許可リスト
（`_ADMIN_PATHS`／`_EDITOR_PATHS`）で守っています。どちらにも載っていないパスは、ゲートなしで通りま
す。

単体ファイルの経路は、既存の `POST /api/scenario` を再利用します。このパスはすでに `_EDITOR_PATHS`に
入っているため、変更は要りません。新しい `POST /api/scenarios/upload` は、まだ存在しないルートです。
この提案は、このルートを `/api/scenario` と同じ階層で `_EDITOR_PATHS` に加えます。放置すれば生まれて
いたはずの抜け穴を、これで防ぎます。

- **既存の単体シナリオ保存経路に、上書きの通知を足す。** `POST /api/scenario` は、任意の名前の
  `.yaml` ファイルをターゲットの scope へ書き込みます（`bajutsu/serve/operations/reads.py` の
  `save_scenario`）。この応答には、保存が既存ファイルを置き換えたという合図が、今のところありません。
  この合図を足します。`scope.save(ref, text)` の前に `scope.read(ref)` を呼び、すでに内容が返るかど
  うかを確かめます。返れば応答は `overwritten: true` を報告し、そうでなければ `false` を報告します。
  `save_scenario` を呼んでいる Author エディタの Save ボタンは、この新しい応答フィールド以外、挙動が
  変わりません。

- **Replay タブに、単体ファイルの「シナリオをアップロード」操作を置く。** Replay の Form にあるシナ
  リオピッカーの隣に、アップロード操作を追加します。マークアップは `bajutsu/templates/serve.html.j2`
  に置きます。配線は `bajutsu/templates/serve.panels.mjs` に置きます。compose ピッカーが使っているフ
  ァイル入力のパターン（`cmp-scenarios-file`）を踏襲します。

  この操作は、選んだ `.yaml` ファイルのテキストをクライアント側で読み取ります。そのテキストを、
  Author エディタの Save ボタンが使う `/api/scenario` エンドポイントへ送信します。パスには、ファイル
  自身の名前を使います。アップロードで scope に着地したシナリオは、Save や `record` で着地したシナリ
  オと区別が付きません。

  成功すると、新しい `overwritten` フラグから「追加しました」「上書きしました」のいずれかを報告しま
  す。続けてシナリオ一覧を再読み込みし、そのファイルをその場で選択・実行できるようにします。configと
  ターゲットの両方が選ばれるまでは、操作を有効にしません。他の Replay の操作も、すでに同じ前提を課し
  ています。

- **シナリオ一式に対する zip アップロード。** 新しいエンドポイント `POST /api/scenarios/upload` は、
  生のリクエストボディを受け取ります。`/api/upload` や `/api/artifacts/*` と同じく、メインのリクエス
  トループの外で処理します。指定したターゲットの scope に向けて、1 つ以上の `.yaml` を含む `.zip` を
  受け付けます。

  新しいオペレーション `upload_scenarios` は、`save_scenario` と同じ scope を解決します。そのうえで、
  アーカイブの最上位にある `*.yaml` エントリだけを読みます。scope にサブディレクトリという概念はあり
  ません。`list_scenarios` が平坦に `glob("*.yaml")` するのと揃えています。

  抽出中は、2 つの封じ込めを効かせます。1 つは、シナリオのテキストに見合った資源の上限です。エントリ
  数、エントリごとのサイズ、合計サイズの 3 つを制限します。いずれも、アプリバイナリ向けの BE-0073 の
  bundle の上限よりはるかに小さくします。もう 1 つは、`bajutsu/serve/uploads.py` が bundle にすでに
  適用している zip-slip の封じ込めです。

  すべてのエントリは、書き込む前に `load_scenario_file` で解析します。解析に失敗するエントリが 1 つ
  でもあれば、zip 全体を打ち切り、何も書き込みません。不備のあるバッチが、部分的な上書きを残すことは
  ありません。同じ「どれかに触れる前にすべてを確かめる」パターンは、`start_run_set` がシナリオの
  fan-out にすでに適用しています（`bajutsu/serve/operations/dispatch.py`）。

  成功すれば、各ファイルを `scope.save(name, text)` で書き込みます。応答は、各名前を「新規作成」か「上
  書き」として一覧します。このルートは `bajutsu/serve/authz.py` の `_EDITOR_PATHS` に加え、
  `/api/scenario` と同じ RBAC の階層に置きます。ホスト型デプロイの viewer からは届きません。

  Replay の Form には、もう 1 つのアップロード操作を足します。あるいは、選んだファイルの拡張子で振り
  分ける同一の操作にします。この操作は `.zip` をこのエンドポイントへ送ります。描画する新規作成／上書
  きの要約は、単体ファイルの経路と同じです。

- **テストとドキュメント。** `save_scenario` の新しい `overwritten` フィールドを、ユニットテストで確
  かめます。同じ ref への 1 回目の保存では false、2 回目では true になることを確認します。
  `upload_scenarios` もユニットテストで確かめます。
  - 1 つの zip に入れた 2 つの正しいシナリオが両方とも配置され、`list_scenarios` にも両方現れること。
  - 解析に失敗するエントリが 1 つでもあれば、何も書き込まれないこと。
  - zip-slip のエントリとサイズ超過のエントリが、どちらも拒まれること。

  新しいアップロード操作には、`data-testid` を付けます。既存の Replay フィクスチャの隣に、dogfood の
  E2E シナリオも追加します。web backend にはネイティブのファイル選択を操作する step がありません。そ
  のため、このシナリオは操作が存在し、有効になっていることだけを確かめます。追加、上書き、
  all-or-nothing、zip-slip、サイズ超過の挙動は、Python のユニットテストと HTTP テストが最初から最後
  まで確かめます。

  `docs/architecture.md` とその `docs/ja/` の対応も更新します。Replay が config を束縛し直すことなく、
  束縛済み config の scenario scope へ直接シナリオを配置できることを記します。

## 検討した代替案

- **config を一切束縛せずにシナリオを実行する。** シナリオ自体は backend 非依存です。しかし実行には
  ターゲットの backend、デバイス、アプリのパスが要り、素のシナリオファイルはそのどれも持っていません。
  したがってアップロードは、単独では成立しません。すでに束縛済みの config のターゲットに対して解決し
  ます。この項目は、シナリオの実行に必要なものを変えません。変えるのは、シナリオファイルがその情報を
  すでに持つ scope へ届く経路だけです。

- **BE-0268 の compose／アーティファクトアップロードの経路を、この用途にも使い回す。**
  `POST /api/artifacts/scenarios` は、アップロードした `.zip`／`.yaml` をコンテンツハッシュでキャッ
  シュ済みです。しかし `bind_composition` は、オペレータに config を束縛し直す意図がなくても、
  `config` アーティファクトを求め続けます。それを持たないリクエストは、受け付けません。

  この経路を拡張するなら、scenarios のレグへ届くためだけに、当て馬の config レグを供給する仕組みを作
  ることになります。config がすでに束縛済みであることを前提にした専用のエンドポイントを足すより、手
  間が増えます。この提案は、まさにその前提が成り立つ場面のためにあります。

- **ネストしたディレクトリ構成の zip を受け付ける。** scope には、今のところサブディレクトリという概
  念がありません。`list_scenarios` はディレクトリを平坦に glob します。ネストしたエントリを入れても、
  一覧には現れません。最上位の `*.yaml` だけを平坦に読む今回の設計は、既存のモデルとそのまま合います。
  ここを見直す価値が出るのは、scope 自体が他の場所でサブディレクトリを持つようになったときです。それ
  までは見直しません。

- **zip の一部エントリが解析に失敗したとき、成功した分だけ書き込む。** all-or-nothing を選び、この案
  は見送りました。バッチが部分的に着地すると、scope は、アップロードした本人が求めていない状態になり
  ます。その状態は、あとから追いにくくなります。`start_run_set` も、同じ理由で、どれかに触れる前にす
  べての項目を確かめています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `save_scenario` の応答に `overwritten` フィールドを足す。
- [x] Replay の Form に、単体ファイルの「シナリオをアップロード」操作を足し、`POST /api/scenario` へ
      送信する。
- [x] `.zip` シナリオ向けに `POST /api/scenarios/upload` エンドポイントと `upload_scenarios`
      オペレーションを足す。書き込み前の確認と、zip-slip／サイズの上限を備える。
      `bajutsu/serve/authz.py` の `_EDITOR_PATHS` にルートを加える。
- [x] Replay の Form に zip アップロードの操作を足し、単体ファイルの経路と新規作成／上書きの要約表示を
      共有する。
- [x] 両方の経路のユニットテスト、`data-testid`、dogfood の E2E シナリオを足す。dogfood のシナリオは、
      実際にアップロードを実行するのではなく、操作が存在し、有効になっていることだけを確かめます。web backend には
      ネイティブのファイル選択を操作する step がないためです。新規作成／上書き／all-or-nothing／
      zip-slip／サイズ超過の挙動は、Python のユニットテストと HTTP テストが最初から最後まで確かめます。
- [x] `docs/architecture.md` とその `docs/ja/` の対応を更新する。

ログ：

- `overwritten` 応答フィールドを追加しました。単体 `.yaml` は既存の `POST /api/scenario` でアップロード
  できます。複数本を束ねた `.zip` は新しい `POST /api/scenarios/upload` でアップロードできます。
  `_EDITOR_PATHS` への追加、Replay の Form の操作、ユニットテストと HTTP テスト、dogfood のシナリオ、
  両言語のドキュメント更新も併せて実装しました。

## 参考

- [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)：config・
  シナリオ・バイナリをまとめた旧来の zip bundle アップロード。この提案が小さな規模で踏襲する、
  zip-slip とシナリオのテキスト向けの資源上限の扱いの元。
- [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)：
  *検討した代替案* で見送った compose／アーティファクトアップロードの経路。`config` アーティファクトを
  今も求め続けるため。
- [BE-0273](../BE-0273-serve-replay-scenario-viewer/BE-0273-serve-replay-scenario-viewer-ja.md)：
  Replay のシナリオビューア。この提案のアップロード操作が加わる、同じ Replay の Form への直近の追加。
- [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)：
  この提案が再利用する `ScenarioScope` の seam がすでに備えている、パス閉じ込めの保証。
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)：Web UI の
  ホスト型公開。この提案が置き換えるファイルシステムの回避策を、オペレータがそもそも使えないデプロイ
  形態。
- 既存エンドポイント：`POST /api/scenario`（`save_scenario`）と `GET /api/scenarios`
  （`list_scenarios`）。いずれも `bajutsu/serve/operations/reads.py`。`ScenarioScope`／
  `ScenarioStore` の seam は `bajutsu/serve/scenarios.py`。`start_run_set` の、送り出す前に確かめる
  パターンは `bajutsu/serve/operations/dispatch.py`。
