[English](BE-0148-serve-doctor.md) · **日本語**

# BE-0148 — serve Web UI の doctor 準備状況パネル

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0148](BE-0148-serve-doctor-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0148") |
| 実装 PR | [#704](https://github.com/bajutsu-e2e/bajutsu/pull/704) |
| トピック | serve Web UI への CLI 機能の取り込み |
<!-- /BE-METADATA -->

## はじめに

`doctor`（実行前の runnability ゲートと、画面の規約スコア）を `serve` Web UI に出します。run が分かりにくい
理由で失敗する前に、「自分の環境は準備できているか、アプリは指定可能か」をブラウザで答えられるようにします。
チェックは構造上、決定的で AI を使いません。UI は既存のコマンドを起動するだけです。

## 動機

run が成功するかどうかは、まずセットアップで決まります。必要な CLI が入っていること、Simulator が起動して
いること、そして画面が指定可能なだけのアクセシビリティ id を備えていることです。

Bajutsu は、この両方を CLI ですでに答えています（`bajutsu/preflight.py`、`bajutsu/doctor.py`）。

- `preflight.runnability` が環境を確認します。対象は、Xcode の `xcrun`、backend の CLI、起動済みの
  Simulator です。
- `doctor.score` が画面の規約準備度を Ready、Partial、Blocked に採点します。入力は、id カバレッジ、
  名前空間への適合、id の重複です。

ところが Web UI は、そのどれも出していません。Simulator の一覧は出します（`GET /api/simulators`）。それでも
利用者はターゲットを選んで Run を押し、分かりにくい失敗に出くわします。「Blocked: 起動済みの Simulator が
ない」や「Partial: この画面の 3 つのコントロールに id がない」の一行があれば、その失敗は未然に防げたはず
です。新しい利用者が出発する場所はブラウザです。だから、実行する前に診断するこの面が置かれるべき場所も、
まさにそこです。

## 詳細設計

Tier 1 の読み取り専用です。UI は既存のチェックを起動するだけです。

- **準備状況パネル**を、それ単独で開けるようにしつつ、Record と Replay のフォームでは実行前のチェックとして
  出します。パネルは `POST /api/doctor`（`{target, udid?, backend?}`）を叩きます。エンドポイントは、
  チェックを serve のジョブとして実行し（既存のジョブとストリームの仕組みを再利用）、2 つの部分を返します。
  - **runnability** の結果（必要な CLI はそろっているか、Simulator は起動しているか）。既存の対処文言を
    添えます。
  - **規約スコア**（Ready / Partial / Blocked）。`doctor.score` がすでに計算している、名前空間ごとの id の
    不足を添えます。
- **決定的で読み取り専用。** `doctor.score` は構造上「AI は関与しない」もので、`preflight` は環境の検査です。
  パネルは合否を計算せず、run にも触れません。
- **プラットフォームを意識する。** UI のほかの部分と同じです。runnability の半分は iOS 固有（起動済みの
  Simulator）です。そのため web ターゲットでは、パネルは Playwright backend が必要とするブラウザとランタイムの
  チェックを出し、Simulator 用のコントロールを隠します。UI は選択中の backend ですでに分岐しています。
- **アプリ非依存。** id の名前空間と backend は config（`targets.<name>`）から来ます。スコアの分母は
  ハードコードではなく、*宣言された*ものです。

## 検討した代替案

* **doctor を CLI 専用のままにする。** 不採用です。準備状況チェックで最も助かる人は、セットアップ中の新しい
  利用者です。その人たちは、CLI の診断を先に走らせる可能性が最も低く、入口はブラウザです。
* **runnability ゲートだけ出し、規約スコアは出さない。** 価値の半分を捨てるので不採用です。「環境は問題ないが
  アプリが指定可能でない」のほうが、よくある分かりにくい失敗であり、スコアはすでに存在します。
* **新規項目にせず
  [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md) に畳み込む。**
  BE-0024 は、小さく CLI 側の doctor とオンボーディングの改善を受け止めるプレースホルダでした。その取り込み
  先としての運用はすでに終わり、項目自体は実装済みです。doctor の Web UI 面は、独立した相応の大きさの面で
  あり、もともとその取り込みの対象でもありません。そこで、BE-0024 の中には置かず、それ自身の項目として立て、
  BE-0024 を参照します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] runnability の結果と規約スコアを返す `POST /api/doctor`（`{target, udid?, backend?}`）
      エンドポイントを追加する
- [x] Record と Replay の実行前チェックとして開ける準備状況パネルを追加する
- [x] パネルをプラットフォーム対応にする（チェックは backend に応じた内容が返り、Simulator 専用の
      フォームコントロールは web ターゲットでは隠れる）

`POST /api/doctor` エンドポイントとその runnability 部分は先に BE-0024 で入っていました。本項目はそれを
完成させます。エンドポイントは `{udid?, backend?}` を受け取り、Simulator の起動チェックを報告し、規約
スコアを加えます（スコアは環境が runnable なときだけインプロセスで query します。CLI の「届かないデバイスは
見にいかない」ガードと同じ挙動です）。準備状況パネルが Record と Replay の両フォームでこの両方を出します。
スコアはジョブの起動ではなく同期のインプロセス query です。既存の `doctor_check` と audit オペレーションに
そろえ、チェックを AI フリーで決定的に保ちます。

- [#704](https://github.com/bajutsu-e2e/bajutsu/pull/704)：doctor の Web UI 面を完成（エンドポイントのスコア＋準備状況パネル）。

## 参考

* `bajutsu/doctor.py`、`bajutsu/preflight.py`、`bajutsu/cli/commands/doctor.py`（ここで露出するチェック）。
* `bajutsu/serve/`（再利用するジョブの土台）。`GET /api/simulators`（このパネルが並ぶ、既存のデバイス
  一覧エンドポイント）。
* [BE-0024 — doctor / オンボーディング](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md)
  （この Web UI 面が補完する、実装済みの CLI 側 doctor／オンボーディングチェック）。
* [BE-0011 — ローカル Web UI（`bajutsu serve`）](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)、
  [BE-0072 — serve Web UI のレスポンシブ対応](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui-ja.md)
  （拡張する UI と、引き継ぐ小さい画面向けレイアウト）。
* [configuration.md](../../docs/ja/configuration.md)（`doctor` スコアと runnability ゲート）。
  [CLAUDE.md](../../CLAUDE.md)、[DESIGN §2](../../DESIGN.md)（決定性ファースト。チェックは AI を
  使いません）。
