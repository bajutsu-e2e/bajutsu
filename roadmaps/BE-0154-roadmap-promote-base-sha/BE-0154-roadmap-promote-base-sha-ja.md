[English](BE-0154-roadmap-promote-base-sha.md) · **日本語**

# BE-0154 — roadmap-promote をベース SHA から実行する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0154](BE-0154-roadmap-promote-base-sha-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案（保留）** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0154") |
| トピック | セキュリティ強化 |
| 無効化 | [BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders-ja.md) |
<!-- /BE-METADATA -->

## はじめに

> **保留。前提は [BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders-ja.md) で失効しました。**
> 本項目は `roadmap-promote.yml` を強化するものですが、BE-0159 がこのワークフローを
> `scripts/promote_roadmap_items.py` ごと削除しました（フラット構成では昇格するものがありません）。本項目が
> 守ろうとしていた `contents: write` を持つ PR 依存のワークフローはもう存在しません。残った自動化を見直しても、
> 書き直す先はありませんでした。いまも `contents: write` を持つロードマップ自動化（`roadmap-id.yml` と
> `roadmap-drift-check.yml`）は `main` への `push` をトリガーとし、信頼できる `main` のチェックアウトから
> スクリプトを実行します。`auto-merge.yml` はリポジトリのコードを一切チェックアウトしません。したがって、
> 本項目が守ろうとしていた「PR の head にあるスクリプトを `contents: write` で実行する」パターンは、もはや
> どこにも存在しません。そのため本項目は保留とし、危険を後から強化するのではなく設計段階で取り除いた BE-0159 に
> よって無効化されたものとして扱います。記録として残します。

`roadmap-promote.yml` は `contents: write` を持ちながら、PR 自身の head ref からスクリプトを
チェックアウトして実行しています。本提案は、実行するスクリプトを信頼できるベース SHA 側へ移し、
PR がその権限で動くコードに影響を与えられないようにします。

## 動機

`.github/workflows/roadmap-promote.yml` は `pull_request` をトリガーに、次の権限を付与して実行されます。

    permissions:
      contents: write

その上で `ref: ${{ github.head_ref }}`（PR ブランチそのもの）をチェックアウトした上で
`python3 scripts/promote_roadmap_items.py` を実行し、その後同じブランチへコミットして push
し返します。実行されるスクリプトが PR の head から来るため、`scripts/promote_roadmap_items.py`
を編集する PR は、このワークフローがリポジトリへの書き込み権限で実行するコードを制御
できてしまいます。ただしこのワークフローには fork ガード（`if: github.event.pull_request.head.repo.full_name == github.repository`）があり、外部の fork からの PR はこのステップに一切
到達しません。したがって現時点でのリスクは、同一リポジトリ内のブランチ（内部の contributor か、
すでに侵害されたアカウント）に限られており、任意の外部 contributor には及びません。

fork ガードがあるため深刻度は Low ですが、「`contents: write` の下で PR head 側のスクリプトを
実行する」という形自体は、このリポジトリの他の CI ハードニング（サードパーティ action の SHA
ピン留めなど）が一般に避けている形です。ガードがすでに到達範囲を制限しているとはいえ、塞いで
おく価値があります。

## 詳細設計

1. **`scripts/promote_roadmap_items.py` を実行するステップで、`github.head_ref` ではなく
   ベースブランチ ref（`github.base_ref`）/ ベース SHA（`github.event.pull_request.base.sha`）をチェックアウトする。** このスクリプトの
   役割（提案のディレクトリを `Status:` フィールドに合わせて移動し、インデックスを再生成する）
   は PR で変更されたロードマップファイルを作業ツリーから読む必要があるため、チェックアウト
   自体は引き続き PR の内容を含める必要があります。修正すべきは、マージ後・head 側のツリーに
   対して操作しつつも、*スクリプト自体*は信頼できるベースコミットから実行することです。例えば
   `actions/checkout` を `ref: ${{ github.event.pull_request.base.sha }}` で行ってから head を
   マージする方法や、スクリプトファイルのベース ref 版だけを取得して head のチェックアウトに
   対して実行する方法が考えられます。
2. **既存の fork ガードは多層防御として残す。** ベース SHA での修正によって head 側スクリプトの
   信頼性の問題自体は解消されるため、ガードは唯一の防御ではなく、二段目の冗長な防御層になります。
3. **ワークフローのコメントに経緯を残す。** `roadmap-promote.yml` にすでにある根拠コメントの
   流儀に合わせ、なぜスクリプトをベース ref から取得するのかを説明しておき、将来の編集で
   head ref 実行が気づかれずに再び入り込むのを防ぎます。

## 検討した代替案

- **fork ガードだけに頼る。** 却下しました。これはまさに本提案が塞ごうとしている抜け穴です。
  ガードは外部 fork は防ぎますが、編集済みスクリプトを実行する同一リポジトリ内のブランチは
  防げません。
- **`contents: write` を外し、メンテナが手動で `make roadmap-promote` を実行する運用にする。**
  却下しました。BE-0078 と BE-0089 が自動化した手作業を再び持ち込むことになり、深刻度が低く
  すでにガードされたリスクと引き換えに、ロードマップ PR のたびに発生する手作業を増やして
  しまいます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] ワークフローが `scripts/promote_roadmap_items.py` を実行する際、PR head ではなくベース
      SHA から取得するようにする。
- [ ] fork ガードを多層防御として残し、ベース SHA を使う理由をコメントで文書化する。

まだ着手した PR はありません。

## 参考

`.github/workflows/roadmap-promote.yml`。関連: BE-0069（実行可能な contributor ガードレール）、
BE-0089（マージ時 BE-ID 割り当て）、BE-0078（ロードマップの状態別フォルダ）。2026-07-02 の
コードベース分析レポート（セキュリティ）に基づきます。
