[English](BE-0145-serve-audit.md) · **日本語**

# BE-0145 — serve Web UI で決定性監査を見る

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0145](BE-0145-serve-audit-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0145") |
| トピック | serve Web UI への CLI 機能の取り込み |
<!-- /BE-METADATA -->

## はじめに

決定性／flakiness の監査（[BE-0049](../../implemented/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)）
を `serve` Web UI に出します。シナリオの静的な安定性スコア（安定性のはしごで採点したセレクタ、timeout のない
待機、生座標のジェスチャ）を、シナリオをオーサリングし閲覧するその場で見せます。読み取り専用で AI を使わず、
ゲートには決してなりません。

## 動機

[BE-0049](../../implemented/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)
は `bajutsu audit` を出荷しています。各セレクタを安定性のはしごで採点し（一意に解決する `id` が `label` /
`traits` に勝り、それらが `index` / 生座標に勝ります）、timeout のない `wait` ステップを指摘し、安定した `id`
で置き換えられる座標ジェスチャを指摘する、読み取り専用の静的スコアです（`bajutsu/audit.py`）。Bajutsu の
「契約として決定的」という主張を手に取れるものにしますが、CLI でだけです。Web UI のシナリオエディタは
決定性のフィードバックをまったく出さないので、生成したばかり、あるいは手で直したばかりのシナリオで、安定な
セレクタを選んだのか脆いものを選んだのかを、作者はずっと後まで（あるいは永遠に）知れません。シナリオを書き、
読む場所はブラウザです。だから安定性の信号が最も役立つのもそこで、GUI エディタ
（[BE-0013](../../implemented/BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)）が `doctor`
スコアをその場に欲しがるのと同じ理由です。

## 詳細設計

Tier 1 の読み取り専用です。UI は既存の監査を起動するだけです。

- **「Audit」パネル／バッジ**を、エディタと Replay ビューでシナリオに添え、`POST /api/audit`
  （`{target, path}`）を叩きます。静的監査を実行し、シナリオごとの決定性スコアを、セレクタごとの採点と指摘
  （timeout のない待機、生座標のジェスチャ、はしごから外れたセレクタ）とともに返します。
- **読み取り専用で決定的、AI を使わない。** 監査はシナリオに対する静的解析です。デバイスを動かさず、モデルを
  呼ばず、ゲートにもなりません。CLI と同じく、あくまで参考情報です。
- **まず静的スコア。** ここでは BE-0049 の静的な半分を出します。最も安価で、入力がすべてディスク上にある図
  です。BE-0049 の動的な「繰り返して差分を取る」半分は、より重くデバイスを要するジョブで、後から出すなら run
  のジョブ／ストリームの仕組みを再利用しますが、静的スコアは単独で成立し、先に出せます。
- **アプリ非依存。** シナリオとそのターゲットは config（`targets.<name>`）から解決します。安定性のはしごは
  ツールのものであり、アプリごとではありません。

## 検討した代替案

* **監査を CLI 専用のままにする。** 不採用です。作者が決して見ない決定性スコアは、書いている最中のシナリオを
  形づくれません。挙動を変えるのはその場に出したときです。
* **GUI エディタ（[BE-0013](../../implemented/BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)）
  に畳み込む。** BE-0013 は `doctor` の規約スコアを構造化編集に統合します。監査スコアはその姉妹の信号で、
  そこに載せることもできますが、いまの textarea でも動く分離可能な読み取り専用パネルなので、それ自身の
  項目として立て、BE-0013 を補完します。
* **動的な「繰り返して差分を取る」監査を先に出す。** 最初の一歩としては不採用です。デバイスと `K` 回の実行が
  要ります。静的スコアは即時でディスク上にあるので、最初の一歩はそちらが正しいところです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 静的監査を実行し、シナリオごとのスコアを返す `POST /api/audit`（`{target, path}`）エンドポイントを
      追加する
- [ ] エディタと Replay ビューに、セレクタごとの採点と指摘を示す「Audit」パネル／バッジを追加する
- [ ] まず静的スコアを出す。動的な「繰り返して差分を取る」半分は、後のデバイス依存のスライスとする

まだ着手した PR はありません。

## 参考

* `bajutsu/audit.py`、`bajutsu/cli/commands/audit.py`（ここで露出する監査）。
* [BE-0049 — 決定性／flakiness 監査](../../implemented/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)
  （これがその Web UI 面となる機能）、
  [BE-0013 — シナリオ GUI エディタ](../../implemented/BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)
  （姉妹のその場フィードバック面、`doctor` スコア）、
  [BE-0050 — E2E カバレッジマップ](../../implemented/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map-ja.md)
  （監査のセレクタ走査を共有し、この面と並ぶ独自の Web UI 面を持つ）。
* [BE-0011 — ローカル Web UI（`bajutsu serve`）](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)、
  [BE-0072 — serve Web UI のレスポンシブ対応](../../implemented/BE-0072-responsive-web-ui/BE-0072-responsive-web-ui-ja.md)
  （拡張する UI と、引き継ぐ小さい画面向けレイアウト）。
* [selectors.md](../../../docs/ja/selectors.md)（監査が採点の基準にする安定性のはしご）。
  [CLAUDE.md](../../../CLAUDE.md)、[DESIGN §2](../../../DESIGN.md)（監査は読み取り専用で AI を使わず、
  合否にはなりません）。
