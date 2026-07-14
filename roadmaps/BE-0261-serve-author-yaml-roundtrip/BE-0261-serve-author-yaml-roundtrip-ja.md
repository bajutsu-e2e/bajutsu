[English](BE-0261-serve-author-yaml-roundtrip.md) · **日本語**

# BE-0261 — Author の YAML 編集を serializer で往復させる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0261](BE-0261-serve-author-yaml-roundtrip-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0261") |
| トピック | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## はじめに

Author エディタがシナリオへ変更を反映するとき — 選んだ selector をステップに書き込む（Edit の Apply）、提案された
アサーションと settle の wait を挿入する（Enrich の Accept）— YAML を JavaScript のコードで、テキストとして手作業で編集します。
`bajutsu/templates/serve.author.js` の Apply クリックハンドラ（`#au-apply`）と `enrichApply` は、textarea を改行で分割し、`trimStart()` や
`startsWith('- tap:')` 風の接頭辞照合でステップの行を探し、正規表現でインデントを算出し、置換行を継ぎ足します。ループに
parser はありません。フロントエンドが文字列の形から YAML 構造を再導出しています。

本項目はこの文字列手術を、正しい parse → mutate → serialize の往復に置き換えます。バックエンドがすでに所有する同じ
シナリオモデルと serializer（`bajutsu/scenario/serialize.py` の `dump_scenario_file`、Capture の保存がすでに使う関数）を
再利用します。

## 動機

YAML を平坦なテキストとして編集するのは、構造的に脆いやり方です。現在の照合は正規形の整形を前提とします。1 ステップ 1 行の
`- action:`、ブロックスタイル、予測できるインデント、間に挟まらないコメント、`- name:` 行の走査で見つかる対象シナリオ。
そこから外れるシナリオ — 複数行にまたがる flow スタイルのステップ（`- tap: { id: … }`）、ステップ間のコメント、非標準の
インデント、1 ファイルに 2 つのシナリオ、`:` や `#` を含む selector 値 — では、Apply/Enrich が誤った行を編集したり、
インデントを壊したり、黙って何もしなかったりします。しかも書き込みは検証されないテキストなので、まずい編集はデバウンスされた
lint が走るまで表に出ず、変更の時点では分かりません。

プロジェクトには正しい道具がすでにあります。シナリオには Pydantic モデルと正規の serializer があり、Capture の保存経路は
`dump_scenario_file` で往復します。Author の*編集*経路は、その劣化した非可逆版をブラウザで再実装しています。parse → mutate →
serialize に統一すれば、Apply と Enrich は元の整形に関係なく構造的に正しくなり、脆い文字列走査の JS がまとまって消えます。
正しさの利得に加え、一貫性の利点（serializer が 1 つになり、「ステップの見た目」という概念が 2 つに割れない）でもあります。

唯一の実質的な緊張は整形の保存です。素朴な再シリアライズは著者のファイル全体を流し直し、コメントを捨てかねません。設計では
（下記のとおり）変更の境界を決め、Apply が無関係な行を書き換えないようにする必要があります。prime directive には影響しません。
これはオーサリング側の編集で、決定的な `run` は保存された YAML を従来どおり読みます。

## 詳細設計

1. **往復の境界を決める。** 1 ステップの編集が文書全体を流し直したりコメントを落としたりしないよう、parse→serialize をどこで
   行うかを決めます。作業内で評価する選択肢:
   (a) パースしたモデルを変更し、ファイル全体の正規 YAML を返すシリアライズ用エンドポイント（最も単純で `dump_scenario_file`
   を再利用するが、整形/コメントを流し直す）。(b) 変更したステップ/expect ブロックだけを出し、parser が特定した範囲にフロント
   エンドが継ぎ足す scope 付き serializer（周囲のテキストを保つが手数は増える）。本項目はどちらかを選び理由を記録します。
2. **Apply（Edit）をモデル経由に。** Apply クリックハンドラの行継ぎ足しを次に置き換えます。パースしたモデルでステップを特定し、selector を
   設定し、シリアライズします。`:`/`#` を含む値の引用符付けを serializer が所有すれば、selector→YAML ヘルパー（`auSelectorYaml`）と
   その手作業の引用符付けは不要になります。
3. **Accept（Enrich）をモデル経由に。** `enrichApply` の expect/settle 行挿入を、モデルの変更（アサーションの追加、最後の
   ステップの後への settle wait の挿入）＋シリアライズに置き換え、`_extractName`/`stepsEnd`/`expectStart` の行探しを取り除きます。
4. **エディタのライブ検証を保つ。** 結果は引き続き textarea と既存の lint/audit/ガター更新に流れ、人が確認して Save します。
   ここで `save_scenario` を迂回するものは何もありません。
5. **テスト。** 今日の文字列照合が誤る整形での往復の正しさ — flow スタイルのステップ、ステップ間のコメント、`:`/`#` を含む
   selector 値、2 シナリオのファイル — を対象に、正しいステップ/シナリオが変更され、残りが選んだ境界の保証どおり保たれることを
   確認します。

## 検討した代替案

- **文字列照合をその場で堅くする。** 基本がテキスト形のやり方に特別扱い（flow スタイル、コメント、引用符付け）を積み増すもので、
   parser が無償で与える正しさには決して届かず、シナリオ構造の相反する 2 つの考え方を残します。
- **ファイル全体を正規再シリアライズし、流し直しを受け入れる。** 実装は最も単純ですが、Apply のたびに著者のファイルを整形し直し
   コメントを落とすのは貧しいオーサリング体験です。設計が明示的にそれを選び UI が示す場合にのみ許容できます。
- **何もしない（lint の検出に頼る）。** lint は不正な*結果*は指摘できますが、継ぎ足しを誤った後で著者の意図は復元できません。
   事後に捕まえるより、壊さないほうが良いです。

## 進捗

> 作業の進行に合わせて最新に保ってください。チェックリストは *詳細設計* の MECE な作業分解を反映し
> （作業単位ごとに 1 ボックス）、ログは変更内容と時期を（古い順に）記録し PR をリンクします。

- [ ] Unit 1 — 往復の境界（ファイル全体か scope 付きブロックか）を決めて記録。
- [ ] Unit 2 — Apply（Edit）をパースしたモデル＋serializer 経由に。
- [ ] Unit 3 — Accept（Enrich）をパースしたモデル＋serializer 経由に。
- [ ] Unit 4 — Save 前のライブな lint/audit 確認を保つ。
- [ ] Unit 5 — 文字列照合が誤る整形での往復テスト。

## 参考

- [BE-0013 — Scenario GUI editor](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)（Apply）
- [BE-0014 — Demarcation from the existing AI record](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md)（Enrich）
- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface-ja.md)
- `bajutsu/templates/serve.author.js`（`#au-apply` の Apply クリックハンドラ・`enrichApply`・`auSelectorYaml`・`enrichAssertionYaml`）、`bajutsu/scenario/serialize.py`（`dump_scenario_file`）
