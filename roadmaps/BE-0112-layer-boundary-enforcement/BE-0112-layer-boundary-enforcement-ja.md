[English](BE-0112-layer-boundary-enforcement.md) · **日本語**

# BE-0112 — コア・契約・周辺のレイヤ境界をゲートで検査する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0112](BE-0112-layer-boundary-enforcement-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0112") |
| 実装 PR | [#642](https://github.com/bajutsu-e2e/bajutsu/pull/642) |
| トピック | コントリビューターワークフロー |
| 関連 | [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md), [BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の決定的コアと周辺を分けるレイヤ規則を、**ゲートで実行できる検査**にします。いま、この規則は
現行コードの行儀だけで守られています。コアのモジュール（`orchestrator/`、`drivers/`、`runner/` など）
が周辺のモジュール（`serve/`、AI やエージェントのモジュール、codegen の emitter）を import すること
を、何も妨げていません。

レイヤと、その間で許される依存を、import-linter 相当の契約として宣言し、`make check` で走らせます。
こうすれば、禁じた import は誰かが気付くまで生き残るのではなく、その場でゲートを落とします。

## 動機

コードベースの背後にある 3 層モデルは、明確に言い切れる程度に定まっています。

1. **決定的コア**：シナリオから verdict と証跡までを決定的に導く経路です。`orchestrator/`、
   `drivers/base`、`assertions`、`evidence`、`report`、`config`、`runner/`、`env`、`preflight`、
   `doctor`、`lint` が該当します。プライムディレクティブの担い手であり、Bajutsu 本体でしか実現できません。
2. **契約**：コアと外界が接する面です。シナリオスキーマ（`bajutsu schema`）、`Driver` Protocol、
   `manifest.json`（`schemaVersion` で版管理）の 3 つです。
3. **周辺**：契約の消費者です。`serve/`、`mcp/`、codegen の emitter、AI プロバイダの経路、webhook
   通知、GitHub ヘルパが該当します。いずれも契約だけに依存し、extra で外せます。

周辺は重く、しかも育っています。`serve/` だけで Python 約 6,500 行あり、パッケージの 2 割強を占め
ます。E2E のコアとは別種の運用スタック（FastAPI、Redis / RQ、SQLAlchemy / Alembic、OAuth）も抱えて
います。ホスティング（BE-0015 / BE-0016）が進めば、さらに育ちます。

これらの依存は、個々には extra と import guard ですでに隔離されています。ところが、**レイヤ**規則
そのもの（「コアは `serve/` に依存しない」「周辺は契約だけに依存する」）は、実行できる検査としては
どこにも存在しません。現行実装の行儀によって守られているだけで、将来のリファクタリングは、どの検査
からも異議を受けずに、この規則を静かに壊せます。

規則をゲートの検査にすれば、慣行が契約になります。後日の判断のリスクも下がります。ホスティングの
需要がいずれ、`serve/` を別配布物（`bajutsu-serve`）へ切り出すことを正当化するかもしれません。その
分割が低リスクになるのは、依存の向きがすでに保証されているときに限ります。規則が先で、配布の分割は
（もし行うなら）後で足ります。

## 詳細設計

作業は、重複も漏れもなく、次の 5 つに分解できます。

### 1. レイヤを明示的に記述する

各レイヤの所属（*動機* に挙げたモジュール一覧）を、検査器が読める形で書き下します。どのパッケージが
コアで、どれが契約を成し、どれが周辺かを記述します。この一覧が、アーキテクチャを宣言する唯一の場所
になります。

### 2. 禁止する依存契約を宣言する

向きのある規則を表します。

- 決定的コアは、いかなる周辺パッケージ（`serve/`、AI やエージェントのモジュール、codegen の
  emitter、webhook や GitHub のヘルパ）も import してはなりません。
- 周辺は、コアの内部ではなく契約（`Driver` Protocol、シナリオスキーマ、manifest）を通じてのみ、
  コアに到達します。

これらを、禁止契約やレイヤ契約として符号化します。

### 3. 検査をゲートに組み込む

検査器を `make` のターゲットとして加え、既存の lint / typecheck / test の各手順と並べて、`make check`
と CI に組み込みます。import-linter が自然な選択です。レイヤ契約と禁止契約を宣言的に表し、推移的な
import も追跡します。この検査は Simulator を要さず、ゲートのほかの手順と同じく Linux で走ります。

### 4. 既存の違反をベースライン化する

検査を `main` に対して走らせ、指摘されたものを解消します。結合を直すか、意図的で今は取り除けない
違反について、狭く注記付きの許可リスト（allowlist）項目を記録します。実際の結合を隠さずに、ゲート
を緑にできます。許可リストは例外の記録であって、雑多なものの捨て場ではありません。

### 5. レイヤモデルと検査を文書化する

3 層モデルと強制する境界を、開発者向けドキュメント（`docs/` と `docs/ja/` の対訳）に記します。
コントリビュータは、なぜある import がゲートを落とすのか、新しいモジュールがどこに属するのかを
理解できます。

### 機械的に検査できる成果

決定的コアのモジュールが周辺のモジュールを import した場合、あるいは宣言した契約に反した場合に、
`make check` が落ちます。検査は静的で決定的であり、LLM は関与しません。この検査は、ディレクティブ 1
とディレクティブ 3 を、慣行ではなくアーキテクチャの契約として表したものです。

### プライムディレクティブとの整合

検査は、import グラフに対する静的解析です。モデルも実行時も使わず、決定的な合否のほかは `run` や
CI の verdict 経路に何も置きません。ディレクティブは構造で守ります。コアを周辺から独立に保つことが、
決定的な verdict 経路を AI や serve のスタックから切り離しておくことにほかなりません。

## 検討した代替案

- **既存の import guard テストに頼る。** 不十分として却下します。それらのテストが固定するのは、
  サブシステムごとの「重い依存が既定の実行時経路で import されない」ことであり、1 モジュールの実行時
  挙動です。パッケージ全体にわたる向きのあるレイヤ規則は表せません。既定でない経路でコアが周辺を
  import することについても、何も言いません。本項目は、欠けている静的でアーキテクチャ全体の契約を
  足すものであり、両者は補い合います。
- **いま `serve/` を別配布物（`bajutsu-serve`）へ切り出す。** 時期尚早として却下します。規則が先
  です。依存の向きが強制されれば、リポジトリ内の分離だけで設計上の利得は得られます。配布の分割は、
  ホスティングの需要が確定するまで待てます。

  パッケージとしての物理分離は、検査より強い保証を持ちます。周辺のコードはコアの実行環境にそもそも
  存在しなくなるため、規則を誰かが緑に保ち続ける必要すらありません。ただし、その代わりに実務コスト
  を負います。二つ目の `pyproject.toml`、独立したバージョニング、リリース手順、パッケージ間の依存
  管理です。このコストは、一度負うと後戻りしにくいものです。

  この分割は、BE-0015 か BE-0016 のどちらかが具体的な動機を生んだ時点で、後続の項目として改めて検討
  します。たとえば、`serve/` を CLI やコアとは別のリリース周期で出す必要が生じる場合です。
  ホスティングで、コアのテストやドライバの資産を含めずに周辺だけを配布したくなる場合もあります。
  需要が確定する前に、見込みで分割はしません。
- **手製の grep / AST 検査。** 却下します。import-linter は、レイヤ契約と禁止契約を宣言的に表し、
  推移的な import を解決します。独自の grep は壊れやすく、確立した道具の仕事を、より劣った形で
  作り直すことになります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] レイヤを明示的に記述する（コア / 契約 / 周辺の所属）
- [x] 禁止する依存契約を宣言する（コア ↛ 周辺、契約は可搬なインナー層に保つ）
- [x] 検査器を `make` ターゲット、`make check`、CI に組み込む
- [x] 既存の違反をベースライン化する（誤配置のヘルパ 3 つを修正。許可リストは不要）
- [x] レイヤモデルと強制する境界を文書化する（両言語）

ログ：

- 2026-07-04: 検査を出荷しました。`[tool.importlinter]`（pyproject）に `import-linter` を置き、
  契約を 2 つ宣言します。決定性コアは周辺を import しないこと、そしてシナリオスキーマと `Driver`
  Protocol を可搬なインナー契約に保つことです。`make lint-imports` として組み込み、`make check`
  と CI に加えました。ベースライン化で誤配置のヘルパが 3 つ見つかり、許可リストにせず修正しました。
  `screen_size_from_elements` と `shows_app_ui` を record 経路から新しいコアモジュール
  `bajutsu/elements.py` へ移し、`AiConfig` を `anthropic_client` から `config` へ移しました（AI
  経路のために再エクスポートします）。3 層モデルと強制する境界を `docs/architecture.md`（および
  `docs/ja` のミラー）に記載しました。どちらの契約も許可リストなしで通過します。

## 参考

`bajutsu/` のパッケージ構成（3 層を定義するモジュール一覧。コアは `orchestrator/`、`drivers/`、
`runner/`、`assertions`、`evidence`、`report`、`config`、周辺は `serve/`、`mcp/`、codegen の emitter
と AI 経路）、`tests/serve/test_import_guard.py`（本項目が静的な契約で補う、サブシステムごとの実行時
import guard）、`Makefile` と [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)（この検査
が加わるゲート）、[DESIGN.md](../../DESIGN.md) と
[architecture.md](../../docs/ja/architecture.md)（本項目が符号化するコア / 契約 / 周辺のモデル）、
[BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md)
（本項目が拡張する、これまでのゲート強化）、
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)、
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)（周辺を育て、
境界の価値を高めるホスティングの作業）。
