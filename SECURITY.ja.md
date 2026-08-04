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

Bajutsu は、バックエンドに依存しないドライバの上に構築された防御的な
E2E（end-to-end）テストツールです。iOS Simulator（XCUITest）、web（Playwright）バックエンド、
Android（adb）バックエンドがいずれも実装済みです。本プロジェクトならではの注意点をいくつか
挙げます。

- **API キー / シークレット。** `ANTHROPIC_API_KEY` が必要なのは AI パス（`record`、
  `run --system-alert-handling`）だけです。API キーはコミットしたり共有したりせず、`.env`
  （gitignore 済み）に保管してください。決定的な `run`／CI ゲートにシークレットは不要です。
  追跡対象の pre-commit フックと CI での再スキャンが、どちらも
  [gitleaks](https://github.com/gitleaks/gitleaks) を使ってシークレットをブランチに
  取り込まれる前後で検出します（詳細は
  [`docs/ja/ai-development.md`](docs/ja/ai-development.md#コミット前にシークレットをブロックする)）。
  ただし、これは最後の砦であり、シークレットを貼り付けてよい理由にはなりません。
- **`setClipboard` はデバイス全体で共有されるクリップボードに書き込みます。** ペースト操作の
  ために使う `setClipboard` シナリオステップは、対象のクリップボードへ値を書き込みます。
  iOS Simulator では `simctl pbcopy` を使います。Android では、ホスト側から OS のクリップボードを
  直接叩くのではなく、`adb shell am broadcast` でアプリ内の受信レシーバ（BajutsuAndroid、
  BE-0233）に指示し、そこから OS のクリップボードへ書き込みます。そのため、テスト対象アプリが
  この受信レシーバを組み込んでいることが前提になります。このクリップボードは、同じ Simulator や
  デバイスにアクセスできる他のプロセスからも読み取れます。シークレットやワンタイムパスコードを
  この経路で書き込まないようにしてください。
- **取得したエビデンス。** `runs/` 配下の実行成果物（スクリーンショット、page source、
  ログ）には、テスト対象アプリの機密情報が含まれることがあります。共有したり、Pull Request に
  添付したり、CI へアップロードしたりする前に内容を確認してください。
- **AI は判定者ではない。** 決定的な `run` ゲートに LLM は一切関与しません。AI はシナリオの
  作成と失敗の調査にのみ使われ、合否は機械的に検証可能なアサーションだけで決まります。
