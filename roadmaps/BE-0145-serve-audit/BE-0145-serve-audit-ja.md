[English](BE-0145-serve-audit.md) · **日本語**

# BE-0145 — serve Web UI で決定性監査を見る

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0145](BE-0145-serve-audit-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0145") |
| 実装 PR | [#700](https://github.com/bajutsu-e2e/bajutsu/pull/700) |
| トピック | serve Web UI への CLI 機能の取り込み |
<!-- /BE-METADATA -->

## はじめに

本項目では、決定性／flakiness の監査（[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)）を
`serve` Web UI に出します。UI は、シナリオをオーサリングして閲覧するその場で、シナリオの静的な
安定性スコアを見せます。スコアは、次の 3 つを対象にします。

- 安定性のはしごで採点したセレクタ
- 具体的な条件を持たない（ゆるい）`until` の待機（たとえば `settled`）
- 生座標のジェスチャ

この機能は読み取り専用で AI を使わず、ゲートには決してなりません。

## 動機

[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) は
`bajutsu audit`（`bajutsu/audit.py`）を出荷しています。このコマンドは、読み取り専用の静的スコアで、
次の 3 つを行います。

- 各セレクタを安定性のはしごで採点する。一意に解決する `id` が `label` / `traits` に勝り、
  それらが `index` / 生座標に勝る。
- 具体的な要素や条件を持たない（ゆるい）`until` の `wait` ステップを指摘する。
- 安定した `id` で置き換えられる座標ジェスチャを指摘する。

この監査は、Bajutsu の「契約として決定的」という主張を手に取れるものにします。ただし、CLI
でだけです。Web UI のシナリオエディタは、決定性のフィードバックをまったく出しません。生成した
ばかりのシナリオや、手で直したばかりのシナリオで、安定なセレクタを選んだのか、脆いものを選んだ
のかを、作者は見られません。気づくのはずっと後で、気づかないまま終わることもあります。

シナリオを書いて読む場所はブラウザです。だから、安定性の信号が最も役立つのもブラウザです。
GUI エディタ（[BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)）が
`doctor` スコアをその場に欲しがるのも、同じ理由です。

## 詳細設計

この機能は Tier 1 の読み取り専用です。UI は既存の監査を起動します。

- **「Audit」パネル／バッジ**を、エディタと Replay ビューでシナリオに添え、`POST /api/audit` を
  叩きます。
  - エディタは、編集中の（未保存かもしれない）`yaml` を送ります。スコアが、作者の書いている内容を
    反映します。
  - Replay ビューは `{target, path}` を送り、サーバが保存済みのファイルを読みます。

  エンドポイントは静的監査を実行し、シナリオごとの決定性スコアを返します。スコアには、セレクタ
  ごとの採点と指摘（具体的な条件を持たない（ゆるい）`until` の待機、生座標のジェスチャ、はしごから
  外れたセレクタ）が付きます。
- **読み取り専用で決定的、AI を使わない。** 監査はシナリオに対する静的解析です。デバイスを動かさ
  ず、モデルを呼ばず、ゲートにもなりません。CLI と同じく、あくまで参考情報です。
- **まず静的スコア。** 本項目では、BE-0049 の静的な半分を出します。最も安価で、入力がすべて
  ディスク上にある図です。BE-0049 の動的な「繰り返して差分を取る」半分は、より重くデバイスを
  要するジョブです。動的な半分を後から出すなら、run のジョブとストリームの仕組みを再利用
  します。静的スコアは単独で成立し、先に出せます。
- **アプリ非依存。** シナリオとそのターゲットは config（`targets.<name>`）から解決します。
  安定性のはしごはツールのものであり、アプリごとではありません。

## 検討した代替案

* **監査を CLI 専用のままにする。** 不採用です。CLI にとどまる決定性スコアは、作者が見ることが
  ないので、書いている最中のシナリオを形づくれません。挙動を変えるのは、スコアをその場に出した
  ときです。
* **GUI エディタ（[BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)）
  に畳み込む。** BE-0013 は、`doctor` の規約スコアを構造化編集に統合します。監査スコアはその
  姉妹の信号で、そこに載せることもできます。しかし、いまの textarea でも動く分離可能な
  読み取り専用パネルなので、それ自身の項目として立て、BE-0013 を補完します。
* **動的な「繰り返して差分を取る」監査を先に出す。** 最初の一歩としては不採用です。動的な監査は
  デバイスと `K` 回の実行を要します。静的スコアは即時でディスク上にあるので、最初の一歩は
  そちらが適切です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 静的監査を実行し、シナリオごとのスコアを返す `POST /api/audit`（編集中の `yaml`、または
      `{target, path}`）エンドポイントを追加する
- [x] エディタと Replay ビューに、グレード・`id` ベースのセレクタ比率・指摘を示す決定性監査バッジと
      findings パネルを追加する
- [x] まず静的スコアを出す。動的な「繰り返して差分を取る」半分は、後のデバイス依存のスライスとする

- [#700](https://github.com/bajutsu-e2e/bajutsu/pull/700)：静的監査 `bajutsu/audit.py` をラップする
  `POST /api/audit` を追加しました。ライブのエディタ内容には編集中の `yaml` を、Replay ビューの
  保存済みファイルには `{target, path}` を受け取ります。あわせて、Author エディタと Replay の
  シナリオピッカーに、グレードのバッジと findings パネルを追加し、ドキュメントを英日の両方で
  更新しました。

## 参考

* `bajutsu/audit.py`、`bajutsu/cli/commands/audit.py`：本項目が露出する監査。
* [BE-0049 — 決定性／flakiness 監査](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)：
  本項目が Web UI に出す機能。
  [BE-0013 — シナリオ GUI エディタ](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)：
  姉妹のその場フィードバック面（`doctor` スコア）。
  [BE-0050 — E2E カバレッジマップ](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map-ja.md)：
  監査のセレクタ走査を共有し、この面と並ぶ独自の Web UI 面を持つ。
* [BE-0011 — ローカル Web UI（`bajutsu serve`）](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)、
  [BE-0072 — serve Web UI のレスポンシブ対応](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui-ja.md)：
  本項目が拡張する UI と、引き継ぐ小さい画面向けレイアウト。
* [selectors.md](../../docs/ja/selectors.md)：監査が採点の基準にする安定性のはしご。
  [CLAUDE.md](../../CLAUDE.md)、[DESIGN §2](../../DESIGN.md)：監査は読み取り専用で AI を使わず、
  合否にはなりません。
