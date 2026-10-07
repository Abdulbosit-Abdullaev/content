#!/bin/sh
# Update yt-dlp (platforms change often). Run weekly from cron, for example:
#   0 4 * * 1 /opt/contentbot/deploy/update-ytdlp.sh >> /opt/contentbot/data/logs/ytdlp-update.log 2>&1
# Restart the bot afterwards so it uses the new version.
cd "$(dirname "$0")/.." && .venv/bin/python -m pip install -U yt-dlp
