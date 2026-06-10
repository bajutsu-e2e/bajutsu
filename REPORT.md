# Simyoke — 作業レポート

自然言語駆動の iOS Simulator 向け E2E テストツール **Simyoke** の開発セッション報告。
設計のブラッシュアップ、M1 実装、デバイス層、同梱サンプルアプリまでをまとめる。

- **リポジトリ:** `simyoke`（ローカル、リモート設定済み）
- **本作業のコミット数:** 19（すべて英語メッセージ）
- **テスト:** 90 passing ・ ruff クリーン ・ mypy `--strict` クリーン
- **ツールチェーン:** uv + Python 3.13 / iOS サンプルは XcodeGen + xcodebuild
- **設計書:** [`DESIGN.md`](DESIGN.md)（日本語）v1.7

---

## 1. 作業フェーズ

### Phase 1 — 設計書のブラッシュアップ（`DESIGN.md` v1.1 → v1.7）

概要レベルの指針を、実装可能な仕様書へ引き上げた。

- **仕様の穴埋め:** `Selector` 型と解決セマンティクス、アサーション DSL（7 種）、ステップ文法、
  `capture` トークン文法、observe→act→verify ループと AI の関与境界。
- **汎用化:** ツールをアプリ非依存に。アプリ固有差分は config（`apps.<name>`）へ集約。
  オンボーディング手順と `doctor` 充足度スコア（§7.1/§7.2）、識別子の命名規約と予約名前空間（§7.3）を追加。
- **アーキ追加:** モックサーバの居場所、並列実行とアイソレーション、シナリオのラウンドトリップ
  （記録 → 編集 → 実行）、**安定度順ラダー**（最も安定する UI 操作から）、**主 + フォールバック**の backend モデル。
- 最後に相互参照・用語の整合性チェック。

### Phase 2 — M1 決定的コア（実機不要）

Tier2 ランナーの決定的な「頭脳」を実装。各モジュールをユニットテスト。

| モジュール | 役割 |
|---|---|
| `drivers/base.py` | Driver 抽象 + **セレクタ解決**（0 件 → NotFound、2 件以上 → Ambiguous）= 決定性の核 |
| `scenario.py` | シナリオスキーマ（ステップ / 待機 / 7 種アサーション）を pydantic で厳格検証 + YAML 読込 |
| `assertions.py` | 機械チェックの評価（例外を投げず理由付き結果を返す総関数）|
| `orchestrator.py` | Tier2 run ループ（act → wait → verify）、各ステップ計時、AI 非関与 |
| `report.py` | `manifest.json`（単一の真実）+ JUnit XML |
| `config.py` | チーム既定 × アプリ別の解決（backend リスト、redact マージ）|
| `drivers/fake.py` | 実機不要テスト用のインメモリ Driver |

### Phase 3 — デバイス層 + CLI

デバイスに触れる backend を実装し CLI を配線（純粋部分はテスト済み、subprocess 実行は実機検証待ち）。

| モジュール | 役割 |
|---|---|
| `env.py` | simctl コマンド層（erase/boot/launch/openurl/io）を差し替え可能な runner 越しに |
| `drivers/idb.py` | idb backend — `ui describe-all` パーサ（配列 + NDJSON）+ フレーム中心の座標 tap（`AXUniqueId` で id ファースト解決）|
| `backends.py` | backend 選択: 安定度順で最初に利用可能なもの |
| `doctor.py` | 充足度スコア（idCoverage / namespaceConformance / uniqueness → Ready/Partial/Blocked）|
| `runner.py` | run パイプライン + デバイス driver factory（シナリオごとに起動）|
| `evidence.py` | 軽量 capture（elements.json / screenshot）|
| `cli.py` | `run` / `doctor` を config + runner + backends に配線 |

### Phase 4 — サンプルアプリ + フィクスチャ（M2 準備）

AI ループ（M2）と実機検証のための、計装済み自己完結 SwiftUI アプリ。

- **`sample/SimyokeSample`**（XcodeGen, `com.simyoke.sample`）: Onboarding → Login →
  Home（list / counter / search / load）→ Settings。**iOS Simulator 向けにコンパイル成功
  （`** BUILD SUCCEEDED **`）。**
- **全プリミティブを網羅:** 全ステップ種別、7 種アサーション、launch-env フック、deeplink
  （`simyokesample://`）、`os_signpost` 区間、テスト時のアニメーション無効化。
- `accessibilityIdentifier` は namespaced・データ由来の規約に準拠
  （`list.row.<id>`、`settings.reindex`、予約 `auth.*` / `nav.*`）。
- **`sample/scenarios/`** — 全プリミティブを使う example シナリオ（smoke / auth / settings / list）。
  スキーマ検証を CI で実施。
- **`simyoke.config.yaml`** — `sample` と `searchsample` の app エントリを持つ実 config。

---

## 2. 主要な設計判断

- **曖昧さはエラー。** 2 件以上に一致するセレクタは「最初の一致を叩く」のではなく送出する
  — 決定性を構造で担保。
- **AI は判定者にならない。** `run` は AI 非依存、合否は機械アサーションのみ。AI は記録（record）か
  調査（triage）だけ。
- **安定度順ラダー。** UI 操作は最も安定する手段/backend を優先（id で解決 → フレーム中心の座標 tap → …）。
  actuator は安定度順で最初に利用可能な backend で、idb は id ファーストセレクタをネイティブの
  `AXUniqueId` から直接解決するので、手元でも CI でも同じシナリオが動く。
- **config によるアプリ非依存。** アプリ追加 = `apps.<name>` を 1 つ足すだけ。ツール・ドライバ・ランナーは不変。

## 3. 発見して修正したバグ 🐛

サンプルの `capturePolicy` を実 YAML で流して潜在バグが表面化:
**PyYAML（YAML 1.1）は `on` を真偽値 `True` に解決する**ため、`on:` トリガーキー（§9 A）が
静かに壊れていた。`simyoke/_yaml.py` で bool リゾルバを `true`/`false` のみに制限して修正、
回帰テスト付き。**実フィクスチャを作ったからこそ**見つかった問題。

---

## 4. ステータス: 検証済み vs 保留

**検証済み（実機不要）:**

- 決定的パイプライン全体 — シナリオ → 解決 → 操作 → 待機 → 判定 → レポート — が
  FakeDriver で end-to-end に動作（90 tests、ruff、mypy strict）。
- CLI はデバイス境界まで動作（config / app / scenario / backend のエラーは exit 2）。
- サンプルアプリは iOS Simulator 向けに**コンパイル成功**。

**実機検証待ち（要 Xcode + Simulator）:**

- idb の **subprocess 実行**と simctl の起動シーケンス。**パーサはテスト済み**だが、
  外部 CLI の仕様と JSON スキーマは**推測**（コードに明記）であり、導入済みツールの実出力に対して
  確認が必要 — 最初に調整が要る箇所。

## 5. 残作業

- **M1 仕上げ（Mac 上）:** idb を実出力に対して検証し、起動済み Simulator で
  `simyoke run sample/scenarios/smoke.yaml --app sample` を実行。シナリオが idb で通ることを確認。
- **M2:** AI ループ — `record`（探索 + 自然言語のシナリオ正規化）と capturePolicy のトリガー発火。
- **M3:** network（モック）+ appTrace（os_signpost）+ redaction + XCUITest codegen + CI。
- **M4:** 自己修復トリアージ（失敗の要約、最小シナリオ差分の提案、人間レビュー前提）。

## 6. ビルド・実行・テスト

```bash
uv sync --extra dev        # Python 3.13 venv + 依存
make check                 # ruff + mypy + pytest （または uv run pytest -q）
make sample-build          # xcodegen generate + xcodebuild（iOS Simulator）

# 起動済み Simulator と idb を入れた Mac で:
simyoke run sample/scenarios/smoke.yaml --app sample
simyoke doctor --app sample
```

## 7. プロジェクト構成

```
simyoke/                  # ツール本体（Python）
├── drivers/{base,fake,idb}.py
├── scenario.py  assertions.py  orchestrator.py  runner.py
├── report.py  evidence.py  config.py  backends.py  env.py  doctor.py
├── cli.py  _yaml.py
sample/                    # 計装済み SwiftUI フィクスチャアプリ（XcodeGen）
├── SimyokeSample/*.swift  project.yml  scenarios/*.yaml  README.md
simyoke.config.yaml       # チーム既定 × アプリ別
DESIGN.md                  # 設計書（日本語、v1.7）
tests/                     # 90 テスト
```
