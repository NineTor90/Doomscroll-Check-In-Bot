# Doomscroll Check-In Bot

Posts an AI-generated daily poll about phone habits, doomscrolling, and behavior
change. Everyone in the chat votes; results reveal at midnight.

## How it works
- Every day at `POST_HOUR` (default 9am), the bot generates a fresh question
  via Claude and posts it as a native Telegram poll.
- Votes are anonymous and tallied by Telegram itself — no custom voting logic needed.
- At midnight (in your configured timezone), the bot closes the poll and posts
  a results breakdown to every registered chat.
- Add the bot to a group and run `/start` to register that chat for daily posts.

## Local setup
1. Create a bot with [@BotFather](https://t.me/BotFather) and grab the token.
2. Get an Anthropic API key from https://console.anthropic.com
3. `pip install -r requirements.txt`
4. Set environment variables (see below) and run `python bot.py`

## Environment variables
| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | from BotFather |
| `ANTHROPIC_API_KEY` | yes | from Anthropic console |
| `BOT_TIMEZONE` | no | IANA tz name, default `America/New_York` |
| `POST_HOUR` | no | hour (0-23) to post the daily question, default `9` |
| `DB_PATH` | no | SQLite file path, default `/data/bot.db` |

## Deploying on Railway
1. Push this folder to a GitHub repo.
2. In Railway: New Project → Deploy from GitHub repo.
3. Add the environment variables above under the service's **Variables** tab.
4. **Important — persistence**: Railway's filesystem is ephemeral by default.
   Add a **Volume** (Settings → Volumes) mounted at `/data` so the SQLite DB
   (registered chats, active polls) survives restarts/redeploys.
5. Railway will detect the `Procfile` and run it as a worker process
   (no public port needed — this bot uses polling, not webhooks).
6. Deploy. Add your bot to a Telegram group, run `/start`, and wait for the
   next scheduled post — or test immediately with `/postnow` and `/revealnow`.

## Commands
| Command | What it does |
|---|---|
| `/start` | Join daily questions in this chat (shows an inline "See commands" button) |
| `/help` | Lists all commands and the daily schedule |
| `/status` | Shows this chat's day streak |
| `/postnow` | Manually triggers today's question |
| `/revealnow` | Manually triggers the results reveal |

## UI details
- Each question is tagged with a topic emoji (e.g. 📱 for phone addiction, 🌙 for
  sleep/screens) so questions have a visual identity at a glance.
- The intro message and results use Telegram's HTML formatting (bold/italic) —
  no external UI needed since Telegram renders it natively.
- Results show a block-bar (`▓░`) per option, percentage, vote count, and a
  🏆 on the leading answer.
- A running day-streak is tracked per chat and surfaced via `/status`.

## Notes / next steps you might want
- Results only show vote counts and percentages, not who voted (polls are
  anonymous) — flip `is_anonymous=False` in `bot.py` if you want per-user tracking.
- The topic list in `bot.py` (`TOPICS`, a dict of topic → emoji) is easy to
  extend — add more angles (e.g. "mindful mornings") with their own emoji.
- Consider a `/history` command showing trends over the past week using the
  data already being written to SQLite.
