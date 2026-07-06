import os
import json
import sqlite3
import logging
import random
from datetime import time, datetime
from zoneinfo import ZoneInfo

from telegram import Update, Poll
from telegram.ext import (
    Application,
    CommandHandler,
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
POST_HOUR = int(os.environ.get("POST_HOUR", "9"))  # when the daily question goes out
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")  # mount a Railway volume at /data

anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

TOPICS = [
    "doomscrolling",
    "phone addiction",
    "social media habits",
    "screen time and attention span",
    "building better daily habits",
    "digital minimalism",
    "notification overload",
    "sleep and screens",
    "comparison culture on social media",
    "dopamine and instant gratification",
]


# ---- Database ----
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS active_polls (
            poll_id TEXT PRIMARY KEY,
            chat_id INTEGER,
            message_id INTEGER,
            question TEXT,
            options TEXT,
            created_at TEXT
        )"""
    )
    conn.commit()
    conn.close()


def add_chat(chat_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR IGNORE INTO chats (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()


def get_all_chats():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT chat_id FROM chats").fetchall()
    conn.close()
    return [r[0] for r in rows]


def save_active_poll(poll_id, chat_id, message_id, question, options):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO active_polls (poll_id, chat_id, message_id, question, options, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (poll_id, chat_id, message_id, question, json.dumps(options), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_todays_polls():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT poll_id, chat_id, message_id, question, options FROM active_polls"
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
    topic = random.choice(TOPICS)
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
    return data


# ---- Handlers ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    add_chat(chat_id)
    await update.message.reply_text(
        "You're in! I'll post a daily question here, and everyone votes. "
        "Results reveal at midnight."
    )


async def post_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual trigger for testing: /postnow"""
    await post_daily_question(context)
    await update.message.reply_text("Posted the daily question.")


async def reveal_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual trigger for testing: /revealnow"""
    await reveal_results(context)
    await update.message.reply_text("Revealed results.")


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
        }

    clear_polls()  # start fresh for the new day

    for chat_id in chats:
        try:
            message = await context.bot.send_poll(
                chat_id=chat_id,
                question=f"🌙 Daily check-in: {q['question']}",
                options=q["options"],
                is_anonymous=True,
                allows_multiple_answers=False,
            )
            save_active_poll(
                message.poll.id, chat_id, message.message_id, q["question"], q["options"]
            )
        except Exception as e:
            logger.error(f"Failed to post to chat {chat_id}: {e}")


async def reveal_results(context: ContextTypes.DEFAULT_TYPE):
    polls = get_todays_polls()
    if not polls:
        logger.info("No active polls to reveal.")
        return

    for poll_id, chat_id, message_id, question, options_json in polls:
        options = json.loads(options_json)
        try:
            stopped_poll = await context.bot.stop_poll(chat_id=chat_id, message_id=message_id)
            lines = [f"📊 Results for: {question}\n"]
            total_votes = sum(o.voter_count for o in stopped_poll.options)
            for opt in stopped_poll.options:
                pct = (opt.voter_count / total_votes * 100) if total_votes else 0
                bar = "█" * int(pct / 10)
                lines.append(f"{opt.text}: {opt.voter_count} votes ({pct:.0f}%) {bar}")
            if total_votes == 0:
                lines.append("\nNobody voted today — see you tomorrow!")
            await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))
        except Exception as e:
            logger.error(f"Failed to reveal results for chat {chat_id}: {e}")

    clear_polls()


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("postnow", post_now))
    app.add_handler(CommandHandler("revealnow", reveal_now))
    app.add_handler(PollAnswerHandler(poll_answer_handler))

    tz = ZoneInfo(TIMEZONE)
    job_queue = app.job_queue
    job_queue.run_daily(post_daily_question, time=time(hour=POST_HOUR, minute=0, tzinfo=tz))
    job_queue.run_daily(reveal_results, time=time(hour=0, minute=0, tzinfo=tz))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
