"""Select the local Ollama brain or Codex for a request."""
from __future__ import annotations

import re

CODEX_PATTERNS = (
    r"\b(code|coding|program|script|function|class|module|api|website|tests?|debug|refactor|implement|"
    r"edit|build|compile|arduino|esp32|project|multi[- ]file|github|document analysis|"
    r"analy[sz]e|research|detailed|step[- ]by[- ]step)\b",
)


def route(text: str, explicit: bool = False) -> str:
    if explicit or re.search(r"\b(codex|openai)\b", text, re.IGNORECASE):
        return "codex"
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in CODEX_PATTERNS):
        return "codex"
    return "ollama"
