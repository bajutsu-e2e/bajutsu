[English](BE-0020-multi-backend-evidence-fallback.md) · **日本語**

# BE-0020 — マルチ backend 証跡フォールバック

* 提案: [BE-0020](BE-0020-multi-backend-evidence-fallback-ja.md)
* Author: [@0x0c](https://github.com/0x0c)
* 状態: **提案**
* トラック: [提案](../../README-ja.md#提案)
* トピック: バックエンド拡張（iOS actuator）

## はじめに

現状 actuator は単一です。証跡取得のみを別 backend に転送することで能力差を吸収します（§9 で設計済み・未配線）。

## 動機

単一の backend があらゆる種類の証跡を提供することはまれです。たとえば idb はネイティブのネットワーク監視を持たず（`capabilities()` は `network` を返しません）、それを必要とする取得は別の場所から来なければなりません。DESIGN §9 はこれを既に設計しています。`backend` は順序付きリストで、**actuator**（最初に利用可能な backend）が操作と解決をすべて行い、それ以外の*あらゆる* backend は、actuator に欠ける能力を供給する **read-only な証跡フォールバック**として働けます。各アーティファクトはどの provider から来たかを記録するので、manifest は各証跡の出所について正直であり続けます。

設計は存在しますが、配線されていません。`docs/drivers.md` ははっきりこう述べています——「現在の実行経路は単一 actuator を使い、マルチ backend 証跡フォールバックはまだ配線されていない」。そのため今日は、actuator が要求された取得を生み出せない場合、たとえ別の宣言済み backend が read-only で供給できたとしても、証跡は単に skip されます。iOS が 2 つ目の actuator（XCUITest、BE-0019）を得て、プロジェクトが他プラットフォームへ向かうにつれ、この穴は広がります。ある backend が持ち別の backend が欠く能力は、まさに §9 の意図どおり、黙って落とすのではなく抽象側で吸収すべきです。本提案は既存の設計を run ループに接続します。

## 詳細設計

仕組みは DESIGN §9 にそのまま従います。操作は 1 つの backend にとどまり、証跡の解決は他の backend を read-only で参照します。

- **能力ごとの provider 解決。** run ループが取得を必要とするとき、`backend` リストを辿り、各 backend の `capabilities()` にそのトークンを問い合わせて provider を解決します。actuator は提供する能力（`screenshot`、`elements`）について試され、backend 非依存の取得（`video`、`deviceLog`）は今と同様 `simctl` から来て、actuator に欠ける能力（例: `network`）は、それを表明する最初の*別の* backend から、厳密に read-only で取得します。これは §9 が既に規定する解決表で、それが今や実挙動を駆動します。
- **read-only を強制する。** `tap` / `type` / `swipe` / `wait` / `query` を行えるのは actuator のみです。フォールバック backend は証跡の取り出しだけに使われ、決して操作しません。これにより単一 actuator の保証（DESIGN §3.3 / §5）が保たれ——2 つのドライバが 1 つのデバイスを操作することはなく——決定性は何も変わりません。固定 sleep は依然なく、ambiguous なセレクタは依然失敗します。証跡取得は合否経路の完全に外側にあります。
- **来歴となだらかな skip。** 各 `Artifact` は既に `provider` を持ちます。フォールバックが配線されれば、そのフィールドは取得を実際に供給した backend（または `simctl` / モックサーバ）を記録します——例: `network: mockServer（idb はネイティブ監視なし）`。リスト中のどの backend も要求された能力を提供できなければ、取得は skip され、その理由が capability フラグとともに manifest に記録されます——run を失敗させるのではなく、既存の劣化開示の規則に沿って。
- **既存の backend リストで設定する。** 新しい config 面は不要です。順序付きの `backend` リスト（および `apps.<name>` 下の per-app backend 設定）は、actuator 選択に使うのと同じものです。ツールは app-agnostic のまま——どの backend が利用可能かは環境/config の事柄で、runner に埋め込まれません。Tier-2 ゲートは LLM フリーのままです。これは証跡のための配管であって、判定のためのものではありません。

## 検討した代替案

- **skip のまま放置する（現状維持）。** actuator に欠ける能力を skip するのは単純ですが、宣言済み backend が生み出せたはずの証跡を捨て、まさに Bajutsu が支えるべき失敗調査を弱めます。§9 が既にフォールバックを設計し `provider` フィールドが既に存在する以上、それを配線するのは新しいアーキテクチャではなく、小さく忠実な一歩です。
- **あるステップで「より良い」フォールバック backend に操作させる。** これは一部のアクションを 2 つ目のドライバへ流します。単一 actuator の規則を破り、1 つのデバイスを取り合う 2 ドライバの非決定性を再導入します。操作は固定された 1 つの actuator にとどまらねばなりません。*操作*の能力差は actuator ラダー（BE-0019）で扱い、ここで埋めるのは*証跡*の差だけで、read-only です。
- **各 backend の取得を機会的に統合する（取れる backend すべてから取る）。** 同じ証跡を複数 backend から同時に取得することはコストを増やし、どのコピーが正本かを曖昧にし、§9 が警告する観測者効果のリスクを招きます。能力ごとに、リスト順で 1 つの provider に解決する方が、単一の明確な出所と有界なコストを保てます。

## 参考

[drivers.md](../../../docs/ja/drivers.md)、[DESIGN §9](../../../DESIGN.md)
