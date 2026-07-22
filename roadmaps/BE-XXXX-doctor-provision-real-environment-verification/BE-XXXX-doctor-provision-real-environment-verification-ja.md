[English](BE-XXXX-doctor-provision-real-environment-verification.md) · **日本語**

# BE-XXXX — オンボーディングゲート（doctor・preflight・provision）の実環境検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-doctor-provision-real-environment-verification-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | doctor / オンボーディング |
<!-- /BE-METADATA -->

## はじめに

オンボーディングのゲート、すなわちバックエンドのツール群をインストールする `provision.py`、
`doctor` が報告する実行可能ゲートを担う `preflight.py`/`requirements.py`、そしてその両方を支える
`simctl.py` の JSON パーサ（`booted_udids`、`device_catalog`）は、いずれも注入された fake だけで
テストされています。正しいコマンド文字列が組み立てられたことを検証するモック化された
`subprocess.run`、monkeypatch された `shutil.which`、さらに `xcrun simctl list devices -j` の
代役を務める手書きの JSON リテラルです。これらが CI で実際に動かされることは一度もありません。
本項目は `bajutsu doctor` と `python -m bajutsu.provision` を既存の E2E レーンに組み込み、オンボーディング
経路全体を、本物の環境、そして意図的に壊した環境の両方に対して少なくとも一度は実行させます。

## 動機

3つのギャップは1つの根本原因と1つの解決策を共有しています。`provision.py` のテストは
`("brew", "install", "facebook/fb/idb-companion")` のようなコマンドが*組み立てられた*ことだけを
検証し、実際の `brew` がそれを受け付けたことは検証しません。しかも実際の E2E ワークフロー
（`ios-e2e.yml`、`web-e2e.yml`、`android-e2e.yml`）はいずれも、`python -m bajutsu.provision` を呼ぶのではなく
同等のインストールコマンドを直接手書きしているため、インストーラ自身のコマンド構築コードは
どこでも実際に実行されることがありません。`preflight.py` のテストは `which`/`booted_count`/
`web_pkg` を手動で注入する `callable` に頼っているため、このゲートが「手渡しの boolean にまさしく
反応する」ことを超えて、本当に壊れた環境（Xcode のライセンス未同意、Chromium 未ダウンロードの
Playwright など）で実際に*失敗する*ことを確認するテストはありません。そして `simctl.py` の
`booted_udids`/`device_catalog` パーサは手書きの JSON リテラルに対してテストされているだけで、
実際に捕捉した `xcrun simctl list devices -j` の出力に対してではありません。将来の Xcode の
バージョンでスキーマが変われば、`doctor` とデバイスプールのラベリングは静かに壊れます。
`.github/actions/bajutsu-e2e/action.yml` はすでに iOS レーンで `bajutsu doctor` を実行して
いますが、それは非ブロッキングの慣習チェック（`|| echo "doctor: non-blocking (convention
score only)"`）にすぎず、終了コードを握りつぶし、レンダリングされた出力に対して何かをアサーション
することも一度もしていないため、そこで動く実際の `simctl` パースは実際には一度も検証されて
いません。Android と web にはそもそも doctor のステップ自体が存在しません。

既存の実機レーンの中で `bajutsu doctor`（および別途 `python -m bajutsu.provision`）を一度実際に呼び
出すだけで、このギャップのほとんどが一度に埋まります。実際の `simctl` の JSON 形状を実際のパーサに通し、
実際のツール有無チェックを実際のゲートに通し、さらに意図的に壊した版と対にすることで、このゲートが
準備完了と未完了を実際に区別できることまで証明できます。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **既存の E2E レーン内で `bajutsu doctor` を実際に実行する**：
  `ios-e2e.yml`（および別途 `android-e2e.yml`/`web-e2e.yml`）に、そのレーンの実際の環境に対して本物のコマンドを実行し、
  レンダリングされた `environment:` セクションに `✘` が1つもない（環境ゲート自体が終了コード 1/2 に
  ならない）ことを検証するステップを追加します。これにより `simctl.py` の実際の JSON パースと
  `preflight.py` の実際のツールチェックを一度に検証します。
- **意図的に壊した環境のケースを1つ追加する**：ツールを意図的に欠落させる、または誤設定した（例：`idb` を
  含まない `PATH`）ジョブ（またはジョブのステップ）で、`doctor`/`preflight` が非ゼロの終了コードで
  レンダリングされた `environment:` セクションに `✘` を報告することを検証します。これは、注入された
  fake によるテストでは今日証明できない失敗側です。
- **新しい環境で `python -m bajutsu.provision` を実際に実行する**：ベアなコンテナ、またはレーンのセットアップ
  前段の新しいステップとして、実際のインストーラをエンドツーエンドで実行し、対象のツールが利用
  可能になることを検証します。「コマンド文字列が正しかった」と「実際のパッケージマネージャが
  それを受け付けた」の間のギャップを埋めます。
- **実際の `simctl list devices -j` の出力をフィクスチャとして捕捉する**：`tests/test_simctl.py`
  の手書き JSON リテラルを、実際の呼び出しから捕捉した1件に置き換える（または補う）ことで、
  将来 Xcode のスキーマがドリフトしても、`doctor` やプールに影響が及ぶ前に捉えられる可能性を作ります。

## 検討した代替案

- **実際のツールは最終的にインストールされるので、E2E レーンでのインストールコマンド手書きを
  そのまま残す**：これが証明するのは*ツール自体*が動くことであり、`python -m bajutsu.provision` 自身の
  コマンド構築コードが正しいことではありません。この体制のもとでは、インストーラが回帰しても
  CI から一切の信号が出ません。これこそが実際のギャップです。
- **注入された fake によるユニットテストをさらに増やす**：手書きシナリオを増やしても、実際の
  `xcrun simctl` のスキーマ変更や実際に壊れたツールチェインは検出できません。fake はすでに
  内部的に整合しており、まさにそれゆえに現実からのドリフトを観測できないのです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] iOS・Android・web の E2E レーン内で `bajutsu doctor` を実際に実行し、`environment:`
  セクションに `✘` がないことを検証する。
- [ ] 意図的に壊した環境のケースを追加し、非ゼロの終了コードで `environment:` セクションに
  `✘` が出ることを検証する。
- [ ] 新しい環境で `python -m bajutsu.provision` を実際に実行する。
- [ ] 実際の `simctl list devices -j` の出力をテストフィクスチャとして捕捉する。

## 参考

- [BE-0024 — doctor / オンボーディング](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md) — 本提案が引き継ぐ、このトピックの起点となる項目です。
- [BE-0024 — doctor / オンボーディング](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md) — 本提案が引き継ぐ、このトピックの起点となる項目です。
- [BE-0164 — config を踏まえた環境インストーラー](../BE-0164-config-aware-environment-installer/BE-0164-config-aware-environment-installer-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/provision.py`、`bajutsu/preflight.py`、`bajutsu/requirements.py`、`bajutsu/simctl.py`、
  `tests/test_provision.py`、`tests/test_preflight.py`、`tests/test_requirements.py`、
  `tests/test_simctl.py`、`.github/actions/bajutsu-e2e/action.yml`、
  `.github/actions/boot-simulator/action.yml`、`.github/workflows/ios-e2e.yml`、
  `.github/workflows/web-e2e.yml`、`.github/workflows/android-e2e.yml`
