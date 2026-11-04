[English](BE-0261-serve-author-yaml-roundtrip.md) · **日本語**

# BE-0261 — Author の YAML 編集を serializer で往復させる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0261](BE-0261-serve-author-yaml-roundtrip-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0261") |
| 実装 PR | [#1079](https://github.com/bajutsu-e2e/bajutsu/pull/1079) |
| トピック | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## はじめに

Author エディタは、シナリオの YAML を JavaScript のコードで、テキストとして手作業で編集します。編集するのは次の 2 つの場面です。

- Edit の Apply が、選んだ[セレクタ](../../docs/ja/glossary.md#シナリオのオーサリング)をステップに書き込む。
- Enrich の Accept が、提案されたアサーションと settle の wait を挿入する。

`bajutsu/templates/serve.author.js` の Apply クリックハンドラ（`#au-apply`）と `enrichApply` は、次のように動きます。

- textarea を改行で分割します。
- `trimStart()` や `startsWith('- tap:')` 風の接頭辞照合で、ステップの行を探します。
- 正規表現でインデントを算出します。
- 置換行を継ぎ足します。

ループに parser はありません。フロントエンドが、文字列の形から YAML 構造を再導出しています。

本項目は、この文字列手術を、正しい parse → mutate → serialize の往復に置き換えます。往復では、バックエンドがすでに所有するシナリオモデルと serializer を再利用します。具体的には、`bajutsu/scenario/serialize.py` の `dump_scenario_file` で、Capture の保存がすでに使う関数です。

## 動機

YAML を平坦なテキストとして編集するのは、構造的に脆いやり方です。現在の照合は、次のような正規形の整形を前提としています。

- 1 ステップにつき 1 行の `- action:`
- ブロックスタイル
- 予測できるインデント
- 間に挟まらないコメント
- `- name:` 行の走査で見つかる対象シナリオ

この前提から外れるシナリオでは、Apply や Enrich が誤った行を編集したり、インデントを壊したり、黙って何もしなかったりします。外れる例は、複数行にまたがる flow スタイルのステップ（`- tap: { id: … }`）、ステップ間のコメント、非標準のインデント、1 ファイルに 2 つのシナリオ、`:` や `#` を含むセレクタ値です。さらに、書き込みは検証されないテキストです。そのため、まずい編集は、変更の時点ではなく、デバウンスされた lint が走るまで表に出ません。

プロジェクトには、正しい道具がすでにあります。シナリオには Pydantic モデルと正規の serializer があり、Capture の保存経路は `dump_scenario_file` で往復します。Author の*編集*経路は、その道具の劣化した非可逆版を、ブラウザで再実装しています。

parse → mutate → serialize に統一すると、Apply と Enrich は、元の整形に関係なく構造的に正しくなります。脆い文字列走査の JavaScript も、まとめて消えます。正しさの利得に加えて、一貫性の点でも利得があります。serializer が 1 つになり、「ステップの見た目」という概念が 2 つに割れないためです。

唯一の実質的な緊張は、整形の保存です。素朴な再シリアライズは、著者のファイル全体を流し直し、コメントを捨てかねません。設計では、下記のとおり変更の境界を決め、Apply が無関係な行を書き換えないようにする必要があります。

プライムディレクティブには影響しません。これはオーサリング側の編集で、決定的な `run` は、保存された YAML を従来どおり読みます。

## 詳細設計

1. **往復の境界を決めます。** 1 ステップの編集が、文書全体を流し直したり、コメントを落としたりしないよう、parse → serialize をどこで行うかを決めます。作業では、次の 2 つの選択肢を評価します。
   - (a) パースしたモデルを変更し、ファイル全体の正規 YAML を返すシリアライズ用エンドポイントです。最も単純で、`dump_scenario_file` を再利用できます。ただし、整形とコメントを流し直します。
   - (b) 変更したステップまたは expect のブロックだけを出す、scope 付きの serializer です。フロントエンドが、parser の特定した範囲にそのブロックを継ぎ足します。周囲のテキストは保てますが、手数が増えます。

   本項目は、どちらかを選び、その理由を記録します。
2. **Apply（Edit）をモデル経由にします。** Apply クリックハンドラの行の継ぎ足しを、3 つの操作に置き換えます。パースしたモデルでステップを特定し、セレクタを設定し、シリアライズします。`:` や `#` を含む値の引用符付けを serializer が所有すれば、セレクタを YAML にするヘルパー（`auSelectorYaml`）と、その手作業の引用符付けは不要になります。
3. **Accept（Enrich）をモデル経由にします。** `enrichApply` の expect と settle の行挿入を、モデルの変更とシリアライズに置き換えます。モデルの変更では、アサーションを追加し、最後のステップの後に settle wait を挿入します。これにより、`_extractName`、`stepsEnd`、`expectStart` による行探しがなくなります。
4. **エディタのライブ検証を保ちます。** 結果は、引き続き textarea と既存の lint、audit、ガターの更新に流れます。人が結果を確認して Save します。ここで `save_scenario` を迂回するものはありません。
5. **テストを書きます。** 今日の文字列照合が誤る整形で、往復の正しさを確認します。対象は、flow スタイルのステップ、ステップ間のコメント、`:` や `#` を含むセレクタ値、2 シナリオのファイルです。正しいステップとシナリオが変更されること、残りが選んだ境界の保証どおり保たれることを確認します。

## 検討した代替案

- **文字列照合をその場で堅くする。** 基本がテキスト形のやり方に、特別扱い（flow スタイル、コメント、引用符付け）を積み増す案です。parser が無償で与える正しさには届かず、シナリオ構造についての相反する 2 つの考え方が残ります。
- **ファイル全体を正規再シリアライズし、流し直しを受け入れる。** 実装は最も単純です。しかし、Apply のたびに著者のファイルを整形し直し、コメントを落とすのは、貧しいオーサリング体験です。設計が明示的にそれを選び、UI がそれを示す場合にだけ、許容できます。
- **何もせず、lint の検出に頼る。** lint は、不正な*結果*を指摘できます。しかし、継ぎ足しを誤った後では、著者の意図を復元できません。事後に捕まえるより、壊さないほうが良いです。

## 進捗

> 作業の進行に合わせて最新に保ってください。チェックリストは *詳細設計* の MECE な作業分解を反映し
> （作業単位ごとに 1 ボックス）、ログは変更内容と時期を（古い順に）記録し PR をリンクします。

- [x] Unit 1 — 往復の境界（ファイル全体か scope 付きブロックか）を決めて記録。
- [x] Unit 2 — Apply（Edit）をパースしたモデル＋serializer 経由に。
- [x] Unit 3 — Accept（Enrich）をパースしたモデル＋serializer 経由に。
- [x] Unit 4 — Save 前のライブな lint/audit 確認を保つ。
- [x] Unit 5 — 文字列照合が誤る整形での往復テスト。

**選んだ境界（Unit 1）：scope 付きブロックの継ぎ足し、PyYAML のみ。** エディタは、ファイルの生テキストをコメントごと読み込みます。そのため、ファイル全体を再シリアライズすると、Apply のたびにコメントを落とし、関係しないシナリオまで整形し直します。そこでバックエンドは、次の 4 段階で処理します。

- PyYAML でファイルをパースします。
- 変更するステップまたは expect のブロックの範囲を、`yaml.compose` のソースマークで特定します。
- そのブロックだけを再シリアライズします。
- 生テキストにブロックを継ぎ足します。

ブロックの外側にあるコメントは残ります。コメントを保つ `ruamel` での往復は、採りませんでした。`bajutsu/_yaml.py` と `on`/`off` の真偽値解釈が異なる、2 つ目の YAML エンジンを持ち込むことになるためです。

ログ（古い順）：

- [#1079](https://github.com/bajutsu-e2e/bajutsu/pull/1079) で実装しました。
  - 新しい `bajutsu/scenario/edit.py`（`apply_selector` / `apply_enrichment`）と、`dump_block` serializer ヘルパーを追加しました。
  - AI を使わない serve エンドポイント 2 つ（`/api/scenario/apply-selector`、`/api/scenario/enrich-apply`）を、`bajutsu/serve/operations/author_edit.py` に追加しました。
  - Author エディタの `#au-apply` ハンドラと `enrichApply` は、これらのエンドポイントへ POST するようになりました。`auSelectorYaml`、`enrichAssertionYaml`、`_extractName`、行走査はすべて削除しました。
  - 往復テストは、flow スタイルのステップ、ステップ間のコメント、`:`/`#` を含むセレクタ値、2 シナリオのファイルを対象にします。

## 参考

- [BE-0013 — Scenario GUI editor](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)（Apply）
- [BE-0014 — Demarcation from the existing AI record](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md)（Enrich）
- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface-ja.md)
- `bajutsu/templates/serve.author.js`（`#au-apply` の Apply クリックハンドラ・`enrichApply`・`auSelectorYaml`・`enrichAssertionYaml`）、`bajutsu/scenario/serialize.py`（`dump_scenario_file`）
