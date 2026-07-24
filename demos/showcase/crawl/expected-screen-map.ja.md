[English](expected-screen-map.md) · **日本語**

# Showcase の crawl：生成されるべき画面マップ

> `crawl`（[BE-0038](../../../roadmaps/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）
> は **提案** であり未実装です。本ファイルは先行きの *テストデータ* です。showcase の a11y アプリを正しく
> 幅優先 crawl したときに発見されるべきグラフを記し、crawl が実装された日に既知の正解マップと照合できる
> ようにします。実装後はこう実行します:
>
> ```bash
> bajutsu crawl --target showcase-swiftui --config demos/showcase/showcase.config.yaml \
>     --seed showcaseswiftui://permissions
> ```

showcase は本当に枝分かれの多い crawl 対象として作られています: 5 タブ × ナビゲーション push × 4 モーダル
様式。すべての識別子がデータ由来で安定（SPEC §5）なので、id ベースの **状態フィンガープリント**（画面上の
識別子の整列集合をハッシュ化したもの。
[BE-0038](../../../roadmaps/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）
は実行をまたいで安定します。

## ノード（到達可能な画面）

| ノード | 到達元 | フィンガープリントの錨（代表 id） |
|---|---|---|
| Stable（一覧） | 起動 / `stable` タブ | `stable.title`、`stable.row.*` |
| Horse Detail | Stable 行タップ / `…://horse/<id>` | `horse.title` |
| Search | `search` タブ | `search.field` |
| Search（空） | Search + 不一致クエリ | `search.results-empty` |
| Log | `log` タブ | `log.submit` |
| Log → Filter シート | `log.openFilter` | `log.sheet.title` |
| Log → Gallery カバー | `log.openGallery` | `log.cover.title` |
| Log → Delete ダイアログ | `log.openDelete` | `log.dialog.delete` |
| Notices（一覧） | `notices` タブ | `notice.title`、`notice.row.*` |
| Notice Detail | Notices 行 / `…://notice/<id>` | `notice.detail.title` |
| Permissions | `permissions` タブ / `…://permissions` | `perm.title` |

## 注目すべきエッジ

- **タブ切替**は、すべてのメインノードから出る 5 本のエッジ（タブバーは常に存在）。
- **モーダル**は Log ノードから開いて閉じる往復。crawl は開くエッジを記録し、戻った先の状態は再探索せず
  フィンガープリントで認識すべき。
- **Permissions** は、その操作がプロセス外の OS アラートを出す唯一のノード（SPEC §7）。ここに到達した
  crawl は `run` と同じく alert guard（`--alert-handling`）に頼るべきで、アラートは crawl が発見する画面では
  ない。

## no-a11y 変種で見えるべきもの

`showcase-swiftui-noax` に対して走らせると、識別子がないので crawl は構造フィンガープリント
（`(traits, frame-bucket)`、BE-0038）へフォールバックせざるを得ません。マップはより粗く、低信頼として
フラグされるべきです。これは、`doctor` が報告するアクセシビリティ負債の信号を、探索側から見たものです。
