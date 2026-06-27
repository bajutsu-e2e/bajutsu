[English](BE-0010-update-scope-statement.md) · **日本語**

# BE-0010 — スコープ文の更新

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0010](BE-0010-update-scope-statement-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | プラットフォーム拡張（Android / Web / Flutter） |
<!-- /BE-METADATA -->

## はじめに

マルチプラットフォーム化はコードだけの話ではなく、**戦略的なスコープ変更**です。現状の Bajutsu は、iOS Simulator 限定とドキュメントに書かれています（[DESIGN §1](../../../DESIGN.md)、[README](../../../README.md)）。2 つ目の本格的なプラットフォームが最初に着地したとき（まず Web、続いて Android。横断的な抽象化の作業は [BE-0009](../../proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md) を参照）、プロジェクトが宣言するスコープもそれに合わせて動かす必要があります。本項目は、それらのドキュメントと位置づけの変更を、意図して協調させた一手として扱い、プロダクトの説明がコードから遅れないようにします。

## 動機

スコープ文は重要な役割を担います。読み手の期待を定め、設計の論拠を枠づけ、コントリビュータに何が範囲内で何が範囲外かを伝えます。コードが Android と Web を操作するまで育ったのに、ドキュメントが依然として「iOS Simulator 限定」と言っているなら、プロジェクトは自らを誤って説明していることになり、丁寧に論じた「なぜ iOS 限定か」の論拠は、移し替えられるのではなく陳腐化します。スコープの更新を独立した項目として扱えば、それが最初のプラットフォームに遅れて漂うのではなく、**同じ変更で**着地することを保証できます。

## 詳細設計

### これが引き起こすスコープ文の更新

最初の新プラットフォームが着地したら（段階 1、Web）、同じ変更で次を更新します。

- **[DESIGN §1](../../../DESIGN.md)** の「やること / やらないこと」：iOS Simulator 限定からマルチプラットフォームへ。「実機 / クラウドデバイスファーム」の論拠は、引き続き当てはまる箇所へ移します。
- **[README](../../../README.md) / [README.ja](../../../README.ja.md)**：プロダクトの一文紹介と中核原則のセクション。
- **[architecture 実装状況](../../../docs/ja/architecture.md)**：実装状況の表に新しいバックエンドを登録します。
- **docs ナビ**：[`docs/README.md`](../../../docs/README.md) と [`docs/ja/README.md`](../../../docs/ja/README.md) の両方。

### Prime directive は不変に保つ

スコープは**広がります**が、prime directive は**変わりません**。決定性ファースト、app-agnostic、そして *AI はオーサーであり失敗の調査者であって、決して判定者にならない* という原則は、Android でも Web でも同じように適用されます。とりわけ、**どの新プラットフォームも Tier-2 の run / CI ゲートに LLM（大規模言語モデル）を持ち込んではなりません**。合否はどのバックエンドでも完全に決定的なままです。スコープの更新は、これらの directive を弱めるのではなく再確認するものでなければなりません。対象面は広がりますが、保証は保たれます。

## 検討した代替案

- **スコープ文を漂わせておき、後で直す。** 却下しました。コードが Web を操作するのにドキュメントが「iOS Simulator 限定」と言うプロダクトは、自らを誤って説明することになり、元の iOS 限定の論拠は、移し替えられるのではなく陳腐化します。更新を最初のプラットフォームの着地に結びつければ、ドキュメントとコードを誠実に保てます。
- **これらのドキュメント編集を、プラットフォーム別のバックエンド項目に畳み込む**（[BE-0041](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)、[BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md)）。却下しました。スコープ変更は横断的（DESIGN、README、architecture 実装状況、docs ナビ）でありかつ戦略的なので、独立した項目として追跡すれば、バックエンドの実装詳細の中に埋もれて見失われるのを防げます。それでいて段階 1 と同じ変更で着地させられます。

## 参考

- [DESIGN §1](../../../DESIGN.md)（スコープ: やること / やらないこと）
- [README](../../../README.md)、[README.ja](../../../README.ja.md)
- [architecture.md](../../../docs/ja/architecture.md)（実装状況）
- 関連項目: [BE-0009](../../proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)（抽象のクロスプラットフォーム化）、[BE-0041](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)（Web Playwright バックエンド）、[BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md)（Android バックエンド）
