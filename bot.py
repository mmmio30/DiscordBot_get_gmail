import discord
from discord.ext import commands
import imaplib
import email
from email.header import decode_header
import os
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv()

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
EMAIL_ADDRESS = os.getenv('EMAIL_ADDRESS')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD') # ここにGmailのアプリパスワードがセットされる
IMAP_SERVER = os.getenv('IMAP_SERVER')       # ここに 'imap.gmail.com' がセットされる
IMAP_PORT = int(os.getenv('IMAP_PORT', 993)) # ここに 993 がセットされる

# (以下、前回の回答と同じコード)
# BotのIntents設定
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)

@bot.event
async def on_ready():
    print(f'{bot.user.name} としてログインしました')

def decode_subject(subject_bytes):
    """メールの件名をデコードする関数"""
    decoded_parts = decode_header(subject_bytes)
    subject = ""
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            try:
                subject += part.decode(charset or 'utf-8', errors='replace')
            except LookupError:
                subject += part.decode('utf-8', errors='replace')
        else:
            subject += part
    return subject

def get_email_body(msg):
    """メール本文を取得する関数（プレーンテキスト優先）"""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            if content_type == "text/plain" and "attachment" not in content_disposition:
                try:
                    charset = part.get_content_charset() or 'utf-8'
                    return part.get_payload(decode=True).decode(charset, errors='replace')
                except Exception:
                    return "(本文のデコードに失敗しました)"
        return "(プレーンテキストの本文が見つかりませんでした)"
    else:
        content_type = msg.get_content_type()
        if content_type == "text/plain":
            try:
                charset = msg.get_content_charset() or 'utf-8'
                return msg.get_payload(decode=True).decode(charset, errors='replace')
            except Exception:
                return "(本文のデコードに失敗しました)"
        else:
            return f"(サポートされていないContent-Type: {content_type} です)"

@bot.command(name='mail')
async def check_mail(ctx):
    """最新3件のメールを表示します。"""
    await ctx.send("メールを確認しています...")
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        mail.select('inbox')

        status, data = mail.search(None, 'ALL')
        mail_ids = []
        if status == 'OK':
            mail_ids = data[0].split()

        if not mail_ids:
            await ctx.send("受信トレイにメールがありません。")
            mail.logout()
            return

        fetched_emails = []
        for i in range(len(mail_ids) - 1, max(len(mail_ids) - 4, -1), -1):
            email_id = mail_ids[i]
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            if status == 'OK':
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        
                        subject = decode_subject(msg['subject'])
                        from_ = decode_subject(msg['from'])
                        body = get_email_body(msg)
                        body_preview = (body[:300] + '...') if len(body) > 300 else body
                        
                        fetched_emails.append({
                            'id': email_id.decode(),
                            'from': from_,
                            'subject': subject,
                            'body_preview': body_preview.strip()
                        })
            if len(fetched_emails) >= 3:
                break
        
        mail.logout()

        if not fetched_emails:
            await ctx.send("メールの取得に失敗しました。")
            return

        for em_data in fetched_emails:
            embed = discord.Embed(
                title=f"件名: {em_data['subject']}",
                description=f"差出人: {em_data['from']}",
                color=discord.Color.blue()
            )
            embed.add_field(name="本文（冒頭）", value=em_data['body_preview'] if em_data['body_preview'] else "(本文なし)", inline=False)
            await ctx.send(embed=embed)

    except imaplib.IMAP4.error as e:
        print(f"IMAPエラー: {e}")
        await ctx.send(f"メールサーバーへの接続または操作に失敗しました: {e}")
        if "AUTHENTICATIONFAILED" in str(e).upper():
             await ctx.send("認証に失敗しました。Gmailの場合、アプリパスワードが正しいか、IMAPアクセスが有効になっているか確認してください。")
    except Exception as e:
        print(f"予期せぬエラー: {e}")
        await ctx.send(f"メールの確認中にエラーが発生しました: {e}")

if DISCORD_BOT_TOKEN:
    bot.run(DISCORD_BOT_TOKEN)
else:
    print("エラー: DISCORD_BOT_TOKEN が設定されていません。")