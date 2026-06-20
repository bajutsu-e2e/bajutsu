[English](BE-0010-update-scope-statement.md) · **日本語**

# BE-0010 — スコープ文の更新

* 提案: [BE-0010](BE-0010-update-scope-statement-ja.md)
* Author: [@0x0c](https://github.com/0x0c)
* 状態: **提案**
* トラック: [提案](../../README-ja.md#提案)
* トピック: プラットフォーム拡張（Android / Web / Flutter）

## はじめに

マルチプラットフォーム化はコードだけでなく**戦略的なスコープ変更**です。現状の Bajutsu は iOS Simulator 限定とドキュメント化されています（[DESIGN §1](../../../DESIGN.md)、[README](../../../README.md)）。最初の本物の 2 つ目のプラットフォームが着地したとき（まず Web、次に Android —— 横断的な抽象化作業は [BE-0009](../../proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md) を参照）、プロジェクトが宣言するスコープもそれに合わせて動かす必要があります。本項目は、それらのドキュメントとポジショニングの変更を、意図的に協調した一手として追跡し、プロダクト説明がコードから遅れないようにします。

## 動機

スコープ文は重要な役割を担います: 読み手の期待を設定し、設計の論拠を枠づけ、コントリビュータに何が範囲内・範囲外かを伝えます。コードが Android と Web を操作するまで育ったのにドキュメントが依然「iOS Simulator 限定」と言っていれば、プロジェクトは自らを誤って表現することになり、丁寧に論じた「なぜ iOS 限定か」の論拠は、移されるのではなく陳腐化します。スコープ更新を独立した項目として扱うことで、それが最初のプラットフォームに遅れて漂うのではなく、**同じ変更で**着地することを保証します。

## 詳細設計

### これが引き起こすスコープ文の更新

最初の新プラットフォームが着地したら（段階 1・Web）、同じ変更で更新します。

- **[DESIGN §1](../../../DESIGN.md)** の「やること / やらないこと」—— iOS Simulator 限定 → マルチプラットフォーム。「実機 / クラウドデバイスファーム」の論拠を、引き続き該当する箇所へ移します。
- **[README](../../../README.md) / [README.ja](../../../README.ja.md)** —— プロダクト一文と中核原則のセクション。
- **[architecture 実装状況](../../../docs/ja/architecture.md)** —— 実装状況の表に新バックエンドを登録します。
- **docs ナビ** —— [`docs/README.md`](../../../docs/README.md) と [`docs/ja/README.md`](../../../docs/ja/README.md) の両方。

### Prime directive は不変に保つ

スコープは**広がります**が、prime directive は**変わりません**。決定性ファースト・app-agnostic・*AI はオーサーであり失敗の調査者であって、決して判定者にならない* は Android でも Web でも同一に適用されます。とりわけ、**どの新プラットフォームも Tier-2 の run / CI ゲートに LLM（大規模言語モデル）を持ち込んではなりません** —— 合否はどのバックエンドでも完全に決定的なままです。スコープ更新はこれらの directive を弱めるのではなく再確認するものでなければなりません: 対象面は広がるが、保証は保たれます。

## 検討した代替案

- **スコープ文を漂わせ、後で直す。** 却下: コードが Web を操作するのにドキュメントが「iOS Simulator 限定」と言うプロダクトは自らを誤って表現し、元の iOS 限定の論拠は、移されるのではなく陳腐化します。更新を最初のプラットフォームの着地に結びつけることで、ドキュメントとコードを誠実に保ちます。
- **これらのドキュメント編集をプラットフォーム別バックエンド項目に畳み込む**（[BE-0041](../../proposals/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)、[BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md)）。却下: スコープ変更は横断的（DESIGN・README・architecture 実装状況・docs ナビ）かつ戦略的なので、独立した項目として追跡することで、バックエンドの実装詳細の中に埋もれて失われるのを防ぎます —— それでいて段階 1 と同じ変更で着地します。

## 参考

- [DESIGN §1](../../../DESIGN.md)（スコープ: やること / やらないこと）
- [README](../../../README.md)、[README.ja](../../../README.ja.md)
- [architecture.md](../../../docs/ja/architecture.md)（実装状況）
- 関連項目: [BE-0009](../../proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)（抽象のクロスプラットフォーム化）、[BE-0041](../../proposals/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)（Web Playwright バックエンド）、[BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md)（Android バックエンド）
