[English](BE-0304-doctor-provision-real-environment-verification.md) · **日本語**

# BE-0304 — オンボーディングゲート（doctor・preflight・provision）の実環境検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0304](BE-0304-doctor-provision-real-environment-verification-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0304") |
| 実装 PR | [#1367](https://github.com/bajutsu-e2e/bajutsu/pull/1367), [#1370](https://github.com/bajutsu-e2e/bajutsu/pull/1370) |
| トピック | doctor / オンボーディング |
<!-- /BE-METADATA -->

## はじめに

オンボーディングのゲートは、3つの部品から成ります。

- バックエンドのツール群をインストールする `provision.py`。
- `doctor` が報告する実行可能ゲートを担う `preflight.py` と `requirements.py`。
- その両方を支える `simctl.py` の JSON パーサ（`booted_udids`、`device_catalog`）。

3つとも、注入された fake だけでテストされています。モック化された `subprocess.run` が、正しいコマンド文字列
を検証します。`shutil.which` は monkeypatch されています。`xcrun simctl list devices -j` の代役は、手書きの
JSON リテラルです。これらが CI で実際に動かされることは、一度もありません。

本項目は、`bajutsu doctor` と `python -m bajutsu.provision` を既存の E2E レーンに組み込みます。これにより、
オンボーディング経路の全体が、本物の環境と、意図的に壊した環境の両方に対して、少なくとも一度は実行
されます。

## 動機

3つのギャップは、1つの根本原因と1つの解決策を共有しています。

**provision**：`provision.py` のテストが検証するのは、`("brew", "install", "facebook/fb/idb-companion")`
のようなコマンドが*組み立てられた*ことだけです。実際の `brew` がそれを受け付けたことは検証しません。
しかも実際の E2E ワークフロー（`ios-e2e.yml`、`web-e2e.yml`、`android-e2e.yml`）は、いずれも
`python -m bajutsu.provision` を呼ばず、同等のインストールコマンドを手書きしています。そのため、インストーラ
自身のコマンド構築コードは、どこでも実際に実行されません。

**preflight**：`preflight.py` のテストは、`which`、`booted_count`、`web_pkg` の `callable` を手動で注入します。
このテストが示すのは、ゲートが手渡しの boolean に正しく反応することです。本当に壊れた環境で、ゲートが実際に
*失敗する*ことを確認するテストはありません。たとえば、Xcode のライセンスが未同意の環境や、Chromium を
ダウンロードしていない Playwright の環境です。

**simctl のパース**：`simctl.py` の `booted_udids` と `device_catalog` のパーサは、手書きの JSON リテラルに
対してテストされているだけです。実際に捕捉した `xcrun simctl list devices -j` の出力に対してではありません。
将来の Xcode のバージョンでスキーマが変われば、`doctor` とデバイスプールのラベリングは静かに壊れます。

`.github/actions/bajutsu-e2e/action.yml` は、すでに iOS レーンで `bajutsu doctor` を実行しています。ただし
それは、非ブロッキングの慣習チェック（`|| echo "doctor: non-blocking (convention score only)"`）にすぎません。
終了コードを握りつぶし、レンダリングされた出力に対して何もアサーションしません。そこで動く実際の `simctl`
パースは、一度も検証されていません。Android と web には、そもそも doctor のステップ自体がありません。

既存の実機レーンの中で `bajutsu doctor`（および別途 `python -m bajutsu.provision`）を実際に1回呼び出すと、
このギャップのほとんどが一度に埋まります。

- 実際の `simctl` の JSON 形状を、実際のパーサに通せます。
- 実際のツール有無チェックを、実際のゲートに通せます。
- 意図的に壊した版と対にすれば、ゲートが準備完了と未完了を区別できることを証明できます。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **既存の E2E レーン内で `bajutsu doctor` を実際に実行する**：`ios-e2e.yml`（および別途 `android-e2e.yml`
  と `web-e2e.yml`）に、ステップを追加します。このステップは、そのレーンの実際の環境に対して本物のコマンドを
  実行します。レンダリングされた `environment:` セクションに `✗` が1つもないことを検証します。これは、環境
  ゲート自体が終了コード 1 や 2 にならないという意味です。1回の実行で、`simctl.py` の実際の JSON パースと
  `preflight.py` の実際のツールチェックを検証します。
- **意図的に壊した環境のケースを1つ追加する**：ツールを意図的に欠落させる、または誤設定したジョブ（または
  ジョブのステップ）を使います。例は、`idb` を含まない `PATH` です。`doctor` / `preflight` が非ゼロの終了
  コードで終了し、レンダリングされた `environment:` セクションに `✗` が出ることを検証します。注入された
  fake によるテストでは、この失敗側を今日証明できません。
- **新しい環境で `python -m bajutsu.provision` を実際に実行する**：実際のインストーラをエンドツーエンドで
  実行するジョブを追加します。ジョブは、ベアなコンテナか、レーンのセットアップ前段の新しいステップです。
  インストーラが対象とするツールが利用可能になることを検証します。「コマンド文字列が正しかった」ことと「実際
  のパッケージマネージャがそれを受け付けた」ことの間のギャップを埋めます。
- **実際の `simctl list devices -j` の出力をフィクスチャとして捕捉する**：`tests/test_simctl.py` の手書き
  JSON リテラルを、実際の呼び出しから捕捉した1件に置き換えます（または補います）。将来 Xcode のスキーマが
  ドリフトしても、`doctor` やプールに影響が及ぶ前に捉えられる可能性が生まれます。

## 検討した代替案

- **実際のツールは最終的にインストールされるので、E2E レーンでのインストールコマンド手書きをそのまま残す**：
  これが証明するのは、*ツール自体*が動くことです。`python -m bajutsu.provision` 自身のコマンド構築コードが
  正しいことは証明しません。この体制では、インストーラが回帰しても CI から一切の信号が出ません。これこそが
  実際のギャップです。
- **注入された fake によるユニットテストをさらに増やす**：手書きシナリオを増やしても、実際の `xcrun simctl`
  のスキーマ変更や、実際に壊れたツールチェインは検出できません。fake はすでに内部的に整合しており、まさに
  それゆえに、現実からのドリフトを観測できません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] iOS・Android・web の E2E レーン内で `bajutsu doctor` を実際に実行し、`environment:`
  セクションに `✗` がないことを検証する。
- [x] 意図的に壊した環境のケースを追加し、非ゼロの終了コードで `environment:` セクションに
  `✗` が出ることを検証する。
- [x] 新しい環境で `python -m bajutsu.provision` を実際に実行する。
- [x] 実際の `simctl list devices -j` の出力をテストフィクスチャとして捕捉する。

### ログ

- **web レーンのオンボーディングスライス**（本 PR）：`web-e2e.yml` に非ゲートの
  `onboarding (doctor / provision)` ジョブを追加し、オンボーディングゲートを実環境に対してエンドツーエンドで
  実行します。ジョブは3つの確認を順に行います。
  - ブラウザのない環境で、`doctor` が `✗` を出して明示的に失敗すること。壊した環境のケースです。
  - `python -m bajutsu.provision --backend web` が、実際に Chromium をインストールすること。
  - 続いて `doctor` の環境ゲートが通過すること。

  あわせて `tests/test_simctl.py` に、実際に捕捉した `simctl list devices available -j` の出力
  （`tests/data/simctl_list_devices_available.json`）を追加します。`device_catalog` / `booted_udids` パーサを
  その出力に対して検証することで、Xcode のスキーマドリフトを捉えられるようにします。iOS と Android の
  `doctor` ステップ（最初のチェック項目の残る 2 レーン）は後続スライスとするため、本項目は*実装中*のままと
  します。
- **iOS + Android オンボーディングスライス**（本 PR）：最初のチェック項目の残る 2 レーンが着地し、本項目を
  完了します。
  - `ios-e2e.yml` の `smoke` ジョブ（シミュレータ起動済み）と `android-e2e.yml` の `smoke` ジョブ
    （エミュレータのセッション内）が、それぞれ `bajutsu doctor` を実環境で実行し、`environment:` セクション
    に `✗` がないことを検証します。この検証により、`simctl.py` と `adb` の実際のデバイス数パースと、
    `preflight.py` の実際のツール検査が、実アクチュエータを通じて動きます。
  - 新しい `bajutsu doctor --environment-only` フラグは、doctor を実行可能性ゲートで停止させます。検証範囲が
    環境ゲートに限定され、画面プローブの起動コストや不安定さの影響を受けません。iOS の画面プローブは、
    常駐 XCUITest ランナーです。
  - web スライスが最初に用いた壊れやすいインラインの `awk` / `grep` は、3 レーンすべてで
    `scripts/assert_doctor_env.py` に置き換えます。そのセクションパーサは、ユニットテスト
    （`tests/test_assert_doctor_env.py`）で検証しています。`scripts/e2e_changes.py` と同じ、「未テストの
    シェル → ユニットテスト済みの Python」への移行です。
  - 両方の `smoke` ジョブは、この検証をゲートに含めます。`✗` は、必須の `E2E` チェックを失敗させます。
    環境ゲートは、各ジョブがすでにゲート対象としている `smoke` 実行より厳密に弱いため、不安定さを増やしません。
    web スライスのジョブは異なります。`provision` を再実行し、`playwright install` のネットワーク由来の不安定
    さがあるため、非ゲートのままです。

## 参考

- [BE-0024 — doctor / オンボーディング](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md)。本提案が引き継ぐ、このトピックの起点となる項目です。
- [BE-0164 — config を踏まえた環境インストーラー](../BE-0164-config-aware-environment-installer/BE-0164-config-aware-environment-installer-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/provision.py`、`bajutsu/preflight.py`、`bajutsu/requirements.py`、`bajutsu/simctl.py`、
  `tests/test_provision.py`、`tests/test_preflight.py`、`tests/test_requirements.py`、
  `tests/test_simctl.py`、`.github/actions/bajutsu-e2e/action.yml`、
  `.github/actions/boot-simulator/action.yml`、`.github/workflows/ios-e2e.yml`、
  `.github/workflows/web-e2e.yml`、`.github/workflows/android-e2e.yml`
