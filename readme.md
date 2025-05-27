# Discord Gmail チェッカーボット

このDiscordボットは、Gmailアカウントに接続して最新のメールを確認することができるボットです。

## 機能

- 最新3件のメールを表示
- メールの件名、差出人、本文のプレビューを表示
- 日本語メールの適切なデコード対応

## 必要条件

- Python 3.7以上
- Discord.py
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

## 使用方法

1. ボットを起動:
```bash
python bot.py
```

2. Discordで以下のコマンドを使用:
- `/mail` - 最新3件のメールを表示

## 注意事項

- GmailのIMAPアクセスが有効になっていることを確認してください
- アプリパスワードは安全に管理してください
- ボットトークンは他人と共有しないでください
