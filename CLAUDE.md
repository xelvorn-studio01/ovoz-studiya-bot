# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

"XELVORN ovoz studiyasi" — a private Telegram bot (aiogram 3) that reads Uzbek text aloud in a cloned voice with a chosen emotion. The model is Navoiy TTS (a fine-tune of CosyVoice2-0.5B). It runs only on a Google Colab T4 GPU, never on the user's weak Windows PC. The user writes in Uzbek; bot messages and comments are in Uzbek.

## Commands

- `python build_notebook.py` — regenerates `ovoz_studiya_bot.ipynb` from the cell definitions in `build_notebook.py`. Never edit the `.ipynb` by hand; edit `build_notebook.py` and rebuild.
- There are no tests or linter. The bot can't run locally because it needs CUDA, CosyVoice and Colab/Drive paths. At most, check syntax locally with `python -m py_compile src/*.py`.

## How deployment works

- The notebook installs CosyVoice and its dependencies, then on every run does `git clone --depth 1` of `REPO_URL` (`xelvorn-studio01/ovoz-studiya-bot` on GitHub) and runs `src/studio_bot.py`. **Changes in `src/` reach the bot only after they are pushed to that GitHub repo**; the notebook itself only needs rebuilding when the setup cells change.
- Secrets come from Colab Secrets as env vars: `TELEGRAM_BOT_TOKEN`, `OWNER_ID`. Other env vars: `STUDIO_DIR` (default `/content/drive/MyDrive/ovoz_studio`), `SAVE_OUTPUTS`, and the model paths `COSY_DIR`, `BASE_MODEL_DIR`, `NAVOIY_DIR`.
- Persistent state is on Google Drive under `STUDIO_DIR`: `voices/*.wav` (reference samples, each with a transcript file beside it), `settings.json`, `lugat.json` (pronunciation lexicon) and `out/`.

## Architecture

- `src/tts_engine.py` is pure TTS logic with no Telegram code:
  - `parse_tagged` / `plan` split text on `[tag]` emotion markers and then into ≤`MAX_PIECE_CHARS` pieces, because short pieces make the model drop fewer sentences.
  - `Engine` loads the CosyVoice2 base model plus the Navoiy checkpoint (`emotion_600h_joint.pt`, emotion prompts in `emotions_40h.json`).
  - `check_length` compares voiced seconds with the expected `LETTERS_PER_S` to detect dropped speech and trigger a retry.
  - `assemble` adds pauses, loudness levelling and pitch, then writes the MP3/WAV.
  - `make_srt` builds subtitles from the timeline; `apply_lexicon` applies pronunciation substitutions.
- `src/studio_bot.py` is the aiogram layer:
  - Only `OWNER_ID` is served; strangers get a reply that shows their ID.
  - The inline-keyboard settings panel is backed by `Settings` (persisted to `settings.json`).
  - The `NewVoice` FSM handles uploading and naming a new voice sample (5–20 s).
  - Voices named like `<voice>_<emotion-suffix>` (e.g. `ovoz_jahldor`) are picked automatically when that emotion tag appears (`emotion_voice`).
  - `run_job` synthesises pieces; the last `MAX_JOBS` jobs are kept so single pieces can be regenerated (🔁, `only=` set).
  - A `.txt` document is treated as a whole script and rendered into one audio file.
  - Telegram `callback_data` has a 64-byte limit, which is why voice names are capped at `NAME_MAX = 24`.

## Rule from the README

Voice cloning is only allowed for the owner's own voice or with the voice owner's written consent. Don't add features that work around this.
