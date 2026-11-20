# XELVORN ovoz studiyasi — Telegram TTS bot

O'zbekcha matnni tanlangan ovoz va kayfiyatda o'qiydigan shaxsiy Telegram bot.
[Navoiy TTS](https://huggingface.co/aisha-org/navoiy-tts) (CosyVoice2-0.5B asosida) modeli bilan Google Colab'ning bepul T4 GPU'sida ishlaydi.

## Imkoniyatlar
- Ovozli xabar yoki audio fayldan (5–20 s) yangi ovoz namunasi.
- 10 ta kayfiyat; matn ichida teglar: `[pichirlab] sekin gap [xursand] quvnoq gap`.
- Kayfiyat namunalari: `ovoz_jahldor`, `ovoz_pichirlab` … bo'lsa, teg kelganda avtomatik ishlatiladi; `/matn` bilan namuna matni berilsa, ohang kuchliroq ko'chadi.
- Tezlik, pitch, MP3/WAV; `.txt` skript → bitta audio; har audio bilan `.srt` subtitr.
- Yoqmagan bo'lakni qayta yaratish (🔁), gap tushib qolsa avtomatik qayta urinish.
- Talaffuz lug'ati (`/lugat`).
- Faqat egasi ishlata oladi (`OWNER_ID`).

## Ishga tushirish
1. `ovoz_studiya_bot.ipynb` ni Google Colab'da oching, GPU: **T4**.
2. Colab Secrets: `TELEGRAM_BOT_TOKEN`, `OWNER_ID` (+ "Notebook access").
3. **Run all**. Ovozlar va sozlamalar Google Drive'da (`MyDrive/ovoz_studio/`) saqlanadi.

Notebook bot kodini har safar shu repodan oladi, shuning uchun kod yangilanganda notebook'ni almashtirish shart emas.

## Tuzilishi
- `src/tts_engine.py` — model, teglar, bo'laklash, audio, subtitr, lug'at.
- `src/studio_bot.py` — aiogram 3 bot.
- `build_notebook.py` — Colab notebook'ni yig'adi.

## Ovoz huquqi
Faqat o'z ovozingizdan yoki egasining yozma roziligi bor ovozdan foydalaning. Boshqa odamning ovozini ruxsatsiz klonlash mumkin emas.
