[English](BE-XXXX-serve-doctor.md) · **日本語**

# BE-XXXX — serve Web UI の doctor 準備状況パネル

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-doctor-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラック | [提案](../../README-ja.md#提案) |
| トピック | serve Web UI への CLI 機能の取り込み |
<!-- /BE-METADATA -->

## はじめに

`doctor`（実行前の runnability ゲートと画面の規約スコア）を `serve` Web UI に出します。run が分かりにくい
理由で失敗する前に、「自分の環境は準備できているか、アプリは指定可能か」をブラウザで答えられるようにします。
チェックは構造上、決定的で AI を使いません。UI は既存のコマンドを起動するだけです。

## 動機

run が成功するかどうかを最初に決めるのはセットアップです。必要な CLI が入っていること、Simulator が起動して
いること、そして画面が指定可能なだけのアクセシビリティ id を備えていること。Bajutsu はこの両方を CLI では
すでに答えています。`preflight.runnability` が環境（Xcode の `xcrun`、backend の CLI、起動済みの Simulator）を
確認し、`doctor.score` が画面の規約準備度（id カバレッジ、名前空間への適合、id の重複）を Ready／Partial／
Blocked に採点します（`bajutsu/preflight.py`、`bajutsu/doctor.py`）。だが Web UI はそのどれも出していません。
Simulator の一覧は出す（`GET /api/simulators`）のに、利用者はターゲットを選んで Run を押し、「Blocked:
起動済みの Simulator がない」や「Partial: この画面の 3 つのコントロールに id がない」の一行が未然に防げた
はずの、分かりにくい失敗に出くわします。新しい利用者が出発する場所はブラウザです。だからこの、実行する前に
診断する面が置かれるべき場所も、まさにそこです。

## 詳細設計

Tier 1 の読み取り専用です。UI は既存のチェックを起動するだけです。

- **準備状況パネル**を、それ単独で開けるようにしつつ、Record と Replay のフォームでは実行前のチェックとして
  出します。`POST /api/doctor`（`{target, udid?, backend?}`）を叩き、チェックを serve のジョブとして実行し
  （既存のジョブ／ストリームの仕組みを再利用）、2 つの部分を返します。**runnability** の結果（必要な CLI は
  そろっているか、Simulator は起動しているか）を既存の対処文言とともに、そして **規約スコア**（Ready／
  Partial／Blocked）を `doctor.score` がすでに計算している名前空間ごとの id の不足とともに。
- **決定的で読み取り専用。** `doctor.score` は構造上「AI は関与しない」もので、`preflight` は環境の検査です。
  ここでは合否を計算せず、run にも触れません。
- **プラットフォームを意識する。** UI の他の部分と同じです。runnability の半分は iOS 固有（起動済みの
  Simulator）なので、web ターゲットではパネルは Playwright backend が必要とするブラウザ／ランタイムの
  チェックを出し、Simulator 用のコントロールを隠します（UI は選択中の backend ですでに分岐しています）。
- **アプリ非依存。** id の名前空間と backend は config（`targets.<name>`）から来るので、スコアの分母は
  ハードコードではなく*宣言された*ものです。

## 検討した代替案

* **doctor を CLI 専用のままにする。** 不採用です。準備状況チェックで最も助かる人（セットアップ中の新しい
  利用者）は、CLI の診断を先に走らせる可能性が最も低く、彼らの入口はブラウザです。
* **runnability ゲートだけ出し、規約スコアは出さない。** 価値の半分を捨てるので不採用です。「環境は問題ないが
  アプリが指定可能でない」のほうがよくある、そして分かりにくい失敗であり、スコアはすでに存在します。
* **新規項目にせず
  [BE-0024](../../implemented/BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md) に畳み込む。**
  BE-0024 は、小さく CLI 側の doctor／オンボーディング改善を受け止めるプレースホルダでしたが、その取り込み
  先としての運用はすでに終わり、項目自体は実装済みです。doctor の Web UI 面は独立した相応の大きさの面であり、
  もともとその取り込みの対象でもないので、BE-0024 の中に置くのではなく、それ自身の項目として立て、BE-0024
  を参照します。

## 参考

* `bajutsu/doctor.py`、`bajutsu/preflight.py`、`bajutsu/cli/commands/doctor.py`（ここで露出するチェック）。
* `bajutsu/serve/`（再利用するジョブの土台）。`GET /api/simulators`（このパネルが並ぶ、既存のデバイス
  一覧エンドポイント）。
* [BE-0024 — doctor / オンボーディング](../../implemented/BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md)
  ——この Web UI 面が補完する、実装済みの CLI 側 doctor／オンボーディングチェック。
* [BE-0011 — ローカル Web UI（`bajutsu serve`）](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)、
  [BE-0072 — serve Web UI のレスポンシブ対応](../../implemented/BE-0072-responsive-web-ui/BE-0072-responsive-web-ui-ja.md)
  ——拡張する UI と、引き継ぐ小さい画面向けレイアウト。
* [configuration.md](../../../docs/ja/configuration.md)（`doctor` スコアと runnability ゲート）。
  [CLAUDE.md](../../../CLAUDE.md)、[DESIGN §2](../../../DESIGN.md)（決定性ファースト。チェックは AI を
  使いません）。
