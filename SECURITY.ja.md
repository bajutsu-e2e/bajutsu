[English](SECURITY.md) · **日本語**

# セキュリティポリシー

## 対象バージョン

Bajutsu は **pre-alpha** です。セキュリティ修正は `main` ブランチにのみ適用します。
バックポート対象となるリリース済みバージョンはまだありません。

## 脆弱性の報告

セキュリティ上の問題は**非公開で**報告してください。公開の Issue や Pull Request を
作成せず、修正が用意できるまで詳細を公開しないでください。

GitHub の[非公開での脆弱性報告（private vulnerability reporting）](https://docs.github.com/ja/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
を利用してください。リポジトリの **Security** タブを開き、**Report a vulnerability**
を選択します。

報告は数日以内の受領連絡をベストエフォートで目標とし、修正の進捗を随時お知らせします。
報告の際は、再現できるだけの情報（対象のコミット、手順、影響範囲）を添えてください。

## スコープと補足

Bajutsu は iOS Simulator 向けの防御的な E2E（end-to-end）テストツールです。本プロジェクト
ならではの注意点をいくつか挙げます。

- **API キー / シークレット。** `ANTHROPIC_API_KEY` が必要なのは AI パス（`record`、
  `run --alert-handling`）だけです。API キーはコミットしたり共有したりせず、`.env`
  （gitignore 済み）に保管してください。決定的な `run`／CI ゲートにシークレットは不要です。
- **iOS Simulator（idb）バックエンドで非ラテン文字を `type` する場合。** idb のハードウェア
  キーボード経由のテキスト入力は、US キーボード配列の文字しか送信できません。そのため、日本語、
  中国語、韓国語、絵文字など非ラテン文字を含む `type` ステップは、Simulator のペーストボード経由で
  フォールバックします。具体的には `simctl pbcopy` でペーストボードへ書き込み、ハードウェア
  キーボードのペースト操作を送ります。このペーストボードは、同じ Simulator インスタンスに
  `simctl` でアクセスできる他のプロセスからも読み取れます。このバックエンドでは、非ラテン文字を
  含むシークレットやワンタイムパスコードを `type` しないようにしてください。
- **取得したエビデンス。** `runs/` 配下の実行成果物（スクリーンショット、page source、
  ログ）には、テスト対象アプリの機密情報が含まれることがあります。共有したり、Pull Request に
  添付したり、CI へアップロードしたりする前に内容を確認してください。
- **AI は判定者ではない。** 決定的な `run` ゲートに LLM は一切関与しません。AI はシナリオの
  作成と失敗の調査にのみ使われ、合否は機械的に検証可能なアサーションだけで決まります。
