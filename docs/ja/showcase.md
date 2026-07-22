[English](../showcase.md) · **日本語**

# showcase 群（唯一の iOS フィクスチャ）

> Bajutsu の iOS テストフィクスチャは [`demos/showcase/`](../../demos/showcase) にあります。**同じアプリを
> 2 回書き**（UIKit と SwiftUI）、**さらに各々をアクセシビリティ有／無の変種**で出すので、2 つのコード
> ベースから 4 つのプロダクト（`showcase-swiftui`、`showcase-swiftui-noax`、`showcase-uikit`、
> `showcase-uikit-noax`）が生まれます。実アプリが持つ操作面（push ナビゲーションを伴う 5 タブ、4 種すべての
> モーダル、テキスト入力、ジェスチャ、非同期ロード、実通信＋モック可能な通信、OS アラートを出す画面）を、
> その全体を語れる最小のアプリに収めています。
>
> BE-0079 でこれを**唯一**の iOS フィクスチャとし、旧来の `demo` / `sample` / `sample2` を退役させました。
> 画面ごとの正式な契約（各識別子と各シナリオの対応）は [`demos/showcase/SPEC.md`](../../demos/showcase/SPEC.md)
> にあります。本ページはそこへの入り口をまとめたものです。

関連：[シナリオ](scenarios.md) · [設定](configuration.md) · [codegen](codegen.md) · [cli](cli.md)

---

## なぜ 2 ツールキット × アクセシビリティ 2 変種なのか

showcase は、Bajutsu の設計が依って立つ 2 つの軸を可視化します。

- **ツールキット軸**（UIKit と SwiftUI）：アクセシビリティ ON の 2 プロダクト（`showcase-swiftui` /
  `showcase-uikit`）は*同一*の識別子契約を露出するので、共有の
  [`demos/showcase/scenarios/`](../../demos/showcase/scenarios) がどちらに対しても変更なしで動きます。
  異なるのは backend が見る要素ツリーで、これこそクロスツールキットのドライバが吸収すべき差異です。
- **アクセシビリティ軸**（サフィックス無し ↔ `-noax`）：`-noax` ビルドは識別子を**一切**持ちません
  （`idNamespaces: []`）。これはセレクタ安定性の対照実験です（DESIGN §5）。同じゴールを両方に対して記録すると、
  アクセシビリティ作業の価値が具体的な差分として現れます。また `record` / `doctor` の「アクセシビリティ欠如」の
  題材でもあります。

## ビルドと実行

4 つの `targets.<name>` として [`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml)
に登録しています（bundle id は `com.bajutsu.showcase.ios.{swiftui,uikit}[.noax]`、deeplink scheme は
`showcase{swiftui,uikit}[noax]`）。XcodeGen ＋ xcodebuild でビルドします（`project.yml` が信頼できる唯一の情報源で、
`.xcodeproj` / `build/` は gitignore 対象です）。5 つめのターゲット `showcase-swiftui-bundled` は同じ SwiftUI
アプリを使いますが、`xcuitest:` の設定を持ちません。そのため Simulator 上の実行はローカルビルドのランナーで
はなく、wheel に同梱されたランナー（BE-0292）に解決されます。`bajutsu doctor --target
showcase-swiftui-bundled` を実行すると、実際にどちらのランナーが使われているかを確認できます。

```bash
make -C demos/showcase swiftui-build       # SwiftUI a11y プロダクトを Simulator 向けにコンパイル
make -C demos/showcase run-swiftui         # ビルド → インストール → 起動中 Simulator に対し bajutsu run（XCUITest）
make -C demos/showcase doctor              # アクセシビリティ A/B：a11y は Ready、-noax は Blocked
make -C demos/showcase ui-test             # codegen 経路：シナリオ → XCUITest → xcodebuild test
```

`bajutsu run` / `serve` は各ターゲットの `build` コマンドでアプリを必要時にビルドするので、先に手動で
ビルドする必要はほとんどありません。

## 起動環境フック

`launchEnv` で注入し、`SIMCTL_CHILD_<NAME>` として渡します（[drivers](drivers.md#simctl-による環境管理)）。

BE-0079 で、*データ状態*と *push で開く画面*への起動時ショートカットを取り除きました。カタログは固定で（シードする
手段はありません）、deeplink が詳細へ直接飛ぶこともありません（詳細は行のタップでのみ到達します）。
BE-0107 では、画面への最後の起動時ショートカットである `SHOWCASE_TAB` を廃止して、この作業を完了しました。

アプリはつねに Stable タブで起動し、ほかのタブへはネイティブのタブバーをタップして移動します。XCUITest
backend はネイティブのタブバーの個々のタブをタップできるので、
タブをまたぐシナリオは `--backend ios` で実行します。起動タブより先の画面はすべて UI を操作して辿り、
シナリオはデータを注入せずアプリ自身の状態を観測します。

| 変数 | 効果 |
|---|---|
| `SHOWCASE_UITEST=1` | アニメーションを無効化（条件待機を締める） |
| `SHOWCASE_API_URL` / `SHOWCASE_HTTP_BASE` | カタログ GET とエコー POST/DELETE エンドポイントの base URL |

識別子カタログの全体、deeplink 文法、プリミティブとシナリオの対応は
[`demos/showcase/SPEC.md`](../../demos/showcase/SPEC.md) にあります。
