"""Token estimation: pluggable tokenizers, zero required dependencies.

Default is a v2 heuristic: CJK chars count ~1 token each; whitespace-delimited
words count ~1 token, but long words (>=8 chars — JSON, code, base64, URLs,
minified blobs) count ~1 token per 4 characters. v1 counted every word as 1
token, which undercounted code/JSON-heavy agent traffic by ~2x (ground-truthed
against tiktoken o200k_base on real session logs).

For absolute precision use ``--tokenizer tiktoken`` (requires the ``tiktoken``
package; not a hard dependency — the heuristic keeps the tool stdlib-only).
"""

from __future__ import annotations

import re

_CJK = re.compile(
    r"[一-鿿㐀-䶿"          # CJK unified
    r"぀-ヿ"                        # hiragana + katakana
    r"가-힯㄰-㆏"           # hangul
    r"＀-￯]"                       # fullwidth forms
)

LONG_WORD = 8  # chars; below this, 1 token/word is a fine approximation


def estimate_tokens(text: str | None) -> int:
    """Rough token count for mixed English/CJK text (v2 heuristic)."""
    if not text:
        return 0
    cjk_chars = len(_CJK.findall(text))
    remainder = _CJK.sub(" ", text)
    words = remainder.split()
    word_tokens = sum(max(1, -(-len(w) // 4)) for w in words)  # ceil(len/4)
    glue = int(0.15 * len(words))  # punctuation/whitespace overhead
    return cjk_chars + word_tokens + glue


def get_tokenizer(name: str = "heuristic", encoding: str = "o200k_base"):
    """Return a callable text -> int token count.

    name="heuristic" (default, stdlib-only) or name="tiktoken"
    (encoding defaults to o200k_base, a good cross-model approximation).
    """
    if name == "heuristic":
        return estimate_tokens
    if name == "tiktoken":
        try:
            import tiktoken
        except ImportError as exc:
            raise RuntimeError(
                "tiktoken is not installed. Install it with `pip install tiktoken`, "
                "or run with --tokenizer heuristic (default)."
            ) from exc
        enc = tiktoken.get_encoding(encoding)

        def count(text: str | None) -> int:
            if not text:
                return 0
            return len(enc.encode(text))

        return count
    raise ValueError(f"unknown tokenizer '{name}' (expected 'heuristic' or 'tiktoken')")
