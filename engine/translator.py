"""
engine/translator.py — Malayalam → English input translation

Uses deep-translator (Google Translate wrapper) for the prototype/base-model phase.
Switch to Google Translate API v2 before patient-facing production deployment.

Only translates the RAG query input. Gemini generates the response directly
in Malayalam — no output translation step is needed.
"""

import logging

log = logging.getLogger(__name__)


def translate_to_english(text: str) -> str:
    """
    Translate Malayalam input to English for Phase 1 classification and RAG query.

    Falls back to the original text on any error — English queries still work
    correctly, and medical shorthand (HbA1c, mg/dL, sugar) survives as-is.
    """
    try:
        from deep_translator import GoogleTranslator
        translated = GoogleTranslator(source="auto", target="en").translate(text)
        log.debug(
            "translator: ml→en | original=%r | translated=%r",
            text[:60], (translated or "")[:60],
        )
        return translated or text
    except Exception as exc:
        log.warning("translator: translation failed (%s) — using original text", exc)
        return text
