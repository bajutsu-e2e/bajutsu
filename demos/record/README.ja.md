[English](README.md) · **日本語**

# sample2 アプリに対してシナリオを生成 → 実行 → 改変する

`record` は Bajutsu の著作経路です: **エージェント**が自然言語の*目標*とライブ画面を読み、一度に一つの
アクションを提案し、ループが実行されたステップを決定論的なシナリオとして書き出します。`run` は後でその
シナリオを **AI なし**で再生します（[recording](../../docs/ja/recording.md)、
[concepts](../../docs/ja/concepts.md)）。

このフォルダは、同梱の **`sample2`** アプリ（`demos/record/app/`）に対する全ライフサイクルを実演します:
目標からシナリオを生成し、Simulator で実行し、改変し、決定論的なランナーがどう反応するかを観察します。

## ガイド付きデモ（`demo.sh`）

```bash
make -C demos record                 # または直接:
./demos/record/demo.sh
```

**前提条件**（スクリプトが確認し、足りないものを教えてくれます）:

- 起動中の Simulator（`open -a Simulator`）、
- idb クライアント（`brew install facebook/fb/idb-companion && uv sync --extra idb`）、
- ビルド済みの sample2 アプリ（`make -C demos/record sample2-build`）、
- `ANTHROPIC_API_KEY`（環境変数か gitignore された `.env`）— ステップ1は Claude で著作します。未設定なら、
  起動時に `y/N` で確認し、`y` の場合は伏せ字のプロンプトでキーを受け取り、その実行限りで使います（ディスクには保存しません）。

目標は [`goals.txt`](goals.txt) の最初の非コメント行から取ります（`GOAL="..." ./demo.sh` で上書き可）。
そして [`demo.config.yaml`](demo.config.yaml)（idb バックエンド上の `sample2` アプリ。`appPath` 指定で
アプリが自動インストールされる）を使って、4つのフェーズを辿ります:

1. **著作（AI）** — `bajutsu record` が本物の Tier-1 ループを実行します: **Claude** が、起動中のアプリ上で
   目標とライブ画面（スクリーンショット＋アクセシビリティツリー）を読み、各ステップを提案し、実行された
   ステップを `generated.yaml`（gitignore 済み）として書き出します。オフライン・キー無しで動かすには、
   キーワードの代役 [`generate_from_nl.py`](generate_from_nl.py) が同じ流れを著作します。
2. **実行（Execute）** — 起動中の Simulator で
   `bajutsu run --scenario generated.yaml --app sample2 --config demo.config.yaml`。カウンターの流れは
   PASS します。
3. **改変（Modify）** — 期待するカウント値を誤った値に編集 → 再実行 → 実行は**失敗**（アサーションが
   捕捉）→ 元に戻す → 再び**成功**。これが AI が著作したシナリオを保守する、編集して再実行するループです。
4. **診断（Diagnose）** — セレクタのラベルを変える（`Log in` → `Log In`）ことで、テストの足元でセレクタが
   ずれた状況を模倣 → 再実行 → タップが対象を**解決できず**失敗 → `bajutsu triage` が失敗した実行を読み、
   **診断**します（カテゴリ＋捕捉した要素ツリーからの likely fix）。triage は助言にとどまり、失敗を説明
   しますが、合否を判定することはありません。その後セレクタを戻し、シナリオは再び**緑（green）**で実行
   されます。

生成されるシナリオは、アプリのオンボーディング → ログイン → home → カウンターの流れに従い、実機での
ログイン画面遷移を乗り切るための `wait for Home` を含みます。

## 生成だけを単独で（オフライン、Simulator 不要）

[`generate_from_nl.py`](generate_from_nl.py) は単独で動きます — 目標を反復するのに便利です:

```bash
uv run python demos/record/generate_from_nl.py                       # 既定の目標
uv run python demos/record/generate_from_nl.py "tap Increment, then check the counter shows 1"
uv run python demos/record/generate_from_nl.py --file demos/record/goals.txt   # 一括
uv run python demos/record/generate_from_nl.py "<goal>" --out demos/record/generated.yaml
```

これは (1) タップがオンボーディング → ログイン → home と進むメモリ上の `FakeDriver` を動かし（その id は
実際の sample2 アプリと一致します）、(2) 本物の `record()` ループで著作し、(3) 結果を再生して妥当性を
証明します。[`goals.txt`](goals.txt) には、すぐ編集できる例が入っています。

## ここでのエージェント vs. 本番

本番のエージェントは [`bajutsu.claude_agent.ClaudeAgent`](../../bajutsu/claude_agent.py) です: Claude が
目標、スクリーンショット、アクセシビリティツリーを読み、各ステップを提案します。API キーとライブアプリが
必要で、その出力は自然にばらつきます。

このデモを再現可能に保つため、スクリプトは決定論的な代役 `KeywordAgent` を注入します。これは*同じ*目標を
いくつかのキーワード規則で解析し、各アクションを可視要素にひも付けます。record ループと、それが使う
`Observation → Proposal` プロトコルは本物です（[`bajutsu/agent.py`](../../bajutsu/agent.py)、
[`bajutsu/record.py`](../../bajutsu/record.py)）— 「頭脳」だけを決定論的なものに差し替えています。この
代役は小さな文法を理解します（節はカンマ／`then` で分割）:

| 表現 | 変換結果 |
|---|---|
| `tap`/`press`/`click`/`open` *X*（任意で `twice` / `N times` / `thrice`） | *X* に一致する要素への1つ以上の `tap` ステップ |
| `log in with email E and password P` | email 欄に `type E`、password 欄に `type P`、続けて **Log In** をタップ |
| `wait for X`（任意で `up to Ns`） | *X* に一致する要素への `wait` |
| `check`/`verify`/`confirm` *X* `shows`/`is` *V* | *X* の値が *V* と等しいという `expect` |

対象（*X*）は、ヒントを各可視要素の `id`（次にラベル）と照合してひも付けます。画面に無いものを指す目標は
明確なエラーを送出します — エージェントが存在しない要素を求めたときに `record` が示すのと同じ失敗形態です。

## ライブの Claude 著作

決定論的な代役の代わりに、起動中の sample2 アプリに対して本物の Claude で著作するには（`ANTHROPIC_API_KEY`
が必要）:

```bash
uv run bajutsu record --out demos/record/generated.yaml --app sample2 \
  --config demos/record/demo.config.yaml --backend idb \
  --goal "increment the counter twice and check it reads 2"
```

そして `demo.sh` が使うのと同じ `bajutsu run` コマンドで実行します。[cli `record`](../../docs/ja/cli.md)、
[recording](../../docs/ja/recording.md) を参照してください。
