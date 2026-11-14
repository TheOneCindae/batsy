"""The spoken kill switch: "Batsy, stop" works at any moment, even in the middle of the tour or a long search, when
the voice loop is busy and not listening for commands.

It has its own copy of the microphone (Ears.tap) and its own tiny Vosk recognizer that only knows the stop phrases,
so it costs little and never takes audio from the voice loop. It only listens while Batsy is busy (talking or
working); when Batsy is idle, "Batsy, stop" goes through the voice loop like any command.
"""
import json
import logging
import queue
import threading

from batsy.voice.ears import RATE

log = logging.getLogger("batsy.killword")

STOPS = ["stop", "be quiet", "shut up"]  # "cancel", "enough": too easy to hear in ordinary speech
# Not in the small Vosk model's vocabulary (it can't say which words it knows): "Batsy" is caught as "patsy",
# "betsy", "bat see" instead. Vosk would otherwise drop the name and leave a bare "stop" in the grammar.
OUT_OF_VOCABULARY = {"batsy", "batsie"}
MIN_CONF = 0.9


class KillWord(threading.Thread):
    def __init__(self, ears, wake_words: list[str], busy, on_kill):
        super().__init__(daemon=True, name="killword")
        from vosk import KaldiRecognizer

        self.frames = ears.tap()
        self.busy, self.on_kill = busy, on_kill
        model = ears.model
        names = [w for w in wake_words if not set(w.split()) & OUT_OF_VOCABULARY]
        stops = STOPS
        # Both orders, and the name is required: a bare "stop" from the room (or a video) must not kill anything.
        self.phrases = {f"{n} {s}" for n in names for s in stops} | {f"{s} {n}" for n in names for s in stops}
        self.rec = KaldiRecognizer(model, RATE, json.dumps(sorted(self.phrases) + ["[unk]"]))
        self.rec.SetWords(True)  # per-word confidence: a tiny grammar forces any speech onto its phrases
        log.info("kill word ready: %d phrases, e.g. %r", len(self.phrases), next(iter(sorted(self.phrases)), ""))

    def run(self) -> None:
        listening = False
        while True:
            try:
                frame = self.frames.get(timeout=1)
            except queue.Empty:
                continue
            if not self.busy():
                if listening:  # idle again: start clean next time
                    self.rec.Reset()
                    listening = False
                continue
            listening = True
            try:
                if not self.rec.AcceptWaveform(frame):  # wait for the end of what was said
                    continue
                words = json.loads(self.rec.Result()).get("result") or []
            except Exception:
                log.exception("kill word")
                continue
            hit = self._match(words)
            if hit:
                log.info("kill word heard: %r", hit)
                self.rec.Reset()
                try:
                    self.on_kill(f"heard {hit!r}")
                except Exception:
                    log.exception("kill")

    def _match(self, words: list[dict]) -> str | None:
        """The phrase must end what was said ("okay so, Batsy stop", not "the bus stop later") and every word of it
        must be confidently heard."""
        if not words or words[-1].get("word") == "[unk]":
            return None
        for p in self.phrases:
            n = len(p.split())
            tail = words[-n:]
            if " ".join(w["word"] for w in tail) == p and min(w.get("conf", 0) for w in tail) >= MIN_CONF:
                return p
        return None
