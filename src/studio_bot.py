"""XELVORN ovoz studiyasi — shaxsiy Telegram TTS bot (aiogram 3 + Navoiy TTS)."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import shutil
import sys
import time
import uuid
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BotCommand, CallbackQuery, FSInputFile,
                           InlineKeyboardButton, InlineKeyboardMarkup, Message)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("studio")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_ID", "0").strip() or 0)
DATA_DIR = Path(os.environ.get("STUDIO_DIR", "/content/drive/MyDrive/ovoz_studio"))
SAVE_OUTPUTS = os.environ.get("SAVE_OUTPUTS", "1") == "1"

VOICES_DIR = DATA_DIR / "voices"
OUT_DIR = DATA_DIR / "out"
SETTINGS_FILE = DATA_DIR / "settings.json"
LEXICON_FILE = DATA_DIR / "lugat.json"
TMP_DIR = Path("/content/studio_tmp")
for d in (VOICES_DIR, OUT_DIR, TMP_DIR):
    d.mkdir(parents=True, exist_ok=True)

if not TOKEN:
    sys.exit("TELEGRAM_BOT_TOKEN topilmadi (Colab Secrets).")

import tts_engine as te  # noqa: E402  (og'ir import — tokendan keyin)

SPEEDS = [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3]
PITCHES = list(range(-4, 5))
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".aac", ".mp4", ".webm"}
NAME_MAX = 24  # callback_data 64 bayt chegarasi uchun

# ---------------------------------------------------------------- sozlamalar

DEFAULTS = {"voice": None, "emotion": "calm", "speed": 1.0, "pitch": 0, "format": "mp3", "srt": True}


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self.data = dict(DEFAULTS)
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except Exception as e:  # buzilgan fayl — standartlar bilan davom etamiz
                log.warning("settings.json o'qilmadi: %s", e)
        if self.data["emotion"] not in te.EMOTIONS:
            self.data["emotion"] = "calm"

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def set(self, **kw):
        self.data.update(kw)
        self.save()


settings = Settings(SETTINGS_FILE)


def list_voices() -> list[str]:
    return sorted(p.stem for p in VOICES_DIR.glob("*.wav"))


def voice_path(name: str) -> Path:
    return VOICES_DIR / f"{name}.wav"


def active_voice() -> str | None:
    v = settings.data.get("voice")
    if v and voice_path(v).exists():
        return v
    voices = list_voices()
    if voices:
        settings.set(voice=voices[0])
        return voices[0]
    return None


def emotion_suffixes(emotion: str) -> list[str]:
    """[angry] uchun: ["jahldor", "angry"] — ovoz_jahldor.wav yoki ovoz_angry.wav."""
    uz = re.sub(r"[ʻʼ'`’‘]", "", te.EMOTIONS[emotion]["uz"].lower())
    return [uz, emotion]


def emotion_voice(voice: str, emotion: str) -> str:
    """Kayfiyat namunasi bo'lsa (masalan morf_jahldor) shuni, bo'lmasa asosiy ovozni qaytaradi."""
    def norm(s: str) -> str:  # katta-kichik harf, _ - bo'sh joy, apostrof farq qilmaydi
        return re.sub(r"[\W_]+", "", s.lower())

    by_norm = {norm(v): v for v in list_voices()}
    base = norm(voice)
    for suffix in emotion_suffixes(emotion):
        for key in (base + norm(suffix), norm(suffix) + base):
            if key in by_norm and by_norm[key] != voice:
                return by_norm[key]
    return voice


def clean_name(raw: str) -> str:
    name = re.sub(r"[ʻʼ'`’‘]", "", raw.strip())
    name = re.sub(r"[^\w\-]+", "_", name).strip("_")
    while len(name.encode()) > NAME_MAX:
        name = name[:-1]
    return name


# ---------------------------------------------------------------- klaviaturalar

def btn(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def panel_text() -> str:
    s = settings.data
    v = active_voice()
    return ("🎛 <b>Studiya sozlamalari</b>\n"
            f"🗣 Ovoz: <b>{html.escape(v) if v else '— (ovozli xabar yuboring)'}</b>\n"
            f"🎭 Kayfiyat: <b>{te.emotion_label(s['emotion'])}</b>\n"
            f"⏩ Tezlik: <b>{s['speed']:.1f}×</b>\n"
            f"🎚 Pitch: <b>{s['pitch']:+d}</b> semiton\n"
            f"💾 Format: <b>{s['format'].upper()}</b>\n"
            f"📄 Subtitr (.srt): <b>{'yoqilgan' if s.get('srt', True) else 'o‘chiq'}</b>\n\n"
            "Matn yozing → audio. Teglar: <code>[whisper] matn [calm] matn</code>")


def panel_kb():
    fmt = settings.data["format"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("🗣 Ovoz", "m:voice"), btn("🎭 Kayfiyat", "m:emo")],
        [btn("⏩ Tezlik", "m:speed"), btn("🎚 Pitch", "m:pitch")],
        [btn(f"💾 Format: {fmt.upper()} → {'WAV' if fmt == 'mp3' else 'MP3'}", "f:toggle")],
        [btn("📄 Subtitr: " + ("✅" if settings.data.get("srt", True) else "❌"), "st:toggle")],
        [btn("🗑 Ovozni o'chirish", "m:del")],
    ])


def back_row():
    return [btn("⬅️ Orqaga", "m:main")]


def grid(buttons, per_row):
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def voice_kb():
    cur = active_voice()
    bs = [btn(("✅ " if v == cur else "") + v, f"v:{v}") for v in list_voices()]
    return InlineKeyboardMarkup(inline_keyboard=grid(bs, 2) + [back_row()])


def delete_kb():
    bs = [btn(f"🗑 {v}", f"d:{v}") for v in list_voices()]
    return InlineKeyboardMarkup(inline_keyboard=grid(bs, 2) + [back_row()])


def emo_kb():
    cur = settings.data["emotion"]
    bs = [btn(("✅ " if k == cur else "") + te.EMOTIONS[k]["uz"], f"e:{k}") for k in te.EMOTION_KEYS]
    return InlineKeyboardMarkup(inline_keyboard=grid(bs, 2) + [back_row()])


def speed_kb():
    cur = settings.data["speed"]
    bs = [btn(("✅" if abs(x - cur) < 1e-6 else "") + f"{x:.1f}×", f"s:{x}") for x in SPEEDS]
    return InlineKeyboardMarkup(inline_keyboard=grid(bs, 4) + [back_row()])


def pitch_kb():
    cur = settings.data["pitch"]
    bs = [btn(("✅" if x == cur else "") + f"{x:+d}", f"p:{x}") for x in PITCHES]
    return InlineKeyboardMarkup(inline_keyboard=grid(bs, 5) + [back_row()])


# ---------------------------------------------------------------- holatlar

class NewVoice(StatesGroup):
    naming = State()


owner = Router(name="owner")
owner.message.filter(F.from_user.id == OWNER_ID)
owner.callback_query.filter(F.from_user.id == OWNER_ID)
stranger = Router(name="stranger")

engine: te.Engine | None = None
job_lock = asyncio.Lock()
pending_jobs = 0

HELP = (
    "🎙 <b>XELVORN ovoz studiyasi</b>\n\n"
    "• <b>Matn</b> yuboring → tanlangan ovoz va kayfiyatda audio.\n"
    "• <b>Teglar</b>: <code>[whisper] sekin gap [happy] quvnoq gap</code> — har bo'lak o'z kayfiyatida, "
    "0.4 s pauza bilan yopishtiriladi.\n"
    "• <b>.txt fayl</b> → butun skript bitta audioga.\n"
    "• <b>Ovozli xabar / audio fayl</b> (5–20 s) → yangi ovoz namunasi, keyin nom so'raladi.\n\n"
    "Kayfiyat teglari: " + ", ".join(f"<code>[{k}]</code>" for k in te.EMOTION_KEYS) +
    " (o'zbekcha nomlar ham bo'ladi: <code>[pichirlab]</code>).\n\n"
    "• Audio ostidagi <b>🔁</b> tugmasi — yoqmagan bo'lakni qayta yaratish. Har audio bilan <b>.srt subtitr</b> keladi.\n\n"
    "Buyruqlar: /sozlamalar, /ovozlar, /kayfiyatlar, /matn, /lugat, /bekor\n\n"
    "⚠️ Faqat o'z ovozingiz yoki egasining yozma roziligi bor ovozlar."
)


@owner.message(CommandStart())
@owner.message(Command("help", "yordam"))
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(HELP)
    await m.answer(panel_text(), reply_markup=panel_kb())


@owner.message(Command("sozlamalar", "settings"))
async def cmd_settings(m: Message):
    await m.answer(panel_text(), reply_markup=panel_kb())


@owner.message(Command("ovozlar", "voices"))
async def cmd_voices(m: Message):
    if not list_voices():
        await m.answer("Hali ovoz yo'q. Ovozli xabar (5–20 s) yuboring.")
        return
    await m.answer("🗣 Ovozni tanlang:", reply_markup=voice_kb())


@owner.message(Command("kayfiyatlar", "emotions"))
async def cmd_emotions(m: Message):
    lines = [f"<code>[{k}]</code> — {te.EMOTIONS[k]['uz']}" for k in te.EMOTION_KEYS]
    await m.answer("🎭 Kayfiyatlar:\n" + "\n".join(lines), reply_markup=emo_kb())


MATN_HELP = (
    "📝 <b>Namuna matni</b> — namunada aytilgan so'zlar. Berilsa, model namunaning ohangini ham "
    "o'rganadi (jahl, pichirlash kuchliroq chiqadi).\n\n"
    "<code>/matn oddiy_jahldor Yetar! Necha marta aytaman…</code> — saqlash\n"
    "<code>/matn oddiy_jahldor</code> — ko'rish\n"
    "<code>/matn oddiy_jahldor -</code> — o'chirish\n\n"
    "Matn namunada aytilganiga iloji boricha aynan mos bo'lsin."
)


def find_voice(raw: str) -> str | None:
    def norm(x: str) -> str:
        return re.sub(r"[\W_]+", "", x.lower())
    want = norm(raw)
    return next((v for v in list_voices() if norm(v) == want), None)


@owner.message(Command("matn"))
async def cmd_matn(m: Message):
    parts = (m.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await m.answer(MATN_HELP)
        return
    name = find_voice(parts[1])
    if not name:
        await m.answer(f"«{html.escape(parts[1])}» ovozi topilmadi. /ovozlar")
        return
    tpath = te.transcript_path(voice_path(name))
    if len(parts) == 2:
        cur = te.read_transcript(voice_path(name))
        await m.answer(f"📝 <b>{html.escape(name)}</b>: " + (html.escape(cur) if cur else "matn yo'q"))
    elif parts[2].strip() == "-":
        tpath.unlink(missing_ok=True)
        await m.answer(f"🗑 «{html.escape(name)}» matni o'chirildi.")
    else:
        tpath.write_text(parts[2].strip(), encoding="utf-8")
        await m.answer(f"💾 «{html.escape(name)}» uchun namuna matni saqlandi.")


@owner.message(Command("bekor", "cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("tmp"):
        Path(data["tmp"]).unlink(missing_ok=True)
    await state.clear()
    await m.answer("Bekor qilindi.")


# ---------------------------------------------------------------- tugmalar

async def show(cq: CallbackQuery, text: str, kb):
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass  # "message is not modified"
    await cq.answer()


@owner.callback_query(F.data.startswith("m:"))
async def cb_menu(cq: CallbackQuery):
    what = cq.data[2:]
    if what == "main":
        await show(cq, panel_text(), panel_kb())
    elif what in ("voice", "del"):
        if not list_voices():
            await cq.answer("Hali ovoz yo'q — ovozli xabar yuboring.", show_alert=True)
            return
        if what == "voice":
            await show(cq, "🗣 Ovozni tanlang:", voice_kb())
        else:
            await show(cq, "🗑 Qaysi ovozni o'chiramiz?", delete_kb())
    elif what == "emo":
        await show(cq, "🎭 Standart kayfiyat (tegsiz matn uchun):", emo_kb())
    elif what == "speed":
        await show(cq, "⏩ Tezlik:", speed_kb())
    elif what == "pitch":
        await show(cq, "🎚 Pitch (semiton):", pitch_kb())


@owner.callback_query(F.data.startswith("v:"))
async def cb_voice(cq: CallbackQuery):
    name = cq.data[2:]
    if not voice_path(name).exists():
        await cq.answer("Bu ovoz topilmadi.", show_alert=True)
        return
    settings.set(voice=name)
    await show(cq, panel_text(), panel_kb())


@owner.callback_query(F.data.startswith("d:"))
async def cb_delete_ask(cq: CallbackQuery):
    name = cq.data[2:]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("✅ Ha, o'chir", f"dy:{name}"), btn("❌ Yo'q", "m:del")]])
    await show(cq, f"«{html.escape(name)}» ovozini o'chiramizmi?", kb)


@owner.callback_query(F.data.startswith("dy:"))
async def cb_delete(cq: CallbackQuery):
    name = cq.data[3:]
    voice_path(name).unlink(missing_ok=True)
    te.transcript_path(voice_path(name)).unlink(missing_ok=True)
    if settings.data.get("voice") == name:
        settings.set(voice=None)
    await cq.answer(f"«{name}» o'chirildi.")
    await show(cq, panel_text(), panel_kb())


@owner.callback_query(F.data.startswith("e:"))
async def cb_emotion(cq: CallbackQuery):
    key = cq.data[2:]
    if key in te.EMOTIONS:
        settings.set(emotion=key)
    await show(cq, panel_text(), panel_kb())


@owner.callback_query(F.data.startswith("s:"))
async def cb_speed(cq: CallbackQuery):
    val = float(cq.data[2:])
    if 0.7 <= val <= 1.3:
        settings.set(speed=val)
    await show(cq, panel_text(), panel_kb())


@owner.callback_query(F.data.startswith("p:"))
async def cb_pitch(cq: CallbackQuery):
    val = int(cq.data[2:])
    if -4 <= val <= 4:
        settings.set(pitch=val)
    await show(cq, panel_text(), panel_kb())


@owner.callback_query(F.data == "st:toggle")
async def cb_srt(cq: CallbackQuery):
    settings.set(srt=not settings.data.get("srt", True))
    await show(cq, panel_text(), panel_kb())


@owner.callback_query(F.data == "f:toggle")
async def cb_format(cq: CallbackQuery):
    settings.set(format="wav" if settings.data["format"] == "mp3" else "mp3")
    await show(cq, panel_text(), panel_kb())


# ---------------------------------------------------------------- yangi ovoz

def _is_audio_document(m: Message) -> bool:
    d = m.document
    if not d:
        return False
    ext = Path(d.file_name or "").suffix.lower()
    return (d.mime_type or "").startswith(("audio/", "video/")) or ext in AUDIO_EXT


@owner.message(F.voice | F.audio | F.video_note | F.video | F.func(_is_audio_document))
async def on_voice_sample(m: Message, state: FSMContext, bot: Bot):
    media = m.voice or m.audio or m.video_note or m.video or m.document
    if getattr(media, "file_size", 0) and media.file_size > 20 * 1024 * 1024:
        await m.answer("Fayl 20 MB dan katta — Telegram bot yuklab ololmaydi. Qisqaroq yuboring.")
        return
    status = await m.answer("⏳ Namuna tayyorlanmoqda…")
    raw = TMP_DIR / f"in_{uuid.uuid4().hex}"
    ready = TMP_DIR / f"ref_{uuid.uuid4().hex}.wav"
    try:
        await bot.download(media, destination=raw)
        duration = await asyncio.to_thread(te.prepare_reference, raw, ready)
    except ValueError as e:
        await status.edit_text(f"❌ {e}")
        return
    except Exception as e:
        log.exception("namuna xatosi")
        await status.edit_text(f"❌ Audio o'qilmadi: {html.escape(str(e))[:300]}")
        return
    finally:
        raw.unlink(missing_ok=True)
    old = (await state.get_data()).get("tmp")
    if old:
        Path(old).unlink(missing_ok=True)
    await state.set_state(NewVoice.naming)
    await state.update_data(tmp=str(ready))
    cut = " (birinchi 20 s olindi)" if duration >= te.REF_MAX_S - 0.05 else ""
    await status.edit_text(f"✅ Namuna tayyor: {duration:.1f} s{cut}.\n"
                           "Bu ovozga <b>nom</b> yozing (masalan: <code>yogon</code>, <code>hikoyachi</code>). "
                           "/bekor — bekor qilish.")


@owner.message(StateFilter(NewVoice.naming), F.text & ~F.text.startswith("/"))
async def on_voice_name(m: Message, state: FSMContext):
    name = clean_name(m.text)
    if not name:
        await m.answer("Nom bo'sh chiqdi. Harf/raqamlardan iborat nom yozing.")
        return
    if voice_path(name).exists():
        await m.answer(f"«{html.escape(name)}» band. Boshqa nom yozing.")
        return
    tmp = (await state.get_data()).get("tmp")
    if not tmp or not Path(tmp).exists():
        await state.clear()
        await m.answer("Namuna topilmadi, ovozli xabarni qayta yuboring.")
        return
    shutil.move(tmp, voice_path(name))
    await state.clear()
    settings.set(voice=name)
    await m.answer(f"💾 «{html.escape(name)}» saqlandi va tanlandi.")
    await m.answer(panel_text(), reply_markup=panel_kb())


# ---------------------------------------------------------------- generatsiya

def _slug(text: str) -> str:
    words = re.sub(r"\[[^\]]*\]", " ", text)
    words = re.sub(r"[^\w\s-]", "", words).split()[:5]
    return "_".join(words)[:40] or "audio"


async def safe_edit(msg: Message, text: str):
    try:
        await msg.edit_text(text)
    except Exception:
        pass


# Oxirgi ishlar xotirada (bo'lakni qayta yaratish uchun). Colab o'chsa yo'qoladi.
JOBS: "OrderedDict[str, dict]" = OrderedDict()
MAX_JOBS = 3


def remember(job: dict):
    JOBS[job["id"]] = job
    JOBS.move_to_end(job["id"])
    while len(JOBS) > MAX_JOBS:
        JOBS.popitem(last=False)


def result_kb(job_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[[btn("🔁 Bo'lakni qayta yaratish", f"rl:{job_id}")]])


async def run_job(m: Message, job: dict, only: set | None = None):
    """Generatsiya (hammasi yoki faqat `only` bo'laklari) → yopishtirish → audio + .srt yuborish."""
    global pending_jobs
    s, voice, items = job["s"], job["voice"], job["items"]
    pending_jobs += 1
    ahead = pending_jobs - 1
    head = f"🔁 {', '.join(str(i + 1) for i in sorted(only))}-bo'lak qayta yaratilmoqda" if only else "🎙 Generatsiya"
    status = await m.answer((f"⏳ Navbatda: oldinda {ahead} ta ish." if ahead else "⏳ Boshlandi…") + job.get("warn", ""))
    loop = asyncio.get_running_loop()
    last = [0.0]

    def progress(i, n):
        now = time.monotonic()
        if n > 1 and (now - last[0] > 3 or i == 0):
            last[0] = now
            asyncio.run_coroutine_threadsafe(
                safe_edit(status, f"{head}: {i + 1}/{n} bo'lak…{job.get('warn', '')}"), loop)

    job["version"] += 1
    ver = f"_v{job['version']}" if job["version"] > 1 else ""
    name = f"{job['stamp']}_{job['title']}_{voice}{ver}.{s['format']}"
    out = (OUT_DIR if SAVE_OUTPUTS else TMP_DIR) / name
    try:
        async with job_lock:
            t0 = time.time()
            job["pieces"], flagged, retried = await asyncio.to_thread(
                engine.synth_items, items, lambda e: voice_path(emotion_voice(voice, e)), s["speed"],
                job["seeds"], only, load_lexicon(), progress, job.get("pieces"))
            duration, timeline = await asyncio.to_thread(te.assemble, items, job["pieces"], s["pitch"], s["format"], out)
            took = time.time() - t0
    except Exception as e:
        log.exception("generatsiya xatosi")
        await safe_edit(status, f"❌ Xato: {html.escape(str(e))[:500]}")
        return
    finally:
        pending_jobs -= 1
    remember(job)
    flags = job.setdefault("flags", set())
    flags -= only or set(range(len(items)))
    flags |= flagged

    emos = sorted({e for e, _, _ in items}, key=te.EMOTION_KEYS.index)
    variants = sorted({emotion_voice(voice, e) for e in emos} - {voice})
    variants = [v + (" 📝" if te.read_transcript(voice_path(v)) else "") for v in variants]
    caption = (f"🗣 {html.escape(voice)} · 🎭 {', '.join(te.EMOTIONS[e]['uz'] for e in emos)} · "
               f"⏩ {s['speed']:.1f}× · 🎚 {s['pitch']:+d}\n"
               f"⏱ {duration:.1f} s audio, {took:.0f} s da · {len(items)} bo'lak"
               + (f"\n🎭 Kayfiyat namunalari: {html.escape(', '.join(variants))}" if variants else "")
               + (f"\n🔁 Qayta yaratildi: {', '.join(str(i + 1) for i in sorted(only))}-bo'lak" if only else "")
               + (f"\n♻️ Avtomatik tuzatildi: {', '.join(str(i + 1) for i in sorted(retried - flagged))}-bo'lak"
                  if retried - flagged else "")
               + (f"\n⚠️ Tekshiring (gap tushib qolgan bo'lishi mumkin): "
                  f"{', '.join(str(i + 1) for i in sorted(flags))}-bo'lak — 🔁 bilan qayta yarating"
                  if flags else ""))
    srt_path = out.with_suffix(".srt")
    try:
        if s["format"] == "mp3":
            await m.answer_audio(FSInputFile(out, filename=name), caption=caption,
                                 title=job["title"] + ver, performer=voice, reply_markup=result_kb(job["id"]))
        else:
            await m.answer_document(FSInputFile(out, filename=name), caption=caption,
                                    reply_markup=result_kb(job["id"]))
        if s.get("srt", True):
            srt_path.write_text(te.make_srt(items, timeline), encoding="utf-8")
            await m.answer_document(FSInputFile(srt_path, filename=srt_path.name),
                                    caption="📄 Subtitr (CapCut / Premiere ga qo'shing)")
        await status.delete()
    except Exception as e:
        log.exception("yuborish xatosi")
        await safe_edit(status, f"❌ Yuborilmadi: {html.escape(str(e))[:300]}")
    finally:
        if not SAVE_OUTPUTS:
            out.unlink(missing_ok=True)
            srt_path.unlink(missing_ok=True)


async def synthesize(m: Message, text: str, title: str | None = None):
    voice = active_voice()
    if not voice:
        await m.answer("Avval ovoz qo'shing: 5–20 s ovozli xabar yuboring.")
        return
    s = dict(settings.data)
    items, unknown = te.plan(text, s["emotion"])
    if not items:
        await m.answer("Matn bo'sh (faqat teglar?).")
        return
    job = {
        "id": uuid.uuid4().hex[:8], "items": items, "voice": voice, "s": s,
        "title": title or _slug(text), "stamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "seeds": [te.SEED] * len(items), "pieces": None, "version": 0,
        "warn": f"\n⚠️ Noma'lum teglar o'tkazib yuborildi: {html.escape(' '.join(unknown))}" if unknown else "",
    }
    await run_job(m, job)


@owner.callback_query(F.data.startswith("rl:"))
async def cb_regen_list(cq: CallbackQuery):
    job = JOBS.get(cq.data[3:])
    if not job:
        await cq.answer("Bu audio eskirgan (faqat oxirgi 3 tasi eslab qolinadi). Matnni qayta yuboring.", show_alert=True)
        return
    await cq.answer()
    lines = [f"<b>{i + 1}.</b> {html.escape(t[:60])}{'…' if len(t) > 60 else ''}"
             for i, (_, t, _) in enumerate(job["items"])]
    text, chunks = "🔁 Qaysi bo'lakni qayta yaratamiz? Raqamni bosing.\n\n", []
    for line in lines:  # Telegram xabari 4096 belgi bilan cheklangan
        if len(text) + len(line) > 3800:
            chunks.append(text)
            text = ""
        text += line + "\n"
    chunks.append(text)
    nums = [btn(str(i + 1), f"r:{job['id']}:{i}") for i in range(min(len(job["items"]), 100))]
    for n, chunk in enumerate(chunks):
        kb = InlineKeyboardMarkup(inline_keyboard=grid(nums, 8)) if n == len(chunks) - 1 else None
        await cq.message.answer(chunk, reply_markup=kb)


@owner.callback_query(F.data.startswith("r:"))
async def cb_regen(cq: CallbackQuery):
    _, job_id, idx = cq.data.split(":")
    job, i = JOBS.get(job_id), int(idx)
    if not job or i >= len(job["items"]) or job.get("pieces") is None:
        await cq.answer("Bu audio eskirgan. Matnni qayta yuboring.", show_alert=True)
        return
    await cq.answer(f"{i + 1}-bo'lak qayta yaratilmoqda…")
    job["seeds"][i] += 1  # boshqa urug' → boshqa variant
    await run_job(cq.message, job, only={i})


# ---------------------------------------------------------------- talaffuz lug'ati

def load_lexicon() -> dict:
    try:
        return json.loads(LEXICON_FILE.read_text(encoding="utf-8")) if LEXICON_FILE.exists() else {}
    except Exception as e:
        log.warning("lugat.json o'qilmadi: %s", e)
        return {}


def save_lexicon(lex: dict):
    LEXICON_FILE.write_text(json.dumps(lex, ensure_ascii=False, indent=2), encoding="utf-8")


LUGAT_HELP = (
    "📖 <b>Talaffuz lug'ati</b> — model xato o'qiydigan so'zlarni qanday o'qishini yozasiz. "
    "Subtitrda asl so'z qoladi.\n\n"
    "<code>/lugat Leonardo = Leonardo</code> — qo'shish (o'ng tomonga eshitilishi kerak bo'lganidek yozing)\n"
    "<code>/lugat Leonardo -</code> — o'chirish\n"
    "<code>/lugat</code> — ro'yxat\n\n"
    "Maslahat: o'ng tomonni o'zbek lotin harflarida, eshitilganidek yozing (masalan <code>NASA = Nasa</code>, "
    "<code>XIX = o'n to'qqizinchi</code>)."
)


@owner.message(Command("lugat", "lug'at"))
async def cmd_lugat(m: Message):
    arg = (m.text or "").split(maxsplit=1)[1].strip() if len((m.text or "").split(maxsplit=1)) > 1 else ""
    lex = load_lexicon()
    if not arg:
        body = "\n".join(f"• {html.escape(k)} → {html.escape(v)}" for k, v in sorted(lex.items())) or "hali bo'sh"
        await m.answer(LUGAT_HELP + "\n\n<b>Lug'at:</b>\n" + body)
        return
    if "=" in arg:
        word, say = (x.strip() for x in arg.split("=", 1))
        if not word or not say:
            await m.answer("Format: <code>/lugat so'z = talaffuz</code>")
            return
        lex[word] = say
        save_lexicon(lex)
        await m.answer(f"📖 Saqlandi: {html.escape(word)} → {html.escape(say)}")
    elif arg.endswith("-"):
        word = arg[:-1].strip()
        key = next((k for k in lex if k.lower() == word.lower()), None)
        if key:
            del lex[key]
            save_lexicon(lex)
            await m.answer(f"🗑 O'chirildi: {html.escape(key)}")
        else:
            await m.answer("Bu so'z lug'atda yo'q.")
    else:
        await m.answer(LUGAT_HELP)


@owner.message(StateFilter(None), F.func(lambda m: bool(m.document) and (m.document.file_name or "").lower().endswith(".txt")))
async def on_script(m: Message, bot: Bot):
    if m.document.file_size and m.document.file_size > 1024 * 1024:
        await m.answer("Skript 1 MB dan katta. Bo'lib yuboring.")
        return
    path = TMP_DIR / f"script_{uuid.uuid4().hex}.txt"
    await bot.download(m.document, destination=path)
    raw = path.read_bytes()
    path.unlink(missing_ok=True)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="replace")
    await synthesize(m, text, title=_slug(Path(m.document.file_name).stem))


@owner.message(StateFilter(None), F.document)
async def on_other_doc(m: Message):
    await m.answer("Qabul qilinadi: .txt skript yoki audio fayl.")


@owner.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    await synthesize(m, m.text)


@owner.message(StateFilter(NewVoice.naming))
async def on_naming_other(m: Message):
    await m.answer("Ovoz nomini matn bilan yozing yoki /bekor.")


@owner.message()
async def on_owner_other(m: Message):
    await m.answer("Tushunmadim. Matn, .txt skript yoki ovozli xabar yuboring. /help")


# ---------------------------------------------------------------- begonalar

@stranger.message()
async def stranger_msg(m: Message):
    uid = m.from_user.id if m.from_user else "?"
    if OWNER_ID == 0:
        await m.answer(f"Sizning Telegram ID: <code>{uid}</code>\n"
                       "Uni Colab Secrets'ga <b>OWNER_ID</b> sifatida qo'shing va bot katakchasini qayta ishga tushiring.")
    else:
        log.warning("Begona foydalanuvchi: %s", uid)
        await m.answer("⛔ Bu shaxsiy bot.")


@stranger.callback_query()
async def stranger_cb(cq: CallbackQuery):
    await cq.answer("⛔ Bu shaxsiy bot.", show_alert=True)


# ---------------------------------------------------------------- ishga tushirish

async def main():
    global engine
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    if OWNER_ID:
        dp.include_router(owner)
    dp.include_router(stranger)

    me = await bot.get_me()
    log.info("Bot: @%s", me.username)
    if OWNER_ID:
        log.info("Model yuklanmoqda (~1 daqiqa)…")
        engine = await asyncio.to_thread(te.Engine)
        await bot.set_my_commands([
            BotCommand(command="sozlamalar", description="Ovoz, kayfiyat, tezlik, pitch, format"),
            BotCommand(command="ovozlar", description="Ovozni tanlash"),
            BotCommand(command="kayfiyatlar", description="Kayfiyatlar va teglar"),
            BotCommand(command="matn", description="Namuna matni (ohangni kuchaytiradi)"),
            BotCommand(command="lugat", description="Talaffuz lug'ati"),
            BotCommand(command="bekor", description="Joriy amalni bekor qilish"),
            BotCommand(command="help", description="Yordam"),
        ])
        try:
            await bot.send_message(OWNER_ID, "✅ Studiya tayyor.\n\n" + panel_text(), reply_markup=panel_kb())
        except Exception as e:
            log.warning("Egaga xabar yuborilmadi (avval botga /start bosing): %s", e)
    else:
        log.warning("OWNER_ID yo'q — bot faqat ID ko'rsatadi. Botga yozing, ID ni Secrets'ga qo'shing.")

    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Bot ishlayapti. To'xtatish: katakchani Stop qiling.")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
