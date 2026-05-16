"""
Parser registry — maps corpus source names to their parser instances.

Source names come from config/corpus_manifest.json.
"""

from __future__ import annotations

from pathlib import Path

from .ada_journal import ADAJournalParser
from .base import BaseParser, ParsedDocument
from .food_table import ICMRNINParser
from .narrative import NarrativeParser
from .recommendation import IDFDARParser, KDIGOParser, RSSDirectParser
from .workflow import ICMRWorkflowParser

# source_name (from corpus_manifest.json) → parser instance
_REGISTRY: dict[str, BaseParser] = {
    # Tier 1 — core always-active
    "ADA_2026":                         ADAJournalParser(),
    "RSSDI_2022":                       RSSDirectParser(),
    "ICMR_STW_2024":                    ICMRWorkflowParser(),
    "ICMR_NIN":                         ICMRNINParser(),
    "Anoop_Misra_South_Asian_Nutrition": ADAJournalParser(),  # same two-col format

    # Tier 2 — condition-triggered
    "KDIGO_2022_DM_CKD":                KDIGOParser(),
    "IDF_DAR":                          IDFDARParser(),
    "ESC_2023_CV_DM":                   ADAJournalParser(),  # ESJ two-col format

    # WHO HEARTS — single narrative columns with tables
    "WHO_HEARTS":                       NarrativeParser(),

    # Compliance namespace
    "Telemedicine_Guidelines_2020":     NarrativeParser(),
}


def get_parser(source: str) -> BaseParser:
    """Return the parser for the given source name.

    Raises KeyError if source is not registered.
    """
    if source not in _REGISTRY:
        raise KeyError(
            f"No parser registered for source '{source}'. "
            f"Available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[source]


def parse_document(source: str, path: Path) -> ParsedDocument:
    """Convenience: look up parser and run it."""
    return get_parser(source).parse(path, source)


def list_sources() -> list[str]:
    return sorted(_REGISTRY)
