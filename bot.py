import os
import json
import sqlite3
import logging
import random
from datetime import time, datetime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    PollAnswerHandler,
    ContextTypes,
)
from anthropic import Anthropic

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---- Config ----
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TIMEZONE = os.environ.get("BOT_TIMEZONE", "America/New_York")
POST_HOUR = int(os.environ.get("POST_HOUR", "9"))
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")  # mount a Railway volume at /data

anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

# Topic -> emoji, so every question has a visual identity
TOPICS = {
    "doomscrolling": "🌀",
    "phone addiction": "📱",
    "social media habits": "💬",
    "screen time and attention span": "⏳",
    "building better daily habits": "🌱",
    "digital minimalism": "🧘",
    "notification overload": "🔔",
    "sleep and screens": "🌙",
    "comparison culture on social media": "🪞",
    "dopamine and instant gratification": "⚡",
}

BAR_FULL = "▓"
BAR_EMPTY = "░"
BAR_LENGTH = 10


# ---- Database ----
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            joined_at TEXT,
            days_active INTEGER DEFAULT 0
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS active_polls (
            poll_id TEXT PRIMARY KEY,
            chat_id INTEGER,
            message_id INTEGER,
            question TEXT,
            options TEXT,
            emoji TEXT,
            created_at TEXT
        )"""
    )
    conn.commit()
    conn.close()


def add_chat(chat_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO chats (chat_id, joined_at, days_active) VALUES (?, ?, 0)",
        (chat_id, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def is_registered(chat_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT 1 FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row is not None


def get_all_chats():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT chat_id FROM chats").fetchall()
    conn.close()
    return [r[0] for r in rows]


def bump_days_active(chat_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE chats SET days_active = days_active + 1 WHERE chat_id = ?", (chat_id,))
    row = conn.execute("SELECT days_active FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.commit()
    conn.close()
    return row[0] if row else 1


def save_active_poll(poll_id, chat_id, message_id, question, options, emoji):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT OR REPLACE INTO active_polls
           (poll_id, chat_id, message_id, question, options, emoji, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (poll_id, chat_id, message_id, question, json.dumps(options), emoji, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_todays_polls():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT poll_id, chat_id, message_id, question, options, emoji FROM active_polls"
    ).fetchall()
    conn.close()
    return rows


def clear_polls():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM active_polls")
    conn.commit()
    conn.close()


# ---- Question generation ----
def generate_question() -> dict:
    topic = random.choice(list(TOPICS.keys()))
    prompt = f"""Write one daily poll question for a habit-awareness community bot.
Topic: {topic}

Requirements:
- The question should be reflective and non-judgmental, inviting honest self-report
- Provide exactly 4 short answer options (each under 6 words)
- Return ONLY valid JSON, no preamble, no markdown fences, in this exact shape:
{{"question": "...", "options": ["...", "...", "...", "..."]}}
"""
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    data = json.loads(text)
    data["topic"] = topic
    data["emoji"] = TOPICS[topic]
    return data


def make_bar(pct: float) -> str:
    filled = round(pct / 100 * BAR_LENGTH)
    return BAR_FULL * filled + BAR_EMPTY * (BAR_LENGTH - filled)


# ---- Handlers ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    already_in = is_registered(chat_id)
    add_chat(chat_id)

    if already_in:
        text = "👋 <b>You're already signed up</b> — no action needed."
    else:
        text = (
            "✅ <b>You're in!</b>\n\n"
            f"Every day at <b>{POST_HOUR}:00</b> I'll drop a reflective question "
            "about phones, habits, and attention. Everyone votes anonymously, "
            "and at <b>midnight</b> I'll reveal how the group answered.\n\n"
            "Type /help to see everything I can do."
        )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📋 See commands", callback_data="show_help")]]
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🧭 <b>Daily Check-In Bot</b>\n\n"
        "<b>/start</b> — join daily questions in this chat\n"
        "<b>/status</b> — see this chat's streak\n"
        "<b>/postnow</b> — post today's question immediately\n"
        "<b>/revealnow</b> — reveal current results immediately\n\n"
        f"Questions post at <b>{POST_HOUR}:00</b> and reveal at <b>midnight</b>, "
        f"{TIMEZONE.split('/')[-1].replace('_', ' ')} time."
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def help_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await help_command(update, context)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_registered(chat_id):
        await update.message.reply_text(
            "You haven't joined yet — run /start to get today's question.",
            parse_mode=ParseMode.HTML,
        )
        return
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT days_active FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    days = row[0] if row else 0
    await update.message.reply_text(
        f"🔥 <b>{days}-day</b> check-in streak in this chat. Keep it going!",
        parse_mode=ParseMode.HTML,
    )


async def post_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Generating today's question...")
    await post_daily_question(context)


async def reveal_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reveal_results(context)


async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Telegram tallies votes server-side; nothing to do here unless you want
    # to track per-user votes for something like a leaderboard later.
    pass


# ---- Scheduled jobs ----
async def post_daily_question(context: ContextTypes.DEFAULT_TYPE):
    chats = get_all_chats()
    if not chats:
        logger.info("No registered chats yet, skipping post.")
        return

    try:
        q = generate_question()
    except Exception as e:
        logger.error(f"Question generation failed: {e}")
        q = {
            "question": "How's your screen time been today?",
            "options": ["Better than usual", "About the same", "Worse", "Haven't checked"],
            "emoji": "📱",
        }

    clear_polls()

    date_str = datetime.now(ZoneInfo(TIMEZONE)).strftime("%A, %b %d")

    for chat_id in chats:
        try:
            intro = (
                f"{q['emoji']} <b>Daily Check-In</b> · {date_str}\n"
                "<i>Answer honestly — results are anonymous and reveal at midnight.</i>"
            )
            await context.bot.send_message(chat_id=chat_id, text=intro, parse_mode=ParseMode.HTML)

            message = await context.bot.send_poll(
                chat_id=chat_id,
                question=q["question"],
                options=q["options"],
                is_anonymous=True,
                allows_multiple_answers=False,
            )
            save_active_poll(
                message.poll.id, chat_id, message.message_id, q["question"], q["options"], q["emoji"]
            )
            bump_days_active(chat_id)
        except Exception as e:
            logger.error(f"Failed to post to chat {chat_id}: {e}")


async def reveal_results(context: ContextTypes.DEFAULT_TYPE):
    polls = get_todays_polls()
    if not polls:
        logger.info("No active polls to reveal.")
        return

    for poll_id, chat_id, message_id, question, options_json, emoji in polls:
        try:
            stopped_poll = await context.bot.stop_poll(chat_id=chat_id, message_id=message_id)
            total_votes = sum(o.voter_count for o in stopped_poll.options)
            max_votes = max((o.voter_count for o in stopped_poll.options), default=0)

            lines = [
                f"📊 <b>Results</b> — {emoji} {question}",
                "",
            ]
            for opt in stopped_poll.options:
                pct = (opt.voter_count / total_votes * 100) if total_votes else 0
                bar = make_bar(pct)
                crown = " 🏆" if opt.voter_count == max_votes and total_votes > 0 else ""
                lines.append(f"<code>{bar}</code> {pct:.0f}%")
                lines.append(f"<b>{opt.text}</b>{crown} — {opt.voter_count} vote(s)")
                lines.append("")

            if total_votes == 0:
                lines.append("😴 Nobody voted today — see you tomorrow!")
            else:
                lines.append(f"👥 <i>{total_votes} total vote(s) tonight</i>")

            await context.bot.send_message(
                chat_id=chat_id, text="\n".join(lines), parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to reveal results for chat {chat_id}: {e}")

    clear_polls()


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("postnow", post_now))
    app.add_handler(CommandHandler("revealnow", reveal_now))
    app.add_handler(CallbackQueryHandler(help_button, pattern="^show_help$"))
    app.add_handler(PollAnswerHandler(poll_answer_handler))

    tz = ZoneInfo(TIMEZONE)
    job_queue = app.job_queue
    job_queue.run_daily(post_daily_question, time=time(hour=POST_HOUR, minute=0, tzinfo=tz))
    job_queue.run_daily(reveal_results, time=time(hour=0, minute=0, tzinfo=tz))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
