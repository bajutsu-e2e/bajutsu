[English](BE-XXXX-job-scoped-binary-override.md) · **日本語**

# BE-XXXX — アクティブな設定バインディングとは独立した、ジョブ単位のbinaryアーティファクト上書き

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-job-scoped-binary-override-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | config の取得元 |
| 関連 | [BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory-ja.md)、[BE-0413](../BE-0413-worker-app-binary-delivery/BE-0413-worker-app-binary-delivery-ja.md)、[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)、[BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads-ja.md)、[BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md) |
<!-- /BE-METADATA -->

## はじめに

ホスティングされた`bajutsu serve`は、各ジョブを[ターゲット](../../docs/ja/glossary.md#target-app-device)の
`appPath`（設定が名指すビルド済みアプリのbinary）に対して実行します。現状、orgのserveがまだ持っていない
binaryをジョブに使わせる唯一の方法は、orgの**アクティブな設定**を差し替えることです。差し替えは、
ジョブ単位の選択ではありません。セッションを持たない呼び出し元（共有トークンやCIからのリクエスト）による
差し替えは、デプロイの**フォールバック**バインディングを置き換えます。これは、セッションを持たない
すべての呼び出し元が次に読む、唯一のレコードです。差し替えは、orgの**記憶された設定**
（[BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory-ja.md)）も書き込むため、
同僚の次のセッションが最初の利用時にそれを引き継ぎます。その後、
[BE-0413](../BE-0413-worker-app-binary-delivery/BE-0413-worker-app-binary-delivery-ja.md)が、バインド先の
ツリーのbinaryを、ジョブをリースしたworkerへ配送します。

本項目は、**ジョブ単位のbinaryアーティファクト上書き**を追加します。`run`、またはクラウドバッチのファンアウト
である`run-set`へのリクエストが、すでに保存済みの`binary`種別のアーティファクトをsha256で名指します。
すると、そのリクエストがdispatchするすべてのジョブが、それだけを解決の対象とします。orgのアクティブな
設定や、それに対して動く他のジョブ・セッションは変わりません。

## 動機

継続的インテグレーション（CI）は、自分が起動する各runに対して「scenariosとconfigは同じまま、今回の
ビルドのbinaryだけ」を求めるのが普通です。今日この目的に使える経路は、
[`POST /api/compose`](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)
と、レガシーな単一zipの`POST /api/upload`の2つです。どちらも、dispatchのたびにorgのアクティブな設定を
差し替えます。この差し替えは、1つではなく2つの別々の問題を引き起こします。

1つ目は、すべてのCIリクエストがセッションを持たないことです（ログイン用のクッキーを一切運びません）。
そのため、すべてのCIによる差し替えは、同じ**デプロイのフォールバック**バインディング
（[BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory-ja.md)）を置き換えます。
1つのデプロイに対する2つの並行したCIのdispatchは、この1つのレコードを取り合います。
`_register_and_dispatch`は、登録の時点でジョブの`cwd`と`bundle`を確定させます。そのため、損失が生じるのは
呼び出し元自身の差し替えとその`run`との間の窓です。そこへもう一方の呼び出し元の差し替えが割り込むと、
runはそちらを解決の対象にしてしまいます。どちらの呼び出し元も、相手のジョブに触れるつもりはなかったにも
かかわらずです。

2つ目は、CIによる差し替えがCIの呼び出し元だけにとどまらないことです。同じ差し替えは、orgの
**記憶された設定**（BE-0393単位6）も書き込むため、同僚の次のセッションが最初の利用時にそれを
引き継ぎます。あるCI runで作られたビルドは、誰も改めて差し替えなくても、人間のWeb UIセッションが
開くbinaryになり得るということです。

この2つの下には、3つ目のギャップがあります。
[BE-0413](../BE-0413-worker-app-binary-delivery/BE-0413-worker-app-binary-delivery-ja.md)は、
リースされたジョブのバインディングが
[`Upload`](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)である
場合に限って、binaryをリモートworkerへ配送します（すべての`start_*`が合流する唯一の末尾である
`_register_and_dispatch`が、`Job.bundle`を刻む前に行う`binding.upload is not None`という判定です）。
Git由来の設定や、scenarioがサーバー側に保存された
`materials`として運ばれるローカル設定からdispatchされたジョブは、`bundle`を一切持ちません。そのリモート
workerは、configとscenarioのテキストを新規のワークスペースへ書き込みます
（`bajutsu/serve/server/worker_job.py`の`_materialize`）が、そこにbinaryを置く手段がありません。
アプリはそのworkerのディスク上にあらかじめ存在しているか、configの`build:`コマンドが取得してくる
必要があります。`build:`はシェルコマンドであり、アップロードされたbundleのサーバーコマンドにすでに
与えられているサンドボックス化
（[BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)）
の外にあります。コントロールプレーンがコンテンツアドレス方式のアーティファクトとしてすでに持っている
binaryを、決定論的かつ認証情報不要な経路でそのworkerに届ける手段は、今日は存在しません。

3つのギャップはいずれも、同じ設計上の選択に行き着きます。binaryの配送が「どの設定がバインドされているか」に
絡め取られているのに対して、呼び出し元が実際に変えたい単位はジョブだからです。両者を切り離せば、CIの
runと人間のWeb UIセッションが同じorgを共有しても互いを踏まずに済み、`materials`ベースのジョブも、
アップロードされたbundleのジョブがすでに受けているのと同じ方法でbinaryを受け取れるようになります。

**検証可能な成果。** セッションを持たない2つの`run`ジョブを、API経由で同じデプロイに対して同時に
dispatchします。それぞれ、呼び出し元が少し前にアップロードした、別々の`binary`アーティファクトのsha256を
名指します。両方のジョブが完了し、それぞれ自分のbinaryをインストールします。各runのマニフェストには、
それぞれが上書きしたsha256が記録されます。デプロイのフォールバックバインディングは変わりません。
セッションを持たない`GET /api/config`は、両方のジョブが戻った前後で同じ値を返します。

これは今日は失敗します。ジョブ単位の経路がないため、どちらの呼び出し元も、まずこの1つの共有フォール
バックを差し替える必要があります。そのため、2つ目の差し替えが、1つ目の呼び出し元のジョブが解決する
対象を変えてしまいます。

## 詳細設計

作業はMECE（漏れなく重複なく）に3つの単位へ分かれます。ジョブ単位でスタンドアローンなアーティファクトを
名指す部分、それをジョブの実行先へ届ける部分、そしてその経路を固定するテストです。

### 単位1 — アクティブなバインディングに触れない、ジョブ単位のアーティファクト参照

[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)は、
`binary`アーティファクトを単体でアップロードし、そのsha256でコンテンツアドレス方式のまま保存すること
（何のアクティブな設定にもバインドせずに、`bind_artifact`が書き込んで終える処理）をすでに可能にして
います。本項目は、この保存先へのジョブ単位の参照を追加します。`start_run`と`start_run_set`が、任意項目
`binaryArtifact`を受け付けます。これは、呼び出し元のorgにすでに保存されている
`binary`アーティファクトを名指すsha256の16進ダイジェストです。`valid_sha256`がその形式を検証します。
dispatch側のゲートは、`artifact_exists`をそのままでは再利用できません。このヘルパーは、ストアの
エラーを意図的に「存在が確認できない」として扱い、本物のミスと
同じ`200 {"exists": false}`を返すからです。そのまま再利用すると、一時的なエラーが確認済みのミスとして
読み替えられ、本項目が禁じる400にそのままなってしまいます。そこで本項目は、`artifact_exists`の背後に、存在・確認済みの不在・未確認という3状態判定を切り出します。
`artifact_exists`自体は、`GET /api/artifacts/exists`をそのまま扱う薄いhandlerとして残り、今の2状態へ
絞り込みます。dispatchゲートは、この3状態の結果をそのまま読み取ります。確認済みの不在であれば、ジョブを
登録する前に400で拒否します。workerが後から取得しようとして初めて失敗が判明する、不透明なジョブ失敗には
しません。存在が未確認であれば、再試行可能な503を返します。アーティファクトが一度もアップロードされて
いないと断定する400には、決してなりません。`GET /api/artifacts/exists`自体は、今の2状態の契約
（確認済みのミスと未確認のどちらにも`200 {"exists": false}`を返す契約）を保ちます。既存の重複排除の
呼び出し元は、どちらも「もう一度アップロードすればよい」と読んで安全なためです。そのため、クライアント
側の挙動は変わりません。

この参照は、`Job.bundle`とは独立した新しい`Job`のフィールドとして運ばれます。`Job.bundle`は、
BE-0413が作った「`Upload`バインディングが解決するツリー」のままであり続けます。したがって
`state.binding_for`、`bind_upload_config`、`remember_org_config_source`のいずれも変わりません。
異なるアーティファクトを名指す2つのジョブや、orgのバインド済み設定はそのままで1つのジョブだけが
アーティファクトを名指す場合も、同じレコードを取り合うことはありません。

アーティファクトの保存キー（`bajutsu/serve/upload_artifacts.py`の`artifact_store_key`、orgの
`uploads/binary/`プレフィックス配下）は、デプロイのオブジェクトストアのプレフィックス・org・
アーティファクトの種別・sha256だけから決まる純粋な関数です。本項目は、このキー構成を実装の詳細では
なく安定した契約として扱います。自前のバケットアクセス権を持つCIランナーを例に取ります。デプロイの
プレフィックス・自分のorg・`binary`という種別・自分が計算したsha256の4つすべてを知っていれば
（sha256だけでは足りません）、そのキーへ直接バイトを書き込めます。`POST /api/artifacts/binary`は
丸ごと省略できます。dispatch APIが必要とするのは結果としてできあがるsha256だけであり、そのバイトを
どう届けたかは問いません。`GET /api/artifacts/exists`は、どちらの経路で書き込まれたsha256であっても
「このsha256はすでに保存済みか」に同じように答えます。そのため、自前の認証情報とこの4つの入力すべてを
持つ呼び出し元も、APIを経由した場合と同じアップロード省略を得られます。

本項目が供給しない前提条件が1つあります。継続的インテグレーションのジョブが提示できる資格情報です。
バイトを書き込むことについては、serveの資格情報を必要としない答えがあります。上で述べたキーへの直接
書き込みです。しかし、dispatchでsha256を名指すことについては、そうはいきません。`POST /api/run`は
editorロールを、`POST /api/artifacts/binary`はadminロールを要求します。そしてGitHub OAuthを設定した
デプロイでは、共有トークンがworkerのトラフィックだけに狭まるため、素のbearerトークンはどちらにも
届きません（`docs/self-hosting.md`）。そうしたデプロイには、今日CIのジョブへ差し出せる機械の身元が
ありません。本項目は、そのデプロイが`POST /api/run`に対してすでに信頼している資格情報（トークン認証の
デプロイであれば共有トークン）をそのまま前提とし、そのリクエストにフィールドを1つ追加します。OAuthの
デプロイでCIのジョブに固有の身元を与えることは、認証についての別の意思決定であり、独自の脅威モデルを
伴います。配送の仕組みに便乗させるのではなく、それ自身の項目に委ねます。

### 単位2 — ジョブの実行先がどこであれ、上書きを届ける

リース時に、`worker_lease`は`bundle_urls`（BE-0413）や`baseline_urls`（BE-0160）にすでに行っている
のと同じ方法で、この上書き用のpresigned GETに署名します。リースは、`binary_url`という名前でもう1つキーを返します。ジョブが運ぶsha256は、ストレージキーへ変換する
前に、完全な16進ダイジェストとして再検証されます。そのキーは、リースされたジョブ自身のorgへスコープを
限ります。workerが値を持ち込むことはありません。`binaryArtifact`を持つジョブに対して、
リースが`binary_url`を一切署名しない場合（オブジェクトストアが設定されていない場合）、workerはただちに
ジョブを失敗させます。これは、bundleのリースがurlを一切署名しなかった場合にBE-0413がすでに取っている
姿勢と同じです（`bajutsu/serve/cli/worker.py`の「job needs bundle …, but the lease signed no url for
it」）。こうしてworkerは、インストール時になって初めて判明する、不透明なジョブ失敗にはしません。
workerはBE-0413のストリーミングダウンロード
兼検証の経路を再利用してそれを取得・ハッシュ検証したうえで、構成済みの`binary`レッグに対してすでに
行っているのと同じ方法でバイトを配置します。`appPath`は、ジョブ自身のconfigから解決します。ただし、
ジョブが名指す1つのターゲットに限られ、configが宣言するすべてのターゲットには及びません。
`materialize_composition`の配置ループ（`_place_scenarios_and_binaries_and_check_coherence`）は、web以外の
すべてのターゲットの`appPath`にbinaryを1つずつ書き込むからです。この解決はプラットフォームを問わないため、
Androidターゲットの`appPath`もiOSターゲットと同じように扱われ、iOSに限られません。バイト列は
`_place_binary`の「unzipするか、コピーするか」という分岐を通ります。この分岐は、`validate_bundle_config`が
構成済みツリーに与えるのと同じBE-0051の経路閉じ込めのもとで動きます。この書き込みは、`Upload`のツリーやGit
チェックアウトが
本来そこに置くはずだったものを上書きします。そして、`bundle`を一切持たない`materials`ベースのジョブが、
ディスク上にまだ持っていないbinaryを受け取る経路として初めて機能します。

ここまでが、テスト対象のアプリについての話です。iOSでは、runがインストールする成果物はアプリだけでは
ありません。XCUITestはrunnerも必要とします。上書きはrunnerを運びませんが、多くの場合それは不要です。
runnerはアプリに依存しないためです。1つのビルド済み`.xctestrun`とその成果物が、runの対象がどのアプリで
あっても駆動します（[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)）。つまりビルドが変わってもrunnerは変わりません。`xcuitest.testRunner`を
指定しないSimulatorターゲットは、Bajutsuのwheelに同梱されたrunnerへ解決されます（[BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner-ja.md)）。wheelを
インストールしたworkerはすでにそれを持っているため、runnerに関するものはジョブごとには一切運ばれません。
`xcuitest.testRunner`を指定するターゲットでは、そのパスが自身のconfigのディレクトリを基準にrebaseされ、
BE-0051によって閉じ込められます。アップロードされたbundleが自分のrunnerをツリー内に載せられるのは、この
仕組みによります。そのツリーはBE-0413がすでに配送します。

1つだけ、本項目では閉じない case が残ります。実機ターゲット（`xcuitest.deviceType: device`）は、Bajutsu
が同梱できない署名済みrunnerを指定しなければなりません（[BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build-ja.md)）。そのため、実機に対する`materials`
ベースのジョブには、そのrunnerの配送経路が依然としてありません。本項目がapp binaryについて閉じるのと
同じギャップです。runnerはビルドではなくデプロイに属するため、ジョブ単位の上書きは形として適していま
せん。署名済みrunnerの配送はそれ自体が別の問題であり、その規模を見積もれる項目に委ねます。

workerのワークスペースは、ジョブより長く生き続けます。`work`はworkerの生涯全体を通じて1つのディレクトリ
のままです。そのため、`appPath`への上書きの配置だけを行い、今日のワークスペースキーを変えずにおくと、
同じworker上で次にリースされるジョブへそれが持ち越されてしまいます。上書きを持たないジョブや、同じ
bundleを使うジョブは、それと知らされないまま、その持ち越されたbinaryに対して動き始めることになります。
そこで本項目は、上書き自体をワークスペースキーの一部にします：

| ジョブ | ワークスペース |
|---|---|
| bundleジョブ、上書きなし | `.bundles/<org>/<bundle id>`（今日と同じキーのまま、変わりません） |
| bundleジョブ、上書きXあり | `(bundle id, 上書きのsha)`でキー付けされた、専用のツリー |
| materialsベースのジョブ、上書きXあり | workerの共有作業ディレクトリの代わりに、`(materials identity, 上書きのsha)`でキー付けされたディレクトリ |
| materialsベースのジョブ、上書きなし | workerの共有作業ディレクトリ（今日と同じまま、変わりません） |

上書きを名指さないジョブは、今日のキーをそのまま保ちます。そのため、既存の挙動は何も変わりません。
上書きを持つジョブだけが、専用のツリーを得ます。これにより、配置は構造的にジョブ単位になり、workerが
削除や再配置の手順を正しく行う必要はなくなります。これは、binaryの同一性はその中身のダイジェストで
あり、異なる2つのbinaryは異なる2つのツリーになるという、BE-0413自身の原則に従うものです。また、
BE-0413のディスクコストも引き継ぎます。`docs/self-hosting.md`は、bundleキャッシュを刈り込むことで
それを管理するよう、運用者にすでに案内しています。

シングルプロセスのローカル`serve`は、別のトポロジーです。そこでは、serve自身がジョブを実行し、運用者
自身のプロジェクトディレクトリで動きます。`_register_and_dispatch`は、どのトポロジーでも
バインディングから`job.cwd`を確定させます。そのため、判別の基準はバインディングの種類ではなく、
executor（`LocalExecutor`か、ホスティングされたデプロイのBE-0106の`DbQueueExecutor`か）です。そこで
本項目は、`LocalExecutor`のデプロイでは`binaryArtifact`を明確なエラーとともに**拒否**します。
`appPath`にある運用者のビルド出力を上書きすることはありません。

serveは、そこに配置を隔離するためのワークスペースを持ちません。そのため、workerでは配置をワークスペース
キーが解決している汚染の問題に、ローカルでは同等の答えがありません。運用者のプロジェクトディレクトリを
黙って書き換えることは、原則2が禁じる、予告のない副作用そのものです。シングルプロセスの`serve`を使う
運用者は、ファイルシステムへ直接アクセスできます。この項目がなくても、`appPath`を好きな場所へ向けられ
ます。

これにより、本項目は、それが解決しようとしている問題が生じるトポロジーだけにスコープを保ちます。worker
がジョブを実行し、serveがワークスペースを持つ、ホスティングされた分離構成です。

`run-set`によるファンアウト（[BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md)）は、
3つ目の分岐です。`start_run_set`は、`appPath`をserveのプロセス自身の上で解決済みです。それを
`BatchRequest.app_path`としてクラウドバッチのproviderに渡します。ジョブが`binaryArtifact`を持つ場合、
そのパスは代わりに、serve自身のコンテンツアドレス方式のアーティファクトキャッシュ内にある上書きの場所へ
解決されます。そこは、`bind_artifact`がすでに`local_artifact_dir`へ書き込んだ場所です。providerはそこから
プロセス内で読み取ります。configの`appPath`には何も配置されないため、この分岐はどのツリーも書き換えません。これは、
上記の`LocalExecutor`による拒否がこの分岐には当てはまらない理由でもあります。ジョブが`BatchRequest`を
持つかどうかは、そのexecutorに基づく拒否より前に決まり、後には決まりません。

取得の404/410は、BE-0413が扱う「bundle不在」と同じ方法でジョブを終了させます。本項目は、ハッシュ
不一致もBE-0413と同じく一時的な扱いに分類します。そのためリースが失効し、次の試行で成功できます。
切り詰められたダウンロード1回で、恒久的な失敗ジョブにはなりません。いずれの場合も、誤ったbinaryの
ままrunが始まることはありません。`appPath`が本来解決していたはずのものへ、静かにフォールバックする
こともありません。runのマニフェストには、上書きしたsha256を、BE-0073がすでに持つbundleプロベナンス
と並べて記録します。どのbinaryを実際にインストールしたジョブだったかを、後から答えられる状態に保つ
ためです。

### 単位3 — テストとドキュメント

ゲートは、ネットワークとSimulatorのどちらも使わずに各接続点をカバーします。

- `start_run`/`start_run_set`は、ジョブを登録する前に、orgのアーティファクトストアが
  保持していない`binaryArtifact`を拒否します。
- `binaryArtifact`の存在確認でdispatchゲートに一時的なストアのエラーが起きた場合は、アーティファクトが
  一度もアップロードされていないと断定する400ではなく503を返します。
- `worker_lease`は、`binaryArtifact`を持つジョブに対して、リースされたジョブのorgに限定した
  `binary_url`に署名します。
- `binaryArtifact`を持つジョブでリースが`binary_url`を一切署名しなかった場合は、bundleジョブでurlが
  署名されなかった場合と同じように、ジョブはただちに失敗します。
- `bundle`を持たない`materials`ベースのジョブが、取得したアーティファクトをconfigの`appPath`へ配置して
  実行します。
- Androidターゲットの上書きは、iOSターゲットと同じ接続点を使って、自分自身の`appPath`に配置されます。
- iOSターゲットとAndroidターゲットの両方を宣言するconfigに対する上書きは、ジョブ自身のターゲットの
  `appPath`にだけ配置され、もう一方のターゲットの`appPath`は変わりません。
- `.app`のzipバンドルアーティファクトは、zipファイルのまま配置されるのではなく、ディレクトリへ展開
  されます。
- バインド済みの`Upload`と`binaryArtifact`の両方を持つジョブは、バインドされたツリー自身のbinaryでは
  なく、上書きをインストールします。
- 異なるアーティファクトを名指す2つの並行ジョブは、それぞれ自分のものをインストールし、どちらもorgの
  アクティブな設定バインディングを変えません。
- 同じ上書きを名指す2つのmaterialsベースのジョブは、それぞれのconfigが異なる`appPath`を名指す場合、
  別々のワークスペースを得ます。
- `binaryArtifact`を持たないジョブが、それを持っていたジョブの後に同じworker上でリースされることが
  あります。この場合も、持ち越された上書きではなく自分自身の`appPath`のbinaryに対して実行されます。
  これは、既存の「並行する2つのジョブ」の箇条書きではカバーされない、1つのworker上での逐次的な失敗です。
- 上書き取得時の404はジョブを失敗させます。この場合、インストール済みのbinaryは残りません。一方で
  ハッシュ不一致は、ジョブを失敗させることなくリースだけ失効させ、再試行に委ねます。
- `LocalExecutor`のデプロイでdispatchされたジョブ（運用者自身がバインドしたツリーで動くジョブ）の
  `binaryArtifact`は拒否され、運用者の`appPath`のbinaryはそのまま残ります。
- 上書きを伴う`run-set`のファンアウトは、configから導かれるパスではなく、アーティファクトキャッシュの
  パスをproviderに渡し、configの`appPath`は変わりません。

`docs/self-hosting.md`、`docs/cli.md`、およびそれぞれの日本語ミラーに、`binaryArtifact`フィールドと、
それに対してジョブのマニフェストが何を記録するかについての段落を追加します。

## 検討した代替案

| 代替案 | 採用しなかった理由 |
|---|---|
| binaryを生のオブジェクトストレージパス（`prefix/org/<path>`）で名指し、場所だけを根拠に信頼する | コンテンツアドレス方式を手放します。そのパスにあるオブジェクトは後から差し替わり得るため、runのマニフェストはどのバイト列をインストールしたのかを言えなくなります。また、BE-0413のsha再検証やBE-0051の閉じ込めがすでに閉じているパス検証の面を作り直すことになり、sha256による参照が与えない能力を何も追加しません。 |
| ジョブ単位の上書きを`config`と`scenarios`にも広げる | 動機となっているギャップはbinary固有です。CIはrunのたびにbinaryを変えます。その一方で、configとscenariosは変えません。そのため、バインディングを変えずに済む対象は、binaryだけです。ここでスコープを広げると、この項目の動機が示していない需要のために、dispatch層でcompose時のピッカーを作り直すことになります。 |
| 今日の2つの経路と同じように、すべてのdispatchの前に差し替え（`bind`/`compose`）を要求する | orgのアクティブな設定を変更の単位にしてしまいます。すべてのセッションを持たないCIの呼び出し元は、自分のジョブが求めたbinaryを得る代わりに、1つのデプロイのフォールバックバインディングを取り合うことになります。加えて、この差し替えはorgの記憶された設定も書き込むため、あるメンバーの次のセッションがそれを引き継ぎます。 |
| アーティファクト用のpresigned PUTアップロードエンドポイントを、唯一サポートする転送手段として追加する | 動機となっているギャップを閉じるためには不要です。`POST /api/artifacts/binary`と`GET /api/artifacts/exists`は、すでに呼び出し元の重複排除とアップロードを今日可能にしています。presigned PUT版は、大きなbinaryについてコントロールプレーン自身のディスクを経由する往復を1回省けますが、それは後続の最適化であり、この項目を妨げるものではありません。この項目がdispatch時に交わす契約は、結果としてのsha256であって、それがどう届いたかではありません。 |
| 上書きを一切持たないすべてのジョブについて、bundle/Gitツリーから`appPath`を再配置する（または持ち越しを削除する） | materialsベースのジョブには、再配置の元になるソースツリーがありません。そのため、この経路には削除の手順とトポロジーごとの分岐が必要になりますが、ワークスペースをキー付けする方法なら分離が構造的になり、どちらも必要ありません。 |
| XCUITestのrunnerを、binaryと並ぶ2つ目の上書きレッグとして運ぶ | runnerはアプリに依存せず、ビルドが変わっても変わりません（[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)）。つまりrunnerはジョブではなくデプロイに属します。Simulatorターゲットはwheel同梱のrunnerへすでに解決され（[BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner-ja.md)）、アップロードされたbundleは指定済みの`xcuitest.testRunner`を自分のツリー内に載せられます。本当に欠けているのは、実機かつmaterialsベースのジョブ向けの署名済みrunner（[BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build-ja.md)）ですが、これはデプロイ単位の配送の問題であり、ジョブ単位の上書きでは解決しません。 |
| `record`と`crawl`にも上書きを提供する | 動機は、すべてが判定に向かうCIのdispatchであり、`run`とその`run-set`バッチファンアウトが応えるものです。2つのTier-1オーサリング経路のどちらも、根拠になりません。`record`は自然言語のゴールに向けてAIとともに探索し、scenarioを書きます。`crawl`は幅優先で探索し、画面マップを書きますが、`docs/cli.md`はこれが決して合否判定のゲートではないと述べています。`record`の出力は、そのジョブより長く残ることも理由です。オーサリングされたscenarioはorgのscenarioストアへ永続化されます（`Job.record_save`、`out_path`）。一時的なジョブ単位のbinaryに対してオーサリングされた永続的なアーティファクトは、どのbinaryがそれを形作ったかの記録を持ちません。一方、`run`のマニフェストは上書きしたsha256を刻みます。この経路は、後の項目がそれ自身の根拠とともに追加できます。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 単位1 — `binaryArtifact`リクエストフィールド。検証と存在確認を行い、`Job.bundle`とは独立に
      `Job`へ運びます。
- [ ] 単位2 — 本項目は、workerのトポロジーで上書きに署名・配送します。`run-set`のファンアウトでは
      serve自身のアーティファクトキャッシュから解決します。`LocalExecutor`のデプロイでは拒否します。
      runのマニフェストには、プロベナンスを記録します。
- [ ] 単位3 — 各接続点のテストと、`self-hosting`/`cli`ドキュメント。

## 参考

- [BE-0393 — org ごとに config を覚え、セッションごとに復元する](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory-ja.md)
  ——バインドが実際に動かすバインディングがどれかを定義する項目。
- [BE-0413 — アップロードされたアプリのbinaryを、ジョブを実行するworkerへ届ける](../BE-0413-worker-app-binary-delivery/BE-0413-worker-app-binary-delivery-ja.md)
  ——本項目がスタンドアローンなアーティファクトに対して再利用する、presigned GETによる配送とダウンロード検証の経路。
- [BE-0073 — config・シナリオ・アプリバイナリを zip でまとめてアップロードし Web UI から実行する](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)
  ——本項目の上書きしたsha256が加わる、runマニフェストのプロベナンスブロック。
- [BE-0268 — config・scenarios・アプリbinaryを、独立したコンテンツアドレス方式のアーティファクトとしてアップロードする](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)
  ——本項目がバインドせずに参照する、スタンドアローンでコンテンツアドレス方式の`binary`アーティファクト。
- [BE-0325 — 変更されたレッグだけをアップロードして、アクティブな構成を再利用する](../BE-0325-compose-incremental-artifact-upload/BE-0325-compose-incremental-artifact-upload-ja.md)
  ——本項目のジョブ単位の上書きが、非破壊な代替手段として位置づく、compose時の利便性向上。
- [BE-0160 — presigned URLによる、認証情報不要なworkerアップロード](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads-ja.md)
  ——`binary_url`が拡張する、presigned URLの仲介の仕組み。
- [BE-0090 — アップロードされたbundle設定からのコマンド実行の統治とサンドボックス化](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)
  ——本項目がなければ、`materials`ベースのジョブ自身のbinary取得が今日頼ることになる`build:`の統治。
- [BE-0106 — 完了後方式のworkerモデル](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md)
  ——`binary_url`が`bundle_urls`や`baseline_urls`と並んで参加する、リースのプロトコル。
- [BE-0292 — XCUITest ランナーを同梱して testRunner を省略可能にする](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner-ja.md)
  ——Simulatorのrunにrunnerの配送が不要な理由。本項目の上書きがアプリだけを対象にできる根拠。
- [BE-0336 — serve から Device Farm へ投入する、デバイス数を制限したシナリオ単位の分割実行](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md)
  ——`start_run_set`が駆動するクラウドバッチのファンアウト。本項目の上書きが`appPath`への配置ではなく、
  serve自身のアーティファクトキャッシュへ解決する、3つ目の分岐です。
