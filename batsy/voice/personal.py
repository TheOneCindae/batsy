"""A wake detector trained only on YOUR "Batsy" (template matching, like the old Snowboy personal models).

Word-based spotters (Vosk, the sherpa keyword spotter) look for the English spelling, and your "Batsy" often comes
out as "That's it" or "Batchy", so they either miss you or fire on everything. This one never turns sound into
words: "train my wake word" records you saying "Batsy" 8 times, and the mic is compared against those recordings by
how they sound (MFCC features, DTW alignment, so saying it faster or slower still matches). Accent doesn't matter.

calibrate() sets the bar from your own clips (each one against the others) and the saved false alarms
(wake_samples/*_false_*.wav: videos, TV, chatter), so the bar sits between you and them.
Everything is numpy/scipy, ~3% CPU on the laptop.
"""
import json
import logging
import wave
from pathlib import Path

import numpy as np
from scipy.fft import dct

from batsy.config import DATA_DIR

log = logging.getLogger("batsy.personal")
STORE = DATA_DIR / "personal_wake.npz"
SAMPLES = DATA_DIR / "wake_samples"
RATE, HOP, WIN, NFFT, MELS, CEPS = 16000, 160, 400, 512, 26, 13
BUFFER = 200  # frames of 10 ms kept for matching: 2 s


def _mel_bank() -> np.ndarray:
    mel = lambda f: 2595 * np.log10(1 + f / 700)  # noqa: E731
    imel = lambda m: 700 * (10 ** (m / 2595) - 1)  # noqa: E731
    pts = imel(np.linspace(mel(80), mel(7600), MELS + 2))
    bins = np.floor((NFFT + 1) * pts / RATE).astype(int)
    bank = np.zeros((MELS, NFFT // 2 + 1))
    for m in range(1, MELS + 1):
        lo, c, hi = bins[m - 1], bins[m], bins[m + 1]
        bank[m - 1, lo:c] = (np.arange(lo, c) - lo) / max(c - lo, 1)
        bank[m - 1, c:hi] = (hi - np.arange(c, hi)) / max(hi - c, 1)
    return bank


BANK = _mel_bank()
WINDOW = np.hamming(WIN)


def features(audio: np.ndarray) -> np.ndarray:
    """int16 samples -> (frames, 12) MFCCs (c0, the loudness, dropped: how loud you are mustn't matter)."""
    x = audio.astype(np.float32) / 32768.0
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    if len(x) < WIN:
        return np.zeros((0, CEPS - 1), np.float32)
    n = 1 + (len(x) - WIN) // HOP
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    power = np.abs(np.fft.rfft(x[idx] * WINDOW, NFFT)) ** 2 / NFFT
    logmel = np.log(power @ BANK.T + 1e-8)
    return dct(logmel, type=2, axis=1, norm="ortho")[:, 1:CEPS].astype(np.float32)


def _norm(f: np.ndarray, mean: np.ndarray | None = None) -> np.ndarray:
    return f - (f.mean(axis=0) if mean is None else mean)


def trim(audio: np.ndarray) -> np.ndarray:
    """Just the spoken word: the loudest stretch of voice (gaps under 0.15 s bridged), not the room around it."""
    e = np.array([np.sqrt(np.mean(audio[i:i + 160].astype(np.float32) ** 2)) for i in range(0, len(audio) - 159, 160)])
    if not len(e) or e.max() < 300:
        return audio
    loud = e > max(np.percentile(e, 20) * 3, e.max() * 0.15)
    runs, start, gap = [], None, 0
    for i, v in enumerate(loud):
        if v:
            start, gap = (i if start is None else start), 0
        elif start is not None:
            gap += 1
            if gap > 15:
                runs.append((start, i - gap + 1)); start, gap = None, 0
    if start is not None:
        runs.append((start, len(loud) - gap))
    a, b = max(runs, key=lambda r: e[r[0]:r[1]].sum())
    return audio[max(a - 5, 0) * 160:min(b + 5, len(e)) * 160]


def match(template: np.ndarray, seq: np.ndarray) -> np.ndarray:
    """Subsequence DTW: for every end frame j of seq, the best average distance of template aligned to a stretch
    of seq ending there. Steps let your "Batsy" be up to half or twice the template's speed."""
    cost = np.sqrt(((template[:, None, :] - seq[None, :, :]) ** 2).sum(-1))  # (T, S)
    inf = np.float32(1e9)
    d = cost[0].copy()  # free start anywhere in seq
    for i in range(1, len(template)):
        prev2 = np.concatenate([[inf, inf], d[:-2]])
        prev1 = np.concatenate([[inf], d[:-1]])
        d = cost[i] + np.minimum(np.minimum(d + 0.3, prev1), prev2)  # (template-only step costs a bit)
    return d / len(template)


class Detector:
    """Feed it the mic's frames; hit() is True when the last ~1 s sounded like one of your "Batsy"s."""

    def __init__(self):
        data = np.load(STORE, allow_pickle=False)
        self.templates = [data[k] for k in sorted(data.files) if k.startswith("t")]
        self.threshold = float(data["threshold"])
        self.mean = data["mean"]
        self.audio = np.zeros(0, np.int16)
        self.cooldown = self.since = 0

    def reset(self) -> None:
        self.audio = np.zeros(0, np.int16)
        self.cooldown = 0

    def hit(self, frame: bytes) -> bool:
        self.audio = np.concatenate([self.audio, np.frombuffer(frame, np.int16)])[-(BUFFER * HOP + WIN):]
        self.cooldown -= 1
        self.since += len(frame) // 2
        if self.cooldown > 0 or len(self.audio) < RATE // 2 or self.since < RATE // 5:  # check 5x a second
            return False
        self.since = 0
        e = np.sqrt(np.mean(self.audio[-RATE // 2:].astype(np.float32) ** 2))
        if e < 250:  # silence: nothing to compare
            return False
        score = self.score(self.audio)
        if score < self.threshold:
            log.info("wake word heard (your voice model, %.2f < %.2f)", score, self.threshold)
            self.reset()
            self.cooldown = 30  # 1.5 s: one "Batsy", one hit
            return True
        return False

    def score(self, audio: np.ndarray) -> float:
        f = features(audio)
        if len(f) < 20:
            return 1e9
        seq = _norm(f)
        # only matches ending in the last 0.3 s (checks run every 0.2 s): the word you just finished saying
        best = sorted(float(match(t, seq)[-30:].min()) for t in self.templates)
        return float(np.mean(best[:3]))  # close to several of your takes, not one by luck


def available() -> bool:
    return STORE.exists()


def _load(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16)


def train(clips: list[np.ndarray], negatives: list[np.ndarray] | None = None) -> dict:
    """Build the model from your "Batsy" clips and pick the bar. Returns what it measured."""
    words = [trim(c) for c in clips]
    temps = [_norm(features(w)) for w in words]
    temps = [t for t in temps if 15 <= len(t) <= 150]
    if len(temps) < 3:
        raise ValueError("not enough clear recordings")
    if negatives is None:
        negatives = [_load(p) for p in sorted(SAMPLES.glob("*_false_*.wav"))[-200:]]

    def score_with(ts, audio):
        seq = _norm(features(audio))
        if len(seq) < 20:
            return 1e9
        best = sorted(float(match(t, seq)[-30:].min()) for t in ts)
        return float(np.mean(best[:3])) if len(best) >= 3 else float(np.mean(best))

    # you: each take, padded like the mic buffer, against the other takes
    pos = []
    for i, c in enumerate(clips):
        others = temps[:i] + temps[i + 1:]
        pos.append(score_with(others, c[-(BUFFER * HOP + WIN):]))
    neg = []
    for a in negatives:  # every 0.5 s through each false alarm, as the mic would see it
        for end in range(RATE, len(a) + 1, RATE // 2):
            neg.append(score_with(temps, a[max(0, end - BUFFER * HOP - WIN):end]))
    pos, neg = np.array(pos), np.array(neg) if neg else np.array([1e9])
    # the bar: under EVERY saved false alarm (it's checked 5x a second, so even 1% would mean false wakes all the
    # time), and no looser than needed to catch your takes
    threshold = min(float(neg.min()) * 0.97, float(pos.max()) * 1.05)
    caught = int((pos < threshold).sum())
    result = {"caught": caught, "of": len(pos), "false": int((neg < threshold).sum()), "checked": len(neg),
              "threshold": round(threshold, 3), "enabled": caught >= max(3, int(0.6 * len(pos)))}
    if result["enabled"]:  # only switch it on if it really tells you apart from the videos and the room
        np.savez(STORE, threshold=np.float32(threshold), mean=np.zeros(CEPS - 1, np.float32),
                 **{f"t{i:02d}": t for i, t in enumerate(temps)})
    log.info("personal wake model: %s", json.dumps(result))
    return result
