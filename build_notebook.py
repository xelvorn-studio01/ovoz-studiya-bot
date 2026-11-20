"""ovoz_studiya_bot.ipynb ni yig'adi:  python build_notebook.py

Bot kodi notebook ichida emas — har ishga tushganda GitHub repodan olinadi,
shuning uchun kod yangilansa notebook'ni qayta yuklash shart emas.
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent
REPO_URL = "https://github.com/xelvorn-studio01/ovoz-studiya-bot.git"
cells = []


def md(text):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(True)})


def code(text, title=None):
    meta = {"cellView": "form"} if title else {}
    src = (f"#@title {title}\n" if title else "") + text.strip("\n")
    cells.append({"cell_type": "code", "metadata": meta, "execution_count": None,
                  "outputs": [], "source": src.splitlines(True)})


md("""
# 🎙 XELVORN ovoz studiyasi — Telegram TTS bot (Navoiy TTS, Colab T4)

Shaxsiy bot: faqat egasi (OWNER_ID) ishlata oladi. Ovozlar, sozlamalar va o'rnatish keshi **Google Drive**'da: `MyDrive/ovoz_studio/`.

**Bir martalik tayyorgarlik**
1. *Среда выполнения → Сменить среду выполнения → **T4 GPU***.
2. Chapdagi 🔑 **Secrets** paneliga qo'shing va "Доступ из блокнотов" ni yoqing:
   - `TELEGRAM_BOT_TOKEN` — @BotFather bergan token (chatga yozilmaydi!)
   - `OWNER_ID` — Telegram ID raqamingiz. Bilmasangiz, bo'sh qoldiring: bot ishga tushgach unga yozing — ID ni o'zi aytadi.

**Har safar:** faqat **▷ Выполнить все** → Drive oynasida ruxsat bering. Birinchi marta ~5 daqiqa, keyingilari tezroq (keshdan).
Bot kodi har safar GitHub'dan eng yangi holatda olinadi — kod yangilansa, bu notebook'ni almashtirish shart emas.
Oxirgi katak ishlab turgan paytda bot tirik. Colab'ni **bitta tab**da oching; kompyuter uxlab qolsa / tab yopilsa sessiya uziladi.
""")

code("""
!nvidia-smi --query-gpu=name,memory.total --format=csv || echo "❌ GPU yo'q: Среда выполнения → Сменить среду выполнения → T4 GPU"
import sys; print("Python", sys.version.split()[0])
""", "1. GPU tekshiruvi")

code(r'''
from google.colab import drive
import os, pathlib
drive.mount("/content/drive")

STUDIO_DIR = "/content/drive/MyDrive/ovoz_studio"  #@param {type:"string"}
SAVE_OUTPUTS = True  #@param {type:"boolean"}
for sub in ("voices", "out", ".cache/pip"):
    pathlib.Path(STUDIO_DIR, sub).mkdir(parents=True, exist_ok=True)
os.environ["STUDIO_DIR"] = STUDIO_DIR
os.environ["SAVE_OUTPUTS"] = "1" if SAVE_OUTPUTS else "0"
print("📁", STUDIO_DIR)
print("Ovozlar:", sorted(p.stem for p in pathlib.Path(STUDIO_DIR, "voices").glob("*.wav")) or "hali yo'q")
''', "2. Google Drive")

code(r'''
import os, re, pathlib, time

COSY_DIR   = "/content/CosyVoice"
MODELS     = "/content/models"
COSY_REV   = "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
BASE_REV   = "eec1ae6c79877dbd9379285cf8789c9e0879293d"
MARK       = pathlib.Path("/content/.studio_install_ok")
# Yuklangan va yig'ilgan paketlar Drive'da saqlanadi — keyingi sessiyalarda qayta yuklanmaydi/yig'ilmaydi
os.environ["PIP_CACHE_DIR"] = os.path.join(STUDIO_DIR, ".cache/pip")

def sh(cmd):
    get_ipython().system(cmd)
    rc = get_ipython().user_ns.get("_exit_code", 0)
    if rc:
        raise RuntimeError(f"Buyruq xato bilan tugadi ({rc}): {cmd}")

if MARK.exists():
    print("✅ Allaqachon o'rnatilgan (shu sessiyada). O'tkazib yuborildi.")
else:
    t0 = time.time()
    # 1) CosyVoice (aniq commit) + submodullar
    if not os.path.isdir(COSY_DIR):
        sh(f"git clone -q https://github.com/FunAudioLLM/CosyVoice.git {COSY_DIR}")
    sh(f"cd {COSY_DIR} && git checkout -q {COSY_REV} && git submodule update --init --recursive -q")

    # 2) requirements: og'ir/keraksiz paketlar chiqariladi, versiya pinlari olib tashlanadi
    skip = re.compile(r"^(torch|torchaudio|onnxruntime|tensorrt|deepspeed|grpcio|gradio|fastapi|uvicorn)", re.I)
    names = []
    for raw in open(f"{COSY_DIR}/requirements.txt", encoding="utf-8"):
        line = raw.split("#")[0].split(";")[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[=<>~!\[\s]", line)[0]
        if not skip.match(name):
            names.append(name)
    pathlib.Path("/content/req_cosy.txt").write_text("\n".join(names))
    sh("pip install -q -r /content/req_cosy.txt onnxruntime-gpu")

    # 3) Modellar
    from huggingface_hub import snapshot_download
    snapshot_download("FunAudioLLM/CosyVoice2-0.5B", revision=BASE_REV, local_dir=f"{MODELS}/CosyVoice2-0.5B")
    snapshot_download("aisha-org/navoiy-tts", local_dir=f"{MODELS}/navoiy-tts")

    # 4) Navoiy requirements (numpy==1.26.4 Python 3.13 da o'rnatilmaydi — keyingi qadamda tuzatiladi)
    nav = [l.strip() for l in open(f"{MODELS}/navoiy-tts/requirements.txt") if l.strip() and not l.startswith("numpy")]
    sh("pip install -q " + " ".join(f'"{x}"' for x in nav))

    # 5) Tuzatishlar + bot kutubxonasi
    sh('pip install -q "numpy>=2.1,<2.3"')
    sh('pip install -q "huggingface_hub>=0.30,<1.0" "diffusers==0.32.2"')
    sh('pip install -q "aiogram>=3.13,<4"')
    MARK.touch()
    print(f"✅ O'rnatish tugadi: {time.time() - t0:.0f} s.")
''', "3. O'rnatish (CosyVoice + Navoiy TTS + aiogram)")

code(r'''
CHECK = r"""
import sys
sys.path[:0] = ["/content/models/navoiy-tts", "/content/CosyVoice/third_party/Matcha-TTS", "/content/CosyVoice"]
import torch, torchaudio, numpy
from cosyvoice.cli.cosyvoice import CosyVoice2
from uztts.normalize import normalize
import aiogram
torchaudio.load("/content/models/navoiy-tts/demo/calm_intro.wav")
print("torch", torch.__version__, "| cuda", torch.cuda.is_available(), "| numpy", numpy.__version__, "| aiogram", aiogram.__version__)
print("normalize:", normalize("2026-yil 20-noyabr, soat 5:20", mode="infer"))
"""
open("/content/check_env.py", "w").write(CHECK)
get_ipython().system("python /content/check_env.py")
if get_ipython().user_ns.get("_exit_code", 0):
    print("\n⚠️ torchaudio.load ishlamadi bo'lishi mumkin — torchcodec o'rnatib qayta tekshiramiz…")
    get_ipython().system("pip install -q torchcodec && python /content/check_env.py")
    if get_ipython().user_ns.get("_exit_code", 0):
        raise RuntimeError("Muhit tekshiruvi o'tmadi — yuqoridagi xatoni ko'ring.")
print("✅ Muhit tayyor.")
''', "4. Muhit tekshiruvi")

code(fr'''
REPO_URL = "{REPO_URL}"  #@param {{type:"string"}}
CODE_DIR = "/content/studio"
import os
if os.path.isdir(os.path.join(CODE_DIR, ".git")):
    get_ipython().system(f"git -C {{CODE_DIR}} pull -q --ff-only")
else:
    get_ipython().system(f"rm -rf {{CODE_DIR}} && git clone -q --depth 1 {{REPO_URL}} {{CODE_DIR}}")
if not os.path.exists(os.path.join(CODE_DIR, "src", "studio_bot.py")):
    raise RuntimeError("Bot kodi GitHub'dan olinmadi — REPO_URL va internetni tekshiring.")
get_ipython().system(f"git -C {{CODE_DIR}} log -1 --format='📦 Bot kodi: %h — %s (%cd)' --date=format:'%Y-%m-%d %H:%M'")
''', "5. Bot kodi (GitHub'dan eng yangi versiya)")

code(r'''
SOURCE_FILE = "/content/drive/MyDrive/ovozim_namuna_14s.wav"  #@param {type:"string"}
VOICE_NAME = "ovozim"  #@param {type:"string"}
import os, subprocess, sys
if os.path.exists(SOURCE_FILE):
    dst = os.path.join(STUDIO_DIR, "voices", f"{VOICE_NAME}.wav")
    script = ("import sys, pathlib, tts_engine as te; "
              "d = te.prepare_reference(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])); "
              "print(f'{d:.1f}')")
    r = subprocess.run([sys.executable, "-c", script, SOURCE_FILE, dst],
                       cwd="/content/studio/src", capture_output=True, text=True)
    print(f"✅ {VOICE_NAME}: {r.stdout.strip()} s" if r.returncode == 0 else "❌ " + r.stderr[-800:])
else:
    print("O'tkazildi: fayl topilmadi. (Ovozni botga ovozli xabar qilib ham yuborsa bo'ladi.)")
''', "6. (ixtiyoriy) Drive'dagi tayyor namunani ovoz sifatida qo'shish")

code(r'''
import os
from google.colab import userdata

def secret(name):
    try:
        return (userdata.get(name) or "").strip()
    except Exception:
        return ""

token = secret("TELEGRAM_BOT_TOKEN")
if not token:
    raise SystemExit("❌ Secrets'da TELEGRAM_BOT_TOKEN yo'q (yoki Доступ из блокнотов o'chiq).")
os.environ["TELEGRAM_BOT_TOKEN"] = token
os.environ["OWNER_ID"] = secret("OWNER_ID") or "0"
del token
print("🔑 Token olindi.", "OWNER_ID:", os.environ["OWNER_ID"] if os.environ["OWNER_ID"] != "0" else "yo'q — botga yozing, ID ni aytadi")

!cd /content/studio/src && python -u studio_bot.py
''', "7. ▶️ Botni ishga tushirish (to'xtatish: Stop ⏹)")

md("""
## Foydalanish
| Botga yuborasiz | Natija |
|---|---|
| Matn | Tanlangan ovoz/kayfiyatda audio + `.srt` subtitr |
| `[pichirlab] Sekin gap. [xursand] Quvnoq gap!` | Har bo'lak o'z kayfiyatida (bor bo'lsa `ovoz_kayfiyat` namunasi bilan) |
| `.txt` fayl | Butun skript → bitta audio (`MyDrive/ovoz_studio/out/` ga ham saqlanadi) |
| Ovozli xabar / audio / video (5–20 s) | Yangi ovoz namunasi → nom so'raladi |
| 🔁 tugmasi (audio ostida) | Yoqmagan bo'lakni qayta yaratish |
| /sozlamalar · /matn · /lugat | Sozlamalar · namuna matni · talaffuz lug'ati |

## Qayta ishga tushirish
- **Sessiya ulangan bo'lsa:** 7-katakni ⏹ → ▶️ (kod yangilangan bo'lsa avval 5-katakni ▶️).
- **Sessiya uzilgan bo'lsa:** ▷ Выполнить все.
- **Qotib qolsa:** Среда выполнения → Отключиться и удалить среду выполнения → ▷ Выполнить все.

## Muammolar
- **"⛔ Bu shaxsiy bot"** → `OWNER_ID` noto'g'ri. Secrets'dan o'chirib, botga yozing — to'g'ri ID ni aytadi.
- **CUDA out of memory** → *Отключиться и удалить среду* → ▷ Выполнить все.
- **Kesh buzilgandek (pip xatolari)** → Drive'dagi `ovoz_studio/.cache` papkasini o'chirib, qayta ishga tushiring.
- ⚖️ Faqat o'z ovozingiz yoki egasining yozma roziligi bor ovozlar.
""")

nb = {
    "nbformat": 4, "nbformat_minor": 0,
    "metadata": {
        "colab": {"provenance": [], "gpuType": "T4", "name": "ovoz_studiya_bot.ipynb"},
        "accelerator": "GPU",
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
        "language_info": {"name": "python"},
    },
    "cells": cells,
}
out = ROOT / "ovoz_studiya_bot.ipynb"
out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print("Yozildi:", out, "| kataklar:", len(cells))
