[English](BE-XXXX-device-step-capability-preflight.md) · **日本語**

# BE-XXXX — デバイス制御ステップをケイパビリティで preflight ゲートする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-device-step-capability-preflight-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | プラットフォーム拡張（Android / Web / Flutter） |
<!-- /BE-METADATA -->

## はじめに

`push`・`clearKeychain`・`setLocation` といったデバイス制御ステップは、共有のシナリオ語彙に simctl 由来の意味論を持ち込んでいながら、実デバイス環境を持たないバックエンドでは今なお実行時にしか失敗しません。これはまさに BE-0082 がジェスチャーと visual アサーションに対してすでに解決した「late failure」の問題です。本提案は同じ preflight チェックをデバイス制御ステップにも拡張し、ケイパビリティによってゲートすることで、対応していないステップをデバイス操作が始まる前に失敗させます。

## 動機

`bajutsu/orchestrator/types.py:45-59` は、simctl に裏打ちされた操作（`set_location`、`push`、`clear_keychain`、`clear_clipboard`、`set_clipboard`、`get_clipboard`、`home`、`foreground`、`override_status_bar`、`clear_status_bar`）をまとめた Protocol である `DeviceControl` を定義しています。実行を支える実デバイス環境がある場合はランナーがこれを注入し、ない場合（フェイクドライバや、単一デバイスに固定しない並列実行）は `None` のままにします。`bajutsu/orchestrator/actions/handlers/device.py` にあるデバイス制御ステップの各ハンドラ（`_do_set_location`、`_do_push`、`_do_clear_keychain` など）は、いずれも `_need_control(control, "<name>")`（`bajutsu/orchestrator/actions/_registry.py:72-78`）を呼び出し、これが `base.UnsupportedAction` を送出します。しかし、それが起きるのはそのステップが実際に実行される瞬間だけです。

これはまさに、BE-0082（ケイパビリティ preflight チェック）がジェスチャーと visual アサーションについて排除しようとした失敗モードそのものです。「対応していないケイパビリティを最後のステップで必要とするシナリオは、まずデバイス上ですべての先行ステップを実行してから、後になって失敗していた」（`bajutsu/capability_preflight.py:1-9`）。BE-0082 の `unsupported()` は `pinch`/`rotate` を `MULTI_TOUCH` で、`visual` アサーションを `SCREENSHOT` でゲートしており、どちらも各ドライバクラスの `CAPABILITIES` 上で宣言されています。しかしデバイス制御ステップにはケイパビリティトークンが一切なく、実行時に `DeviceControl` がたまたま配線されているかどうかでゲートされています。これは、`scenario` とドライバの `capabilities()` だけの純粋関数である preflight（`bajutsu/capability_preflight.py:109-122`）からは見えません。

この隙間は、idb が常に実体を持つ `DeviceControl` を提供し、Playwright ベースのシナリオがデバイス制御ステップをめったに使わない現状では潜在的なものにとどまっています。しかし Android（BE-0007）や、より広い Web の対応領域が実現した瞬間に、これは避けて通れない問題になります。あるターゲット向けに書かれ `push` や `setLocation` を使うシナリオを新しいバックエンドで実行すると、先行するすべてのステップを実行したあとになって初めて、その構文に対応するものが存在しないことが実行途中で判明します。深刻度は中です。現在出荷されている 2 つのバックエンドでは実際に発生している失敗ではありませんが、新しいバックエンドが増えるたびに悪化することが予見でき、プライムディレクティブ 2（「大きな声で失敗する」。まさに BE-0082 が閉じようとした関心事）を直接後退させます。

## 詳細設計

1. **デバイス制御ステップ用のケイパビリティトークンを追加する。** `bajutsu/drivers/base.py` の `Capability` クラスに、simctl 的なデバイス制御を表すトークン（例えば `DEVICE_CONTROL = "deviceControl"`）を、既存のパターン（`MULTI_TOUCH`、`WEBVIEW`）に倣って追加します。デバイス制御ステップがバックエンドごとにオール・オア・ナッシングのケイパビリティである限り（idb は `DeviceControl` を通じてそのすべてを持ち、あるバックエンドはこのファミリー全体を対応するかしないかのどちらかである限り）、共有のトークンを 1 つ用意すれば十分です。既存の `DeviceControl` Protocol がまさにこの理由でこれらを 1 つの単位としてまとめています。将来のバックエンドが一部だけをサポートする場合は、操作ごとの細粒度なトークンに分割します。その判断は、部分的な対応を最初に必要とするバックエンドが現れたときまで先送りできます。
2. **対応しているバックエンドでケイパビリティを宣言する。** idb は実体を持つ `DeviceControl` を裏付けとして持つため、idb の `CAPABILITIES` frozenset（`bajutsu/drivers/idb.py:326-328`）に新しいトークンを追加します。Playwright の `CAPABILITIES`（`bajutsu/drivers/playwright.py:566-576`）にはこのトークンを追加しません。これは、Playwright のシナリオには `DeviceControl` が配線されていないという現状と一致します。
3. **`capability_preflight.py` の要求テーブルを拡張する。** `bajutsu/capability_preflight.py` の `_REQUIREMENTS` に `_Requirement` エントリを（手順 1 で選んだトークンの粒度に応じて、1 つ、あるいはデバイス制御ステップの種類ごとに）追加します。`locations` 関数は（`_walk_steps` を再利用して）ステップツリーを歩き、`step.set_location`、`step.push`、`step.clear_keychain` などが `None` でないステップを見つけます。これにより `unsupported()` は、対象のバックエンドが宣言していないケイパビリティを必要とするデバイス制御ステップの位置を、既存の `pinch`/`rotate`/`visual` のエントリとまったく同じように、デバイス操作が始まる前にすべて報告できるようになります。
4. **実行時の `_need_control` チェックは、主要なゲートではなく安全網として残す。** preflight チェックが、シナリオを決定的かつ早期に失敗させる主要な仕組みになります。既存の `_need_control` による `UnsupportedAction` は、ケイパビリティ上はバックエンドがデバイス制御に対応していても、その実行時の環境（フェイクドライバ、あるいは単一デバイスに固定しない並列実行）が配線されていない場合に備える多層防御として残します。これはケイパビリティではなく環境の問題であり、preflight が解決すべき対象ではありません。

これはプライムディレクティブ 2（デバイス操作の前に大きな声で失敗する）とプライムディレクティブ 3（app-agnostic）の両方に直接資するものです。修正はどのターゲット向け config にも置かず、ケイパビリティ／preflight という抽象化の内部だけに完結します。そして Android にとっての先取りの仕組みにもなります。同じテーブルが、Android バックエンドが実装する・しないデバイス制御操作をゲートし、ランナー側の変更は一切不要です。

## 検討した代替案

- **この隙間を放置し、`_need_control` の実行時失敗に任せる。** 最も安上がりですが、BE-0082 がジェスチャーについてすでに排除した「先行ステップをすべて実行してから、後になって失敗する」という問題をそのまま再現してしまいます。同じ失敗クラスに対する保護が一貫していないことは、preflight がない状態より悪いことです。ユーザーに「preflight は当てにならない」と学習させてしまうためです。
- **専用のトークンを設けず、既存の `MULTI_TOUCH`/`SCREENSHOT` 的な場当たり的な要求にデバイス制御ケイパビリティを混ぜ込む。** 却下します。デバイス制御は本質的に異なる関心事です（アクチュエータ自体のジェスチャー・描画ケイパビリティではなく、simctl に裏打ちされた OS 操作です）。無関係なトークンを流用すると、要求の名前が実態と食い違い、`unsupported()` のエラーメッセージが誤解を招くものになります。
- **最初からデバイス制御の各操作に、それぞれ専用の細粒度なケイパビリティトークンを与える。** より正確ではありますが、どのバックエンドも部分的な対応を必要としていない段階では先取りしすぎです。idb は今日、`DeviceControl` の対応領域全体を 1 つの単位としてサポートしており、単一の共有トークンは現状のバックエンドの実態と一致します。後から（実際にそれを必要とするバックエンドが現れたときに）分割するのは、破壊的な変更ではなく要求テーブルへの小さな追加変更で済みます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `bajutsu/drivers/base.py` の `Capability` にデバイス制御用のケイパビリティトークンを追加する
- [ ] 実体を持つ `DeviceControl` を提供するバックエンド（idb）でトークンを宣言する
- [ ] `capability_preflight.py` の `_REQUIREMENTS` を拡張し、デバイス制御ステップをゲートする
- [ ] `_need_control` の実行時チェックは、主要なゲートではなく多層防御のフォールバックとして残す

まだ着手した PR はありません。

## 参考

- `bajutsu/orchestrator/types.py:45-59` — simctl 由来の操作をまとめた `DeviceControl` Protocol
- `bajutsu/orchestrator/actions/_registry.py:72-78` — `_need_control`、現状で唯一の（実行時の）ゲート
- `bajutsu/orchestrator/actions/handlers/device.py` — デバイス制御ステップの各ハンドラ
- `bajutsu/capability_preflight.py:1-9,109-122` — BE-0082 の preflight チェックと、本提案が拡張する `unsupported()` のエントリポイント
- `bajutsu/drivers/base.py` — 新しいトークンを追加する `Capability`
- `bajutsu/drivers/idb.py:326-328`、`bajutsu/drivers/playwright.py:566-576` — 各バックエンドの `CAPABILITIES` 宣言
- 関連: BE-0082（ケイパビリティ preflight チェック）、BE-0035（デバイス制御プリミティブ）、BE-0007（Android バックエンド）
- 2026-07-02 のコードベース分析レポート（設計）に由来します。
