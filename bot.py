import asyncio
import email
import html as html_lib
import imaplib
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv()

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
EMAIL_ADDRESS = os.getenv('EMAIL_ADDRESS')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD') # ここにGmailのアプリパスワードがセットされる
IMAP_SERVER = os.getenv('IMAP_SERVER')       # ここに 'imap.gmail.com' がセットされる
IMAP_PORT = int(os.getenv('IMAP_PORT', 993)) # ここに 993 がセットされる

# 待ち受け（/otp）の挙動を調整する設定
OTP_WATCH_SECONDS = int(os.getenv('OTP_WATCH_SECONDS', 300))     # 待ち受ける時間（既定5分）
OTP_POLL_INTERVAL = int(os.getenv('OTP_POLL_INTERVAL', 10))      # 新着チェックの間隔（既定10秒）
OTP_LOOKBACK_SECONDS = int(os.getenv('OTP_LOOKBACK_SECONDS', 120))  # 待ち受け開始時に遡って探す範囲
OTP_MAX_WATCH_SECONDS = int(os.getenv('OTP_MAX_WATCH_SECONDS', 900))  # 待ち受け時間の上限
MAIL_DEFAULT_COUNT = int(os.getenv('MAIL_DEFAULT_COUNT', 3))     # /mail で表示する件数
MAIL_SEARCH_DAYS = int(os.getenv('MAIL_SEARCH_DAYS', 7))         # IMAP検索を何日分に絞るか

# BotのIntents設定
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)


@bot.event
async def on_ready():
    print(f'{bot.user.name} としてログインしました')


@bot.event
async def on_command_error(ctx, error):
    """未知のコマンドは黙って無視し、それ以外はチャンネルに知らせる"""
    if isinstance(error, commands.CommandNotFound):
        return
    print(f"コマンドエラー: {error}")
    await ctx.send(f"コマンドの実行に失敗しました: {error}")


# --------------------------------------------------------------------------
# メールの解析
# --------------------------------------------------------------------------

def decode_mime_header(header_value):
    """メールヘッダ（件名・差出人など）をデコードする関数"""
    if not header_value:
        return ""
    decoded_parts = decode_header(header_value)
    text = ""
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            try:
                text += part.decode(charset or 'utf-8', errors='replace')
            except LookupError:
                text += part.decode('utf-8', errors='replace')
        else:
            text += part
    return text


def html_to_text(html_text):
    """HTMLメールをざっくりプレーンテキストに変換する関数"""
    text = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', html_text)
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(p|div|tr|li|h[1-6]|table)\s*>', '\n', text)
    text = re.sub(r'(?s)<[^>]+>', ' ', text)
    text = html_lib.unescape(text)
    text = text.replace(' ', ' ')
    text = re.sub(r'[ \t　]+', ' ', text)
    text = re.sub(r'\n[ \t]+', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _decode_payload(part):
    """パートのペイロードを文字列にデコードする関数"""
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or 'utf-8'
    try:
        return payload.decode(charset, errors='replace')
    except LookupError:
        return payload.decode('utf-8', errors='replace')


def get_email_body(msg):
    """メール本文を取得する関数（text/plain 優先、無ければ text/html をテキスト化）"""
    plain_parts = []
    html_parts = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == 'multipart':
                continue
            disposition = str(part.get('Content-Disposition') or '').lower()
            if 'attachment' in disposition:
                continue
            content_type = part.get_content_type()
            if content_type == 'text/plain':
                plain_parts.append(_decode_payload(part))
            elif content_type == 'text/html':
                html_parts.append(_decode_payload(part))
    else:
        if msg.get_content_type() == 'text/html':
            html_parts.append(_decode_payload(msg))
        else:
            plain_parts.append(_decode_payload(msg))

    body = "\n".join(p for p in plain_parts if p.strip()).strip()
    if not body:
        # HTMLしか無い認証メールが多いので、その場合はHTMLから抽出する
        body = "\n".join(html_to_text(h) for h in html_parts if h.strip()).strip()
    return body or "(本文を取得できませんでした)"


# --------------------------------------------------------------------------
# ワンタイムパスワードの抽出
# --------------------------------------------------------------------------

# コードの近くに出てくる語。多いほど「これがOTPだ」と判断しやすくなる
CONTEXT_KEYWORDS = (
    'ワンタイム', '認証コード', '確認コード', 'セキュリティコード', 'パスコード',
    '認証番号', '確認番号', '本人確認', '確認用', '認証用', 'コード', '暗証',
    'verification', 'verify', 'security code', 'one-time', 'one time', 'onetime',
    'passcode', 'pass code', 'otp', 'authentication', 'auth code', 'access code',
    'confirmation', 'code', 'pin',
)

_URL_RE = re.compile(r'https?://\S+')
_DIGIT_CODE_RE = re.compile(r'(?<![0-9A-Za-z])(\d{4,8})(?![0-9A-Za-z])')
_SPACED_CODE_RE = re.compile(r'(?<![0-9A-Za-z])(\d{3})[ \-](\d{3})(?![0-9A-Za-z])')
_ALNUM_CODE_RE = re.compile(r'(?<![0-9A-Za-z])([0-9A-Za-z]{5,8})(?![0-9A-Za-z])')


def _has_keyword(text):
    lowered = text.lower()
    return any(keyword in lowered for keyword in CONTEXT_KEYWORDS)


def _looks_like_noise(before, after):
    """日付・時刻・金額・URLの一部など、OTPではない数字を弾く"""
    if re.search(r'[¥$￥]\s*$', before):
        return True
    if re.match(r'\s*(円|年|月|日|時|分|秒|件|通|%|％)', after):
        return True
    if re.search(r'\d\s*[/\-:.,]\s*$', before):
        return True
    if re.match(r'\s*[/\-:,]\s*\d', after):
        return True
    return False


def _collect_candidates(text, in_subject, url_spans, results):
    """1つのテキストからコード候補を集め、スコアを付けて results に足し込む"""

    def add(code, start, end, bonus=0):
        if any(start >= s and end <= e for s, e in url_spans):
            return
        before = text[max(0, start - 70):start]
        after = text[end:end + 50]
        if _looks_like_noise(before, after):
            return

        near_keyword_before = _has_keyword(before)
        near_keyword_after = _has_keyword(after)
        if (len(code) == 4 and code.isdigit() and 1900 <= int(code) <= 2099
                and not (near_keyword_before or near_keyword_after)):
            return  # コピーライト表記などの西暦

        score = bonus
        if near_keyword_before:
            score += 40
        if near_keyword_after:
            score += 25
        if in_subject:
            score += 20

        line_start = text.rfind('\n', 0, start) + 1
        line_end = text.find('\n', end)
        line = text[line_start:line_end if line_end != -1 else len(text)]
        if line.strip() == code:
            # コードだけが1行に置かれている書式は非常に多い
            score += 20

        score += 10 if len(code) == 6 else 5

        if code not in results or results[code] < score:
            results[code] = score

    for match in _SPACED_CODE_RE.finditer(text):
        add(match.group(1) + match.group(2), match.start(), match.end(), bonus=10)
    for match in _DIGIT_CODE_RE.finditer(text):
        add(match.group(1), match.start(1), match.end(1))
    for match in _ALNUM_CODE_RE.finditer(text):
        code = match.group(1)
        if code.isdigit() or code.isalpha():
            continue  # 数字のみは上で処理済み、英字のみはただの単語
        if not any(c.isdigit() for c in code):
            continue
        add(code, match.start(1), match.end(1))


def extract_otp_candidates(subject, body):
    """件名と本文からワンタイムパスワードらしき文字列を抜き出し、確度順に返す"""
    # 全角数字・全角英字を半角に揃えてから探す
    norm_subject = unicodedata.normalize('NFKC', subject or "")
    norm_body = unicodedata.normalize('NFKC', body or "")

    results = {}
    for text, in_subject in ((norm_subject, True), (norm_body, False)):
        if not text:
            continue
        url_spans = [m.span() for m in _URL_RE.finditer(text)]
        _collect_candidates(text, in_subject, url_spans, results)

    ordered = sorted(results.items(), key=lambda kv: (-kv[1], len(kv[0])))
    return [code for code, _ in ordered]


# --------------------------------------------------------------------------
# IMAP アクセス（すべて同期。呼び出し側で asyncio.to_thread に載せる）
# --------------------------------------------------------------------------

_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
           'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _imap_date(dt):
    """IMAPのSEARCHで使う 06-Sep-2026 形式の日付文字列を作る（ロケール非依存）"""
    return f"{dt.day:02d}-{_MONTHS[dt.month - 1]}-{dt.year}"


def _connect():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    mail.select('inbox')
    return mail


def _close(mail):
    try:
        mail.logout()
    except Exception:
        pass


def _search_uids(mail):
    """直近 MAIL_SEARCH_DAYS 日のUID一覧を返す（空なら全件にフォールバック）"""
    since = _imap_date(datetime.now() - timedelta(days=MAIL_SEARCH_DAYS))
    status, data = mail.uid('search', None, 'SINCE', since)
    uids = data[0].split() if status == 'OK' and data and data[0] else []
    if not uids:
        status, data = mail.uid('search', None, 'ALL')
        uids = data[0].split() if status == 'OK' and data and data[0] else []
    return uids


def _fetch_one(mail, uid):
    """UIDを1件取得して、表示に必要な情報にまとめる"""
    status, msg_data = mail.uid('fetch', uid, '(RFC822)')
    if status != 'OK' or not msg_data:
        return None
    for response_part in msg_data:
        if not isinstance(response_part, tuple):
            continue
        msg = email.message_from_bytes(response_part[1])
        subject = decode_mime_header(msg['subject'])
        from_ = decode_mime_header(msg['from'])
        body = get_email_body(msg)

        received_at = None
        if msg['date']:
            try:
                received_at = parsedate_to_datetime(msg['date'])
                if received_at.tzinfo is None:
                    received_at = received_at.replace(tzinfo=timezone.utc)
            except Exception:
                received_at = None

        return {
            'uid': int(uid),
            'from': from_,
            'subject': subject,
            'body': body,
            'received_at': received_at,
            'codes': extract_otp_candidates(subject, body),
        }
    return None


def fetch_recent_sync(count):
    """最新 count 件のメールを新しい順に取得する"""
    mail = _connect()
    try:
        uids = _search_uids(mail)
        if not uids:
            return []
        items = []
        for uid in reversed(uids[-count:]):
            item = _fetch_one(mail, uid)
            if item:
                items.append(item)
        return items
    finally:
        _close(mail)


class MailWatcher:
    """待ち受け中はIMAP接続を張りっぱなしにして、10秒ごとに新着UIDを見る"""

    def __init__(self):
        self.mail = None
        self.baseline_uid = 0

    def start(self):
        self.mail = _connect()
        uids = _search_uids(self.mail)
        self.baseline_uid = int(uids[-1]) if uids else 0
        recent = []
        # 待ち受けを始める直前に届いていたメールも拾っておく
        # （コマンドを打つ前にメールが来ていたケースの救済）
        threshold = datetime.now(timezone.utc) - timedelta(seconds=OTP_LOOKBACK_SECONDS)
        for uid in reversed(uids[-5:]):
            item = _fetch_one(self.mail, uid)
            if not item or not item['codes']:
                continue
            if item['received_at'] and item['received_at'] >= threshold:
                recent.append(item)
        return list(reversed(recent))

    def _reconnect(self):
        if self.mail is not None:
            _close(self.mail)
        self.mail = _connect()

    def poll(self):
        """baseline より新しいUIDのメールを取得し、baseline を進める"""
        for attempt in range(2):
            try:
                self.mail.noop()  # NOOPを挟まないと新着が見えないサーバーがある
                status, data = self.mail.uid('search', None, f'UID {self.baseline_uid + 1}:*')
                raw = data[0].split() if status == 'OK' and data and data[0] else []
                # 'UID n:*' は該当が無くても最後の1件を返すので、必ず絞り直す
                uids = sorted(int(u) for u in raw if int(u) > self.baseline_uid)
                items = []
                for uid in uids:
                    item = _fetch_one(self.mail, str(uid))
                    if item:
                        items.append(item)
                    self.baseline_uid = max(self.baseline_uid, uid)
                return items
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError):
                if attempt == 0:
                    self._reconnect()
                    continue
                raise
        return []

    def stop(self):
        if self.mail is not None:
            _close(self.mail)
            self.mail = None


# --------------------------------------------------------------------------
# Discordへの表示
# --------------------------------------------------------------------------

def _trim(text, limit):
    return text if len(text) <= limit else text[:limit - 3] + '...'


def build_mail_embed(item, color=discord.Color.blue()):
    """1通分のEmbedを組み立てる。OTPが見つかっていれば先頭に大きく出す"""
    embed = discord.Embed(
        title=_trim(f"件名: {item['subject'] or '(件名なし)'}", 256),
        description=_trim(f"差出人: {item['from'] or '(不明)'}", 4096),
        color=color,
    )

    if item['codes']:
        primary = item['codes'][0]
        value = f"# `{primary}`"
        others = item['codes'][1:3]
        if others:
            value += "\n他の候補: " + " / ".join(f"`{c}`" for c in others)
        embed.add_field(name="🔑 ワンタイムパスワード", value=value, inline=False)

    body = item['body'].strip() or "(本文なし)"
    embed.add_field(name="本文", value=_trim(body, 1024), inline=False)

    if len(body) > 1024:
        embed.set_footer(text="本文が長いため省略しています。全文は /mail full で表示できます。")
    elif item['received_at']:
        embed.set_footer(text=item['received_at'].astimezone().strftime('%Y-%m-%d %H:%M:%S'))
    return embed


async def send_full_body(ctx, item):
    """本文をDiscordの文字数制限に合わせて分割送信する"""
    await ctx.send(f"**件名: {_trim(item['subject'] or '(件名なし)', 200)}**")
    body = item['body'].strip() or "(本文なし)"
    for i in range(0, len(body), 1900):
        await ctx.send(f"```\n{body[i:i + 1900]}\n```")


def _imap_error_message(error):
    message = f"メールサーバーへの接続または操作に失敗しました: {error}"
    if "AUTHENTICATIONFAILED" in str(error).upper():
        message += "\n認証に失敗しました。Gmailの場合、アプリパスワードが正しいか、IMAPアクセスが有効になっているか確認してください。"
    return message


# --------------------------------------------------------------------------
# コマンド
# --------------------------------------------------------------------------

@bot.command(name='mail')
async def check_mail(ctx, *args):
    """最新のメールを表示します。 例: /mail  /mail 5  /mail full"""
    count = MAIL_DEFAULT_COUNT
    full = False
    for arg in args:
        if arg.lower() in ('full', 'all', '全文'):
            full = True
        elif arg.isdigit():
            count = max(1, min(int(arg), 10))

    await ctx.send("メールを確認しています...")
    try:
        items = await asyncio.to_thread(fetch_recent_sync, count)
    except imaplib.IMAP4.error as e:
        print(f"IMAPエラー: {e}")
        await ctx.send(_imap_error_message(e))
        return
    except Exception as e:
        print(f"予期せぬエラー: {e}")
        await ctx.send(f"メールの確認中にエラーが発生しました: {e}")
        return

    if not items:
        await ctx.send("受信トレイにメールがありません。")
        return

    for item in items:
        await ctx.send(embed=build_mail_embed(item))
        if full:
            await send_full_body(ctx, item)


# チャンネルごとに待ち受けタスクを1つだけ持つ
active_watches = {}


async def _run_watch(ctx, watch_seconds):
    watcher = MailWatcher()
    try:
        try:
            already_arrived = await asyncio.to_thread(watcher.start)
        except imaplib.IMAP4.error as e:
            print(f"IMAPエラー: {e}")
            await ctx.send(_imap_error_message(e))
            return
        except Exception as e:
            print(f"予期せぬエラー: {e}")
            await ctx.send(f"待ち受けの開始に失敗しました: {e}")
            return

        if already_arrived:
            await ctx.send(f"直前に届いていたメールからコードが見つかりました（{len(already_arrived)}件）。")
            for item in already_arrived:
                await ctx.send(embed=build_mail_embed(item, discord.Color.green()))
            return

        minutes = watch_seconds / 60
        await ctx.send(
            f"📬 これから **{minutes:g}分間** 新着メールを見張ります"
            f"（{OTP_POLL_INTERVAL}秒ごとに確認）。"
            f"\nワンタイムパスワードを見つけた時点で自動的に投稿して終了します。"
            f"途中でやめる場合は `/otpstop` を送ってください。"
        )

        deadline = asyncio.get_running_loop().time() + watch_seconds
        seen_without_code = 0
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(OTP_POLL_INTERVAL)
            try:
                new_items = await asyncio.to_thread(watcher.poll)
            except Exception as e:
                print(f"待ち受け中のエラー: {e}")
                await ctx.send(f"新着の確認中にエラーが発生したため待ち受けを終了します: {e}")
                return

            for item in new_items:
                if item['codes']:
                    await ctx.send("🔑 ワンタイムパスワードが届きました。")
                    await ctx.send(embed=build_mail_embed(item, discord.Color.green()))
                    return
                seen_without_code += 1
                await ctx.send(embed=build_mail_embed(item, discord.Color.light_grey()))

        note = "" if seen_without_code == 0 else f"（コードを含まない新着は{seen_without_code}件ありました）"
        await ctx.send(f"⌛ 待ち受け時間が終了しました。ワンタイムパスワードは届きませんでした。{note}")
    finally:
        active_watches.pop(ctx.channel.id, None)
        # キャンセル直後でも確実に切断できるよう、await せずに後始末する
        asyncio.get_running_loop().run_in_executor(None, watcher.stop)


@bot.command(name='otp')
async def watch_otp(ctx, minutes: float = None):
    """ワンタイムパスワードのメールを待ち受けます。 例: /otp  /otp 10"""
    if ctx.channel.id in active_watches:
        await ctx.send("このチャンネルでは既に待ち受け中です。`/otpstop` で停止できます。")
        return

    watch_seconds = OTP_WATCH_SECONDS if minutes is None else int(minutes * 60)
    watch_seconds = max(OTP_POLL_INTERVAL, min(watch_seconds, OTP_MAX_WATCH_SECONDS))

    task = asyncio.create_task(_run_watch(ctx, watch_seconds))
    active_watches[ctx.channel.id] = task
    try:
        await task
    except asyncio.CancelledError:
        await ctx.send("待ち受けを停止しました。")


@bot.command(name='otpstop')
async def stop_otp(ctx):
    """実行中の待ち受けを停止します。"""
    task = active_watches.get(ctx.channel.id)
    if task is None:
        await ctx.send("待ち受けは実行されていません。")
        return
    task.cancel()


if DISCORD_BOT_TOKEN:
    bot.run(DISCORD_BOT_TOKEN)
else:
    print("エラー: DISCORD_BOT_TOKEN が設定されていません。")
