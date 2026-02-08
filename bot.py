import os
import re
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Load .env values into environment variables
load_dotenv()

ASK_EMAIL = 1

EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)

# Required environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "").strip()
SHEET_ID = os.getenv("SHEET_ID", "").strip()

# Service account key file path (keep this file OUT of GitHub)
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "service-account.json").strip()

# Invite link settings
INVITE_EXPIRE_MINUTES = int(os.getenv("INVITE_EXPIRE_MINUTES", "10"))


HEADER = [
    "timestamp_utc",
    "telegram_user_id",
    "telegram_username",
    "telegram_full_name",
    "patreon_email",
    "invite_link_created",
]


def must_env(name: str, value: str):
    if not value:
        raise RuntimeError(f"Missing env var: {name}")


def get_ws():
    """Open the Google Sheet and ensure header exists."""
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_JSON, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.sheet1

    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(HEADER)

    return ws


def user_already_submitted(ws, telegram_user_id: int) -> bool:
    """Enforce only one submission per Telegram user id."""
    headers = ws.row_values(1)
    try:
        col_idx = headers.index("telegram_user_id") + 1  # 1-based
    except ValueError:
        # Fallback if header is different
        col_idx = 2

    col = ws.col_values(col_idx)
    return str(telegram_user_id) in set(col[1:])


def append_submission(ws, user, email: str, invite_link: str):
    full_name = " ".join([x for x in [user.first_name, user.last_name] if x]) or ""
    row = [
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        str(user.id),
        user.username or "",
        full_name,
        email,
        invite_link,
    ]
    ws.append_row(row, value_input_option="RAW")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only allow DM use
    if update.effective_chat.type != "private":
        await update.message.reply_text("Please DM me to get access.")
        return ConversationHandler.END

    await update.message.reply_text(
        "To get access, reply with the email address you use for Patreon.\n\n"
        "Note: you can only submit once."
    )
    return ASK_EMAIL


async def handle_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return ConversationHandler.END

    email = (update.message.text or "").strip()
    if not EMAIL_RE.match(email):
        await update.message.reply_text(
            "That doesn’t look like a valid email. Please try again (example: name@gmail.com)."
        )
        return ASK_EMAIL

    ws = get_ws()

    tg_user = update.effective_user
    if user_already_submitted(ws, tg_user.id):
        await update.message.reply_text(
            "You’ve already submitted your details, so I can’t accept another entry.\n"
            "If you need help, message the admin."
        )
        return ConversationHandler.END

    # Create one-time invite link (single use, expires soon)
    expire_dt = datetime.now(timezone.utc) + timedelta(minutes=INVITE_EXPIRE_MINUTES)
    expire_ts = int(expire_dt.timestamp())

    try:
        invite = await context.bot.create_chat_invite_link(
            chat_id=int(GROUP_CHAT_ID),
            member_limit=1,
            expire_date=expire_ts,
            creates_join_request=False,
            name=f"patreon_gate_{tg_user.id}_{int(time.time())}",
        )
        invite_link = invite.invite_link
    except Exception as e:
        await update.message.reply_text(
            "I couldn't create an invite link. Common causes:\n"
            "• bot is not an admin in the group\n"
            "• bot lacks permission to manage invite links\n"
            "• GROUP_CHAT_ID is wrong (supergroups often start with -100...)\n\n"
            f"Error: {type(e).__name__}: {e}"
        )
        return ConversationHandler.END

    append_submission(ws, tg_user, email, invite_link)

    await update.message.reply_text(
        "Thanks — here’s your one-time join link (single use, expires soon):\n\n"
        f"{invite_link}"
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled. You can restart with /start.")
    return ConversationHandler.END


def main():
    must_env("BOT_TOKEN", BOT_TOKEN)
    must_env("GROUP_CHAT_ID", GROUP_CHAT_ID)
    must_env("SHEET_ID", SHEET_ID)

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_email)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    app.run_polling()


if __name__ == "__main__":
    main()
