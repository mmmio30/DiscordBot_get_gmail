#!/usr/bin/env python3
"""自動更新の結果をメールで通知する（Discord Webhook が使えないときの代替）。

auto_update.sh から呼ばれ、EMAIL_ADDRESS / EMAIL_PASSWORD / NOTIFY_MAIL_TO を
環境変数で受け取る。EMAIL_PASSWORD は Gmail のアプリパスワード。
"""
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage

SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 465))


def main():
    body = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    address = os.getenv('EMAIL_ADDRESS')
    password = os.getenv('EMAIL_PASSWORD')
    to = os.getenv('NOTIFY_MAIL_TO') or address

    if not (address and password and to):
        print("メール通知に必要な EMAIL_ADDRESS / EMAIL_PASSWORD が未設定です")
        return 1

    message = EmailMessage()
    message['Subject'] = body.splitlines()[0][:200] if body.strip() else 'Bot 自動更新の通知'
    message['From'] = address
    message['To'] = to
    message.set_content(body)

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=ssl.create_default_context()) as smtp:
            smtp.login(address, password)
            smtp.send_message(message)
    except Exception as e:
        print(f"メール通知に失敗しました: {e}")
        return 1

    print(f"メールで通知しました: {to}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
