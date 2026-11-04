[English](BE-XXXX-android-non-ascii-text-entry.md) · **日本語**

# BE-XXXX — 常駐サーバーによる Android の非 ASCII 文字入力

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-android-non-ascii-text-entry-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | PR の作成後に記入します |
| トピック | Platform support |
<!-- /BE-METADATA -->

## はじめに

Android backend の `type` step で、日本語や絵文字を含む任意の文字列を入力できるようにします。これまでは、非 ASCII 文字が 1 文字でも含まれると、この step はデバイス上でクラッシュしていました。

正しく扱える ASCII には `adb shell input text` を使い続けます。それ以外は、常駐 UI Automator サーバーの新しい `/type` エンドポイントで入力します。常駐 UI Automator サーバーとは、Bajutsu がデバイス上で動かし続けるインストルメンテーションのプロセスです。階層の読み取りとジェスチャーを担当します。

## 動機

`input text` は、仮想キーボードの文字マップを通じて各文字をキーイベントに変換します。文字マップには非 ASCII 文字に対応するキーがないので、検索結果が null になり、コマンドが異常終了します。

```text
java.lang.NullPointerException: Attempt to get length of null array
    at com.android.server.input.InputShellCommand.sendText(InputShellCommand.java:319)
```

step は `adb shell` の 0 以外の終了コードで失敗するだけで、原因も対処法も示しません。日本語アプリのシナリオは Android で検索欄やフォームを埋められず、同じシナリオが他の backend では動きます。テスト対象アプリの言語を入力できないテストツールでは、そのアプリの文字列の経路を検証できません。

`input text` には、気付きにくい欠陥がもう 1 つあります。文字どおりの `%s` を空白のエスケープとして解釈し、それを回避する手段もないので、`100%sure` は `100 ure` と入力されます。step は成功し、入力欄には誤った値が残ります。

成果は `demos/showcase/scenarios/text_entry_android.yaml` で確かめられます。このシナリオは `馬`、`術🐎`、` x`、`語` を順に入力します。Compose と Views の両方の showcase アプリで、入力欄の値は `馬術🐎 x語` になります。

## 詳細設計

### 入力経路の選択

`AdbDriver.type_text` は `adb.input_text_can_type(text)` で文字列を判定します。条件は、ASCII のみで文字どおりの `%s` を含まないことです。条件を満たす文字列は従来どおり `input text` で入力するので、既存のシナリオは実際のキーイベントを送り続けます。満たさない文字列は `/type` に送ります。経路は文字列だけで決まるので、選択は決定的です（プライムディレクティブ 2）。

### `/type` エンドポイント

host は、文字列を base64 にして本文に入れた `POST /type` を送ります。入力値は秘密情報の場合があります。リクエストの宛先はプロキシやログに残りやすいので、文字列を宛先には載せません。base64 にすると、サーバーの文字単位の読み取りでも `Content-Length` とバイト数が一対一で対応します。サーバーは本文もその長さもログに記録しません。

サーバーは次の順に処理します。

1. アプリが idle になるのを待ちます。前の step が入力した文字列を読み落とさないためです。続けて、編集可能なノードが入力フォーカスを持つまで最大 2 秒待ちます。`into` のタップによるフォーカスが届くまでは、タップ前のノードがフォーカスを持っている場合があるためです。
2. ノードがパスワード欄の場合、編集できない場合、`ACTION_SET_TEXT` を提供しない場合は拒否します。
3. 現在の文字列と選択範囲から新しい値を組み立てます。`ACTION_SET_TEXT` は内容全体を置き換えます。一方、文字入力はカーソル位置への挿入なので、挿入後の結果をサーバーが計算します。入力欄が文字列として報告するヒントは空として扱います。
4. `ACTION_SET_TEXT` を実行します。挿入先が末尾でなかった場合は、カーソルを挿入した文字列の直後に戻します。

### 失敗時の動作

この文字列を入力する別の経路はないので、拒否された場合は理由を示して step を失敗させます。

| 状況 | 結果 |
|---|---|
| このリースに常駐サーバーがない | サーバーが必要であることを示して step が失敗します |
| サーバーが `/type` より古い（404） | サーバーの再ビルドを促して step が失敗します |
| フォーカス中の欄がない、パスワード欄である、または `ACTION_SET_TEXT` を提供しない | デバイスが返した理由を示して step が失敗します |
| リクエストの送信後に応答が失われた | 警告を出し、再送せずに実行を続けます |

最後の行は、タップのジェスチャーがすでに採用している規則に従います。再送すると文字列を二重に入力するおそれがあります。一方、届かなかった文字列はシナリオ自身のアサーションで失敗します。応答にもログにも文字列は含めません。

### 制約

`ACTION_SET_TEXT` は入力欄に直接届くので、この経路は入力メソッドエディター（IME）を通りません。変換中の状態も変換候補も発生しません。シナリオはこの経路で IME の動作を試験できません。Flutter の独自描画の入力欄のように `ACTION_SET_TEXT` を提供しない欄では、step が失敗します。

文字列はフォーカス中の編集可能な欄に入ります。エンドポイントはその欄が `into` の対象であるかを確認しません。`input text` のキーイベントもフォーカス中の欄に届くので、動作は同じです。

パスワード欄は一律に拒否します。パスワード欄のアクセシビリティ上の文字列は、伏せられているか存在しません。欄が空であることさえ確認できず、`ACTION_SET_TEXT` が何を上書きするのかをサーバーが判断できないためです。

## 検討した代替案

| 代替案 | 採用しなかった理由 |
|---|---|
| 文字列を確定する独自の IME | 実際の入力に最も近いものの、端末全体の設定を変更するので、クラッシュ時も含めて復元が必要です |
| ADBKeyBoard | 同じく IME の切り替えが必要で、さらにテスト対象の端末へサードパーティ APK を導入します |
| クリップボードとペースト | クリップボードを上書きして `clipboard` アサーションを壊し、入力した秘密情報も残します |
| `Instrumentation.sendStringSync` | `input text` と同じ文字マップを通るので、同じ文字で失敗します |
| 非 ASCII の `type` をすべて明確なエラーにする | 変更は小さいものの、シナリオを実行できないままです |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `AdbDriver.type_text` で `input_text_can_type` により経路を選択する
- [x] 常駐サーバーに `/type` エンドポイントとリクエスト本文の読み取りを追加する
- [x] host 側のクライアント、チャネルの接続、失敗の変換を追加する
- [x] ユニットテストと、Compose と Views 向けの `text_entry_android` E2E シナリオを追加する
- [x] driver のドキュメントとサーバーの README を両言語で更新する

ログ:

- 2026-11-04: API 34 のエミュレーターで NullPointerException を再現してから、経路を実装しました。新しいシナリオは同じ環境の Compose と Views で成功しました。常駐サーバーを無効にすると、理由を示して失敗しました。パスワード欄の拒否と、文字列の途中への挿入を検証する E2E シナリオはありません。

## 参考

- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)：このエンドポイントを追加した常駐 UI Automator サーバー
- [BE-0155](../BE-0155-idb-input-text-via-stdin/BE-0155-idb-input-text-via-stdin-ja.md)：入力値を host のコマンドラインに載せない規則。このエンドポイントはリクエストの宛先にも同じ考え方を適用します
- [`AccessibilityNodeInfo.ACTION_SET_TEXT`](https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo#ACTION_SET_TEXT)：エンドポイントが使うプラットフォームのアクション
