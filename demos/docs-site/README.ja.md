# docs サイト E2E デモ（Playwright backend）

[English](README.md)

公開中の Bajutsu ドキュメントサイト（<https://bajutsu-e2e.github.io/bajutsu/>）を **Playwright
（web）backend** で駆動する Bajutsu の設定です。[`demos/web`](../web/README.ja.md) と異なり、配信するローカル
アプリはありません。対象は公開済みの URL なので、backend はそこへ直接ナビゲートするだけです。

## 実行

```bash
uv sync --extra web                 # Playwright の python パッケージ
uv run playwright install chromium  # Chromium バイナリ（初回のみ）

# スイート全体
uv run bajutsu run --target docs --backend web --config demos/docs-site/bajutsu.config.yaml
# シナリオ単体
uv run bajutsu run --scenario demos/docs-site/scenarios/smoke.yaml \
  --target docs --backend web --config demos/docs-site/bajutsu.config.yaml
```

`run` はすべてのシナリオが通れば終了コード 0、一つでも失敗すれば 1 を返します。各実行は
`runs/<runId>/{manifest.json,junit.xml,report.html}` を書き出します。

## シナリオ

| ファイル | 検証内容 |
|---|---|
| [`smoke.yaml`](scenarios/smoke.yaml) | トップページが表示され、「Get started」「GitHub」のヒーローボタンが出ます |
| [`search.yaml`](scenarios/search.yaml) | ヘッダの検索ボックスへの入力によって、マッチする結果リンクが表示されます（テキスト入力と非同期の条件待ちを検証） |

このサイトは Material for MkDocs 製で `data-testid` の id を持たないため、シナリオは `id` の代わりに可視
テキスト（`label` / `labelMatches`）と種別（`traits: [link]` / `[button]`）で要素を指定します。`exists` は
複数マッチを許容します（`find_all` は 1 件以上でよい）が、単一要素を要求するアサーションは一意なマッチが
必要です。各セレクタがなぜ一意に解決するかは、シナリオ内のコメントに記してあります。

## ページ遷移を伴うリンクのナビゲーションの制約

フルページの再読み込みを起こすリンクをクリックするシナリオ（例: 「Get started」のヒーローから
getting-started ページへの遷移）は、現状 web backend を詰まらせます。座標クリックの直後、run loop の
条件待ちが遷移中の DOM を再クエリし、Chromium が実行コンテキストを破棄するためです
（`Execution context was destroyed, most likely because of a navigation`）。`base.wait_until` は
この一時的な不具合を「まだマッチしていない」として扱わないため、run は新しいページに落ち着く前に
中断してしまいます。

これはシナリオの不具合ではなく、backend と run loop 側の欠落です。driver が Playwright 本来のナビゲー
ション確定を使わず、`query()` のポーリングで再クエリしているために起こります。そのため、このスイートは
現行の backend が決定論的に扱える同一ドキュメント内の操作（検索シナリオ）にとどめています。ロードマップ
（BE）項目として、`wait_until`（あるいは web driver の `wait_for`）が遷移中の一時的な詰まりを飲み込み、
期限までポーリングを続けるようにする価値があります。
