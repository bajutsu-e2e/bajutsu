[English](BE-0432-devicefarm-pretest-extension-hook.md) · **日本語**

# BE-0432 — Device Farmテストスペックの汎用的なpre_testフック

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0432](BE-0432-devicefarm-pretest-extension-hook-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0432") |
| 実装 PR | [#2033](https://github.com/bajutsu-e2e/bajutsu/pull/2033) |
| トピック | デバイスクラウド実行 |
| 関連 | [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`render_test_spec`は、Device Farmカスタム環境テストスペックをレンダリングします。レンダリングされた
スペックは、予約済みデバイス上で一連の[シナリオ](../../docs/ja/glossary.md#シナリオのオーサリング)を実行します。
この関数は[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md)が導入し、
現在は`bajutsu/common/cloud/devicefarm/_functions.py`にあります。`bajutsu.common.cloud.devicefarm`が
再エクスポートしています。

そのスペックの`pre_test`フェーズは現在、1つのコマンドしか実行しません。予約済みデバイスの疎通確認
プローブです。呼び出し元が、デバイスに別の処理を先にさせたい場合があります。その処理は、`test`
フェーズの`bajutsu run`呼び出しより前に終える必要があります。ところが呼び出し元には、`pre_test`
フェーズへコマンドを追加する手段がありません。唯一の代替は、`render_test_spec`がすでに
レンダリングしているものをすべて作り直すことです。

本提案は、`pre_test_commands`パラメータを追加します。呼び出し元はこれを使い、自分のコマンドを
`pre_test`フェーズへ差し込みます。あるデプロイ環境のbackendに固有のデバイス側セットアップは、
これにより`bajutsu/`の外側だけで完結します。

## 動機

大前提3は、Bajutsuをアプリ非依存に保つことを求めています。アプリごとの差分は設定側に置き、ツール
自体には持ち込みません。この制約が具体的な形で表面化した例が、IP許可リストで保護されたstaging
backendとの連携です。

Device Farmのデバイスからそのbackendへ到達するには、run単位で認証される中継サーバが必要です。
シナリオ自体の通信が始まる前に、デバイス側のスクリプトでこの中継サーバを設定しなければなりません。
このセットアップは、1つのデプロイ環境のbackendに固有です。別のデプロイ環境では、必要なものが
違います。

- Virtual Private Network（VPN）クライアント
- Mobile Device Management（MDM）プロファイル
- 何も要らない場合

これらはいずれも、Bajutsu自体が抱え込むべき事情ではありません。Bajutsuは、呼び出し元のbackendが
何を要求しようと使える状態を保つ必要があります。

現状、呼び出し元には2つの選択肢しかありません。どちらも、共有され、テストされた
`render_test_spec`を持つ意味を損ないます。

1つ目の選択肢は、`render_test_spec`の出力をフォークする方法です。たった1行のコマンドを差し込む
ために、次のすべてを複製します。

- Pythonのブートストラップ
- 疎通確認プローブ
- `test`フェーズと`post_test`フェーズ
- 成果物の収集
- YAML（YAML Ain't Markup Language）レンダリング

2つ目の選択肢は、Bajutsuの外に並行のテストスペック生成器を持つ方法です。同じ形をした2つの実装を、
手作業で同期させ続けることになります。

`build_package`はすでに`extra_texts`パラメータを持ちます。これを使うと、呼び出し元は任意のファイル
（セットアップ用スクリプトなど）をテストパッケージへ持ち込めます。ただし、そのファイルを`pre_test`
の最中に実際に実行させる手段は、今のところありません。

## 詳細設計

`render_test_spec`に`pre_test_commands: Sequence[str] = ()`パラメータを追加します。各要素は
`pre_test.commands`リストへ追記されます。追記は、既存の疎通確認プローブの後に、順序を保って行います。

```python
render_test_spec(
    scenarios,
    target=target,
    config=config,
    platform="ios",
    pre_test_commands=["bash configure-proxy.sh"],
)
```

は次をレンダリングします。

```yaml
phases:
  pre_test:
    commands:
    - <既存のプラットフォームプローブ>
    - bash configure-proxy.sh
```

`render_test_spec`は各要素を、すでにシェル向けに安全な文字列として扱います。中身は関知しません。
Device Farmのテストスペックも、もともと同じ信頼境界を持っています。どのフェーズも任意のシェル
コマンドを実行するからです。

この扱いは、`build_package`の`extra_texts`が呼び出し元の内容をそのまま通すのと同じ発想です。
ただし違いが1つあります。`extra_texts`の内容は、zipの中の不活性なデータです。
`pre_test`のコマンドは、Device Farmのマシン上で実行されます。

この扱いは、`render_test_spec`が現在`scenarios`、`target`、`config`をクォートしている扱いとは
異なります。この3つは、関数自身が1つのコマンドへ組み立てる素材です。リクエストやワークフローの
テキスト入力に由来するので、関数がクォートします。呼び出し元が渡すコマンドのリストは、呼び出し元
自身が組み立てたものです。

この理由から、`pre_test_commands`はPython API専用のパラメータにとどめます。`serve`エンドポイント、
configフィールド、`BatchRequest`フィールドのいずれも、このパラメータを運んではなりません。

`DeviceFarmBatchProvider`は、リポジトリ内で唯一の呼び出し元です。`render_test_spec`への引数を、
Hypertext Transfer Protocol（HTTP）のリクエストボディから組み立てます。この経路でこのパラメータを
通すと、クライアントはシェルを手にします。そのマシンは、実行中のAmazon Web Services（AWS）ロールの
認証情報を持っています。

デフォルト値の`()`は何も追加でレンダリングしません。既存の呼び出し元とテストはすべて、これまでと
バイト単位で同一の出力を生成し続けます。

`pre_test_commands`は、`build_package`の`extra_texts`パラメータと組み合わせられます。

```python
build_package(entries, out_zip, extra_texts={"configure-proxy.sh": script_text})
```

2つのパラメータを併用すると、呼び出し元はbackend固有の`pre_test`セットアップを丸ごと組み立てられます。
スクリプトの中身と、それを実行するコマンドの両方です。この組み立ては`bajutsu/`の外側だけで完結します。
Bajutsuは、セットアップの内容を検査も解釈もしません。

## 検討した代替案

- **レンダリング中に呼び出すコールバック(`Callable[[], list[str]]`)。** 却下します。
  `render_test_spec`は現在、入力を受け取りYAMLテキストを返す純粋関数です。コールバックを受け付けると、
  レンダリング中に呼び出し元のコードを実行することになります。計算済みのリストを渡せば同じ結果が
  得られるので、その代償に見合う利点はありません。テストも複雑になります。素のリストに対する
  アサーションではなく、コールバックのモックが必要になるからです。
- **専用の`proxy_config`や`vpn_config`パラメータ。** 却下します。これはまさに、大前提3が禁じている
  アプリ固有の特別扱いです。デプロイ環境ごとに、デバイス側のセットアップは全く異なります。

  - VPNクライアント
  - HTTPプロキシ
  - MDMプロファイル
  - 何も要らない場合

  Bajutsuは、backendの形ごとにパラメータを増やすのではなく、汎用的なパラメータを1つだけ提供する
  べきです。
- **`build_package`の`extra_texts`だけを拡張する。** 対応する`pre_test`のパラメータがなければ、
  不十分です。呼び出し元はセットアップ用スクリプトをテストパッケージへ持ち込めます。しかし、
  レンダリングされた`pre_test.commands`にそれを実行するコマンドがありません。Device Farmは、
  そのスクリプトを決して実行しません。

## 進捗

- [x] `render_test_spec`に`pre_test_commands: Sequence[str] = ()`を追加し、既存のプローブの後に
  `pre_test.commands`へ追記する。
- [x] デフォルト値(`()`)が今日の出力と同一になることを確認するユニットテストを追加する。
- [x] 渡したコマンドが、プローブの直後に順序どおりそのまま現れることを確認するユニットテストを追
  加する。
- [x] `render_test_spec`のdocstringを更新し、新しいパラメータとそのシェル安全性に関する信頼境界を
  記載する。
- [x] `serve`エンドポイント・configフィールド・`BatchRequest`フィールドのいずれも、このパラメータ
  へ配線しないこと（リクエスト由来の値が到達しないこと）を確認する。ユニットテストは
  `render_test_spec`呼び出し箇所の抽象構文木（AST）をたどるので、`BatchRequest`のフィールド名が
  変わっても、`**mapping`展開で渡されても検知できます。
- [x] `pre_test_commands`だけでなく`scenarios`についても、単なる`str`を渡した場合は拒否します。
  `Sequence[str]`は`str`にもマッチしますが、そのまま渡すと1文字ずつ1コマンド（または1シナリオ）として
  展開されてしまうためです。どちらの拒否にもユニットテストがあります。

ログ：

- [#2033](https://github.com/bajutsu-e2e/bajutsu/pull/2033) — `render_test_spec`に
  `pre_test_commands`を追加し、可視性プローブの後に`pre_test`フェーズへそのまま（`build_package`の
  `extra_texts`と同様にクオートせず）追記するようにしました。`serve`・config・`BatchRequest`のいず
  れからも配線しないPython API専用のフックとし、デフォルトで出力が同一になること・順序どおりそのま
  ま現れること・単なる文字列を渡すと拒否されること・配線されないことをユニットテストでカバーしまし
  た。`docs/devicefarm.md`とその日本語ミラーにフックを記載しました。

## 参考

[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md)が
`render_test_spec`と`build_package`を導入しています。
