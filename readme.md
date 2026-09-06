# Discord Gmail チェッカーボット

Discord から Gmail を確認し、届いたワンタイムパスワード（OTP）を自動で抜き出して
表示するボットです。Proxmox 上の LXC など、常時起動のサーバーで動かす想定です。

## 機能

- `/mail` の1コマンドで「最新メールの表示」と「新着メールの待ち受け」を両方行う
- **ワンタイムパスワードの自動抽出** — 本文のどこにコードがあっても拾い出し、先頭に表示
- **HTMLメール対応** — プレーンテキストが無いメールでもHTMLから本文を取り出す
- **待ち受けモード** — `/mail` の後そのまま5分間（既定）新着を見張り、コードが届いた瞬間に投稿
- 全角数字（`４９２８３７`）やスペース区切り（`418 902`）のコードも正規化して認識
- 日付・金額・西暦・URL内の数字はコード候補から除外
- **毎日AM3:00の自動更新** — git の更新をファストフォワードで取り込み、Bot を再起動

## 必要条件

- Python 3.9以上（`asyncio.to_thread` を使用しています）
- discord.py
- python-dotenv

## セットアップ

1. 必要なパッケージをインストール:
```bash
pip install discord.py python-dotenv
```

2. `.env`ファイルを作成し、以下の環境変数を設定:
```
DISCORD_BOT_TOKEN=あなたのDiscordボットトークン
EMAIL_ADDRESS=あなたのGmailアドレス
EMAIL_PASSWORD=Gmailのアプリパスワード
IMAP_SERVER=imap.gmail.com
IMAP_PORT=993
```

### Gmailの設定

1. Gmailアカウントで2段階認証を有効にする
2. アプリパスワードを生成:
   - Googleアカウント設定 → セキュリティ → 2段階認証 → アプリパスワード
   - アプリを選択し、パスワードを生成
   - 生成されたパスワードを`.env`ファイルの`EMAIL_PASSWORD`に設定

### 任意の設定（未設定なら既定値が使われます）

| 変数 | 既定値 | 説明 |
| --- | --- | --- |
| `OTP_WATCH_SECONDS` | `300` | `/mail` のあと待ち受ける秒数 |
| `OTP_POLL_INTERVAL` | `10` | 新着を確認する間隔（秒） |
| `MAIL_DEFAULT_COUNT` | `3` | `/mail` で表示する件数 |
| `MAIL_SEARCH_DAYS` | `7` | IMAP検索を何日分に絞るか（高速化のため） |

## 使用方法

1. ボットを起動:
```bash
python main.py
```

2. Discordで以下のコマンドを使用:

| コマンド | 説明 |
| --- | --- |
| `/mail` | 最新3件を表示し、続けて5分間の待ち受けに入る |
| `/mail 5` | 表示件数を変える（1〜10件）。待ち受けは同じように始まる |
| `/mail full` | 本文を省略せず全文表示する。待ち受けは同じように始まる |

### `/mail` の流れ

1. 最新3件を表示します。ワンタイムパスワードが含まれていれば先頭に大きく出ます
2. 続けて「📬 **待ち受けを開始しました。**」と流れ、5分間10秒ごとに新着を確認します
3. コードを含むメールが届いた時点で投稿し、「✅ **待ち受けを終了しました。**」で終わります
4. 5分間届かなければ「⌛ **待ち受けを終了しました。**」で終わります

サイト側で「コードを送信」を押す**前**でも**後**でも構いません。
先に押していれば1のステップで表示され、後から押しても2〜3で拾えます。
`/mail` を何度も叩き直す必要はありません。

- 待ち受け中にもう一度 `/mail` を送ると、待ち受け時間が5分に延長されます
  （待ち受けが二重に走ることはありません）
- コードを含まない新着メールは通知だけして、待ち受けは続きます

## サーバーへの導入と自動更新

`deploy/` に systemd のユニットと自動更新スクリプトが入っています。
Bot は `/root/discord-bot` に置き、`main.py` を実行する前提です。
別の場所に置く場合は `deploy/*.service` の `WorkingDirectory` と `ExecStart` を
書き換えてください。

### 既に手動で動かしている場合（既存ディレクトリを git 管理に切り替える）

`/root/discord-bot` に `main.py` を直接置いて動かしている状態からの移行手順です。
`.env` は `.gitignore` 済みなので、この操作では消えません。

```bash
# 0. 動いている Bot を止め、念のため現物を退避する
pkill -f main.py || true
cd /root/discord-bot
cp main.py main.py.backup
cp .env .env.backup

# 1. git リポジトリとして紐付ける
git init -b main
git remote add origin https://github.com/mmmio30/DiscordBot_get_gmail.git
git fetch origin

# 2. 追従したいブランチに切り替える（既存の main.py は上書きされる）
#    main 以外に追従する場合はブランチ名を読み替え、.env の UPDATE_BRANCH にも書くこと
git checkout -f -b main origin/main

# 3. 中身が入れ替わったことを確認する
git log --oneline -3
ls -a

# 4. 依存パッケージを入れる
pip install discord.py python-dotenv
```

### systemd への登録

```bash
cd /root/discord-bot

# タイムゾーンを日本時間にする（AM3:00 の解釈に必要）
timedatectl set-timezone Asia/Tokyo

cp deploy/discord-gmail-bot.service /etc/systemd/system/
cp deploy/discord-gmail-bot-update.service /etc/systemd/system/
cp deploy/discord-gmail-bot-update.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now discord-gmail-bot.service
systemctl enable --now discord-gmail-bot-update.timer

# 確認
systemctl status discord-gmail-bot.service
systemctl list-timers discord-gmail-bot-update.timer
journalctl -u discord-gmail-bot.service -f
```

### 新規に構築する場合

```bash
git clone https://github.com/mmmio30/DiscordBot_get_gmail.git /root/discord-bot
cd /root/discord-bot
vi .env                        # 上記のセットアップを参照
pip install discord.py python-dotenv
```
このあと「systemd への登録」に進みます。

### 自動更新の設定

`.env` に以下を追記します（すべて任意）。

| 変数 | 既定値 | 説明 |
| --- | --- | --- |
| `UPDATE_BRANCH` | 現在のブランチ | 追従するブランチ |
| `BOT_SERVICE_NAME` | （なし） | 更新後に再起動する systemd サービス名。例: `discord-gmail-bot` |
| `DISCORD_WEBHOOK_URL` | （なし） | 通知先の Discord Webhook URL |
| `NOTIFY_MAIL_TO` | `EMAIL_ADDRESS` | メール通知の宛先 |
| `NOTIFY_ON_SUCCESS` | `true` | 更新成功時も通知するか。`false` でログのみ |

通知は Discord Webhook を優先し、未設定または送信失敗の場合は `EMAIL_ADDRESS` の
Gmail 経由でメールを送ります。**Bot が落ちているときでも通知が届くよう、Bot 自身では
なく Webhook を使っています。** Webhook は Discord のチャンネル設定 →
連携サービス → ウェブフック から作成できます。

### 自動更新の動作

毎日 AM3:00 に `deploy/auto_update.sh` が動き、以下を行います。

1. `git fetch` して、追従ブランチに更新があるか確認する。**無ければ何もせず、通知もしない**
2. ローカルに未コミットの変更がある、または履歴が分岐している場合は
   **更新せずに通知する**（勝手に上書きしません）
3. `git merge --ff-only` でファストフォワード更新する
4. Python ファイルの構文をチェックし、`BOT_SERVICE_NAME` のサービスを再起動する
5. 構文エラーや起動失敗があれば**元のコミットに戻して再起動し、通知する**

多重起動は `flock` で防いでいます。手動で実行したい場合は次のとおりです。

```bash
systemctl start discord-gmail-bot-update.service   # 実行
journalctl -u discord-gmail-bot-update.service -n 50   # ログ確認
/root/discord-bot/deploy/auto_update.sh    # 直接実行してもよい
```

### cron を使う場合

systemd タイマーの代わりに cron でも構いません。

```cron
0 3 * * * /root/discord-bot/deploy/auto_update.sh >> /var/log/discord-gmail-bot-update.log 2>&1
```

### 補足

- スクリプトは `systemctl restart` を行うため **root で実行する前提**です。
  一般ユーザーで動かす場合は、そのユーザーに該当サービスの再起動権限
  （sudoers か polkit ルール）を与えてください。
- リポジトリが public のため、AM3:00 の `git fetch` に認証は不要です。
  後から private にした場合は、デプロイキー（読み取り専用の SSH 鍵）を
  GitHub のリポジトリ設定 → Deploy keys に登録し、
  `git remote set-url origin git@github.com:mmmio30/DiscordBot_get_gmail.git`
  で SSH に切り替えてください。

## 注意事項

- GmailのIMAPアクセスが有効になっていることを確認してください
- アプリパスワードは安全に管理してください（`.env` は `.gitignore` 済みです）
- ボットトークンは他人と共有しないでください
- ワンタイムパスワードがDiscordのチャンネルに残るため、**プライベートなチャンネルでの利用を推奨します**
