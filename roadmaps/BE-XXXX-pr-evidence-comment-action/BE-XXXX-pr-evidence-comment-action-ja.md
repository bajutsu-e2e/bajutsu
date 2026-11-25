[English](BE-XXXX-pr-evidence-comment-action.md) · **日本語**

# BE-XXXX — 実行の証跡を、どのリポジトリでも使える GitHub Action でプルリクエストや Issue に投稿する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-pr-evidence-comment-action-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Integration with external services |
| 関連 | [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md), [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md), [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md), [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md) |
<!-- /BE-METADATA -->

## はじめに

実行を終えた `bajutsu run` の結果を、プルリクエストまたは Issue に投稿する composite GitHub Action `post-evidence` を追加します。コメントには、失敗したシナリオ、失敗したステップ、その理由を載せます。さらに、実行の証跡（`report.html`、失敗ステップのスクリーンショット、シナリオの動画）へのリンクを載せます。証跡は、この Action が GitHub Actions の artifact としてアップロードします。再実行したときは、新しいコメントを増やさず、同じコメントを更新します。

この Action が Bajutsu から読むのは `manifest.json` だけです。Bajutsu のパッケージを入れず、Bajutsu のコマンドも呼びません。Bajutsu の実行ディレクトリを出力するリポジトリなら、`uses:` を1行足すだけで使えます。

## 動機

Bajutsu のチェックが赤くなったプルリクエストで、レビュアーが証跡を見るまでの手間は大きいままです。Actions の実行を開き、ジョブを探し、artifact を開き、zip をダウンロードして展開し、`report.html` を開きます。最後の手順まで進むレビュアーは多くありません。失敗の理由はジョブのログにあり、それを説明するスクリーンショットは zip の中にあります。

[BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check-ja.md) は、`serve` を運用するチームにとってこの状況を改善します。ただし、失敗したシナリオと理由を並べるチェックにとどまります。スクリーンショット、動画、レポートへのリンクは載りません。`serve` の運用と GitHub App も必要です。[BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export-ja.md) は、外部のレポーターが表示できる結果の表を出力します。証跡そのものをプルリクエストへ添える経路は、どちらにもありません。[BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri-ja.md) はオブジェクトストレージへ証跡をアップロードします。しかし、アップロードしたファイルをプルリクエスト上でレビュアーに見せる仕組みはありません。

導入後に確かめられる違いは、プルリクエストのページに現れます。失敗した実行のあとには、コメントが1件付きます。コメントは、どのシナリオがなぜ失敗したかを述べ、レポート、失敗時のスクリーンショット、動画を収めた artifact にリンクします。レビュアーは、プルリクエストから1クリックで証跡に届きます。再実行するとコメントが書き換わるので、会話に残るコメントは、実行ごとではなくジョブごとに1件です。

## 詳細設計

### Action の置き場所と前提

Action は、このリポジトリの `.github/actions/post-evidence/` に置きます。ほかのリポジトリは `bajutsu-e2e/bajutsu/.github/actions/post-evidence@<ref>` として使います。描画処理は、標準ライブラリだけを使う Python スクリプト1本です。Action は `actions/setup-python` でリポジトリの Python（3.13）を固定し、`update-environment: false` を指定して、ステップの出力 `python-path` からスクリプトを起動します。ランナーのシステム Python は使わず、呼び出し側のジョブの Python も変えません。そのため、`uv sync` も Bajutsu のチェックアウトも要りません。

呼び出すジョブには、プルリクエストなら `pull-requests: write`、Issue なら `issues: write` を与えます。この Action は `pull_request_target` を必要としません。このトリガーは、フォークのコードに対して書き込み権限つきのトークンで動きます。信頼できないシナリオがコメントを操作できてしまうため、ドキュメントでは非対応と明記します。

### 入力と出力

| 入力 | 既定値 | 意味 |
|---|---|---|
| `run-dir` | `runs` | 1つの実行ディレクトリか、複数の実行を収めたルートです。ルートを渡すと、直下の `*/manifest.json` をすべて読み、実行ごとに1節を描画します。 |
| `artifact-name` | `${{ github.workflow }}-${{ github.job }}` | アップロードする Actions artifact の名前です。1回のワークフロー実行は同名の artifact を2つ持てないため、マトリクスのジョブは名前にマトリクスの値を足します。アップロードの前に、`actions/upload-artifact` が受け付けない文字（`" : < > | * ? \ /`、CR、LF）を `-` に置き換えます。 |
| `issue-number` | イベントのプルリクエスト番号 | コメントを付けるプルリクエストまたは Issue です。プルリクエストのイベント以外では省略でき、省略したときはジョブサマリーだけを書き、コメントは投稿しません。 |
| `github-token` | `${{ github.token }}` | コメントを書き込むトークンです。 |
| `comment-key` | `${{ github.workflow }}-${{ github.job }}` | 固定コメントのキーです。キーが異なる2つのジョブは、2件のコメントを保ちます。マトリクスのジョブは、キーにマトリクスの値を足します。足さないと、各レグが前のレグのコメントを上書きします。 |
| `comment-author` | `github-actions[bot]` | 固定コメントを所有するアカウントのログインです。`github-token` が GitHub App のものなら、App のボットのログインを指定します。 |
| `max-failures` | `10` | 詳しく列挙する失敗シナリオの上限です。残りは件数だけを示します。 |
| `evidence-base-url` | 空 | 実行ディレクトリを HTTP で配信する任意のベース URL です。[証跡のベース URL による画像の埋め込み](#証跡のベース-url-による画像の埋め込み)を参照してください。 |

出力は `artifact-url` の1つで、アップロードのステップから取ります。

### Action の動作

1. **アップロード。** `actions/upload-artifact` で `run-dir` をアップロードします。レポート、スクリーンショット、動画、ログを含む実行全体が、1つの artifact になります。
2. **描画。** スクリプトが `manifest.json` を読み、Markdown を作ります。先頭には、判定と、成功と失敗のシナリオ数を置きます。描画処理は、シナリオ名と失敗理由をそれぞれコードスパンに入れ、複数行にわたる文字列はフェンスつきブロックに入れます。コードスパンの区切りには、文字列の中で最も長いバッククォートの連続より1つ長い連続を使い、両端に空白を1つずつ入れます。フェンスつきブロックのフェンスも、中のどのバッククォートの連続より長くします。`<!--` と `-->` は、残らなくなるまで繰り返し取り除きます。アプリ由来の文字列が、メンションや HTML、2つ目のマーカーを加えられないようにするためです。その下に、失敗したシナリオごとのブロックを置きます。ブロックには、シナリオ名、最初に失敗したステップの番号とアクション、失敗の理由を書きます。さらに、失敗したステップに属するスクリーンショットとシナリオの動画を、artifact 内のパスで示します。
3. **投稿。** スクリプトは Markdown をジョブサマリーに書きます。そのため、プルリクエストのない実行でも結果が見えます。`issue-number` が決まるときは、1行目が隠しマーカー `<!-- bajutsu-evidence:<comment-key> -->` と完全に一致するコメントを探します。照合の対象は、作者のログインが `comment-author` と等しいコメントに限ります。見つかれば更新し、なければ新規に作ります。

スクリプトが読むのは、マニフェストがすでに記録しているフィールドだけです。マニフェストに載っていないスクリーンショットや動画は、コメントから省きます。省略はエラーにしません。

### サイズと失敗時の動作

GitHub のコメントには、約65,000字の上限があります。描画処理は、`run-dir` 配下のすべての実行を通して数え、`max-failures` 件を超えるシナリオの列挙をやめ、省いた件数を書きます。本文全体も、上限より低い決まった長さで切ります。各失敗理由も、決まった長さで切り詰めます。

アップロードのステップは `continue-on-error: true` で動かします。アップロードに失敗しても、ほかの失敗と同じく警告を出すだけにとどめます。そのときコメントは、証跡のアップロードに失敗したことを書き、artifact の代わりにワークフローの実行へリンクします。Action がジョブの結果を変えることはありません。合否は、すべての Bajutsu の実行と同じく、テストのステップ自身の終了コードが決めます。トークンに書き込み権限がない場合（フォークからのプルリクエストのトークンは読み取り専用です）や、API 呼び出しが失敗した場合は、スクリプトが警告を出します。サマリーだけを書き、終了コード0で終わります。Action のどこにも LLM は登場しません。コメントが運ぶのは、決定的な実行がすでに確定した判定です。

### シークレット

Action が投稿するのは、`manifest.json` がすでに持っている文字列です。その文字列は、実行がマニフェストを書く前に伏せ字にしています（[BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary-ja.md)）。Action は、デバイスログや `network.json` のような新しい文字列の出所を足しません。コメントにファイルの中身を埋め込むこともありません。artifact には、リポジトリのアクセス規則がそのまま適用されます。ダウンロードするには、読み取り権限が必要です。

### 証跡のベース URL による画像の埋め込み

Actions artifact のリンクは、ダウンロードページを開きます。GitHub は、そこからコメントの中に画像を描画できません。そのためコメントは、artifact へのリンクを置き、スクリーンショットと動画をパスで名指しします。これが既定の動作であり、明示した制約です。

実行を HTTP でも配信しているチームは、この制約を埋められます。`evidence-base-url` を設定すると、描画処理は、スクリーンショットを `![](<base>/<path>)` として出力し、動画を同じベースの下のリンクとして出力します。Action がその場所へアップロードすることはありません。[BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri-ja.md) のアップロードを済ませたジョブが、公開 URL か署名付き URL のベースを渡します。この入力は認証情報を運びません。署名付き URL は、期限が切れると画像が壊れます。この点も、ドキュメントに明記します。

### ドキュメントと導入

`docs/reporting.md` と `docs/ja/` のミラーに、そのままコピーできるワークフローの節を足します。実行のステップに続けて、`if: always()` つきで `post-evidence` を置く形です。最初の利用者として、このリポジトリの web E2E ワークフローに Action を導入します。実際の失敗を使って、コメントの形を CI で確かめるためです。

## 検討した代替案

- **コメントを投稿する `bajutsu` のサブコマンド。** サブコマンドなら、レポートのコードを直接再利用でき、GitHub Actions の外でも動きます。採用しなかったのは、目標が Bajutsu 本体を変えないポータブルな形だからです。さらに、コメントを投稿するためだけに、利用側の全ジョブが Bajutsu を入れる必要が出ます。
- **[BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check-ja.md) の拡張。** チェックはすでに `serve` 上で動くので、証跡のリンクを足すのは自然です。それでも分けたのは、BE-0500 が `serve` の運用と GitHub App を必要とし、この Action はどちらも要らないからです。Markdown の描画処理を将来共有する余地はありますが、ここでは扱いません。
- **`ctrf.json` を読む CTRF レポーターの Action。** [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export-ja.md) のファイルから、結果の表を描画するレポーターはすでにあります。ただし、添付ファイルの扱いはレポーターごとに異なります。失敗と artifact 内のスクリーンショットを結ぶ保証もありません。自分たちで制御できる描画処理を選びました。両方を使うことも妨げません。
- **スクリーンショットを `data:` URI で埋め込む。** GitHub のサニタイザーが取り除きます。コメントのサイズ上限にも収まりません。
- **証跡を孤立ブランチやリリースにコミットする。** どちらでも、安定した画像 URL が手に入ります。ただし、リポジトリが肥大化します。コンテンツへの書き込み権限と、保持期間の方針も要ります。任意のベース URL なら、チームがこの選択を自分で行えます。
- **`report.html` を GitHub Pages に公開する。** Pages なら、レポート全体をブラウザーで見られます。一方で、多くのリポジトリで実行結果が既定で公開されます。プルリクエストごとの削除方針も要るため、後続の項目に回します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 描画処理：`manifest.json` から Markdown を作り、長さを切り詰める標準ライブラリのスクリプトと、`make test` で動く単体テスト。
- [ ] コメントのクライアント：固定コメントの検索と更新、ジョブサマリーへの書き込み、警告を出して終了コード0で終わる経路。
- [ ] composite action：入力の配線、artifact のアップロード、`artifact-url` の出力、任意の `evidence-base-url`。
- [ ] ゲートの対象化：スクリプトが `make check` の `ruff` と `mypy --strict` を通ること。`make typecheck` の対象に `.github/actions/post-evidence` を加え、単体テストがそのパスからスクリプトを読み込みます。
- [ ] ドキュメント：`docs/reporting.md` の節と、`docs/ja/` のミラー。
- [ ] 導入：このリポジトリの web E2E ワークフローから Action を呼ぶこと。

## 参考

- [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check-ja.md)：証跡を含まない、run-set グループの GitHub check run としての公開。
- [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export-ja.md)：実行の CTRF 出力。
- [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri-ja.md)：実行の証跡をオブジェクトストレージへアップロードします。
- [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md)：確定した判定を運び、実行を失敗させない配信の先例。
- [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary-ja.md)：artifact 境界での伏せ字処理。
- [`docs/ja/reporting.md`](../../docs/ja/reporting.md)：Action が読む `manifest.json` のスキーマ。
