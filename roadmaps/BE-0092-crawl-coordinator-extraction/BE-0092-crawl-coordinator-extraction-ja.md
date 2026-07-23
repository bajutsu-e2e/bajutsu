[English](BE-0092-crawl-coordinator-extraction.md) · **日本語**

# BE-0092 — クロール調整役をクラスに切り出す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0092](BE-0092-crawl-coordinator-extraction-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0092") |
| 実装 PR | [#321](https://github.com/bajutsu-e2e/bajutsu/pull/321) |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

[`bajutsu/crawl.py`](../../bajutsu/crawl.py) の `crawl()` をリファクタリングし、共有される並行状態
（スクリーンマップ、フロンティア、各種の上限値、そしてそれらを守るロック）を `_Coordinator` という
小さなクラス一つにまとめます。`crawl()` 自身は、その調整役を呼び出しながら端末を歩く処理だけが残ります。
これは**挙動を変えない**内部リファクタリングです。クロールは引き続き探索のための道具（Tier 1 であり、
CI ゲートには決して入りません）であり、スクリーンの同一性、遷移、クラッシュの判定は今とまったく同じで、
既存の `test_crawl*` のテスト群が回帰の歯止めになります。公開 API もディスク上のスクリーンマップも
変わりません。本提案は、ここで再編する並行処理を導入した
[BE-0064](../BE-0064-parallel-crawl/BE-0064-parallel-crawl-ja.md) と
[BE-0077](../BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl-ja.md) の保守性面での
対になる提案です。挙動や性能の変更ではなく、プロダクトコードの挙動を変えない内部リファクタリングなので、
「コードベース品質・技術的負債」のトピックの下に置きます。これは
[BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md)
が扱う *コントリビューターワークフロー*——このリポジトリで作業するためのツール——とは区別される
ものです。
[BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md)
は、これと同種の挙動を変えない内部構造リファクタリングです。

## 動機

`crawl()` はこのコードベースで最も複雑な関数です。現状では、約 370 行の関数一つの中に**11 個のネストした
クロージャ**（`_bootstrap`、`_observe`、`_emit`、`_claim`、`_finish`、`_discover`、`_publish`、
`_select_next_work`、`_worker`、`_give_back`、`_run`、`_run_extra`）を抱え、それらが `nonlocal` で
**1 個の `threading.Condition` と 8 個の可変な共有変数**（`path_to`、`pending`、`claimed`、
`discovering`、`steps`、`active`、`stopped`、`failure`）を共有しています。

この形から、3 つの問題が生じます。いずれも今この瞬間のバグではありませんが、クロールエンジンへの今後の
変更すべてのコストを押し上げます。

1. **ロックの契約が関数全体に散らばっている。** マルチスレッドのフロンティアの正しさは、厳密な規律に
   依存します。どの変更を `cond` の下で行うか、`active`／`steps` をいつ増やすか、
   `cond.notify_all()`／`cond.wait()` をいつ呼ぶか、そしてワーカーごとの `errors` カウンタは*共有では
   なく* `_worker` に留めなければならない、という微妙な事実です。今はその規律が、約 250 行に散らばった
   `# holding the lock`／`# off-lock` のコメントとして記録されています。レビュアーは一箇所で不変条件を
   読むのではなく、コメントから組み立て直さなければなりません。
2. **共有状態が暗黙的である。** 8 個の `nonlocal` 変数と 1 個の `Condition` が事実上クロールの調整役の
   オブジェクトになっていますが、名前も境界もありません。どのクロージャもどの変数にも触れられるため、
   「並行状態とは何で、誰が書き換えてよいのか」は関数全体を読まなければ答えられません。
3. **各ステップを単独でテストしたり変更したりしにくい。** フロンティアのロジック
   （`_select_next_work`、`_claim`、`_publish`）が端末の歩行処理（`_worker`、`_observe`、`_discover`）と
   溶接されているため、偽の端末を歩行全体に通さずにスケジューリングの判断を単体テストできません。

このコードは、構造としてはともかくコメントの上では、既に一つの切れ目できれいに分かれています。すべての
クロージャが、**ロックを保持する（共有状態に触れる）**ものか、**ロック外（端末 I/O を行う）**ものかの
どちらかとして印付けされています。本提案は、その既存の切れ目をそのままクラスの境界として明示するもの
です。

## 詳細設計

ロックを保持する側を、`Condition` と 8 個の共有変数を所有する `_Coordinator` クラスに移します。ロック外の
側（`_observe`、`_discover`、`_bootstrap`、`_worker` の端末ステップ、`_replay`）は、driver を受け取り、
共有状態の遷移ごとに調整役のメソッドを呼ぶ関数として残します。

```python
class _Coordinator:
    """1 個のロックの背後にあるクロールの共有並行状態: スクリーンマップ、フロンティア
    (path_to / pending)、グローバル操作の claim、探索中集合、step/active/stopped の上限値。
    共有状態のすべての変更はここのメソッドを通るので、ロックの規律が一箇所で読める。"""

    def __init__(
        self,
        screen_map: ScreenMap,
        *,
        max_screens: int,
        max_steps: int,
        prune_global: bool,
        on_event: OnEvent | None,
    ) -> None:
        self._cond = threading.Condition()
        self._sm = screen_map
        self.path_to: dict[str, list[Action]] = {}
        self.pending: dict[str, list[Action]] = {}
        self._claimed: dict[str, str] = {}
        self._discovering: set[str] = set()
        self._steps = 0
        self._active = 0
        self._stopped = False
        self.failure: list[Exception] = []
        ...

    # 次のフロンティア項目を予約する while/wait ループ（steps と active をアトミックに増やす）
    def select_next_work(self, current_fp: str | None) -> _Work | None: ...
    # ロックの下でノード登録と操作の claim を行い、そのスクリーンのフロンティアを返す
    def publish(self, fp: Fingerprint, node: Node, actions: list[Action]) -> list[Action]: ...
    # エッジを記録し、新たに見た遷移先を予約し、このワーカーが探索するかどうかを返す
    def record_edge(self, src_fp, action, dst_fp, dismissed, path) -> bool: ...
    def record_crash(self, path) -> None: ...
    def record_alert(self, path, dismissed) -> None: ...
    def give_back(self, src_fp: str, action: Action) -> None: ...   # プールの障害隔離
    def drop_screen(self, src_fp: str) -> None: ...                 # 解決できない再生パス
    def finish_discovery(self, dst_fp: str, node, actions) -> None: ...
    def finish(self, reason: str) -> None: ...
    def emit(self) -> None: ...
    def note_failure(self, exc: Exception) -> None: ...
```

`crawl()` は次のようになります。`_Coordinator` を組み立て、（今と同じく単一スレッドで）`_bootstrap` を
一度走らせ、追加ワーカーのスレッドを起動し、主ワーカーを走らせる。各ワーカーは、共有状態へのすべての
接触が調整役のメソッド呼び出しになった、上から下へ読める端末歩行です。結果として、並行処理の不変条件は
クラス一つとして読めるようになり、端末歩行のコードは `with cond:` ブロックを途中に挟まずに上から下へ
読めるようになります。

**厳密に保たなければならない不変条件**（これがレビューのチェックリストであり、既存テストが固定している
点です）:

- ワーカーごとの `errors` カウンタは `_worker` のローカルのままにします。これは共有状態では*なく*、
  `_Coordinator` に移してはいけません。
- `select_next_work` は `while True` ＋ `cond.wait()` のループを保ちます。ワーカーが今いるスクリーンに
  まだフロンティアがあればそこから続け、なければ最も安い項目（既知パスが最短、次に fingerprint）へ
  バックトラックし、それもなく `active == 0` なら終了し、それ以外は待ちます。
- `give_back` は取り出した操作をフロンティアの**先頭**に戻し、`active` を 1 減らします。
- 操作を予約するとき `steps` と `active` をロックの下で一緒に増やすので、2 つのワーカーが同じ操作を
  取り出すことはありません。
- 唯一の確定的な最終 `emit()` は join の後に走り、遅れて入った記録も捉えます。
- `on_event`／`on_node` のコールバックは、今と同じタイミングで発火します。

**検証。** この変更全体は、既存の確定的なテスト群（`test_crawl*`、`fake` driver）でシミュレータなしに
動かされるので、Linux 上の `make check` だけで完全に検証できます。この切れ目はスケジューラを新たに
露出させるので、本リファクタリングは `_Coordinator` のインスタンスに対して `select_next_work`／`_claim`
を直接単体テストすることを*可能にします*（ただし必須ではありません）。これは後続の利点であり、挙動を
変えないスライスの一部ではありません。

これは Tier 1 のホットなファイル一つに対する、単一の面をまたぐ変更なので、ワーキングアグリーメントに
従い、無関係な作業に混ぜずに**一つの焦点を絞った PR** として、前もって告知した上で出します。

## 検討した代替案

- **現状のまま残す。** この関数は動作しており、コメントも手厚く付いています。しかし、ここはコードベースで
  並行処理の正しさに関する推論が最も濃く集まる箇所であり、今後のクロールの変更はすべて、散らばった
  コメントからロックの契約を再導出するコストを払います。本リファクタリングは、微妙な回帰が最も捕まえ
  にくいこの部分への、安価な保険です。
- **状態を明示的に渡す自由関数に分割する。** `(cond, path_to, pending, …)` をモジュールレベルの関数へ
  渡す形はクロージャを取り除きますが、暗黙性は取り除きません。8 個の状態の組は依然として名前も境界も
  なく、呼び出し側がうるさくなります。クラスは境界に名前を与え、ロックを隠します。それこそが狙いです。
- **スケジューラをアクター／キューで全面的に再設計する。** `Condition` ベースのフロンティアを作業キューで
  置き換えるのは、リファクタリングではなく挙動*変更*であり、クロールが依拠する確定性（最安項目への
  バックトラック順、正確な停止理由）を危険にさらします。これはスコープ外です。本提案は既存のアルゴリズムを
  厳密にそのまま保ち、その状態の置き場所だけを移します。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- [`bajutsu/crawl.py`](../../bajutsu/crawl.py) — `crawl()` とそのネストしたクロージャ。
- [BE-0064 — 複数シミュレータにまたがる並行クロール](../BE-0064-parallel-crawl/BE-0064-parallel-crawl-ja.md) — ここで再編するマルチワーカーのフロンティアを導入。
- [BE-0077 — 複数ブラウザにまたがる並行 Web クロール](../BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl-ja.md) — スレッド内ワーカーファクトリと `recover` を導入。
- [BE-0083 — codegen エミッタを共有のシナリオ走査の背後に統合する](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md) — これと同種の、挙動を変えない内部構造リファクタリング。
- [BE-0067 — コード品質ゲートの強化](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md) — 本項目と区別される、コントリビューターワークフロー側の対比先。
