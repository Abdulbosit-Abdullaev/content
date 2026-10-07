# Evim Content Bot — Design Spec

**Date:** 2026-10-07
**Status:** Reviewed by owner (script, phones, interface language confirmed 2026-10-07)
**Channel:** [@evim_uzb](https://t.me/evim_uzb) · [Instagram](https://www.instagram.com/evim_uzb/)

---

## 1. Goal

Evim sells materials and components for soft-furniture makers: mechanisms (sofa-bed, chair), foam, fabric, leather, sofa legs, tools, clips and small fittings.

The bot finds short videos about **soft furniture** on the internet every day. It sends them to a private Telegram **review group**, where a person chooses the sound, edits the caption and approves. The bot then posts the approved videos to the **@evim_uzb** channel at fixed times, **3–5 posts per day**.

### Success criteria

1. Every day by about 07:30 (Tashkent time) the review group receives up to **8** candidate videos. They must be relevant, **under 2 minutes**, and come from at least 2 platforms.
2. For each candidate, a reviewer can switch the sound, edit the caption and approve in **under 1 minute**.
3. Approved videos are posted to the channel automatically at the scheduled times, with the right caption and footer.
4. **No video is ever shown twice.**
5. The bot runs unattended for weeks. The only regular maintenance is updating yt-dlp.
6. Running cost is **about $50/month or less**, not counting the existing server.

---

## 2. Decisions made with the owner

| Topic | Decision |
|---|---|
| Video sources | YouTube Shorts (official API, free) + TikTok, Instagram Reels, Pinterest via **Apify** (start on the free $5/month plan, upgrade to $29 only if needed) |
| Not included | WeChat, Douyin (closed outside China), Facebook (no public video search) |
| AI | **Claude** checks relevance and writes caption drafts. Default model `claude-opus-5-5`; can be switched to `claude-sonnet-5-5` in settings |
| Topic | Anything related to soft furniture: Evim's materials, plus upholstery work, workshops, finished sofas and chairs, mechanisms in action |
| Max length | **120 seconds** (minimum 5 s) |
| Search languages | English, Russian, Turkish, Uzbek, Chinese |
| Sound options | 🔊 Original · 🔇 No sound · 🎵 Calm music (replaces the original) |
| Caption | AI draft + reviewer edits. Footer (phones, address, hours, links) added automatically |
| Caption script | **Uzbek Latin**, matching the existing channel posts |
| Footer phones | **+998557770007**, **+998991330007** only |
| Bot buttons and messages | **Uzbek (Latin)** |
| Posting | Approved videos go to a **queue** and are posted at **10:00, 13:00, 16:00, 19:00** |
| Hosting | Owner's existing Linux server, started with `nohup` |
| Architecture | **One Python program** (bot + timers + pipeline) with one SQLite database |

---

## 3. Daily flow

All times are `Asia/Tashkent` and set in `settings.yaml`.

```
07:00  SEARCH       Each source searches today's keyword set     → ~100 candidates
       FREE FILTER  Duration 5–120 s, not seen before, min views → ~30
       AI CHECK     Claude scores preview + title + description  → ranked list
       PICK         Top 8, at most 2 per category                → 8 selected
       DOWNLOAD     Direct media URL, or yt-dlp; on failure take the next one
       CAPTION      Claude drafts an Uzbek Latin caption for each
~07:30 REVIEW       8 messages arrive in the review group (video + caption + buttons)
any    HUMAN        Change sound, edit caption, approve / reject
10:00  POST         Video assigned to this slot is posted to the channel
13:00  POST
16:00  POST
19:00  POST
```

**Rules**

- **Approving:** each approval takes the **earliest free slot** (today if there is one, otherwise the next day). The slot is shown on the message: "✅ Will post at 13:00".
- **Cancelling:** an approval can be cancelled until the video is posted, and its slot becomes free again.
- **Empty slots:** if a slot has no video, nothing is posted.
- **Expiry:** candidates that nobody approves or rejects within **48 hours** become `expired`. Their files are deleted and their buttons removed.
- **Extra searches:** `/search` runs an extra search at any time (for example when all 8 were bad).

---

## 4. Architecture

One process, `main.py`, runs:

- the **Telegram bot** (aiogram 3, long polling)
- the **scheduler** (APScheduler, `AsyncIOScheduler`, timezone `Asia/Tashkent`), with a daily search job, posting jobs at each slot, and an hourly cleanup job
- the **pipeline** (search → filter → AI check → pick → download → caption → send to review)

State lives in **SQLite** (`data/bot.db`). Video files live in `data/videos/`. Music lives in `music/`.

### 4.1 File layout

```
contentbot/
  main.py                    # start bot + scheduler; --dry-run flag
  settings.yaml              # editable settings (times, counts, keywords seed, footer, model)
  .env                       # secrets
  requirements.txt
  contentbot/
    config.py                # load .env + settings.yaml into a typed Settings object
    db.py                    # SQLite schema + data-access functions
    models.py                # Candidate dataclass, enums (Status, AudioMode, Category)
    sources/
      base.py                # Source protocol: async search(keywords, limit) -> list[Candidate]
      youtube.py             # YouTube Data API v3
      apify_common.py        # run an Apify actor with limits, return items
      tiktok.py              # Apify actor output -> Candidate
      instagram.py           # Apify actor output -> Candidate
      pinterest.py           # Apify actor output -> Candidate
    pipeline/
      discover.py            # run all sources, collect results, record per-source status
      prefilter.py           # duration / seen / min-views rules (pure functions)
      ai_check.py            # Claude scoring -> score, category, reason
      picker.py              # top-N with per-category cap (pure function)
      downloader.py          # direct URL or yt-dlp -> local mp4
      captions.py            # Claude draft + footer assembly + length check
      audio.py               # ffmpeg: original / mute / music, size limit
      run.py                 # orchestrates one full daily run
    bot/
      review.py              # send candidate, button callbacks, caption-edit flow
      commands.py            # /queue /search /status /keywords /addkw /delkw, music upload
      publisher.py           # slot assignment, posting job, missed-slot recovery
      texts.py               # all user-visible bot strings in one place
  music/                     # calm tracks (mp3/m4a)
  data/                      # bot.db, videos/, thumbs/, logs/
  tests/
```

Each unit has one job and a small interface. The `prefilter`, `picker`, footer/length logic and slot assignment are **pure functions**, so they can be tested without any network.

### 4.2 Core data type

```python
@dataclass
class Candidate:
    platform: str            # "youtube" | "tiktok" | "instagram" | "pinterest"
    platform_id: str         # the platform's own video id
    url: str                 # public page URL (shown as "Source")
    media_url: str | None    # direct video URL if the source gives one
    thumbnail_url: str | None
    title: str
    description: str         # text + hashtags
    author: str
    duration_s: float | None
    views: int | None
    published_at: datetime | None
```

---

## 5. Data model (SQLite)

**`videos`** has one row per video ever seen. This table is the "never show twice" memory.

| column | notes |
|---|---|
| id | integer PK |
| platform, platform_id | **UNIQUE together** |
| url, media_url, thumbnail_url, title, description, author, duration_s, views, published_at | from `Candidate` |
| found_at | timestamp |
| status | `found` → `filtered_out` / `scored` → `selected` → `in_review` → `approved` → `posted`; also `rejected`, `expired`, `failed` |
| ai_score (0–10), ai_category, ai_reason | from the AI check |
| caption_body | editable text **without** the footer |
| audio_mode | `original` / `mute` / `music` |
| music_track_id | FK, nullable |
| original_path, rendered_path | local files (deleted after posting / expiry) |
| review_message_id | Telegram message id in the review group |
| slot_at | assigned posting time (approved only) |
| posted_at, channel_message_id | after posting |
| error | last error text, if any |

**`keywords`**: id, text, language (`en`/`ru`/`tr`/`uz`/`zh`), kind (`query`/`hashtag`), active, last_used_at.

**`music_tracks`**: id, file_path, title, added_at, last_used_at.

**`runs`**: id, started_at, finished_at, trigger (`schedule`/`manual`), summary JSON (per-source counts and errors, counts per stage). Used by `/status`.

**`caption_edits`**: prompt_message_id → video_id. Links the "send new text" prompt to its video.

---

## 6. Finding videos

### 6.1 Keywords

- **Starting list:** the keywords are stored in the DB, seeded from `settings.yaml` with about 40 keywords and hashtags across 5 languages. Examples:
  - `sofa mechanism`, `upholstery`, `sofa bed mechanism`, `furniture foam`, `#upholstery`
  - `перетяжка дивана`, `механизм дивана`, `поролон для мебели`
  - `koltuk döşeme`, `mobilya ayağı`, `kanepe mekanizması`
  - `divan`, `mebel oyoqlari`, `yumshoq mebel`
  - `沙发机构`, `沙发面料`, `海绵`, `沙发脚`, `沙发制作`
- **Daily rotation:** each run uses **`keywords_per_run`** (default 6) keywords, choosing the least recently used, so content varies day to day.
- **Editing:** `/addkw`, `/delkw` and `/keywords` edit and show the list from the review group.

### 6.2 Sources

| Source | Method | Notes |
|---|---|---|
| YouTube | Data API v3 `search.list` (`type=video`, `videoDuration=short`, `order=relevance`), then `videos.list` for exact duration and views | 100 quota units per search; the free daily quota is 10,000, so 6 keywords use about 600 |
| TikTok | Apify actor, search by keyword or hashtag | Gives a direct video URL, cover image, duration and play count |
| Instagram | Apify actor, Reels by hashtag | Gives `videoUrl`, preview image and view count. Most fragile source |
| Pinterest | Apify actor, keyword search, video pins only | No view counts |

- **Choosing the Apify actors:** actor IDs are **set in `settings.yaml`**. During implementation we pick, for each platform, the cheapest actor that works (current examples are about $0.25–$4 per 1,000 results). Each adapter maps that actor's output to `Candidate`.
- **Cost limits:**
  - `max_results_per_source` (default 25)
  - Apify run limits (`maxItems`, plus a USD cap per run where the actor supports it)
  - a monthly budget setting (`apify_monthly_budget_usd`, default 5). Each run's Apify cost is **estimated** as `results returned × price_per_1000 / 1000` (the price of each actor is in `settings.yaml`), because pay-per-result actors may not report their charges in the run's own cost field. The estimate is saved in `runs.summary`. When the month's total reaches the budget, Apify sources are skipped and the review group gets a warning.
- **Isolation:** sources run **concurrently**. If one source fails, the error is recorded in `runs.summary`, the review group gets "⚠️ Instagram failed today", and the run continues with the other sources.

### 6.3 Free filter (no cost)

A candidate passes only if all of these are true:

- It's **not already in `videos`** (platform + id).
- `5 ≤ duration_s ≤ 120`. When the duration is unknown it is checked again after download.
- `views ≥ min_views[platform]`. Defaults: YouTube 1,000, TikTok 2,000, Instagram 1,000. Pinterest has no view counts, so this rule doesn't apply to it.

Candidates that pass are **interleaved across platforms** (one from each platform in turn, each platform's list sorted by views, unknown views last), and the first **`ai_check_limit`** (default 30) go to the AI check. Interleaving makes sure every platform, including Pinterest, which has no view counts, reaches the AI check. Every candidate, including the ones filtered out, is saved in `videos`, so it will never be seen again.

### 6.4 AI check (Claude)

- **Input per candidate:** the preview image, plus title, description, hashtags and platform. The bot downloads the preview itself, shrinks it to at most 768 px and sends it as base64. Many CDNs block hot-linking, so the bot does not send image URLs.
- **Batching:** up to 10 candidates go in one request to share the instructions.
- **Model and settings:** `claude-opus-5-5` (from settings) at effort `low` (this is a classification task). The request uses structured output (`output_config.format` with a JSON schema) and server-side refusal fallback (`fallbacks: "default"`), and the code checks `stop_reason` before reading the result.
- **Output per candidate:**
  ```json
  {"index": 3, "score": 8, "category": "mechanism", "competitor_branding": false, "reason": "sofa-bed mechanism opening demo"}
  ```
- **Categories:** `mechanism`, `foam`, `fabric`, `leather`, `legs`, `tools`, `fittings`, `upholstery_work`, `finished_furniture`, `other`.
- **Scoring rules given to Claude:**
  - High score: clearly about soft furniture, materials or upholstery work, and visually clear.
  - Low score: off-topic, mostly a talking person, a meme, or an ad with a lot of text.
  - **`competitor_branding = true`** (visible phone number, shop logo, price list or another store's name) caps the score at 3.

### 6.5 Picking

1. Sort by score.
2. Drop anything with a score below `min_ai_score` (default 6).
3. Take up to **`candidates_per_day`** (default 8), with at most **`max_per_category`** (default 2) from the same category.
4. If the download fails, take the next candidate.

---

## 7. Download

1. If `media_url` is present, download it directly with `httpx`, using browser-like headers.
2. Otherwise, or if that fails, use **yt-dlp** with format `bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b`. An optional `ytdlp_cookies_file` setting covers YouTube blocking server IPs.
3. After download, read the real duration with `ffprobe` and reject the video if it's over 120 s.
4. If every attempt fails, the video is marked `failed` and the next candidate is used.

---

## 8. Captions

### 8.1 Caption = body + footer

The **body** is short and editable. The **footer** is fixed text from `settings.yaml`, confirmed by the owner:

```
☎️ Aloqa uchun:
+998557770007
+998991330007

📍 Manzil: Toshkent shahar, Kichik Halqa Yo'li, 151A/1
🕑 Ish vaqti: 08:00-19:00

📢 Telegram: @evim_uzb
📸 Instagram: instagram.com/evim_uzb
```

- Captions are sent as **plain text** (no parse mode). @mentions and links are still clickable, and nothing a user types can break the message.
- **Length check:** Telegram allows 1,024 characters per video caption and counts them in UTF-16 units (most emojis count as 2). The bot counts the same way: `utf16_len(body + "\n\n" + footer) ≤ 1024`. If the limit is exceeded, the bot replies "Too long by N characters" and keeps the old caption.

### 8.2 AI draft (Claude)

- **Input:** the candidate's title, description, AI category and reason, plus **6 real example captions from @evim_uzb** (stored in `settings.yaml`) as style examples.
- **Model:** the same model at effort `medium`, with the same refusal fallback.
- **Rules given to Claude:**
  - **Uzbek, Latin script**, matching the channel. Russian product words are fine where the channel uses them.
  - Start with a category hashtag in the channel's style (`#mexanizm`, `#material`, `#porolon`, `#mebel_oyoqlari`, `#charm`, `#asbob`, `#furnitura` …).
  - 1–3 short sentences and 1–3 emojis.
  - End with a call to action such as "Bunday mahsulotlarni Evimda topishingiz mumkin!".
  - **Never** invent prices, sizes, model names or brand names, and never name another company.
  - **Never claim the video was filmed at Evim.** The message is "you can find such products at Evim".
  - Body length at most 500 characters.
- **If Claude fails**, the body is empty and the review message shows "✏️ AI unavailable, please write a caption". The video can't be approved while the body is empty.

---

## 9. Review group

### 9.1 Review message

There is one message per candidate: `sendVideo` with caption = body + footer, exactly as it will appear in the channel. Buttons (all bot texts are in **Uzbek Latin**, §9.5):

```
[🔊 Asl ovoz ✓] [🔇 Ovozsiz] [🎵 Musiqa]
[✏️ Matnni tahrirlash]  [🔀 Boshqa musiqa]   ← "Boshqa musiqa" only when 🎵 is active
[✅ Tasdiqlash]           [❌ Rad etish]
[🔗 Manba: TikTok · 8/10 · mexanizm]          ← URL button to the original video
```

In the rest of this spec, buttons are called by their English meaning: Original, No sound, Music, Edit caption, Another track, Approve, Reject, Cancel.

### 9.2 Actions

| Button | Effect |
|---|---|
| 🔊 / 🔇 / 🎵 | Render the new sound (§10) from `original_path`, then `editMessageMedia` to replace the video in the same message. A ✓ marks the active mode. A "⏳" toast shows while rendering. |
| 🔀 Another track | Pick a different random track (not the current or last-used one) and re-render. |
| ✏️ Edit caption | The bot replies with a prompt containing the **current body in a `<code>` block** (tap to copy) and a `ForceReply`. The reviewer replies with the new text. The bot checks the length, saves it, and edits the caption of the review message. The link between prompt and video is stored in `caption_edits`, so several people can edit at the same time. |
| ✅ Approve | Requires a non-empty body. Assigns the earliest free slot. The buttons change to "✅ Will post at 13:00 · [↩️ Cancel]". |
| ↩️ Cancel | Frees the slot, sets the status back to `in_review` and restores the full buttons. |
| ❌ Reject | Status becomes `rejected`, the files are deleted and the review message is deleted. |

- **Who can act:** any member of the review group (`review_chat_id` in `.env`). Button presses from other chats are ignored.
- **Double presses:** each action is idempotent. A press on a video whose status no longer allows that action gets a "Already handled" toast.

### 9.3 Commands (review group only)

| Command | Effect |
|---|---|
| `/queue` | Lists approved videos with slot times |
| `/search` | Runs the pipeline now (only one run at a time) |
| `/status` | Last run time, per-source result and errors, queue size, number of music tracks, estimated Apify spend this month |
| `/keywords` | Lists active keywords by language |
| `/addkw <text>` / `/delkw <text>` | Add or remove a keyword. A leading `#` means hashtag |
| Audio file sent to the group | Bot asks "Add to music library?" [Yes] [No] |

### 9.4 Required bot setup

- The bot must be an **admin in the review group**, so it receives audio uploads and can delete messages.
- The bot must be an **admin in the channel** with "Post messages".
- Until the owner makes it an admin of @evim_uzb, `channel_id` points to a **private test channel**.

### 9.5 Language of the bot interface

All bot texts are in **Uzbek (Latin script)**, kept together in `bot/texts.py` so wording can be changed in one place. Main texts:

| Meaning | Uzbek text |
|---|---|
| Original / No sound / Music | 🔊 Asl ovoz / 🔇 Ovozsiz / 🎵 Musiqa |
| Edit caption / Another track | ✏️ Matnni tahrirlash / 🔀 Boshqa musiqa |
| Approve / Reject / Cancel | ✅ Tasdiqlash / ❌ Rad etish / ↩️ Bekor qilish |
| Will post at 13:00 | ✅ 13:00 da joylanadi |
| Posted at 13:00 | 📢 13:00 da joylandi |
| Send the new text (reply to this message) | ✏️ Yangi matnni yuboring (shu xabarga javob qilib) |
| Too long by N characters | ⚠️ Matn N belgiga uzun |
| Instagram failed today | ⚠️ Bugun Instagram ishlamadi |
| Only N good videos today | Bugun faqat N ta yaxshi video topildi |
| Add to music library? | Musiqa kutubxonasiga qo'shilsinmi? [Ha] [Yo'q] |
| A search is already running | Qidiruv allaqachon ishlayapti |
| Already handled | Bu video allaqachon ko'rib chiqilgan |
| AI unavailable, please write a caption | ✏️ AI ishlamadi, matnni o'zingiz yozing |
| Rendering… | ⏳ Tayyorlanmoqda… |

Command names stay short Latin words (`/queue`, `/search`, `/status`, `/keywords`, `/addkw`, `/delkw`), because Telegram only allows Latin letters in command names. Their descriptions in Telegram's command menu are in Uzbek.

---

## 10. Sound processing (ffmpeg)

All outputs are MP4: H.264 `yuv420p` + AAC, `-movflags +faststart`. If the input video is already H.264, the video stream is copied (`-c:v copy`).

| Mode | Processing |
|---|---|
| `original` | Keep the source. Transcode only when the codecs are not H.264/AAC. |
| `mute` | Replace the audio with a silent stereo track (`anullsrc`, `-shortest`). A real silent track, not a missing one, keeps Telegram showing it as a video and not a GIF. |
| `music` | `-stream_loop -1` on the track; map the video from the clip and the audio from the track; `-t <video duration>`; `afade` in 0.5 s, `afade` out over the last 1.5 s, `loudnorm` (I=-16). |

- **Size limit:** if the result is over **49 MB** (the Bot API upload limit is 50 MB), re-encode with a target bitrate calculated from the duration.
- **Track choice:** random, excluding the most recently used track. `last_used_at` is updated.
- **Starter music:** about 10 calm tracks from **Pixabay Music** (license allows commercial use without credit), downloaded during setup **after the owner agrees**.

---

## 11. Posting

- **Slots:** `post_times` default to `["10:00", "13:00", "16:00", "19:00"]`.
- **Slot assignment** (a pure function): given the current time and the slots already taken, return the earliest future slot not taken, today or later.
- **Posting job:** at each slot time, find the `approved` video with `slot_at` equal to this slot, `sendVideo` it to `channel_id` with the final caption, and save `channel_message_id` and `posted_at`. The review message then changes to "📢 Posted at 13:00" with a link to the post. The video files are deleted.
- **Retries:** 3 attempts, 1 min, 2 min and 4 min apart. If all fail, the video is moved to the next free slot and the review group gets a warning.
- **Missed slots:** on startup, any `approved` video whose `slot_at` has already passed (the bot was down) is moved to the next free slot.

---

## 12. Settings

**`.env` (secrets)**

```
TELEGRAM_BOT_TOKEN=
REVIEW_CHAT_ID=          # private review group id (negative number)
CHANNEL_ID=              # test channel first, later @evim_uzb
ANTHROPIC_API_KEY=
YOUTUBE_API_KEY=
APIFY_TOKEN=
```

**`settings.yaml` (owner-editable)**

```yaml
timezone: Asia/Tashkent
search_time: "07:00"
post_times: ["10:00", "13:00", "16:00", "19:00"]
candidates_per_day: 8
max_per_category: 2
review_expiry_hours: 48
duration: {min_s: 5, max_s: 120}
min_views: {youtube: 1000, tiktok: 2000, instagram: 1000}
keywords_per_run: 6
youtube_per_keyword: 5
ai_check_limit: 30
min_ai_score: 6
ai:
  model: claude-opus-5-5
apify:
  monthly_budget_usd: 5
  actors:   # chosen during planning; ~ $4.40/month at these limits
    tiktok:    {actor_id: clockworks/tiktok-scraper,       price_per_1000: 1.70, max_results: 20}
    instagram: {actor_id: apify/instagram-hashtag-scraper, price_per_1000: 2.60, max_results: 20}
    pinterest: {actor_id: cirkit/pinterest-pins-scraper,   price_per_1000: 2.00, max_results: 30}
ytdlp_cookies_file: null
footer: |            # full text exactly as in §8.1
  ☎️ Aloqa uchun:
  +998557770007
  +998991330007
  # (rest of §8.1 footer)
caption_examples:    # 6 real captions copied from @evim_uzb posts
  - "#mexanizm Eng eksklyuziv mexanizmlar Evimda! ..."
seed_keywords:       # ~40 entries, see §6.1
  en: ["sofa mechanism", "upholstery", "#upholstery"]
  ru: ["перетяжка дивана", "механизм дивана"]
  tr: ["koltuk döşeme", "mobilya ayağı"]
  uz: ["mebel oyoqlari", "yumshoq mebel"]
  zh: ["沙发机构", "沙发制作"]
```

---

## 13. Error handling (summary)

| Failure | Behaviour |
|---|---|
| A source errors or times out (YouTube 90 s, Apify sources 5.5 min) | Skip it, record it in `runs`, warn in the review group, continue |
| Apify budget reached | Skip Apify sources until the next month, warn once |
| Fewer than 8 good candidates | Send what there is, plus "Only N good videos today" |
| Claude unavailable during the AI check | Rank by views, mark the messages "AI unavailable" |
| Claude unavailable during captioning | Empty body; approval blocked until a human writes one |
| Download fails | Mark `failed`, take the next candidate |
| ffmpeg fails | Toast "Sound change failed", keep the previous version |
| Posting fails | 3 retries, then move to the next slot and warn |
| Two runs at once | A lock: `/search` replies "A search is already running" |
| Process restart | All state is in SQLite, so the review buttons keep working (callback data contains the video id) and missed slots are re-planned |

**Logs:** `data/logs/bot.log` (rotating, 5 × 5 MB), plus `nohup.out`.

---

## 14. Testing

- **pytest unit tests** (no network):
  - `prefilter` rules
  - `picker` (score threshold, per-category cap)
  - footer assembly, HTML escaping and the 1,024-character check
  - slot assignment, including rollover to tomorrow and cancel/free
  - missed-slot re-planning
  - keyword rotation
  - DB layer against a temporary SQLite file
- **Source adapters:** each one is tested against a **saved sample JSON** response from its API or actor.
- **Claude calls:** tested with a fake client returning fixed JSON, to check parsing and fallbacks (refusal, error, malformed output).
- **ffmpeg:** tests generate a 3-second test clip (`testsrc` + `sine`) and check each mode with `ffprobe` (audio present or silent, duration kept, codecs, size).
- **Dry run:** `python main.py --dry-run` runs a real search + AI check + caption drafting and prints a table. Nothing is sent to Telegram and nothing is marked as seen.
- **Live test:** run against the test channel and review group for a few days before switching `CHANNEL_ID` to @evim_uzb.

---

## 15. Deployment

- **Server:** Linux, Python ≥ 3.11, ffmpeg ≥ 5 (`apt install ffmpeg`).
- **Install and start:**
  ```
  python3 -m venv .venv && . .venv/bin/activate
  pip install -r requirements.txt
  cp .env.example .env   # fill in keys
  nohup python main.py > nohup.out 2>&1 &
  ```
- **Optional auto-restart:** a `systemd` unit file (`deploy/contentbot.service`) that restarts on crash and on reboot.
- **Weekly maintenance:** `pip install -U yt-dlp`. A cron line is provided.
- **Backup:** copy `data/bot.db`, `settings.yaml` and `music/`.

---

## 16. Costs (estimates)

| Item | Monthly |
|---|---|
| Server | already owned |
| YouTube Data API | free |
| Apify | $0 on the free $5 credit; $29 plan if more is needed |
| Claude (Opus 5.5: ~30 checks + ~8 captions/day) | about $10–20; about half with Sonnet 5.5 |
| Telegram | free |

---

## 17. Risks

- **Scrapers break.** Platforms change their sites. Mitigation: one adapter per source, actor IDs set in settings, failures isolated and reported in the review group.
- **YouTube blocks downloads from server IPs.** Mitigation: the cookies-file setting. Worst case, YouTube candidates are skipped.
- **Copyright.** The videos belong to other people. Mitigation: a human approves every post, the source link is stored forever for every post, and captions never claim the video is Evim's own. The owner accepts the remaining risk of takedown requests.
- **AI mistakes.** Mitigation: every post is checked by a human before publishing.

---

## 18. Out of scope (v1)

WeChat, Douyin and Facebook sources · removing watermarks or logos · cross-posting to Instagram · post performance analytics · AI "rewrite caption" button · detecting the same video re-uploaded on different platforms · web dashboard · multiple channels.
