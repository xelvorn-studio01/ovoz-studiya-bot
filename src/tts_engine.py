"""Navoiy TTS dvigateli: modelni bir marta yuklaydi, teglar bo'yicha bo'laklaydi,
balandlikni tenglashtiradi, pauza bilan yopishtiradi, pitch/format beradi."""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

COSY_DIR = Path(os.environ.get("COSY_DIR", "/content/CosyVoice"))
BASE_MODEL_DIR = Path(os.environ.get("BASE_MODEL_DIR", "/content/models/CosyVoice2-0.5B"))
NAVOIY_DIR = Path(os.environ.get("NAVOIY_DIR", "/content/models/navoiy-tts"))
CHECKPOINT = NAVOIY_DIR / "emotion_600h_joint.pt"
EMOTIONS_FILE = NAVOIY_DIR / "emotions_40h.json"

SR = 24000
SEED = 1986
PAUSE_SEGMENT = 0.4   # teglar / paragraflar orasidagi pauza (s)
PAUSE_SENTENCE = 0.2  # bitta paragraf ichidagi bo'laklar orasida (s)
MAX_PIECE_CHARS = 160  # qisqa bo'lak = model gapni tashlab ketishi kamroq
TARGET_RMS_DB = -20.0
MAX_GAIN_DB = 18.0

REF_MIN_S = 5.0
REF_MAX_S = 20.0

for _p in (NAVOIY_DIR, COSY_DIR / "third_party" / "Matcha-TTS", COSY_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Botdagi 10 ta kayfiyat kaliti -> emotions_40h.json dagi teg
EMOTION_KEYS = ["warm", "calm", "happy", "sad", "angry",
                "nervous", "surprised", "whisper", "tired", "sarcastic"]
_KEY_TO_JSON_TAG = {"whisper": "whispers"}


def _load_emotions():
    entries = json.loads(EMOTIONS_FILE.read_text(encoding="utf-8"))
    lookup = {}
    for e in entries:
        lookup[e["uz"].lower()] = e
        for t in e["tag"].replace("[", " ").replace("]", " ").split():
            lookup[t.lower()] = e
    emotions, tag2key = {}, {}
    for key in EMOTION_KEYS:
        e = lookup.get(_KEY_TO_JSON_TAG.get(key, key))
        if e is None:
            raise RuntimeError(f"emotions_40h.json da '{key}' topilmadi")
        emotions[key] = {"uz": e["uz"], "instruct": e["instruct"].strip()}
        tag2key[key] = key
        tag2key[e["uz"].lower()] = key
        for t in e["tag"].replace("[", " ").replace("]", " ").split():
            tag2key[t.lower()] = key
    return emotions, tag2key


EMOTIONS, TAG2KEY = _load_emotions()


def emotion_label(key: str) -> str:
    return f"{EMOTIONS[key]['uz']} ({key})"


# ---------------------------------------------------------------- matn

TAG_RE = re.compile(r"\[([^\[\]\n]{1,30})\]")
_SENT_RE = re.compile(r"(?<=[.!?…])\s+")


def parse_tagged(text: str, default_emotion: str):
    """'[whisper] matn [calm] matn' -> [(kayfiyat, matn), ...], noma'lum teglar."""
    segments, unknown = [], []
    emotion, pos = default_emotion, 0
    for m in TAG_RE.finditer(text):
        before = text[pos:m.start()]
        if before.strip():
            segments.append((emotion, before.strip()))
        tag = re.sub(r"[ʻʼ’‘`]", "'", m.group(1)).strip().lower()
        if tag in TAG2KEY:
            emotion = TAG2KEY[tag]
        else:
            unknown.append(m.group(0))
        pos = m.end()
    rest = text[pos:]
    if rest.strip():
        segments.append((emotion, rest.strip()))
    return segments, unknown


def _split_long(sentence: str, limit: int):
    if len(sentence) <= limit:
        return [sentence]
    out, cur = [], ""
    for part in re.split(r"(?<=[,;:])\s+|\s+", sentence):
        if cur and len(cur) + 1 + len(part) > limit:
            out.append(cur)
            cur = part
        else:
            cur = f"{cur} {part}".strip()
    if cur:
        out.append(cur)
    return out


def split_pieces(text: str, limit: int = MAX_PIECE_CHARS):
    """Matnni paragraf -> gap -> ≤limit bo'laklarga ajratadi: [(bo'lak, pauza), ...]."""
    result = []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    for paragraph in paragraphs:
        pieces, cur = [], ""
        for sent in _SENT_RE.split(paragraph):
            for s in _split_long(sent.strip(), limit):
                if not s:
                    continue
                if cur and len(cur) + 1 + len(s) > limit:
                    pieces.append(cur)
                    cur = s
                else:
                    cur = f"{cur} {s}".strip()
        if cur:
            pieces.append(cur)
        for i, piece in enumerate(pieces):
            last = i == len(pieces) - 1
            result.append((piece, PAUSE_SEGMENT if last else PAUSE_SENTENCE))
    return result


def plan(text: str, default_emotion: str):
    """Butun matn -> [(kayfiyat, bo'lak, pauza_keyin), ...], noma'lum teglar."""
    segments, unknown = parse_tagged(text, default_emotion)
    items = []
    for emotion, seg_text in segments:
        for piece, pause in split_pieces(seg_text):
            if re.search(r"\w", piece):
                items.append((emotion, piece, pause))
    return items, unknown


# ---------------------------------------------------------------- audio

def _ffmpeg(*args: str):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg xatosi: {res.stderr.strip()[-500:]}")


def prepare_reference(src: Path, dst: Path) -> float:
    """Ovoz namunasi: mono, 24 kHz, boshi/oxiridagi jimlik kesiladi, loudnorm, ≤20 s."""
    trim = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.1,"
            "areverse,"
            "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.1,"
            "areverse")
    dst.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg("-i", str(src), "-vn", "-ac", "1", "-ar", str(SR),
            "-af", f"{trim},loudnorm=I=-20:TP=-2:LRA=11",
            "-t", str(REF_MAX_S), "-ar", str(SR), "-c:a", "pcm_s16le", str(dst))
    duration = sf.info(str(dst)).duration
    if duration < REF_MIN_S:
        dst.unlink(missing_ok=True)
        raise ValueError(f"Namuna juda qisqa: {duration:.1f} s (kamida {REF_MIN_S:.0f} s kerak).")
    return duration


def transcript_path(ref_wav: Path) -> Path:
    return Path(ref_wav).with_suffix(".txt")


def read_transcript(ref_wav: Path) -> str:
    p = transcript_path(ref_wav)
    try:
        return p.read_text(encoding="utf-8-sig").strip() if p.exists() else ""
    except Exception:
        return ""


LETTERS_PER_S = 12.0  # o'zbekcha nutqning taxminiy tezligi (harf/s, tezlik 1.0)


def voiced_seconds(audio: np.ndarray) -> float:
    """Ovozli qism davomiyligi (20 ms kadrlar, cho'qqidan -35 dB dan baland)."""
    if audio is None or audio.size == 0:
        return 0.0
    hop = int(SR * 0.02)
    n = audio.size // hop
    if n == 0:
        return 0.0
    frames = audio[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    thr = rms.max() * (10 ** (-35 / 20))
    return float(np.count_nonzero(rms > thr) * 0.02)


def check_length(audio: np.ndarray, letters: int, speed: float):
    """Bo'lak matnga nisbatan juda qisqa (gap tashlangan) yoki juda uzun (takror) emasmi.
    Qaytaradi: (yaxshimi, og'ish bahosi — kichigi yaxshi)."""
    if letters == 0:
        return True, 0.0
    sec = voiced_seconds(audio)
    if sec <= 0:
        return False, 99.0
    rate = letters / sec / speed  # harf/s
    ok = 5.0 <= rate <= 22.0 if letters >= 15 else rate <= 30.0
    return ok, abs(np.log(rate / LETTERS_PER_S))


def _level(audio: np.ndarray) -> np.ndarray:
    if audio.size == 0:
        return audio
    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms < 1e-6:
        return audio
    gain_db = min(TARGET_RMS_DB - 20 * np.log10(rms), MAX_GAIN_DB)
    audio = audio * (10 ** (gain_db / 20))
    peak = float(np.max(np.abs(audio)))
    if peak > 0.97:
        audio = audio * (0.97 / peak)
    return audio


def _fade(audio: np.ndarray, ms: float = 10) -> np.ndarray:
    n = min(int(SR * ms / 1000), audio.size // 2)
    if n > 0:
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        audio[:n] *= ramp
        audio[-n:] *= ramp[::-1]
    return audio


def postprocess(raw_wav: Path, out_path: Path, pitch: int, fmt: str):
    filters = []
    if pitch:
        ratio = 2 ** (pitch / 12)
        filters += [f"asetrate={SR * ratio:.3f}", f"aresample={SR}", f"atempo={1 / ratio:.6f}"]
    filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    codec = ["-c:a", "libmp3lame", "-b:a", "192k"] if fmt == "mp3" else ["-c:a", "pcm_s16le"]
    _ffmpeg("-i", str(raw_wav), "-af", ",".join(filters), "-ar", str(SR), "-ac", "1",
            *codec, str(out_path))


# ---------------------------------------------------------------- model

def _unwrap_state_dict(value):
    if not isinstance(value, dict):
        return value
    for key in ("state_dict", "model", "llm"):
        nested = value.get(key)
        if isinstance(nested, dict):
            return _unwrap_state_dict(nested)
    return value


class Engine:
    def __init__(self):
        import torch
        from cosyvoice.cli.cosyvoice import CosyVoice2
        from uztts.normalize import normalize

        if not torch.cuda.is_available():
            raise RuntimeError("GPU topilmadi: Colab'da Runtime → Change runtime type → T4 GPU.")
        self.torch = torch
        self.normalize = normalize
        t0 = time.time()
        self.model = CosyVoice2(str(BASE_MODEL_DIR), load_jit=False, load_trt=False, fp16=True)
        llm = self.model.model.llm
        # Asl CosyVoice2 LLM og'irliklari (CPU'da nusxa) — "asl" rejim uchun
        base_state = {k: v.detach().cpu().clone() for k, v in llm.state_dict().items()}
        state = _unwrap_state_dict(torch.load(CHECKPOINT, map_location="cpu", weights_only=True))
        res = llm.load_state_dict(state, strict=False)
        if res.missing_keys:
            print(f"Ogohlantirish: {len(res.missing_keys)} ta kalit yo'q", file=sys.stderr)
        if res.unexpected_keys:
            print(f"Ogohlantirish: {len(res.unexpected_keys)} ta ortiqcha kalit", file=sys.stderr)
        llm.eval()
        navoiy_state = {k: v.detach().cpu().clone() for k, v in llm.state_dict().items()}
        self._llm_states = {"navoiy": navoiy_state, "asl": base_state}
        self.mode = "navoiy"
        self.sr = getattr(self.model, "sample_rate", SR)
        self.lock = threading.Lock()
        print(f"Model yuklandi: {time.time() - t0:.0f} s, sample rate {self.sr}")

    def set_mode(self, mode: str):
        """'navoiy' — o'zbekcha talaffuz yaxshi, ovoz umumiyroq;
        'asl' — asl CosyVoice2: ovoz namunaga yaqinroq, o'zbekcha talaffuz zaifroq."""
        mode = mode if mode in self._llm_states else "navoiy"
        if mode != self.mode:
            self.model.model.llm.load_state_dict(self._llm_states[mode])
            self.model.model.llm.eval()
            self.mode = mode
            print(f"Model rejimi: {mode}")

    def _seed(self, seed: int):
        random.seed(seed)
        np.random.seed(seed)
        self.torch.manual_seed(seed)
        self.torch.cuda.manual_seed_all(seed)

    def synth_piece(self, text: str, emotion: str, ref_wav: Path, speed: float,
                    seed: int = SEED, lexicon: dict | None = None,
                    use_transcript: bool = True) -> np.ndarray:
        torch = self.torch
        self._seed(seed)
        norm = self.normalize(apply_lexicon(text, lexicon), mode="infer")
        instruction = EMOTIONS[emotion]["instruct"] + "<|endofprompt|>"
        # Namuna yonida <nom>.txt (namunada aytilgan matn) bo'lsa — "namuna matni" rejimi:
        # model namunaning ohangini (intonatsiya, ritm) ham ko'radi, nafaqat tembrini.
        transcript = read_transcript(ref_wav) if use_transcript else ""
        chunks = []
        with torch.inference_mode():
            if transcript:
                try:
                    prompt = self.normalize(transcript, mode="infer")
                    if self.mode == "navoiy":
                        prompt = instruction + prompt
                    for r in self.model.inference_zero_shot(norm, prompt, str(ref_wav),
                                                            stream=False, speed=speed):
                        chunks.append(r["tts_speech"].detach().float().cpu())
                except Exception as e:
                    print(f"Namuna matni rejimi xatosi, oddiy rejimga o'tildi: {e}", file=sys.stderr)
                    chunks = []
            if not chunks:
                for r in self.model.inference_instruct2(norm, instruction, str(ref_wav),
                                                        stream=False, speed=speed):
                    chunks.append(r["tts_speech"].detach().float().cpu())
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = torch.cat(chunks, dim=1).squeeze(0).numpy().astype(np.float32)
        if self.sr != SR:
            import librosa
            audio = librosa.resample(audio, orig_sr=self.sr, target_sr=SR)
        return audio

    def synth_items(self, items, ref_for, speed: float, seeds=None, only=None,
                    lexicon: dict | None = None, progress=None, pieces=None, mode: str = "navoiy"):
        """Har bo'lakni generatsiya qiladi (balandlik tenglashtirilgan).
        only = qayta yaratiladigan bo'lak indekslari (qolganlari `pieces` dan olinadi)."""
        seeds = seeds or [SEED] * len(items)
        out = list(pieces) if pieces is not None else [None] * len(items)
        todo = [i for i in range(len(items)) if only is None or i in only]
        flagged, retried = set(), set()
        with self.lock:
            self.set_mode(mode)
            for n, i in enumerate(todo):
                if progress:
                    progress(n, len(todo))
                emotion, piece, _ = items[i]
                letters = len(re.findall(r"\w", self.normalize(apply_lexicon(piece, lexicon), mode="infer")))
                # 1-urinish; gap tashlab ketilgan / cho'zilgan bo'lsa: boshqa urug', keyin oddiy rejim
                attempts = [(seeds[i], True), (seeds[i] + 1000, True), (seeds[i] + 2000, False)]
                best, best_score = None, None
                for a, (seed, use_tr) in enumerate(attempts):
                    audio = self.synth_piece(piece, emotion, ref_for(emotion), speed,
                                             seed=seed, lexicon=lexicon, use_transcript=use_tr)
                    ok, score = check_length(audio, letters, speed)
                    if best_score is None or score < best_score:
                        best, best_score = audio, score
                    if ok:
                        break
                    retried.add(i)
                    print(f"{i + 1}-bo'lak shubhali (urinish {a + 1}), qayta yaratilmoqda", file=sys.stderr)
                if not check_length(best, letters, speed)[0]:
                    flagged.add(i)
                out[i] = _fade(_level(best)) if best is not None and best.size else np.zeros(0, np.float32)
            self.torch.cuda.empty_cache()
        return out, flagged, retried


def assemble(items, pieces, pitch: int, fmt: str, out_path: Path):
    """Bo'laklarni pauzalar bilan yopishtiradi, faylni yozadi.
    Qaytaradi: (davomiylik_s, [(boshlanish_s, tugash_s) har bo'lak uchun])."""
    parts, timeline, pos = [], [], 0
    last = max((i for i, a in enumerate(pieces) if a is not None and a.size), default=-1)
    if last < 0:
        raise RuntimeError("Model audio qaytarmadi.")
    for i, ((_, _, pause), audio) in enumerate(zip(items, pieces)):
        n = 0 if audio is None else audio.size
        timeline.append((pos / SR, (pos + n) / SR))
        if n:
            parts.append(audio)
            pos += n
            if i < last:
                gap = int(SR * pause)
                parts.append(np.zeros(gap, dtype=np.float32))
                pos += gap
    full = np.concatenate(parts)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw = out_path.with_suffix(".raw.wav")
    sf.write(str(raw), full, SR, subtype="PCM_16")
    try:
        postprocess(raw, out_path, pitch, fmt)
    finally:
        raw.unlink(missing_ok=True)
    return full.size / SR, timeline


# ---------------------------------------------------------------- subtitr

SUB_MAX_CHARS = 84  # bitta subtitr kadri (2 qator × ~42 belgi)


def _srt_time(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wrap2(text: str, width: int = 42) -> str:
    if len(text) <= width:
        return text
    words, line1 = text.split(), ""
    while words and len(line1) + len(words[0]) + 1 <= max(width, len(text) // 2 + 1):
        line1 = f"{line1} {words.pop(0)}".strip()
    return line1 + ("\n" + " ".join(words) if words else "")


def make_srt(items, timeline) -> str:
    """Har bo'lakni ≤84 belgili kadrlarga bo'lib, vaqtni belgilar soniga mutanosib taqsimlaydi."""
    cues = []
    for (_, text, _), (start, end) in zip(items, timeline):
        if end <= start:
            continue
        chunks = []
        for sent in _SENT_RE.split(text):
            chunks += _split_long(sent.strip(), SUB_MAX_CHARS)
        chunks = [c for c in chunks if c]
        total = sum(len(c) for c in chunks) or 1
        t = start
        for c in chunks:
            dt = (end - start) * len(c) / total
            cues.append((t, t + dt, c))
            t += dt
    return "\n".join(f"{n}\n{_srt_time(a)} --> {_srt_time(b)}\n{_wrap2(c)}\n"
                     for n, (a, b, c) in enumerate(cues, 1))


# ---------------------------------------------------------------- talaffuz lug'ati

_APOS = re.compile(r"[ʻʼ’‘`]")


def apply_lexicon(text: str, lexicon: dict | None) -> str:
    """{'Tutanxamon': 'Tutanxamon talaffuzi'} — butun so'z, katta-kichik harf farqsiz."""
    if not lexicon:
        return text
    text = _APOS.sub("'", text)
    for word in sorted(lexicon, key=len, reverse=True):
        pattern = r"(?<!\w)" + re.escape(_APOS.sub("'", word)) + r"(?!\w)"
        text = re.sub(pattern, lambda _m, r=lexicon[word]: r, text, flags=re.IGNORECASE)
    return text
