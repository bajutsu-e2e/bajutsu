[English](BE-0298-device-pool-concurrent-real-verification.md) · **日本語**

# BE-0298 — 並列デバイスプール分離の実機並行検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0298](BE-0298-device-pool-concurrent-real-verification-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0298") |
| 実装 PR | [#PENDING](https://github.com/bajutsu-e2e/bajutsu/pull/PENDING) |
| トピック | 検証とカバレッジ |
| 関連 | [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`runner/pool.py` の `device_pool` は、`--workers N` による実行に対して特定の分離保証を主張して
います。各ワーカーは固有の `udid` を借り受け、共有された1つの run ディレクトリ（`runner/pipeline.py`
の `run_dir = runs_dir / run_id`）の下にある固有の `run_dir/<scenario_id>` サブディレクトリへ
証跡を書き込み、他のどのワーカーのシナリオともモックのポートや索引を共有しません。これは
`DESIGN.md` §3.3 が述べる「状態を共有しない」という不変条件そのものですが、同節の
「ワーカーごとに固有の `runs/<runId>`」という記述自体は、現在の共有された `run_dir` という
構成より前の記述です。この保証に対するテストである `tests/runner/test_pool.py` は、
`bajutsu.backends.make_driver` を
monkeypatch して、`"UDID-A"`/`"UDID-B"` のような架空の udid に対する `FakeDriver` インスタンスを
返すようにしているだけです。実際の Simulator を2台、あるいは実際のエミュレータを2台同時に起動
する CI レーンは1つもありません。`ios-e2e.yml`/`android-e2e.yml` のどのジョブもデバイスを
ちょうど1台しか起動しません。本項目は、実際の並行デバイスレーンを追加します。

## 動機

架空の udid と `FakeDriver` は、プールの記帳ロジックが内部的に整合していること、すなわち
*プールが管理するデータ構造の中では*ワーカー A のリソースがワーカー B のものと本当に分離
されていることを証明します。しかし、実際の OS レベルのデバイスやプロセスの競合に対してこの保証が
成立するかどうかは証明できません。異なるデバイスを対象とする2つの実際の `simctl`/`adb` 呼び出し
が、プール自身の記帳の外側で idb/adb が触れる共有リソース(共有された boot ロックやポートの衝突、
ワーカーの `run_dir/<scenario_id>` サブディレクトリ確立前に計算される成果物パスなど)で競合する
ことはないか、という点です。2台の実際のデバイスの
[証跡](../../docs/ja/glossary.md#証跡-capturepolicy-trace-triage)捕捉が、合成的で逐次実行される fake テストでは発生し得ない実際の
タイミング圧力の下で互いに書き込みを衝突させることはないか、という点も問われます。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **既存の E2E レーンで実際のデバイスを2台同時に起動する**：`ios-e2e.yml`(実際の Simulator を
  2台起動)、そして別途 `android-e2e.yml`(リソースが許せば実際のエミュレータを2台起動)を拡張し、
  両方のワーカーを同時にビジーな状態に保てるだけの規模のシナリオセットに対して `--workers 2`
  を実行します。
- **完了だけでなく実際の分離を検証する**：各ワーカーの `udid` と `run_dir/<scenario_id>`
  サブディレクトリがきれいに分離されており、一方のワーカーのシナリオの成果物が他方の下に
  現れないことを確認します。これが分離という主張の、具体的で検証可能な形です。
- **まずゲート対象外のシグナルとして着地させる**：並行デバイスレーンは、既存の単一デバイス
  ジョブよりもリソース消費が大きく、環境への感度も高くなりえます。
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例に従い、安定を確認してから必須化します。

## 検討した代替案

- **記帳ロジックがユニットテストされていることを根拠に、fake driver によるプールのテストを
  信頼する**：プール自身のデータ構造の中での記帳が正しいことは、そのデータ構造の外側にある
  OS/サブプロセスレベルの競合については何も語りません。これはまさに、実際の並行デバイスだけが
  表面化させられ、逐次実行の fake では表面化させられないものです。
- **実際のデバイスの代わりに、合成的なストレスハーネスで競合を模擬する**：専用のハーネスでは、
  実際の競合が起こりうる実際の `simctl`/`adb` サブプロセス層を検証できません。実際に同時起動
  した2台のデバイスのほうが、コストは高くとも、より忠実なテストです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 実際のデバイスを2台同時に起動する（`ios-e2e.yml` の Simulator、`android-e2e.yml` のエミュレータを
  リソースが許せば）、両方に対して `--workers 2` を実行する。
- [x] `udid` と `run_dir/<scenario_id>` サブディレクトリのワーカーごとの分離を検証する。
- [x] まずゲート対象外として着地させる。
- [ ] 安定を確認してから各レーンを必須化する。

ログ:

- [#PENDING](https://github.com/bajutsu-e2e/bajutsu/pull/PENDING) — 両レーンをゲート対象外の
  シグナルとして着地させ、あわせて判定に使うアサーションを追加しました。**アサーション**：
  `scripts/assert_pool_isolation.py` は、終了した run の `manifest.json` と run ディレクトリの
  サブディレクトリ一覧を読み、5 種類の違反で失敗します。ほかのワーカーの slug の下に記録された
  成果物、1 つの slug を共有する 2 つの結果、どの結果にも属さないサブディレクトリ、片方のデバイスが
  すべてのシナリオを引き受けた状態、異なるデバイス上の 2 本のシナリオが実時間で重ならなかった
  状態です。最後の 1 つは、競合を一度も起こしていないレーンが通ってしまうのを防ぎます。デバイス
  2 台とシナリオ 4 本なら、プールが逐次的に 2 台を交互に使っただけでも相異なる udid は 2 つに
  なるからです。そこで各シナリオの実行区間を、ステップ自身が持つ絶対時刻 `started_at`
  （manifest v6）から求め、デバイスをまたぐ重なりを実際に要求します。違反はいずれもユニット
  テストで固定してあり（`tests/test_assert_pool_isolation.py`）、同一デバイス上の重なりが
  この検査を満たしては*ならない*ケースも含みます。**iOS**：`pool (xcuitest)` は Simulator を
  2 台起動し、smoke / search / notices / navigation を 1 回の `bajutsu run --workers 2` で
  走らせます。2 台目を別のデバイスにするのは `boot-simulator` action に追加した `exclude-udid`
  入力です。これがないと action の再利用分岐が 1 台目を返し、`--udid "$A,$A"` が誤った理由で
  すべての検査を通ってしまいます。**Android**：`pool (adb)` は、キャッシュ済み AVD の 2 つ目の
  インスタンスを `-read-only` で起動します。起動は emulator-runner のステップの内側で行います。
  この action には 2 台起動のモードがなく、エミュレータはそのステップの長さしか生きないためです
  （`scripts/android_pool_e2e.sh`、`make -C demos/showcase/android e2e-pool`）。両インスタンスは
  `-memory 3072 -cores 1` で動くので、このジョブは専用の AVD キャッシュキーを持ちます。再開する
  スナップショットのマシン構成は、保存時の構成と一致しなければならないからです。**変更
  フィルタ**：両ジョブは `scripts/e2e_changes.py` の新しい `pool` 出力（`touches_pool`）で
  発火します。ほかのすべてのジョブが読むレーン全体のシグナルより狭いので、通常の
  `bajutsu/` の変更でデバイス 2 台ぶんのコストを払いません。`workflow_dispatch` でも発火し、
  それが任意のタイミングで動かす手段になります。**DESIGN.md §3.3** は同じ変更で現状に
  合わせました（BE-0113）。「ワーカーごとに固有の `runs/<runId>`」という記述は、本項目の
  はじめに自身が述べる共有 run ディレクトリより前の記述でしたし、「`--udid` 明示時は単一デバイス
  に固定する」という記述は、本レーンが依拠するカンマ区切りリストと矛盾していました。

## 参考

- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/runner/pool.py`、`bajutsu/runner/pipeline.py`、`tests/runner/test_pool.py`、`.github/workflows/ios-e2e.yml`、
  `.github/workflows/android-e2e.yml`、`DESIGN.md` §3.3(並列実行とアイソレーション)
