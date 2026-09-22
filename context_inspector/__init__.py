"""Context Inspector — see what is actually eating your agent's context window."""

from .adapters import SOURCES, detect_source, load_any
from .analyzer import analyze, load_transcript, render_text, to_dict
from .estimator import estimate_tokens, get_tokenizer

__all__ = [
    "analyze",
    "load_transcript",
    "load_any",
    "detect_source",
    "render_text",
    "to_dict",
    "estimate_tokens",
    "get_tokenizer",
    "SOURCES",
]
__version__ = "0.3.2"
