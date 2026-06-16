[English](README.md) · **日本語**

# BajutsuDemo — デモ専用アプリ

実機デモ（`make tour` / `make features`）のための、小さく的を絞った SwiftUI アプリです。あえて最小限に
してあり、**オンボーディング → ログイン → ホーム（カウンター）** の流れだけを持ちます。これにより、余計な
要素に気を取られず、デモが伝えたい一連の流れ（著作 → 実行 → 改変 → 診断）をそのまま見せられます。あらゆる
機能を一通り試せる、より作り込んだフィクスチャは別の [`sample` アプリ](../features/app/README.ja.md)です。

## 画面と accessibility id

操作対象の要素にはすべて安定した `accessibilityIdentifier` を付けてあります。これがシナリオの解決する
セレクタになります（Bajutsu はラベルや座標よりも id を優先します）。

| 画面 | 要素 | id | 補足 |
|---|---|---|---|
| オンボーディング | 「Get Started」ボタン | `onboarding.start` | ログインへ進む |
| ログイン | メール欄 | `auth.email` | |
| ログイン | パスワード欄（secure） | `auth.password` | 実際にマスクされる `SecureField`（スクリーンショットでも秘密は伏せ字のまま） |
| ログイン | 「Log in」ボタン | `auth.submit` | 両欄が空でない間だけ有効 |
| ホーム | 「Home」タイトル | `home.title` | ログイン後の遷移先。シナリオが `wait` で待つ対象 |
| ホーム | 「Count: N」ラベル | `counter.value` | カウント値を `accessibilityValue` に映すので、idb でも `value.equals` で読める |
| ホーム | 「Increment」ボタン | `counter.increment` | タップごとに +1 |
| ホーム | 「Log out」ボタン | `home.logout` | リセットしてオンボーディングへ戻る |

## 起動時の環境変数フック

アプリは起動環境から次の変数を読みます（Bajutsu が config の `launchEnv` 経由で注入します）。

- `DEMO_UITEST` — アニメーションを無効化し、条件待ちが間延びしないようにする
  （[`demos/demo.config.yaml`](../demo.config.yaml) で設定済み）。
- `DEMO_SKIP_ONBOARDING` — ログイン画面から開始する。
- `DEMO_LOGGED_IN` — ホームから開始する（オンボーディングとログインを飛ばす）。

認証フローは、常に存在するホームの上に重なるモーダル（`fullScreenCover`）です。そのため、ログイン直後に
ホームを操作しても、作り直されたビューと競合しません。画面を丸ごと切り替える方式だと、その遷移の瞬間を
idb の accessibility クエリが空のツリーとして一瞬見てしまうことがありますが、この構成ならそれを避けられます。

さらに、ログインに成功すると、パスワード欄が first responder を手放す前にその中身をクリアします。iOS の
SpringBoard 側に出る「Save Password?（パスワードを保存しますか？）」プロンプトは idb からは見えず、表示中は
アプリの要素ツリーが単一ノードに潰れてしまいます。決定論的な tour / features の実行は APIキーを持たないため、
画像認識ベースのアラートガードでは消せません。欄をクリアしておけば iOS に保存を促す対象が残らないので、
ログインはブロックするプロンプトなしにそのままホームへ落ち着きます。

## ビルド

```bash
make -C demos app-build        # xcodegen generate -> iOS Simulator 向けに xcodebuild
```

これで `demos/app/build/…` の下に `BajutsuDemo.app` が生成されます（`.xcodeproj` と `build/` は
gitignore 済み — 手元で再生成してください）。`bajutsu run` / `bajutsu serve` も config の `build`
コマンド経由で必要に応じてビルドするので、バイナリが無ければデモ側がビルドしてくれます。

このアプリとシナリオは、デモの一部として実機でビルド・実行されます。`make -C demos tour`
（実行 → 改変 → 診断）と `make -C demos features`（タグ・共有ステップ・秘密情報のショーケース）は、
どちらも起動中の Simulator 上で idb 経由でこのアプリを動かします。
