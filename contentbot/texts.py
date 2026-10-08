"""Every message and button text the bot shows (Uzbek, Latin script). Edit wording here only."""

# Review buttons
BTN_ORIGINAL = "🔊 Asl ovoz"
BTN_MUTE = "🔇 Ovozsiz"
BTN_MUSIC = "🎵 Musiqa"
BTN_EDIT = "✏️ Matnni tahrirlash"
BTN_TRACK = "🔀 Boshqa musiqa"
BTN_APPROVE = "✅ Tasdiqlash"
BTN_REJECT = "❌ Rad etish"
BTN_CANCEL = "↩️ Bekor qilish"
BTN_YES = "Ha"
BTN_NO = "Yo'q"
CHECK = " ✓"
SOURCE = "🔗 Manba: {platform} · {score} · {category}"
NO_SCORE = "AI baholamagan"

# Status labels on review messages
WILL_POST = "✅ {when} da joylanadi"
POSTED = "📢 {when} da joylandi"
EXPIRED = "⌛ Muddati o'tdi"
REJECTED = "❌ Rad etildi"

# Short pop-up answers to button presses
RENDERING = "⏳ Tayyorlanmoqda…"
ALREADY = "Bu video allaqachon ko'rib chiqilgan"
NEED_CAPTION = "✏️ Avval matn yozing"
MUSIC_EMPTY = "🎵 Musiqa kutubxonasi bo'sh"
SOUND_FAILED = "⚠️ Ovozni o'zgartirib bo'lmadi"
APPROVED_TOAST = "✅ Tasdiqlandi"

# Caption editing
EDIT_PROMPT = "✏️ Yangi matnni yuboring (shu xabarga javob qilib)."
EDIT_CURRENT = "Hozirgi matn (bosib nusxa oling):"
TOO_LONG = "⚠️ Matn {n} belgiga uzun"
AI_FAILED_BODY = "✏️ AI ishlamadi, matnni o'zingiz yozing"

# Messages from the daily search and posting
SOURCE_FAILED = "⚠️ Bugun {source} ishlamadi"
ONLY_N = "Bugun faqat {n} ta yaxshi video topildi"
AI_UNAVAILABLE = "⚠️ AI ishlamadi: videolar ko'rishlar soni bo'yicha tanlandi"
RUN_FAILED = "⚠️ Qidiruvda xatolik yuz berdi, loglarni tekshiring"
APIFY_BUDGET = "⚠️ Apify oylik limiti tugadi. TikTok, Instagram va Pinterest keyingi oygacha to'xtatildi"
POST_FAILED = "⚠️ Kanalga joylab bo'lmadi. Keyingi urinish: {when}"
POST_UNCERTAIN = (
    "⚠️ Kanalga joylashda aloqa uzildi. Video kanalga chiqqanini tekshiring: "
    "chiqmagan bo'lsa, qaytadan tasdiqlang."
)

# Commands
SEARCH_STARTED = "🔎 Qidiruv boshlandi"
SEARCH_RUNNING = "Qidiruv allaqachon ishlayapti"
QUEUE_EMPTY = "Navbat bo'sh"
QUEUE_TITLE = "📋 Navbat:"
KW_TITLE = "🔑 Kalit so'zlar:"
KW_ADDED = "✅ Kalit so'z qo'shildi: {text}"
KW_EXISTS = "Bu kalit so'z allaqachon bor"
KW_REMOVED = "🗑 O'chirildi: {text}"
KW_NOT_FOUND = "Bunday kalit so'z topilmadi"
KW_USAGE_ADD = "Masalan: /addkw divan mexanizmi"
KW_USAGE_DEL = "Masalan: /delkw divan mexanizmi"
MUSIC_ASK = "Musiqa kutubxonasiga qo'shilsinmi?"
MUSIC_ADDED = "✅ Qo'shildi: {title}"
MUSIC_TOO_BIG = "⚠️ Fayl juda katta (20 MB gacha bo'lishi kerak)"
NEVER = "hali bo'lmagan"
ERROR_WORD = "xato"
STATUS = (
    "📊 Holat\n"
    "Oxirgi qidiruv: {last_run}\n"
    "Manbalar: {sources}\n"
    "Ko'rib chiqilmoqda: {in_review}\n"
    "Navbatda: {queued}\n"
    "Musiqalar: {tracks}\n"
    "Apify (shu oy): ${spent:.2f} / ${budget:.2f}"
)

COMMANDS = {
    "queue": "Joylanadigan videolar navbati",
    "search": "Hozir qo'shimcha qidiruv",
    "status": "Bot holati",
    "keywords": "Kalit so'zlar ro'yxati",
    "addkw": "Kalit so'z qo'shish",
    "delkw": "Kalit so'zni o'chirish",
}

PLATFORM_NAMES = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "pinterest": "Pinterest"}

CATEGORY_NAMES = {
    "mechanism": "mexanizm",
    "foam": "porolon",
    "fabric": "mato",
    "leather": "charm",
    "legs": "oyoqlar",
    "tools": "asboblar",
    "fittings": "furnitura",
    "upholstery_work": "obivka",
    "finished_furniture": "tayyor mebel",
    "other": "boshqa",
}
