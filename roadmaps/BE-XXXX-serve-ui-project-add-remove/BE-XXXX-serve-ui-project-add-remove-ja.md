[English](BE-XXXX-serve-ui-project-add-remove.md) · **日本語**

# BE-XXXX — serve の Web UI からプロジェクトを追加・削除する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-ui-project-add-remove-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Surfacing CLI features in the serve Web UI |
| 関連 | [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md), [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md), [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) |
<!-- /BE-METADATA -->

## はじめに

設定プロジェクトハブ（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）は、
`serve` を複数の名前付き設定バインディングを束ねるハブにしました。ヘッダーの切り替えセレクターと
**Projects** モーダルによって、プロジェクトの一覧表示、アクティブなプロジェクトの切り替え、実行が、
すべてブラウザから行えます。しかし、ハブを増やしたり減らしたりする 2 つの操作、すなわちプロジェクトの
**追加**と**削除**は、この UI から外されたままです。Projects モーダルにできるのは一覧表示と切り替えと
実行だけで、モーダル自身のヒントもターミナルを使うよう促しています（「Add or remove projects with
`bajutsu project add` / `rm`.」）。

本項目はこのギャップを埋めます。Projects モーダルに**プロジェクト追加フォーム**を置き、各行に
**削除**操作を加えることで、ハブのライフサイクル全体（作成、切り替え、実行、削除）を 1 つの画面に
まとめます。これは純粋な UI のフォローアップであり、エンドポイントはすでに存在します。

## 動機

登録と登録解除の API は BE-0225 のユニット 3 で実装済みです。`POST /api/projects`（登録・再バインド、
BE-0108 の設定ソース許可リストで検査）と `DELETE /api/projects/<name>`（登録解除、実行履歴は保持）が
それにあたります。BE-0225 のユニット 4 は意図的に MVP の UI として出荷され（「MVP scope confirmed
with the author」）、これらのエンドポイントの読み取り側と切り替え側だけを利用しています。

- `loadProjects` が `GET /api/projects` を呼び、`switchProject` が
  `POST /api/projects/<name>/activate` を呼びます。
- Projects モーダルは一覧と行ごとの **Run** ボタンを描画します。追加フォームも削除操作もなく、
  `serve.core.mjs` の設計コメントがその境界を明示しています（「Projects are added/removed with the
  `bajutsu project` CLI (unit 5), not here — this surface switches and inspects them.」）。

その結果、「ハブ」という位置づけを損なう非対称が残りました。`serve` を設定を並べて見る共有の場として
使うチームは、プロジェクトを 1 つ追加したり退役させたりするたびに、ホスト上のターミナルへ降りる必要が
あります。これはハブが取り除こうとしたはずの文脈の切り替え、そのものです。ホスティング構成
（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）ではさらに不自然です。
そこではブラウザが利用者に与えられた唯一の画面であり、`bajutsu project add` を実行するシェルがありません。
登録エンドポイントはすでに到達可能で、まさにその用途を想定して許可リスト検査まで備わっているのに、
今のホスト版ハブは自身の UI からはまったく増やせない状態です。

不足している UI に必要なものは、すべてすでに存在します。エンドポイント（BE-0225 ユニット 3）、
ランチャーが使う設定ソースの入力ウィジェット（任意の認証情報を伴う Git リポジトリ欄と `.zip` アップロード、
[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) /
[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)）、許可リスト検査
（[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)）、
そして RBAC のゲートです。本項目はこれらを、モーダルに足りない 2 つの操作へと結線します。

これはプライムディレクティブの範囲内に収まります。プロジェクトの追加や削除は完全に決定的で、CLI が
書き込むのと同じ `ProjectRegistry` の継ぎ目へ名前付きの設定バインディングを書くだけであり、どの経路にも
LLM は入りません。ハブそのものはアプリ非依存のままです。アプリごとの違いは各プロジェクトの設定に置かれ、
この UI は設定ソースに名前を付けてバインドするだけだからです。

## 詳細設計

作業は 3 つのユニットに MECE に分かれます。追加フォーム、削除操作、そしてそれらを現す表示条件とロールの
結線です。

### 1. プロジェクト追加フォーム

Projects モーダルに新しい**プロジェクト追加**の入り口を設け、次の項目を持つ小さなフォームを開きます。

- **名前**欄（組織内で一意なプロジェクト名）、および
- **設定ソース**の選択欄。新しいウィジェットを作らず、ランチャーの既存のソースウィジェットを再利用します。
  すなわち Git リポジトリ欄（spec と任意の認証情報、
  [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）と `.zip` アップロード
  （[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)）です。ローカルの
  ファイルシステムパスは、サーバーが許可する場合に限り提示します（ユニット 3）。

送信すると、名前と判別可能なソースレコードを付けて `/api/projects` へ `POST` し、既存の `loadProjects` を
通じて一覧を更新します。`POST /api/projects` は `(org, name)` について冪等なので（BE-0225 ユニット 1・3）、
既存の名前で再送するとエラーにならず、そのソースを**再バインド**します。フォームはこれを暗黙の重複ではなく
明示的な「再バインド」の結果として示します。サーバー側の拒否（別 id 下での名前衝突、許可リストによる拒否、
不正な Git spec）は、ランチャーのエラー表示を再利用してフォーム内にインラインで示します。

### 2. プロジェクト削除操作

一覧の各行に、既存の **Run** やアクティブ表示と並べて**削除**操作を加えます。実行すると確認を求め
（登録解除は履歴には破壊的でないものの、バインディングには破壊的です）、`/api/projects/<name>` を
`DELETE` して一覧を更新します。UI は BE-0225 の契約を明示します。すなわち**実行履歴は保持され**、
バインディングだけが取り除かれます。アクティブなプロジェクトを削除した場合は、その状況に対して
エンドポイントがすでに行う挙動（アクティブなしにする、または別のプロジェクトへ切り替える）に従います。
UI は削除後の `GET /api/projects` の状態を反映し、結果を推測しません。

### 3. 表示条件と RBAC

追加・削除の操作は、ハブ UI の他の部分がすでに従っている規則に従います。

- **ハブが存在するときに現す。** 切り替えセレクターや Projects ボタンと同様に、これらの操作が意味を持つのは
  モーダルの中であり、そのモーダル自体はハブが存在してはじめて現れます。ただし**追加**の入り口だけは検討の
  余地があります。単一設定の `serve` を UI からハブへ育てたい場合があるため、2 つ目のプロジェクトができる
  前でも追加へ到達できるようにするか（たとえば切り替えセレクターが隠れている段階でも追加だけは出す）を
  設計で決めます。ここでは唯一の未決の UI 上の問いとして記録し、実装時に解決します。
- **RBAC。** `POST` / `DELETE /api/projects` はサーバー側で **admin** ゲートです（BE-0225 ユニット 3、
  `/api/config` と同じ扱い）。クライアントもこれをそのまま反映します。追加フォームと削除操作は非 admin の
  セッションでは隠すか無効にし、viewer や editor には今日と同じ読み取り専用のハブを見せ、403 になる操作を
  決して見せません。ここでは UI が他の admin 専用画面ですでに利用しているロール情報を再利用します。
- **ホスト時の許可リスト。** ソース選択欄は、サーバーが許可しない場合にファイルシステムの選択肢を隠します
  （[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)）。
  サーバー側の検査と揃え、ホストの利用者に API が拒否するソースを提示しないようにします。

新しいエンドポイントも、スキーマ変更も、CLI 変更もありません。これは UI と、2 つのフォームが必要とする
わずかなクライアント状態だけです。

## 検討した代替案

- **追加・削除を CLI 専用のままにする（現状維持）。** 却下します。これは本項目が解消しようとしている
  非対称そのものであり、シェルのないホスト版ハブを自身の UI から育てられないままにします。
- **モーダルを拡張せず、独立した「プロジェクト管理」ページを設ける。** ギャップに対して重すぎるため却下します。
  モーダルはすでにプロジェクトを一覧し、利用者がハブについて考える場所です。追加・削除は、それらが作用する
  一覧の隣にあるべきで、別画面に置くものではありません。
- **ランチャーとは別の、追加専用の設定ソース選択欄を作る。** 却下します。ランチャーにはすでに、レビュー済みで
  許可リストを意識したソース UI（Git と認証情報、`.zip` アップロード）があります。これを再利用すれば、ソース
  入力の概念と検査経路を 1 つに保て、
  [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md) と
  歩調を合わせ続けるべき 2 つ目のウィジェットを抱えずに済みます。
- **MCP や CLI 機能の一括対応の中で、プロジェクト管理をまとめて出す。** 粗すぎるため却下します。これは
  エンドポイントの揃った、具体的で価値の高い 1 組の操作であり、「すべての CLI サブコマンドを出す」汎用の
  取り組みを待たずに単独で出す価値があります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 1 — Projects モーダルの追加フォーム。ランチャーの設定ソースウィジェットを再利用し、`POST /api/projects`（再バインド対応、インラインエラー）。
- [ ] 2 — 行ごとの削除操作。確認 + `DELETE /api/projects/<name>`。履歴が保持されることを明示。
- [ ] 3 — 表示条件（ハブゲート、および単一設定を「ハブへ育てる」問い）と、RBAC・許可リストの反映。非 admin とホストのセッションには許可された操作だけを見せる。

## 参考

`bajutsu/templates/serve.html.j2`（Projects モーダルとランチャーの設定ソースウィジェット）、
`bajutsu/templates/serve.core.mjs`（`loadProjects` / `switchProject` とプロジェクトハブの節）、
`bajutsu/serve/operations/projects.py`（本項目が利用する `POST` / `DELETE` エンドポイント）、
[cli](../../docs/ja/cli.md#serve)、[architecture](../../docs/ja/architecture.md)。
[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)（本項目が仕上げるハブ）、
[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
（追加フォームが尊重する設定ソース許可リスト）、
[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) と
[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)（選択欄が再利用する
Git と `.zip` の設定ソース）。
