[English](BE-XXXX-serve-config-view.md) · **日本語**

# BE-XXXX — serve の Web UI で読み込み中の config を確認する（生 YAML、構造化ツリー、Git 由来）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-config-view-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#734](https://github.com/bajutsu-e2e/bajutsu/pull/734) |
| トピック | Configuration sourcing |
| 関連 | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) |
<!-- /BE-METADATA -->

## はじめに

serve の Web UI は 1 つの**アクティブな config** を bind し、すべてのタブ（Record / Replay / Crawl など）は
その config に対して動きます。いまヘッダに出るのは「Open config」の隣の config の**パス**だけです。この提案は、
bind した config を確認するための **View** ボタンを追加します。config の中身を折りたたみ可能な key-value の
ツリーで（生 YAML への切り替え付きで）表示し、Git ソースから取得したものであれば、materialize されたコミットも
示します。読み取り専用の機能で、config を編集したり bind し直したりはしません。

## 動機

config を bind したあと、いま実際に何に対して動いているのかを UI からは確認できません。パスは出ますが、
**Git ソースの config**（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）では、その
パスは content-addressed なキャッシュ上の不透明な場所（`…/gitsrc/<host>/<owner>/<repo>/<sha>/…`）です。ここから
2 つの問題が生じます。

- **中身を確認できません。** タブがどの target、どの `baseUrl`、どのシナリオを使うのかを確かめるには、UI を
  離れてディスク上のファイル（あるいはリポジトリ）を自分で開くしかありません。
- **コミットを確認できません。** Git ソースは `ref`（ブランチ、タグ、SHA）で bind し、bind 時にその ref を
  不変のコミットへ解決します（BE-0063 の決定性のアンカーです）。キャッシュパスにはその SHA が含まれますが、
  長いパスから 40 文字のハッシュを読み取るのは「いまどのコミットか」の答えとして使いものになりませんし、
  ブランチ ref の場合は何に解決されたのかの手がかりが何も得られません。

必要な情報はすでにサーバ側にあります。bind したパス、ファイルのバイト列、そして Git bind であれば BE-0063 が
計算する `source_provenance` の刻印です。ただ、それが表に出ていないだけです。これを表に出すことで、「正しい
ものが bind されていると信じる」状態が「正しいものが bind されていると見て確かめられる」状態に変わります。これは
この提案が土台とする Git のパスでとくに効きます。

## 詳細設計

読み取り専用の serve エンドポイントと、UI 側のビューアです。`run` / CI のパスには一切触れません（プライム
ディレクティブ 1）。これは Tier 1 の利便性のための機能です。

- **bind した状態に Git 由来を保持する。** `ServeState` に `config_provenance` フィールドを足し、アクティブな
  config が Git ソース由来のとき、BE-0063 の `source_provenance` の刻印（host / owner / repo / 要求した ref /
  解決された SHA）を保持します。ローカルファイルやアップロードしたバンドルのときは `None` です。実行時の
  Git bind で設定し、ローカルファイルやバンドルの bind でクリアします。起動時の Git `--config` も、その由来を
  状態まで伝搬させるので、起動時に bind した Git config もコミットを表示できます。
- **`config_content` の serve オペレーションとエンドポイント。** 新しいオペレーションが、bind した config の
  生のテキスト、そのパス、パースした構造（同じテキストの `yaml.safe_load` なので `${secrets.*}` はそのまま
  文字列で残ります）、そして由来（または `None`）を返します。両方の serve トランスポート（stdlib のハンドラと
  FastAPI アプリ）に `GET /api/config/content` として配線します。読み取りなので role による制限はなく、config が
  bind されていなければ 404 を返します。
- **そのまま表示し、秘匿情報を漏らさない。** テキスト（とパースした構造）は bind されたままのファイルです。
  `${secrets.*}` のようなプレースホルダは書かれたとおりに現れ、解決されることはありません。したがって、Git に
  コミット済みの、あるいはバンドルに同梱されたファイルを超える情報は何も出しません。
- **UI：「View」ボタンとビューアのモーダル。** config 名の隣の **View** ボタン（config を bind したときだけ
  現れます）でモーダルを開き、由来の行（Git ソースのとき）、パス、そして config を切り替え可能な 2 つのビューで
  表示します。**Structured** は折りたたみ可能な key-value のツリー（入れ子のオブジェクトとリストはトグルになり、
  最初の 2 階層ほどは既定で開き、スカラーは型で色分けします）、**Raw** は生の YAML です。パースできない config は
  Raw ビューにフォールバックします。
- **ドキュメント。** `docs/web-ui.md` とその `docs/ja/web-ui.md` の対訳が、View ボタン、2 つのビュー、由来の行を
  説明します。

## 検討した代替案

- **モーダルを使わず、ヘッダに YAML 全文をインライン表示する。** 却下しました。config は長く、ヘッダはタブバーと
  場所を共有しています。クリックで開くモーダルにすれば、既定の UI を散らかさずに済みます。
- **ヘッダのパスの整形だけ（キャッシュパスの代わりに `owner/repo@ref` を出す）で、中身のビューは持たない。** これは
  「どのコミットか」の半分は解決しますが、「どんな中身か」の半分は解決しません。UI を離れなければ bind された
  target やシナリオを確認できないままです。中身のビューはこれを包含します。
- **表示のために `${secrets.*}` を解決する。** はっきり却下します。読み取り専用の確認画面を、秘匿情報の漏洩
  経路に変えてしまいます。このビューは意図的にそのまま表示します。
- **編集可能にする（ビューアから編集した config を bind する）。** スコープ外です。bind には専用のソース
  （ファイルブラウザ / Git / アップロード）がすでにあります。この項目は確認だけを行います。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] bind した状態への Git 由来の保持（Git bind で設定、ローカル / バンドルの bind でクリア、起動時から伝搬）。
- [x] 中身、パス、パースした構造、由来を返す `config_content` オペレーション。
- [x] 両トランスポート（stdlib のハンドラと FastAPI アプリ）への `GET /api/config/content`。
- [x] UI：Structured / Raw トグル付きの View ボタンとビューアのモーダル。
- [x] ドキュメント（英日）がビューアと由来の行を説明する。

- [#734](https://github.com/bajutsu-e2e/bajutsu/pull/734) — エンドポイント、由来の配線、Structured / Raw の
  ビューアを実装します。merge 時にこの項目を *実装済み* に切り替えます。

## 参考

- [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) — 解決されたコミットの由来をこの提案が
  表に出す、Git config source です。
- [`docs/ja/web-ui.md`](../../docs/ja/web-ui.md) — Web UI ガイド（「アクティブな config を選ぶ」）。
