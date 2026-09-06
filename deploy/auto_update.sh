#!/usr/bin/env bash
#
# git の更新を確認し、あればファストフォワードで取り込んで Bot を再起動する。
# systemd タイマー（または cron）から毎日 AM3:00 に呼ばれることを想定している。
#
# 動作:
#   - 更新が無ければ何もせず、通知もしない
#   - 未コミットの変更がある、履歴が分岐しているなど、ファストフォワードできない
#     場合は更新せずに通知する
#   - 更新後に構文エラーや起動失敗があれば元のコミットに戻してから通知する
#   - 通知先は Discord Webhook。失敗したらメールにフォールバックする
#
# 設定は .env に書く（すべて任意）:
#   UPDATE_BRANCH=main                 追従するブランチ（既定: 現在のブランチ）
#   BOT_SERVICE_NAME=discord-gmail-bot 更新後に再起動する systemd サービス名
#   DISCORD_WEBHOOK_URL=https://...    通知先の Discord Webhook
#   NOTIFY_MAIL_TO=you@example.com     メール通知の宛先（既定: EMAIL_ADDRESS）
#   NOTIFY_ON_SUCCESS=true             更新成功時も通知するか（既定: true）

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# .env から値を1つ読む。source と違い、中身をシェルとして実行しない
env_get() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | tail -n 1 \
        | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

BRANCH="$(env_get UPDATE_BRANCH)"
BOT_SERVICE="$(env_get BOT_SERVICE_NAME)"
WEBHOOK_URL="$(env_get DISCORD_WEBHOOK_URL)"
NOTIFY_ON_SUCCESS="$(env_get NOTIFY_ON_SUCCESS)"
[ -n "$NOTIFY_ON_SUCCESS" ] || NOTIFY_ON_SUCCESS=true

send_mail() {
    EMAIL_ADDRESS="$(env_get EMAIL_ADDRESS)" \
    EMAIL_PASSWORD="$(env_get EMAIL_PASSWORD)" \
    NOTIFY_MAIL_TO="$(env_get NOTIFY_MAIL_TO)" \
    python3 "$REPO_DIR/deploy/notify_mail.py" "$1" 2>&1 | while read -r line; do log "$line"; done
}

notify() {
    local text="$1"
    log "通知: ${text//$'\n'/ / }"
    if [ -n "$WEBHOOK_URL" ]; then
        local payload
        payload="$(printf '%s' "$text" | python3 -c 'import json,sys; print(json.dumps({"content": sys.stdin.read()[:1900]}))')"
        if curl -fsS -m 20 -H 'Content-Type: application/json' -d "$payload" "$WEBHOOK_URL" >/dev/null; then
            return 0
        fi
        log "Discord への通知に失敗しました。メールに切り替えます"
    fi
    send_mail "$text"
}

fail() {
    notify "❌ Bot の自動更新に失敗しました（$(hostname)）
ブランチ: ${BRANCH:-?}
$1"
    exit 1
}

cd "$REPO_DIR" || { log "リポジトリのディレクトリに入れません: $REPO_DIR"; exit 1; }

# 前回の実行が残っていた場合に多重起動しない
exec 9>"$REPO_DIR/.auto_update.lock"
if ! flock -n 9; then
    log "他の更新処理が実行中のため終了します"
    exit 0
fi

command -v git >/dev/null 2>&1 || fail "git が見つかりません"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
[ -n "$BRANCH" ] || BRANCH="$CURRENT_BRANCH"
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
    fail "現在のブランチ（$CURRENT_BRANCH）が更新対象（$BRANCH）と異なるため中止しました"
fi

FETCH_OUT="$(git fetch --prune origin "$BRANCH" 2>&1)" \
    || fail "git fetch に失敗しました
$FETCH_OUT"

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH" 2>/dev/null)" \
    || fail "origin/$BRANCH が見つかりません"

if [ "$LOCAL" = "$REMOTE" ]; then
    log "更新はありません（$BRANCH ${LOCAL:0:7}）"
    exit 0
fi

log "更新を検出しました: ${LOCAL:0:7} -> ${REMOTE:0:7}"

if ! git diff --quiet || ! git diff --cached --quiet; then
    fail "ローカルに未コミットの変更があるため更新を中止しました
$(git status --short | head -n 20)"
fi

if ! git merge-base --is-ancestor "$LOCAL" "$REMOTE"; then
    fail "履歴が分岐しているためファストフォワードできません（${LOCAL:0:7} -> ${REMOTE:0:7}）"
fi

MERGE_OUT="$(git merge --ff-only "origin/$BRANCH" 2>&1)" \
    || fail "ファストフォワードに失敗しました
$MERGE_OUT"

rollback() {
    log "元のコミット ${LOCAL:0:7} に戻します"
    git reset --hard "$LOCAL" >/dev/null 2>&1
    [ -n "$BOT_SERVICE" ] && systemctl restart "$BOT_SERVICE" >/dev/null 2>&1
    return 0
}

COMPILE_OUT="$(python3 -m py_compile bot.py 2>&1)"
if [ $? -ne 0 ]; then
    rollback
    fail "更新後の bot.py に構文エラーがあったため、${LOCAL:0:7} に戻しました
$COMPILE_OUT"
fi

if [ -n "$BOT_SERVICE" ]; then
    RESTART_OUT="$(systemctl restart "$BOT_SERVICE" 2>&1)"
    if [ $? -ne 0 ]; then
        rollback
        fail "$BOT_SERVICE の再起動に失敗したため、${LOCAL:0:7} に戻しました
$RESTART_OUT"
    fi
    sleep 5
    if ! systemctl is-active --quiet "$BOT_SERVICE"; then
        JOURNAL="$(journalctl -u "$BOT_SERVICE" -n 20 --no-pager 2>&1 | tail -n 20)"
        rollback
        fail "更新後に $BOT_SERVICE が起動しなかったため、${LOCAL:0:7} に戻しました
$JOURNAL"
    fi
else
    log "BOT_SERVICE_NAME が未設定のため再起動はスキップしました（手動で再起動してください）"
fi

MESSAGE="✅ Bot を更新しました（$(hostname)）
ブランチ: $BRANCH
${LOCAL:0:7} -> ${REMOTE:0:7}
$(git log --oneline "$LOCAL..$REMOTE" | head -n 10)"

if [ "$NOTIFY_ON_SUCCESS" = "true" ] || [ "$NOTIFY_ON_SUCCESS" = "1" ]; then
    notify "$MESSAGE"
else
    log "${MESSAGE//$'\n'/ / }"
fi

# 通知の成否は更新の成否ではないので、ここまで来たら成功として終わる
exit 0
