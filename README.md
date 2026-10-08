# Evim content bot

Every morning the bot finds short soft-furniture videos (YouTube Shorts, TikTok, Instagram Reels, Pinterest), checks them with Claude AI, writes an Uzbek caption draft and sends 8 of them to your private review group. There you choose the sound, edit the caption and approve. Approved videos are posted to the channel at 10:00, 13:00, 16:00 and 19:00 (Tashkent time).

All settings (times, counts, keywords, footer, AI model) are in `settings.yaml`. All bot texts are in `contentbot/texts.py`.

## 1. Create the Telegram parts

1. In Telegram, open **@BotFather** → `/newbot` → copy the token into `TELEGRAM_BOT_TOKEN`.
2. Create a **private group** for review. Add the bot and make it an **admin**, so it can see uploaded music and delete messages.
3. Create a **private test channel**. Add the bot as an **admin** with "Post messages".
4. Find the ids: send any message in the review group and one post in the test channel. Then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser. The group id is `chat.id` (a negative number such as `-100…`) → `REVIEW_CHAT_ID`. The channel id (also `-100…`) → `CHANNEL_ID`. Do this before you start the bot.

## 2. Create the API keys

- **Claude (optional, paid):** https://console.anthropic.com → add a payment card → API keys → `ANTHROPIC_API_KEY`. Leave it empty to run without AI: videos are picked by views, and you write each caption with ✏️ Matnni tahrirlash. Add the key later and restart to switch AI on.
- **YouTube:** https://console.cloud.google.com → new project → enable "YouTube Data API v3" → Credentials → Create API key → `YOUTUBE_API_KEY`.
- **Apify:** https://apify.com → sign up (free plan, $5 credit every month) → Settings → API & Integrations → `APIFY_TOKEN`.

Copy `.env.example` to `.env` and fill in the values (the Claude key is optional). Values in `.env` always win over system environment variables with the same name.

## 3. Add calm music

Put about 10 calm tracks (`.mp3` or `.m4a`) into the `music/` folder. Pixabay Music (https://pixabay.com/music/, search "calm" or "ambient") allows business use without credit. You can also send audio files to the review group later; the bot asks "Musiqa kutubxonasiga qo'shilsinmi?".

## 4. Install on the server (Linux)

```bash
sudo apt install python3 python3-venv ffmpeg
cd /opt/contentbot            # the folder with this project
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Python 3.11 or newer is required (`python3 --version`).

## 5. Test without sending anything

```bash
.venv/bin/python main.py --dry-run
```

It prints the videos it would send, with scores and caption drafts. Nothing is posted and nothing is marked as seen.

## 6. Start the bot

```bash
nohup .venv/bin/python main.py > nohup.out 2>&1 &
```

Logs: `data/logs/bot.log`. To stop it: `pkill -f "python main.py"`.

Optional auto-restart: see `deploy/contentbot.service`.

## 7. Weekly maintenance

Platforms change often, so update yt-dlp once a week. The bot starts yt-dlp fresh for every download, so no restart is needed:

```bash
.venv/bin/python -m pip install -U yt-dlp
```

`deploy/update-ytdlp.sh` has a ready cron line.

## 8. Switch to the real channel

When the test channel looks good: make the bot an admin of **@evim_uzb** with "Post messages", set `CHANNEL_ID=@evim_uzb` in `.env` and restart the bot.

## Review group commands

| Command | What it does |
|---|---|
| `/queue` | Approved videos and their posting times |
| `/search` | Run an extra search now |
| `/status` | Last search, sources, queue, music count, Apify spend this month |
| `/keywords` | All search keywords |
| `/addkw <text>` | Add a keyword (`#word` searches as a hashtag) |
| `/delkw <text>` | Remove a keyword |

## Backup

Copy `data/bot.db`, `settings.yaml`, `.env` and `music/`.

## Costs (estimate)

YouTube API: free. Apify: within the free $5/month at the default limits. Claude: optional. Without a key it costs $0; with a key, Opus 5.5 is about $10–20/month, or set `ai.model: claude-sonnet-5-5` in `settings.yaml` for about half.

## For developers

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt   # Windows: .venv\Scripts\python
.venv/bin/python -m pytest
```

Tests never call real services. The three ffmpeg tests are skipped when ffmpeg is not on PATH.
