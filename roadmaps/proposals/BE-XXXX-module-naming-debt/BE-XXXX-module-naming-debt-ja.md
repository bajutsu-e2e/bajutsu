[English](BE-XXXX-module-naming-debt.md) · **日本語**

# BE-XXXX — environment と config のモジュール命名の負債を解消する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-module-naming-debt-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | 開発基盤（コントリビュータ体験） |
| 関連 | [BE-0063](../../implemented/BE-0063-git-config-source/BE-0063-git-config-source-ja.md)、[BE-0044](../../implemented/BE-0044-scenario-provenance/BE-0044-scenario-provenance-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/` 直下の 6 つのモジュールは、互いに名前が重複しているか、実際の役割とは違う仕事を
名前が約束しているかのどちらかです。どちらも機能上のバグではありません。各モジュールは
docstring に書かれているとおりに正しく動作します。しかし、新しいコントリビュータが名前から
中身を推測すると、外れることのほうが多くなります。本提案は、これらのモジュールを意図が
伝わる名前にリネームします。

## 動機

4 つのモジュールがいずれも「environment」または「config」の何らかの一部を名乗っていますが、
その意味は互いに重なりません。

- `bajutsu/env.py`（338 行）— `simctl` のコマンドビルダーをラップするモジュールです。
  「Command builders are pure and unit-tested. Execution goes through an injectable runner」
  （`env.py:1-5`）とあるとおり、一般的な意味での「environment」とは無関係で、実体は iOS
  Simulator を制御する面そのものです。
- `bajutsu/environment.py`（686 行）— 「Per-platform app lifecycle behind one Protocol」
  （BE-0009 Phase 0）、つまりプラットフォームごとにアプリを新しい起動済み状態へ持っていく
  `Environment` Protocol です（`environment.py:1-11`）。このモジュールは、将来のフォローアップ項目として想定している
  「backend のライフサイクルを型システムに載せる」（TBD）でも手を入れる可能性があり、`type: ignore`
  によるライフサイクルのエスケープが集中している箇所でもあります。
- `bajutsu/dotenv.py`（55 行）— 「Minimal .env loader: read KEY=VALUE lines into the
  environment」（`dotenv.py:1`）、つまり `.env` ファイルのシークレットを `os.environ` に
  読み込むモジュールです。
- `bajutsu/config_source.py`（243 行）— 「Acquire a config (and its scenario tree) from a Git
  source」（`config_source.py:1`、BE-0063）、つまり `github:owner/repo` や
  `git+https://...` の指定から config を取得・実体化するモジュールです。

「環境変数の扱いはどこにあるか」「アプリの起動シーケンスはどこか」と尋ねられたコントリビュータ
には、名前だけを見るとそれらしいファイルが 4 つあり、名前だけでは 3 つを除外する手掛かりが
ありません。`env.py` と `environment.py` はその中でも最も紛らわしい組です。ファイル名の
2 文字の違いが、互いに無関係な 2 つの関心事（simctl のコマンドと、プラットフォーム横断の
ライフサイクル）に対応しており、オートコンプリートやあいまい検索で誤ったファイルを選んで
しまう典型的な原因になります。

さらに 2 つのモジュールは、本来別のモジュールが担うはずの仕事を名前として約束しています。

- `bajutsu/capture.py`（180 行）— 名前とは裏腹に、画面キャプチャやネットワークキャプチャの
  ことではありません。これは `record` のアクションキャプチャであり、「proxy-actuation
  capture of tap / type / swipe」（`capture.py:1`）、つまりヒットテストした座標から安定した
  selector を解決し、scenario の step を生成する処理です。「capture」は、コードベースの
  別の場所に別名で存在するスクリーンショットのキャプチャ、動画のキャプチャ、ネットワークの
  キャプチャを指すのにも読み手が真っ先に思い浮かべる一般的な語であり、この名前だけでは
  どの capture を指しているのかが決まりません。
- `bajutsu/provenance.py`（20 行）— 6 つの中で最も小さく、最も狭い役割を持つモジュールです。
  「Display grouping for the `from:` provenance field」（`provenance.py:1`、BE-0044）、
  つまり timeline とレポートが連続する同一の `from:` の値を 1 つのラベル付きグループへ
  まとめる処理だけを担っています。しかし「provenance」という語は、コードベースの他の箇所
  でもっと広い意味で使われています。実行マニフェストの `provenance.scenarioHash`
  （`audit.py:351,368,374,383,393`）、Git config source の `source_provenance()`
  （`config_source.py:127-128`）、idb のバージョンのスタンプ（`idb_version.py:7,31,95`）は、
  いずれも一般的な意味での「provenance」であり、この 20 行のモジュールとは何の関係も
  ありません。これらのいずれかを探している読み手にも、まず `provenance.py` を開いてみる
  理由が等しくあり、開いてみると `from:` の表示グルーピングのヘルパーしか見つかりません。

これはオンボーディング時の負荷の負債であり（コードベース分析レポートによれば深刻度は
**小**）、機能上のバグではありません。各モジュールは内部的には正しく、冒頭の docstring も
きちんと書かれています。コストは、目当てのモジュールではないと知るために誰かがそれを
開くたびに、少しずつ繰り返し支払われます。

## 詳細設計

リネームは互いに独立しており（モジュールごとに MECE）、個別のコミットとしても 1 つの PR
にまとめても着地できます。各リネームは挙動を変えない、純粋な `git mv` とインポートパスの
更新です。

1. **`bajutsu/env.py` → `bajutsu/simctl.py`。** このモジュールが実際にラップしている対象、
   すなわち `simctl` CLI をそのまま名前にし、`environment.py` との 2 文字違いの衝突を
   解消します。
2. **`bajutsu/environment.py` → `bajutsu/lifecycle.py`**（モジュール一覧を並べたときに
   `lifecycle.py` が汎用的に見えすぎる場合は `platform_lifecycle.py`）。このモジュールの
   実際の仕事、すなわちプラットフォームごとの `Environment` Protocol とその実装を名前に
   します。この名前の決定は、姉妹にあたる提案「backend のライフサイクルを型システムに
   載せる」と調整する必要があります。もしその項目がこのモジュール内に `Lifecycle` という
   Protocol を導入するなら、モジュール名と Protocol 名が衝突します
   （`bajutsu.lifecycle.Lifecycle` は読みづらい書き方になります）。2 つの項目のうち先に
   着地したほうが、もう一方の余地を残す名前を選びます（モジュールが `lifecycle.py` を
   名乗るなら Protocol は `BackendLifecycle` に、Protocol が `Lifecycle` を名乗るなら
   モジュールは `platform_lifecycle.py` にする、など）。
3. **`bajutsu/dotenv.py`** — そのまま残します。「dotenv」は `.env` ファイルの慣習を指す
   確立された固有の用語であり、他の 3 つと衝突しません。リネームすると、正確で広く
   通用している名前を、より標準的でない名前と引き換えにすることになります。
4. **`bajutsu/config_source.py`** — そのまま残します。「config source」はその仕事
   （ある source から config を取得すること）を正確に名指ししており、`env.py`／
   `environment.py` をリネームした後は衝突もありません。重なっていたのは名前に「config」が
   含まれているという表面上の一致だけで、説明と実態が食い違っていたわけではありません。
5. **`bajutsu/capture.py` → `bajutsu/record_capture.py`**（または `action_capture.py`）。
   コードベースの他の箇所にあるスクリーンショット・ネットワーク・動画のキャプチャと
   区別できるよう、このモジュールが担う具体的な種類のキャプチャ、すなわち `record` 中の
   アクションの記録であることを名前にします。
6. **`bajutsu/provenance.py` → `bajutsu/from_grouping.py`**（または
   `provenance_display.py`）。このモジュールの実際の狭い仕事、すなわち表示のために連続する
   `from:` の値をグルーピングすることを名前にします。コードベースの他の無関係な部分でも
   正当に使われている「provenance」という広い用語は使いません。

ステップ 3 と 4 は「リネームしない」という結論であっても、MECE を満たすために動機で挙げた
6 つのモジュールすべてを網羅する目的で含めています。検討した上でリネームは不要だと判断
したことを記録しておくことで、将来の読み手が同じ疑問を再び持ち出さずに済みます。

## 検討した代替案

- **最も紛らわしい `env.py`／`environment.py` の衝突だけをリネームし、残りはそのままにする。**
  あいまい検索で誤ったファイルを選んでしまうという、最も気付きやすい間違いは解消しますが、
  `capture.py` と `provenance.py` が誤った仕事を約束したままになり、こちらのほうが
  気付きにくい半分の負債です。「provenance」を検索したコントリビュータには、
  `provenance.py` を開くまでそれが目当てのファイルではないという手掛かりがありません。
- **リネームの代わりに、モジュール docstring の索引（例えば `docs/` のテーブル）を
  追加する。** 導入コストは低くなりますが、コードとずれうる第 2 の情報源が増えるだけで
  なく、ドキュメントを先に読むのではなく grep やあいまい検索でファイル名から辿る
  コントリビュータの助けにはなりません。レポートが述べている実際の失敗の形は、まさに
  後者です。
- **何もしない。** 何も壊れていない以上、最もリスクの低い選択ではあります。しかし
  レポートはこのコストを、新しいコントリビュータが来るたびに繰り返し支払われるものだと
  明言しており、先送りを続けることは、一度限りのリネームのコストを避ける代わりに、この
  コストを払い続けることを意味します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `bajutsu/env.py` → `bajutsu/simctl.py` にリネームする。
- [ ] `bajutsu/environment.py` → `bajutsu/lifecycle.py` にリネームする（名前は姉妹項目の `Lifecycle` Protocol と調整する）。
- [ ] `bajutsu/dotenv.py` と `bajutsu/config_source.py` はリネーム不要であることを確認する（記録のみ、対応なし）。
- [ ] `bajutsu/capture.py` → `bajutsu/record_capture.py` にリネームする。
- [ ] `bajutsu/provenance.py` → `bajutsu/from_grouping.py` にリネームする。

まだ着手した PR はありません。

## 参考

- `bajutsu/env.py:1-5` — モジュール docstring：`simctl` コマンドビルダーのラッパー。
- `bajutsu/environment.py:1-11` — モジュール docstring：プラットフォームごとの `Environment`
  Protocol。
- `bajutsu/dotenv.py:1-7` — モジュール docstring：`.env` ローダー。
- `bajutsu/config_source.py:1-11` — モジュール docstring：Git からの config 取得（BE-0063）。
- `bajutsu/capture.py:1-5` — モジュール docstring：record 時のアクションキャプチャ
  （BE-0012）。
- `bajutsu/provenance.py:1-7` — モジュール docstring：`from:` の表示グルーピング
  （BE-0044）。
- `bajutsu/audit.py:351,368,374,383,393`、`bajutsu/config_source.py:127-128`、
  `bajutsu/idb_version.py:7,31,95` — `provenance.py` の名前と衝突する、コードベースの
  他の箇所での無関係な「provenance」の使用。
- 関連するロードマップ項目：[BE-0063](../../implemented/BE-0063-git-config-source/BE-0063-git-config-source-ja.md)
  （Git config source — `config_source.py` を名指しする項目）、
  [BE-0044](../../implemented/BE-0044-scenario-provenance/BE-0044-scenario-provenance-ja.md)
  （scenario provenance — `provenance.py` を名指しする項目）。
- 2026-07-02 のコードベース分析レポート（design）に由来します。
