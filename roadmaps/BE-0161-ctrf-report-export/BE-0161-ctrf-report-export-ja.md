[English](BE-0161-ctrf-report-export.md) · **日本語**

# BE-0161 — Common Test Report Format (CTRF) での実行結果の出力

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0161](BE-0161-ctrf-report-export-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0161") |
| トピック | Integration with external services |
<!-- /BE-METADATA -->

## はじめに

実行結果を [Common Test Report Format (CTRF)](https://ctrf.io/) 形式の文書として出力します。出力先は
`ctrf.json` で、既存の `manifest.json`・`junit.xml`・`report.html` と並べて実行ディレクトリに書き出し
ます。CTRF はテストレポート向けのオープン標準の JSON スキーマで（`reportFormat: "CTRF"` と、`tool` /
`summary` / `tests` を持つ `results` オブジェクトからなります）、どのフレームワークでも同じ形の文書を
生成できるように設計されています。この標準のおかげで、GitHub Actions の PR コメント出力、ダッシュ
ボード、flaky なテストの分析といった消費側のエコシステムが、ツールごとのアダプターなしに同じファイル
を読めます。この出力機能は、Bajutsu がすでに算出している実行データを決定的に射影するだけのものです。
入力は既存の `junit_xml()` と同じで、その隣に新しい出力形式を一つ足す形になります。LLM は関与せず、
判定にも影響しません。

## 動機

Bajutsu はすでに CI 連携のために `junit.xml` を出力しており（BE-0003）、これは CI がテスト結果を取り込む
ときの共通語であり続けています。とはいえ JUnit XML は最大公約数的な形式です。テストの名前、クラス、
所要時間、失敗内容の塊を運びますが、それ以上のものはほとんど持ちません。Bajutsu が実行について把握して
いる、より豊かな情報、たとえばステップごとの結果、シナリオが動いたバックエンド／エンジンやデバイス、
第一級のアーティファクトとしてのスクリーンショットや動画やネットワークログ、ビジュアル差分の証跡、
クロスブラウザマトリックスのセル、シナリオの由来情報は、JUnit には収まる場所がなく、自由記述の文字列
へ押し込められるか、失われます。

CTRF は、まさにこの豊かで構造化された形を運ぶために存在しており、消費側のエコシステムも育っています。

1. **PR コメント・サマリーの出力ツール**：`ctrf-io` の GitHub Actions（`github-test-reporter` など）は、
   一つの `ctrf.json` から、失敗表とメッセージ、flaky なテストの指摘、履歴の傾向を備えた PR コメントや
   ジョブサマリーを生成します。しかもツールを問わず、標準のファイル一つからそれができます。今の Bajutsu
   利用者が同じ体験を得るには、JUnit からコメントを組み立てるステップを自分で書かなければなりません。
2. **ツールをまたぐダッシュボードと分析**：CTRF はフレームワーク間で形を正規化するため、E2E に Bajutsu を、
   単体テストに別のフレームワークを使っているチームは、両方を一つの CTRF ベースのダッシュボードに流し
   込めます。デバイス・エンジン・ステップ・アタッチメントといった Bajutsu 固有の情報も、その道のりで
   失われません。
3. **Bajutsu の詳細を保つ構造化アーティファクト**：CTRF の `tests[].steps`・`attachments`・
   `browser`／`device`、および `extra` の拡張点により、出力ファイルは `manifest.json` の内容をほぼ
   そのまま保てます。情報が失われる JUnit とは対照的です。

この機能を安く安全に保つ捉え方は、こうです。Bajutsu は新たな内部モデルを採用しませんし、実行が算出する
内容も変えません。`manifest.json` はすでに正規かつバージョン管理された実行モデルであり
（[BE-0068](../BE-0068-regenerable-reports/BE-0068-regenerable-reports-ja.md)）、調査の結果、それは
CTRF の上位集合だとわかっています。CTRF の必須フィールドはどれも直接の供給元を持ち、Bajutsu の余剰は
CTRF の第一級のオプションフィールド（`steps`・`attachments`・`browser`・`device`・`environment`）か、
`extra` の拡張点に収まります。したがってこの出力機能は、既存データの射影を `junit_xml()` の隣に足すだけで、
新しい記帳は生じません。判定後に確定済みの結果を直列化するだけなので、その性質上、決定性を第一とする
契約の外側に完全に収まります。

## 詳細設計

### フィールドの対応（manifest から CTRF へ）

この出力機能は、レポート生成がすでに組み立てている実行結果モデル（`bajutsu/orchestrator/types.py`・
`bajutsu/assertions.py`・`bajutsu/evidence.py` の `RunResult` / `StepOutcome` / `AssertionResult` /
`Artifact`）をそのまま読み、CTRF 文書を生成します。

| CTRF フィールド | 供給元 | 備考 |
|---|---|---|
| `reportFormat` / `specVersion` | 定数 | `"CTRF"` と対象とする spec バージョン |
| `generatedBy` / `timestamp` | `"bajutsu"` / 現在時刻 | 文書メタデータ |
| `results.tool.{name,version}` | `"bajutsu"` / `provenance.toolVersion` | |
| `results.summary.tests` | `scenarios` の件数（マトリックスはセル数） | |
| `results.summary.{passed,failed}` | `ok` の集計 | |
| `results.summary.{skipped,pending,other}` | `0` | Bajutsu に現状これらの状態はありません |
| `results.summary.duration` | Σ `duration_s × 1000` | ミリ秒 |
| `results.summary.{start,stop}` | 実行開始時刻 + 所要時間 | 後述の時刻の注記を参照 |
| `tests[].name` | `RunResult.scenario` | マトリックスのセルは `scenario` + エンジン接尾辞 |
| `tests[].status` | `ok` → `passed` / `failed` | Bajutsu が出す状態はこの二つだけです |
| `tests[].duration` | `duration_s × 1000` | ミリ秒 |
| `tests[].message` / `trace` | `RunResult.failure` / ステップとアサーションの reason | |
| `tests[].steps[]` | `StepOutcome` → `{name: action, status}` | 豊富なフィールドは `step.extra` へ（後述） |
| `tests[].browser` / `device` | `engine` / `device_name` + `device_runtime` | |
| `tests[].attachments[]` | `Artifact`（`name`／`kind`／`provider`） | `contentType` は `kind → MIME` 表から |
| `results.environment.{commit,osPlatform,…}` | `provenance.gitRevision`、ホスト情報 | オプションのブロック |
| 余剰（マトリックスのセル、`expect_results`、`expect_alerts`、`skipped_captures`、`sid`、ビジュアル差分） | `extra` | CTRF は「完全に拡張可能」 |

調査により、CTRF の必須フィールド、すなわち `reportFormat`・`specVersion`・
`results.{tool,summary,tests}`、および各テストの `name`／`status`／`duration` は、どれも直接の供給元を
持つと確認できました。したがってこの対応で情報は失われません。

### すり合わせが要る二つの形の不一致（どちらもブロッカーではありません）

1. **絶対時刻**：CTRF は `start`／`stop` を **Unix エポックからのミリ秒**として、実行ごと・テストごとに
   求めます。Bajutsu は現状、`runId`（`YYYYmmdd-HHMMSS` の壁時計文字列）と、相対的な `duration_s`、各
   `StepOutcome.started_at` のオフセットを持ちますが、シナリオごとの絶対開始時刻は持ちません。この出力
   機能は `summary.start` を `runId` から導出し、テストごとの時刻を累積オフセットから求めます。これは
   直列実行では正確で、並列やマトリックス実行では近似になります。完全な忠実性が要るなら、実行とシナリオ
   に絶対エポック開始時刻を記録する小さな後続作業で対応できますが、本提案ではこれを `manifest.json` の
   スキーマバージョンを上げるオプションの改良と位置づけ、前提条件とはしません。`duration`（CTRF と多くの
   消費側が拠り所にするフィールド）は、いずれの場合も正確です。
2. **CTRF のステップは最小限**：CTRF の `step` は `{name, status, extra}` しか許さず、トップレベルに
   duration や reason はありません。Bajutsu の `StepOutcome` はより豊か（duration、reason、ステップ
   ごとのアサーション結果、アーティファクト）です。これらは `step.extra` へ入れるので、失われるものは
   ありません。name／status だけを描画する消費側にはきれいな一覧が見え、Bajutsu を理解するツールは
   extra を読めます。

`Artifact.kind`（`video` / `screenshot` / `deviceLog` / `elements` / `network` など）は、小さな対応表で
MIME の `contentType`（`video/mp4`、`image/png`、`text/plain`、`application/json` など）へ写します。未知の
kind には安全側の既定値 `application/octet-stream` を充てます。アタッチメントの `path` は実行ディレクトリ
からの相対パスのままとし、`manifest.json` がアーティファクトを記録する方法に合わせます。

### 組み込み位置

CTRF の生成は、既存の JUnit の経路の隣に置きます。`bajutsu/report/` に `ctrf_json()` ビルダーを設け
（`bajutsu/report/manifest.py` の `junit_xml()` と `manifest_dict()` の隣）、`manifest.json` /
`junit.xml` / `report.html` をすでに書き出しているレポート組み立て地点（`bajutsu/runner/pipeline.py` の
`_assemble_report()`、`bajutsu/report/html.py` の `write_report()` 経由）から呼びます。BE-0068 により
レポートは保存済みの実行データから再生成できるので、`bajutsu report <run>` は、HTML や JUnit を再出力する
のと同じ要領で過去の実行の `ctrf.json` を再出力します。この出力機能は永続化済みのモデルだけを読み、実行
時のデバイス状態には触れません。

マトリックス実行（[BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines-ja.md)、
該当する場合）は、セルごとに CTRF の `test` を一つ出し、エンジンをテスト名と `browser` に入れます。これは
`junit_xml()` がすでにエンジンを JUnit の `classname` に埋め込んでいるやり方に倣ったものです。

### 決定性と Prime Directive

この機能は判定より後にだけ存在し、それが契約の内側に収まる理由です。

- **LLM は一切使いません**：CTRF は `manifest.json` の機械的な直列化であり、Tier-2 のゲートにも判定にも
  触れません。
- **判定後で副作用のみ**：ファイルは `run` が合否を決め、結果モデルを組み立てた後に書きます。書いても
  判定や終了コードは動きません。
- **アプリ非依存**：この出力機能はドライバーにもターゲットにも依存しません。共有の結果モデルを読むので、
  iOS（idb）・Web（Playwright）・将来のバックエンドは、アプリごとの分岐なしに同一の出力を得ます。
- **秘匿処理を継承**：CTRF は秘匿処理済みの manifest から射影されるので
  （[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) /
  [BE-0153](../BE-0153-encode-aware-secret-redaction/BE-0153-encode-aware-secret-redaction-ja.md)）、
  出力ファイルに生のシークレットは届きません。

### 作業分解（MECE）

1. アーティファクトの `kind → MIME` 対応表（既定値は `octet-stream`）。
2. 結果モデルを CTRF 文書へ射影する `ctrf_json()` ビルダー（summary・tests・steps・attachments・
   environment、余剰は `extra`）。単一エンジンとマトリックスの両方に対応。
3. ビルダーをレポート組み立てに配線し、`run` が `ctrf.json` を書くようにする。あわせて `bajutsu report`
   に配線し、過去の実行から再生成できるようにする。
4. テスト：直列実行とマトリックス実行が、CTRF スキーマに適合し、対応づけたフィールド（状態の集計、
   所要時間、ステップ一覧、アタッチメントの MIME タイプ）を往復できる `ctrf.json` を出すこと。
5. ドキュメント（日英両方）：`ctrf.json` を実行アーティファクトとして記載し、「CI で消費する」短い注記を
   添える。
6. *(オプションの後続作業)* 並列実行でも `start`／`stop` を正確にするため、実行／シナリオに絶対エポック
   開始時刻を記録する。`manifest.json` のスキーマバージョンを上げる。

## 検討した代替案

- **何もしない、JUnit XML で十分とする**：JUnit は合否ゲートを賄いますが、Bajutsu の特徴的な出力である
  構造化された詳細（ステップ、デバイス／エンジン、アーティファクト、ビジュアル差分）を削ぎ落とし、
  さらに CTRF の消費側エコシステム（PR コメント、ダッシュボード）は `manifest.json` を読めません。CTRF は
  「私たちの豊かなモデル」と「標準の消費側」をつなぐ橋です。
- **CI 実行時に外部 CLI ツールで `manifest.json` → CTRF に変換する**：これは独自でドリフトしやすい変換を、
  利用者ごとの CI 設定へ押し込みます。`junit.xml` の隣に第一級の `ctrf.json` があれば、連携は消費側の
  一行で済みます。変換は、そのモデルを所有するツールの側にあるべきです。
- **`manifest.json` を廃し CTRF を正規モデルにする**：却下します。`manifest.json` は Bajutsu 固有かつ
  バージョン管理されており（BE-0068）、CTRF に収まる場所のない Bajutsu 固有の構造（マトリックスのセル、
  スキップしたキャプチャ、ビジュアル証跡）を運び、`report.html` を駆動します。CTRF は出力先であって内部
  契約ではありません。これは JUnit がすでに持っているのと同じ関係です。
- **CTRF だけを出し、JUnit をやめる**：やりません。JUnit は最も広く対応された CI 形式であり続けます。
  CTRF は付加であり、JUnit では賄えない消費側のためのものです。
- **まず正確な絶対時刻を用意することを前提にする**：不要です。`duration`（CTRF とその消費側が拠り所に
  するもの）はすでに正確で、`start`／`stop` は `runId` から許容できる形で導出できます。絶対エポックの
  精緻化はオプションの後続作業であり、この出力機能を出荷する条件ではありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] アーティファクトの `kind → MIME` 対応表。
- [ ] `ctrf_json()` ビルダー（summary / tests / steps / attachments / environment / `extra`、直列 + マトリックス）。
- [ ] レポート組み立て（`run`）と `bajutsu report` の再生成への配線。
- [ ] テスト：直列実行とマトリックス実行でスキーマ適合の `ctrf.json`、フィールドの往復。
- [ ] 日英ドキュメント：`ctrf.json` を実行アーティファクトとして記載し「CI で消費する」注記を添える。
- [ ] *(オプション)* 実行／シナリオへの絶対エポック開始時刻の記録で `start`／`stop` を正確化。`manifest.json` のスキーマ更新。

## 参考

- [CTRF — Common Test Report Format](https://ctrf.io/) と [JSON スキーマ](https://github.com/ctrf-io/ctrf) — この項目が対象とする標準。
- [BE-0068 — Regenerable reports](../BE-0068-regenerable-reports/BE-0068-regenerable-reports-ja.md) — この出力機能が射影元とする、正規かつバージョン管理された実行モデル `manifest.json` と、CTRF が再利用する `bajutsu report` の再生成経路。
- [BE-0060 — Download / export a run report as a zip](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) — `ctrf.json` が加わる、実行ディレクトリのアーティファクト一式。
- [BE-0099 — Webhook notifications for run results](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md) — 判定後に `manifest.json` を形式中立に射影する姉妹項目。同じ「射影する、再算出しない」という方針。
- [BE-0003 — M3: codegen, traces, network, CI](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci-ja.md) — CI 向けの JUnit XML が入った場所。CTRF はより豊かな相棒の形式。
- [BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) — 出力ファイルが manifest から継承する秘匿処理。
