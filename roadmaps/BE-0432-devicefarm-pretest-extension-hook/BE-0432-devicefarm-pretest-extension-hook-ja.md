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

`render_test_spec`は、Device Farmカスタム環境テストスペックをレンダリングします。この関数は
[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md)が
導入し、現在は`bajutsu/common/cloud/devicefarm/_functions.py`にあります
（`bajutsu.common.cloud.devicefarm`から再エクスポートされます）。レンダリングされたスペックは、予約済みデバイス上で
一連のシナリオを実行します。

その`pre_test`フェーズは現在、1つのコマンドしか実行しません。予約済みデバイスの疎通確認プローブ
です。`test`フェーズの`bajutsu run`呼び出しより前に、デバイスへ別の処理をさせたい呼び出し元がい
るとします。その呼び出し元には、`pre_test`フェーズにコマンドを追加する手段がありません。唯一の
代替は、`render_test_spec`がすでにレンダリングしているものをすべて作り直すことです。

本提案は、`pre_test_commands`パラメータを追加します。呼び出し元はこれを使い、自分のコマンドを
`pre_test`フェーズへ差し込みます。あるデプロイ環境のbackendに固有のデバイス側セットアップは、
これにより`bajutsu/`の外側だけで完結します。

## 動機

大前提3は、Bajutsuをアプリ非依存に保つことを求めています。アプリごとの差分は設定側に置き、ツール
自体には持ち込みません。この制約が具体的な形で表面化した例が、IP許可リストで保護されたstaging
backendとの連携です。

Device Farmのデバイスからそのbackendへ到達するには、run単位で認証される中継サーバが必要です。
シナリオ自体の通信が始まる前に、デバイス側のスクリプトでその中継サーバを設定しなければなりませ
ん。このセットアップは1つのデプロイ環境のbackendに固有のものです。別のデプロイ環境では、必要な
ものが違います。

- Virtual Private Network（VPN）クライアント
- モバイルデバイス管理（MDM）プロファイル
- あるいは何も要らない場合

これらはいずれもBajutsu自体が抱え込むべき事情ではありません。Bajutsuは、呼び出し元のbackendが
何を要求しようと使える状態を保つ必要があります。

現状、呼び出し元には2つの選択肢しかありません。どちらも、共有され、テストされた
`render_test_spec`を持つ意味を損ないます。

出力をフォークする方法は、たった1行のコマンドを差し込むためだけに、次のすべてを複製します。

- Pythonのブートストラップ
- 疎通確認プローブ
- `test`フェーズと`post_test`フェーズ
- 成果物の収集
- YAML(YAML Ain't Markup Language)レンダリング

Bajutsuの外に並行のテストスペック生成器を持つ方法は、同じ形をした2つの実装を手作業で同期させ続
けることになります。

`build_package`がすでに持つ`extra_texts`パラメータは、任意のファイルをテストパッケージへ呼び出し
元が持ち込むことを許しています。セットアップ用スクリプトなどです。ただし、そのファイルを
`pre_test`の最中に実際に実行させる手段は今のところありません。

## 詳細設計

`render_test_spec`に`pre_test_commands: Sequence[str] = ()`パラメータを追加します。各要素は
`pre_test.commands`リストへ追記されます。要素は、既存の疎通確認プローブの後に、順序を保ったまま
追記されます。

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
Device Farmのテストスペックも、もともと同じ信頼境界を持っています。どのフェーズも任意のシェルコ
マンドを実行するからです。この扱いは、`build_package`の`extra_texts`が呼び出し元のコンテンツを
そのまま通すのと同じ発想です。

一方で、`render_test_spec`が現在`scenarios`、`target`、`config`をクォートしている扱いとは異なり
ます。この3つは、関数自身が1つのコマンドへ組み立てる素材です。呼び出し元が渡すコマンドのリスト
は、呼び出し元自身が組み立てたものだからです。

デフォルト値の`()`は何も追加でレンダリングしません。既存の呼び出し元とテストはすべて、これまでと
バイト単位で同一の出力を生成し続けます。

`pre_test_commands`は、`build_package`の`extra_texts`パラメータと組み合わせられます。

```python
build_package(entries, out_zip, extra_texts={"configure-proxy.sh": script_text})
```

これにより、呼び出し元はbackend固有の`pre_test`セットアップを組み立てられます。スクリプトの中身
と、それを実行するコマンドの両方です。この組み立ては`bajutsu/`の外側だけで完結し、Bajutsuはその
セットアップが何をしているか関知しません。

## 検討した代替案

- **レンダリング中に呼び出すコールバック(`Callable[[], list[str]]`)。** 却下します。
  `render_test_spec`は現在、入力を受け取りYAMLテキストを返す純粋関数です。コールバックを受け付け
  ると、レンダリング中に呼び出し元のコードを実行することになります。すでに計算済みのリストを渡す
  場合と比べて、利点はありません。テストの書き方も複雑になります。素のリストに対するアサーション
  ではなく、コールバックのモックが必要になるからです。
- **専用の`proxy_config`や`vpn_config`パラメータ。** 却下します。これはまさに、大前提3が禁じてい
  るアプリ固有の特別扱いです。デプロイ環境ごとに、デバイス側のセットアップは全く異なります。

  - VPNクライアント
  - HTTPプロキシ
  - MDMプロファイル
  - あるいは何も要らない場合

  Bajutsuはbackendの形ごとにパラメータを増やすのではなく、1つの汎用的なフックだけを提供するべき
  です。
- **`pre_test`フックを伴わず、`build_package`の`extra_texts`だけを拡張する。** それだけでは不十分
  です。呼び出し元はセットアップ用スクリプトをテストパッケージへ持ち込めます。ただし、レンダリン
  グされた`pre_test.commands`にそれを実行するコマンドがない以上、Device Farmは決してそのスクリプ
  トを実行しません。

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
