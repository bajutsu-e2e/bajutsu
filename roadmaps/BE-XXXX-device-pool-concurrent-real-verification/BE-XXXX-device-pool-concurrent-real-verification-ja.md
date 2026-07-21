[English](BE-XXXX-device-pool-concurrent-real-verification.md) · **日本語**

# BE-XXXX — Real concurrent-device verification of parallel device-pool isolation

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-device-pool-concurrent-real-verification-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Verification & coverage |
<!-- /BE-METADATA -->

## はじめに

`runner/pool.py` の `device_pool` は、`--workers N` による実行に対して特定の分離保証を主張して
います。各ワーカーは固有の `udid` を持ち、固有の `runs/<runId>` ディレクトリを持ち、他のどの
ワーカーともモックのポートやインデックスを共有しない、というものです(`DESIGN.md` §3.3)。この
保証に対するテストである `tests/runner/test_pool.py` は、`bajutsu.backends.make_driver` を
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
ワーカーごとのディレクトリ確立前に計算される成果物パスなど)で競合することはないか、という点です。
2台の実際のデバイスの証跡捕捉が、合成的で逐次実行される fake テストでは発生し得ない実際の
タイミング圧力の下で互いに書き込みを衝突させることはないか、という点も問われます。

## 詳細設計

提案の粒度は次の単位で MECE に分解します。

- **既存の E2E レーンで実際のデバイスを2台同時に起動する**：`ios-e2e.yml`(実際の Simulator を
  2台起動)、そして別途 `android-e2e.yml`(リソースが許せば実際のエミュレータを2台起動)を拡張し、
  両方のワーカーを同時にビジーな状態に保てるだけの規模のシナリオセットに対して `--workers 2`
  を実行します。
- **完了だけでなく実際の分離を検証する**：各ワーカーの `runs/<runId>` ディレクトリ、udid、証跡
  成果物がきれいに分離されており、一方のワーカーの成果物が他方のディレクトリに現れないことを
  確認します。これが分離という主張の、具体的で検証可能な形です。
- **まず非 gating の signal として着地させる**：並行デバイスレーンは、既存の単一デバイス
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

- [ ] `ios-e2e.yml` で実際の Simulator を2台同時に起動し、`--workers 2` を実行する。
- [ ] リソースが許せば `android-e2e.yml` で実際のエミュレータ2台にも拡張する。
- [ ] `runs/<runId>`、udid、証跡成果物のワーカーごとの分離を検証する。
- [ ] まず非 gating として着地させ、安定後に必須化する。

## 参考

- [BE-0282 — CI における実 backend のネットワーク捕捉・モック・アサーションのカバレッジ](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/runner/pool.py`、`tests/runner/test_pool.py`、`.github/workflows/ios-e2e.yml`、
  `.github/workflows/android-e2e.yml`、`DESIGN.md` §3.3(並列実行とアイソレーション)
