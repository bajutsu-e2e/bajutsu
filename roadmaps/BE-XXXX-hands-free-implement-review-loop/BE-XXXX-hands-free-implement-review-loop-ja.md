[English](BE-XXXX-hands-free-implement-review-loop.md) · **日本語**

# BE-XXXX — implement から review まで一気通貫にする自動ループ（implement-be の auto-PR と pr-followup ポーリング）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-hands-free-implement-review-loop-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Development infrastructure (contributor workflow) |
<!-- /BE-METADATA -->

## はじめに

[`implement-be`](../../.claude/skills/implement-be/SKILL.md) スキルを拡張し、実装が決定的なゲートを
通過したあとの一連の後処理をスキル自身が進めるようにします。ドラフトの Pull Request を開き、セッションを
compact して文脈を解放し、その後 [`pr-followup`](../../.claude/skills/pr-followup/SKILL.md) スキルを
一定間隔でループさせ、PR が静かになりゲートが緑になるまで対応を続けます。現在の `implement-be` は「ブランチへ
push したら人間が PR を開く」で止まっており、CI ログの確認、レビューへの返信、コンフリクトの解消といった後続の
一巡は、そのたびに手で起動されています。この項目は、こうした手動の受け渡しを、それが純粋な手間にしかならない
一つのスキル、すなわち出力が常に自己完結しゲートも緑な `implement-be` に限って取り除きます。

これは**スキルと working agreement だけの変更**です。プロダクトコード（`bajutsu/`、`BajutsuKit/`、runner、
driver）には触れません。変更はすべて author と investigation の経路にとどまり、`run` や CI の判定には
関与しません。

## 動機

`implement-be` は、すでに明確な状態で終わります。ブランチが push され、`make check` が緑になり、ロードマップ
項目が `Implemented` に変わった状態です。ここから先に残る作業は、機械的で反復的なものです。

1. 人間がドラフト PR を開く。
2. CI が走る。落ちたら、誰かがログを読み直して修正を push する。
3. レビュアーがコメントする。誰かが答え、修正し、スレッドを解消する。
4. `main` が進む。誰かが rebase してコンフリクトを解消する。

これらはどれも `pr-followup` が自動化する作業そのものですが、その起動は注意を奪う手動ポーリングです。作業者は
CI が終わったか、レビューが届いたかを確認し続けなければなりません。このポーリングは手間であるうえに、長く続く
巨大な文脈を抱えたセッションの使い方としても効率が悪いものです。implement セッションは設計の会話をまるごと
抱えているため、手動の後続ターンはそのたびに高価な文脈を読み直します。

次の二つの変更が、この手間を取り除きます。

- **auto-PR と自動 followup** は、受け渡しを一続きの流れにします。implement → ゲート → ドラフト PR → followup
  ループとつながり、人間が呼ばれるのは*判断*が要るとき、すなわち設計変更を求めるレビューコメントに `pr-followup`
  の既存のエスカレーション規則が当たったときだけです。
- **ループ前の compact** は、この流れのトークンコストを下げます。implement フェーズの文脈（設計のやり取りや
  ファイルの読み込み）は、PR ができてしまえば不要な重荷です。followup ループに入る前に compact しておけば、
  ポーリングの各ターンは implement のトランスクリプト全体ではなく、痩せた文脈に対して走ります。長いセッションの
  トークン経済は、このプロジェクトで継続的に気にかけている点です。

適用範囲は意図して狭く、**`implement-be` に限ります**。BE を作成するスキル
（[`ideation`](../../.claude/skills/ideation/SKILL.md) と
[`propose-and-build`](../../.claude/skills/propose-and-build/SKILL.md) の提案フェーズ）は auto-PR しては
いけません。提案 PR は人間のレビューの分岐点であり、その BE id は人間がマージしたあとに初めて採番されるからです
（[BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)）。この分岐を
明示するため、[`CLAUDE.md`](../../CLAUDE.md) の working agreement も更新します。

## 詳細設計

作業はドキュメントと working agreement の編集であり、実行されるプロダクトコードはありません。以下の単位は
MECE です。

### Unit 1 — implement-be の step 10 でゲート通過後にドラフト PR を自動で開く

現在の「頼まれたときだけ PR」の step を、**auto-PR** の step に置き換えます。step 9 の `make check` が緑に
なりブランチが push されたら、次を行います。

- `gh pr create --draft` でドラフト PR を開きます。タイトルと本文は既存の規約に従う英語です（`[BE-NNNN]`
  接頭辞、テンプレートに沿った十分な本文、`make check` の検証行）。
- 両言語の BE ファイルの `Implementing PR:` 行に実際の番号を入れ、その追従を push します（既存の step 10 の
  要件を、無条件で行うようにしたものです）。
- 「ユーザーが頼まない限り PR を開かない」という但し書きは、**このスキルに限り**取り除きます。`CLAUDE.md` の
  ドラフトで作成する規則と、緑になるまで ready にしない規則はそのまま生きます。PR はドラフトとして作られ、
  ready にするのは人間（またはあとの明示的な step）だけで、CI が赤いあいだに自動で ready になることはありません。

### Unit 2 — ループに入る前にセッションを compact する

PR を開く（Unit 1）とループを始める（Unit 3）のあいだで、スキルはセッションに **compact**（ハーネスの
`/compact`）を指示します。これにより followup のポーリングは、implement のトランスクリプト全体ではなく痩せた
文脈に対して走ります。将来の編集者がこの compact を「最適化」で消してしまわないよう、その理由（トークン経済）を
その場に書き残します。implement フェーズの設計の会話やファイルの読み込みは followup ループには不要で、PR と
そのブランチ、BE ファイルだけが引き継ぐべき状態です。

### Unit 3 — 一定間隔の pr-followup ループと、その停止条件

compact のあと、`implement-be` はセッションに組み込みの **[`/loop` スキル](../../.claude/skills/loop/)**
を `pr-followup` を対象として起動するよう指示します。具体的には、「`/loop /pr-followup #NNN` を実行してください」
とセッションに伝えます。ループの制御は `/loop` スキルが担い、各周回で `pr-followup` を一度呼び、周回のあいだは
`ScheduleWakeup`（`/loop` dynamic mode のハーネスの self-pacing 機構）で待機します。間隔は標準の
キャッシュウィンドウの指針に従い、CI が動いているあいだ（実行の完了を待つあいだ）は短く、人間のレビューを
待つあいだは長くします。

ループが**停止する**のは、次のすべてが成り立つときだけです。

1. **CI が緑**。必須チェックがすべて通っている。
2. **コンフリクトがない**。ブランチが `main` へきれいにマージできる。
3. **レビューの新規コメントがない周回が2回連続**。レビュー面が静かになった状態です（1回の空振りでは足りず、
   2回目で静止を確かめます）。

これらの停止条件が成り立ってループが終わっても、**Draft → Ready への遷移は人間が行います**。ループは PR が
静かになりゲートが緑になったことを報告しますが、`gh pr ready` は自身では呼びません。会話の内容を確認し、
見落とした懸念がないかを判断したうえで準備完了にするのは人間の最終確認です。「hands-free」が指すのは機械的な
後処理（CI 修正、コメントへの返信、rebase）であり、マージへの判断は人間に残します。

さらにループは、`pr-followup` が設計や仕様の変更を要するコメントに当たった瞬間に、**停止して人間へ
エスカレートします**。これは `pr-followup` の既存のエスカレーション規則そのままで、上の停止条件よりも優先します
（設計の判断はループではなく人間のものだからです）。加えて、PR が収束しない場合に無限ループを防ぐ歯止め（周回数
または実時間の上限）を設けます。上限に達したら、ループを続けるのではなく、停止して状態を報告します。

Prime directive の確認として、ここには `run` や CI の判定に LLM を載せる箇所はありません。`pr-followup` の
修正は依然として `make check` と CI が判定し、ループはその決定的なチェックを*予定に組む*のとレビュアーへ答える
だけです。エスカレーション規則により、本当の判断はすべて人間に残ります。

### Unit 4 — 新しい分岐に合わせて CLAUDE.md を整合させる

working agreement の PR 規則を更新し、二つの経路を明示的かつ一貫して述べます。

- **BE 作成の作業**（`ideation` や `propose-and-build` の提案フェーズによる提案 PR）：これまでどおり PR を
  自動作成せず、push して人間を待ちます。その理由（提案は人間の分岐点であり、id はマージ時に採番される）も
  書き添えます。
- **BE 作成以外の実装**（`implement-be`）：Unit 1〜3 のとおり、**ドラフト PR を自動作成し、そのあと
  pr-followup ループを走らせます**。

既存の「Claude Code が作る PR は必ずドラフトで始まる」規則と「赤いあいだは ready にしない」規則は保ち、相互に
参照させます。新しい auto-PR の経路もこれらを引き継ぎます。

### Unit 5 — スキル同士の相互参照に変更を反映する

`implement-be` の References と `pr-followup` スキルの位置づけに、両者が `implement-be` において自動化された
後処理（implement → PR → followup ループ）へと組み合わさる旨の短い注記を加えます。どちらのスキルの読者も、
この流れに気付けるようにします。ほかのスキルの挙動は変わりません。

## 検討した代替案

- **すべてのスキルで auto-PR する（`ideation` や `propose-and-build` を含む）**。却下します。提案 PR は
  意図された人間の分岐点であり、その id は人間がマージしたときに初めて採番されます（BE-0089）。自動で開くと、
  この分岐点が損なわれます。`implement-be` に絞ることで、出力が明確に準備できている場所に自動化を置きます。
- **compact を省いて、そのままループする**。トークン経済の理由で却下します。implement のトランスクリプトは
  大きく、followup には無関係なので、ポーリングの各ターンが高価な不要文脈を読み直すことになります。受け渡しの
  時点で一度 compact するのは、安価で大きな削減です。
- **最初の静かな周回で止める（CI 緑・コンフリクトなし・新規コメント0が1回で停止）**。性急すぎるため却下します。
  1回の空振りは、コメントを書いている最中のレビュアーと競合しえます。2回連続の静かな周回を求めるのが、合意した
  やや保守的な停止です。
- **単一の固定間隔でポーリングする**。却下します。CI 待ちとレビュー待ちは自然な周期が大きく異なります。適応的な
  間隔（CI が動くあいだは短く、レビュー待ちのあいだは長く）はハーネスのキャッシュウィンドウの指針を尊重し、
  無駄な高速ポーリングと反応の鈍さの両方を避けます。
- **スキルではなくプロダクトコードや CI ボットとして作る**。範囲外であり、必要以上に重いため却下します。この
  流れはエージェントの working agreement であり、`pr-followup` はすでに存在します。スキルの中に置けば新しい
  サービスは要らず、あらゆる修正を既存のゲートが判定する形を保てます。

## 進捗

> 作業の進行に合わせて最新に保ちます。チェックリストは *Detailed design* の MECE な作業分解を写したもので
> （作業単位ごとに1つ）、ログは変更内容とその時期を（古い順に）記録し、PR へリンクします。

- [ ] Unit 1 — implement-be の step 10 を、ゲート通過後にドラフト PR を自動で開くよう書き換える。
- [ ] Unit 2 — ループ前の compact step を、そのトークン経済の理由とともに追加する。
- [ ] Unit 3 — 3つの停止条件とエスカレーション、歯止めを備えた一定間隔の pr-followup ループ。
- [ ] Unit 4 — CLAUDE.md の PR 規則を、BE 作成と実装の二経路に分ける。
- [ ] Unit 5 — implement-be と pr-followup の相互参照を更新する。

## 参考

- [`implement-be`](../../.claude/skills/implement-be/SKILL.md) — この項目が拡張するスキル（step 10）。
- [`pr-followup`](../../.claude/skills/pr-followup/SKILL.md) — ループが各周回で呼ぶスキル。
- [`ideation`](../../.claude/skills/ideation/SKILL.md) · [`propose-and-build`](../../.claude/skills/propose-and-build/SKILL.md)
  — auto-PR から明示的に除外する、BE を作成するスキル。
- [`CLAUDE.md`](../../CLAUDE.md) — Unit 4 が PR 規則を更新する working agreement。
- [BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md) — マージ時の
  BE id 採番。提案 PR が人間の分岐点であり続ける理由。
