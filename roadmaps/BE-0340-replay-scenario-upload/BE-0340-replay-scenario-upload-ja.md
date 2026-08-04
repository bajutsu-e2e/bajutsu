[English](BE-0340-replay-scenario-upload.md) · **日本語**

# BE-0340 — シナリオファイルを Replay へ直接アップロードする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0340](BE-0340-replay-scenario-upload-ja.md) |
| 提案者 | [@akira-matsuda](https://github.com/akira-matsuda) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0340") |
| トピック | serve Web UI への CLI 機能の取り込み |
| 関連 | [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)、[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)、[BE-0273](../BE-0273-serve-replay-scenario-viewer/BE-0273-serve-replay-scenario-viewer-ja.md) |
<!-- /BE-METADATA -->

## はじめに

serve Web UI の **Replay** タブが実行できるのは、束縛中の config が持つターゲットの scenario scope
に、すでに置かれているシナリオだけです。scenario scope は、ローカルでは scenarios ディレクトリを指し、
ホスト型デプロイでは [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)
のプロジェクトごとのストレージを指します。シナリオを新しく足す手段がありません。この提案は、Replay に
アップロード操作を追加します。手元の `.yaml` シナリオファイル、または複数をまとめた `.zip` を選ぶと、
そのままそのスコープへ配置され、その場で選択・実行できるようになります。アップロードの対象は常にすでに
開いている config で、config を新しく束縛する手段は増やしません。同名のファイルはその場で上書きし、UI
には上書きが起きたことを表示します。

## 動機

Replay のシナリオ一覧は常に `GET /api/scenarios` から届き、その内部で
`state.for_org(org).scenarios.scope(target)`（`bajutsu/serve/operations/reads.py`）を呼びます。
config が束縛されていなければこの呼び出しは `None` を返すため、一覧はエラーにならず、黙って空になり
ます。`"open a config first"` という明示的なエラーは、実際にシナリオを実行・作成する
`start_run`・`start_record`（`bajutsu/serve/operations/dispatch.py`）の側が返すものです。
現状、このスコープにシナリオを足す経路は 3 つです。1 つは、サーバのファイルシステムへ手作業で置く経路
で、これはホスト型デプロイでは使えない、ローカル限定の手段です
（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）。もう 1 つは
`record` を実行し、AI にシナリオを書かせる経路です。最後の 1 つは、zip／compose のアップロード一式を
使って config 全体を差し替える経路です
（[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) ／
[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)）。

3 つ目の経路は、よくある場面には大がかりすぎます。ローカル・Git 由来・以前のアップロードのいずれかで
config がすでに開いている状態で、手で書いた、あるいは他所から持ち込んだシナリオファイルを 1 つか
数個、足したい・差し替えたいだけの場面です。compose 経路（BE-0268）は、config を束縛し直す意図が
受け付けません（`bajutsu/serve/operations/upload.py`）。旧来の bundle アップロード（BE-0073）は、稼働中の設定をまるごと捨てます。どちらの
受け付けません（`bajutsu/serve/operations/upload.py`）。旧来の bundle アップロード（BE-0073）は、
稼働中の設定をまるごと捨てます。どちらの経路も「開いているものにシナリオを足す」ではなく、
「別のプロジェクトを開く」です。

このギャップは、ホスト型デプロイでもっとも大きくなります。オペレータがサーバのファイルシステムに触れられず、
ファイルを直接置けないためです。`record` による AI オーサリングも、あらゆるシナリオに向くわけでは
ありません。Bajutsu の外ですでにレビュー済み・調整済みのシナリオを、Replay に届けるためだけに AI で
書き直させる理由はありません。すでに開いている config に絞ったアップロード操作は、config の束縛には
一切触れずにこのギャップを埋めます。

## 詳細設計

どちらのアップロード経路も、既存の `ScenarioScope`／`ScenarioStore` の seam
（`bajutsu/serve/scenarios.py`）を通ります。`save_scenario`、`start_run`、`start_record` はすでにこの
seam を共有しており、
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) の
パス閉じ込めと BE-0015 の org スコープはそのまま効き続けます。どちらの経路も新しい信頼境界を開かず、
すでに束縛済みの config が開いたスコープへ、新しい配置手段を足すだけです。どちらも `state.config` が
すでに束縛済みで有効な `target` を必要とします。単体ファイルの経路は、そのスコープの解決に失敗した
とき `save_scenario` がすでに返しているエラー、`"path must be a *.yaml under the scenarios dir"` を
そのまま受け継ぎます。新しい zip エンドポイントも、同じ場面に対して自前の同等なエラーを返します。
この提案は、config を束縛・差し替える手段を増やしません。

ロールベースアクセス制御（RBAC、BE-0015 7c-2）は、上記の `ScenarioScope` の seam とは別の仕組みで、
それ自体の更新が要ります。`bajutsu/serve/authz.py` は、状態を変える `POST` を明示的な許可リスト
（`_ADMIN_PATHS`／`_EDITOR_PATHS`）で守っており、どちらにも載っていないパスはゲートなしで通ります。
単体ファイルの経路は既存の `POST /api/scenario` を再利用し、すでに `_EDITOR_PATHS` に入っているため
変更は要りません。新しい `POST /api/scenarios/upload` はまだ存在しないルートなので、この提案は
`/api/scenario` と同じ階層で `_EDITOR_PATHS` に加え、放置すれば生まれていたはずの抜け穴を防ぎます。

- **既存の単体シナリオ保存経路に、上書きの通知を足す。** `POST /api/scenario`
  （`bajutsu/serve/operations/reads.py` の `save_scenario`）は、すでに任意の名前の `.yaml` ファイルを
  ターゲットのスコープへ書き込んでいますが、その応答には、保存が既存ファイルを置き換えたという合図が
  今のところありません。この合図を足します。`scope.save(ref, text)` の前に `scope.read(ref)` を呼び、
  すでに内容が返るかどうかを確かめます。返れば応答は `overwritten: true` を報告し、そうでなければ
  `false` を報告します。今日 `save_scenario` を呼んでいる Author エディタの Save ボタンは、この新しい
  応答フィールド以外、挙動が変わりません。

- **Replay タブに、単体ファイルの「シナリオをアップロード」操作を置く。** Replay の Form にある
  シナリオピッカーの隣に、アップロード操作を追加します。マークアップは `bajutsu/templates/serve.html.j2`
  に置き、配線は `bajutsu/templates/serve.core.mjs` に置きます。compose ピッカーがすでに使っている
  ファイル入力のパターン（`cmp-scenarios-file`）を踏襲します。この操作は、選んだ `.yaml` ファイルの
  テキストをクライアント側で読み取り、Author エディタの Save ボタンが使うのと同じ `/api/scenario`
  エンドポイントへ、ファイル自身の名前をパスとして送信します。こうすると、アップロードでスコープへ
  着地したシナリオは、Save や `record` で着地したシナリオと区別が付きません。成功すると、新しい
  `overwritten` フラグから「追加しました」「上書きしました」のいずれかを報告し、シナリオ一覧を再読み込み
  して、そのファイルをその場で選択・実行できるようにします。config とターゲットの両方が選ばれるまでは
  操作を有効にしません。これは、他の Replay の操作がすでに課している前提と同じです。

- **シナリオ一式に対する zip アップロード。** 新しいエンドポイント `POST /api/scenarios/upload` は、
  生のリクエストボディを受け取り、`/api/upload` や `/api/artifacts/*` と同じくメインのリクエストループの
  外で処理します。指定したターゲットのスコープに向けて、1 つ以上の `.yaml` を含む `.zip` を受け付けます。
  新しいオペレーション `upload_scenarios` は、`save_scenario` と同じスコープを解決したうえで、アーカイブの
  最上位にある `*.yaml` エントリだけを読みます。スコープにサブディレクトリという概念はなく、
  `list_scenarios` が平坦に `glob("*.yaml")` するのと揃えています。抽出中は、シナリオのテキストに見合った
  資源の上限（エントリ数、エントリごとのサイズ、合計サイズ。いずれも、アプリバイナリ向けの BE-0073 の
  bundle の上限よりはるかに小さくします）と、`bajutsu/serve/uploads.py` がすでに bundle に適用している
  zip-slip の封じ込めの両方を効かせます。すべてのエントリは、書き込む前に `load_scenario_file` で解析
  します。解析に失敗するエントリが 1 つでもあれば zip 全体を打ち切り、何も書き込みません。これにより、
  不備のあるバッチが部分的な上書きを残すことはありません。これは、`start_run_set`
  （`bajutsu/serve/operations/dispatch.py`）がシナリオの fan-out にすでに適用している、
  「どれかに触れる前にすべてを確かめる」パターンと同じです。成功すれば各ファイルを `scope.save(name,
  text)` で書き込み、応答は各名前を「新規作成」か「上書き」として一覧します。このルートは
  `bajutsu/serve/authz.py` の `_EDITOR_PATHS` に加え、`/api/scenario` と同じ RBAC の階層に置くため、
  ホスト型デプロイの viewer からは届きません。Replay の Form には、もう 1 つのアップロード操作
  （あるいは、選んだファイルの拡張子で振り分ける同一の操作）を足し、`.zip` をここへ送って、単体ファイル
  の経路とそろえた新規作成／上書きの要約を描画します。

- **テストとドキュメント。** `save_scenario` の新しい `overwritten` フィールドを、ユニットテストで
  確かめます。同じ ref への 1 回目の保存では false、2 回目では true になることを確認します。
  `upload_scenarios` もユニットテストで確かめます。1 つの zip に入れた 2 つの正しいシナリオが両方とも
  配置され、`list_scenarios` にも両方現れること、解析に失敗するエントリが 1 つでもあれば何も書き込まれ
  ないこと、zip-slip のエントリとサイズ超過のエントリがどちらも拒まれることを確認します。新しい
  アップロード操作には `data-testid` を付け、既存の Replay フィクスチャの隣に、単体ファイルのアップロード
  経路を最初から最後まで動かす dogfood の E2E シナリオを追加します。`docs/architecture.md` とその
  `docs/ja/` の対応を更新し、Replay が config を束縛し直すことなく、束縛済み config の scenario scope
  へ直接シナリオを配置できるようになったことを記します。

## 検討した代替案

- **config を一切束縛せずにシナリオを実行する。** シナリオ自体は backend 非依存ですが、実行にはターゲットの
  backend・デバイス・アプリのパスが要り、素のシナリオファイルはそのどれも持っていません。したがって
  アップロードは、単独で成立するのではなく、すでに束縛済みの config のターゲットに対して解決します。
  この項目は、シナリオの実行に必要なものを変えず、シナリオファイルがその情報をすでに持つスコープへ
  届く経路だけを変えます。

- **BE-0268 の compose／アーティファクトアップロードの経路を、この用途にも使い回す。**
  `POST /api/artifacts/scenarios` は、アップロードした `.zip`／`.yaml` をコンテンツハッシュでキャッシュ
  済みですが、`bind_composition` は、オペレータに config を束縛し直す意図がなくても `config`
  アーティファクトを求め続けます。`bind_composition` は、それを持たないリクエストを受け付けません。
  この経路を拡張するとすれば、scenarios のレグへ届くためだけに、当て馬の config レグを供給する仕組みを
  作ることになります。これは、config がすでに束縛済みであることを前提にした専用のエンドポイントを
  足すより手間が増えます。この提案は、まさにその前提が成り立つ場面のためにあります。

- **ネストしたディレクトリ構成の zip を受け付ける。** スコープには、今のところサブディレクトリという
  概念がありません。`list_scenarios` はディレクトリを平坦に glob します。ネストしたエントリを入れても
  一覧には現れません。最上位の `*.yaml` だけを平坦に読む今回の設計は、既存のモデルとそのまま合います。
  ここを見直す価値が出るのは、スコープ自体が他の場所でサブディレクトリを持つようになったときであり、
  それまでは見直しません。

- **zip の一部エントリが解析に失敗したとき、成功した分だけ書き込む。** all-or-nothing を選び、この案は
  見送りました。バッチが部分的に着地すると、スコープは、アップロードした本人が求めてはいない状態になり、
  あとから追いにくくなります。`start_run_set` も、同じ理由で、どれかに触れる前にすべての項目を
  確かめています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `save_scenario` の応答に `overwritten` フィールドを足す。
- [ ] Replay の Form に、単体ファイルの「シナリオをアップロード」操作を足し、`POST /api/scenario` へ
      送信する。
- [ ] `.zip` シナリオ向けに `POST /api/scenarios/upload` エンドポイントと `upload_scenarios`
      オペレーションを足す。書き込み前の確認と、zip-slip／サイズの上限を備える。
      `bajutsu/serve/authz.py` の `_EDITOR_PATHS` にルートを加える。
- [ ] Replay の Form に zip アップロードの操作を足し、単体ファイルの経路と新規作成／上書きの要約表示を
      共有する。
- [ ] 両方の経路のユニットテスト、`data-testid`、dogfood の E2E シナリオを足す。
- [ ] `docs/architecture.md` とその `docs/ja/` の対応を更新する。

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
