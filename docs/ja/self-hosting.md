[English](../self-hosting.md) · **日本語**

# 単一 Mac での Web UI セルフホスティング

> `bajutsu serve`（[cli](cli.md#serve)）を、自前の Mac 上でトークン認証付きの常駐サービスとして動かし、
> プライベートな Tailscale ネットワーク越しにチームから到達できるようにします。これはセルフホスティング
> ロードマップ（[BE-0016](../../roadmaps/proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)）の
> **Tier A** —— stdlib サーバで**今日動く**構成です。
> [BE-0051](../../roadmaps/proposals/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
> が公開を安全にする認証と入力検証を追加したことで成立します。マルチテナントなクラウド構成（コントロール
> プレーン + Mac ワーカープール）は別の将来の段階です
> （[BE-0015](../../roadmaps/proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)）。

## macOS の制約

ランナーは **iOS Simulator** を駆動し、Simulator は **GUI ログインセッション**（Aqua セッション）を必要と
します —— ヘッドレスな daemon では動きません。以下の選択はすべてここから来ます:

- serve は per-user の **`LaunchAgent`**（GUI セッション）として動かす。**`LaunchDaemon` ではない**。
- 再起動後に GUI セッションを回復するよう Mac を **auto-login** にする（FileVault はコールドブート後、
  auto-login が進む前に一度だけ対話ログインが必要）。
- セッションを生かし続けるためスリープを無効化: `sudo pmset -a sleep 0 disablesleep 1`。

## 1. LaunchAgent を生成する

`bajutsu serve --emit-launchagent` は、渡した serve フラグに対応する launchd plist を出力して、サーバを
起動せずに終了します。強いトークンを選び、plist を LaunchAgents に書き出します:

```bash
export TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
bajutsu serve --emit-launchagent --config bajutsu.config.yaml --token "$TOKEN" \
  > ~/Library/LaunchAgents/com.bajutsu.serve.plist
chmod 600 ~/Library/LaunchAgents/com.bajutsu.serve.plist   # plist はトークンを含む
```

出力される plist は:

- `python -m bajutsu serve --host 127.0.0.1 --port 8765 --config …` を実行（コマンドを動かしたのと同じ
  インタプリタ＝あなたの venv を使う）し、**`RunAtLoad`** + **`KeepAlive`** 付き;
- トークンを **`EnvironmentVariables`**（`BAJUTSU_SERVE_TOKEN`）に入れる —— argv には載せないので `ps` から
  見えません;
- stdout/stderr を `~/Library/Logs/bajutsu-serve.{out,err}.log` に書きます。

AI パス（`record`、`--dismiss-alerts`）を使うなら、plist の `EnvironmentVariables` に `ANTHROPIC_API_KEY`
を追記してください（自動では埋め込みません）。バインドは `127.0.0.1` のままで、到達可能にするのは次の手順です。

## 2. ロードする

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bajutsu.serve.plist
launchctl print gui/$(id -u)/com.bajutsu.serve        # ロード確認
```

plist 編集後の再読み込みは `launchctl bootout gui/$(id -u)/com.bajutsu.serve` の後に再度 bootstrap。

## 3. Tailscale で公開する（推奨）

serve は `127.0.0.1` のまま、**Tailscale** が tailnet 内にだけ公開します —— identity ベースのアクセスと
自動 TLS で、公開面はありません:

```bash
tailscale serve --bg 8765    # → https://<machine>.<tailnet>.ts.net（tailnet 内のみ到達可能）
```

チームはその URL を開き、初回に UI がトークンを尋ねます（以後ブラウザはセッション Cookie を持ちます）。
API クライアントは `Authorization: Bearer $TOKEN` を送ります。

> **`0.0.0.0` をインターネットに公開しないでください。** トークンがあっても、安全な既定はプライベートな
> tailnet です。serve はトークン無しの非 loopback `--host` を拒否しますが、公開バインドは不必要に面を広げます。
> 本当に内部ホスト名が必要なら、serve の前段に **Caddy** を置いて TLS（+ basic auth）にし、オープンな
> インターネットからは隔離してください。

## セキュリティのまとめ（BE-0051）

セルフホストの serve は
[BE-0051](../../roadmaps/proposals/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
のハードニングに依存します: 全リクエストのトークン認証、`/api/run`・`/api/record` をアプリの scenarios dir
に限定し `backend`/`udid` を検証、CSRF Origin チェックとセキュリティヘッダ、run dispatch の同時実行上限。
トークンは秘匿し、Mac は tailnet 上に置き、OS は更新し続けてください。
