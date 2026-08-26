"""Force LLM output (and agent-visible strings) onto ASCII."""

from __future__ import annotations

import unicodedata
from typing import Any

ASCII_OUTPUT_RULES = """
CHARACTER SET (non-negotiable):
- Every string you output (JSON values, assistant_message, markdown, Mermaid labels)
  MUST use ASCII only: tab, newline, carriage return, and bytes 0x20-0x7E.
- No emoji, smart quotes, em dashes, non-ASCII letters, or symbols such as
  arrows, ellipsis, or math glyphs.
- Write -> not a unicode arrow, - not an em dash, ... not an ellipsis character,
  <= >= != x approx not their unicode forms, and ? not a question-mark emoji.
- JSON itself stays ASCII. Escape newlines in strings as \\n.
""".strip()


def with_ascii_instruction(system: str) -> str:
    body = system or ""
    if "CHARACTER SET (non-negotiable)" in body:
        return body
    return f"{ASCII_OUTPUT_RULES}\n\n{body}"


_REPLACEMENTS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
        "\u2022": "-",
        "\u00b7": "-",
        "\u00d7": "x",
        "\u00f7": "/",
    }
)

_MULTI = (
    ("\u2026", "..."),
    ("\u2190", "<-"),
    ("\u2192", "->"),
    ("\u2194", "<->"),
    ("\u21d2", "=>"),
    ("\u21e8", "->"),
    ("\u27a1", "->"),
    ("\u00bb", ">>"),
    ("\u00ab", "<<"),
    ("\u2248", "approx"),
    ("\u2264", "<="),
    ("\u2265", ">="),
    ("\u2260", "!="),
    ("\u2753", "?"),
    ("\u2754", "?"),
    ("\u2049", "?!"),
    ("\u2122", "(TM)"),
    ("\u00ae", "(R)"),
)


def fold_to_ascii(text: str) -> str:
    """Best-effort ASCII for chat, specs, and diagrams. Keeps JSON-safe whitespace."""
    if not text:
        return text
    body = text.translate(_REPLACEMENTS)
    for src, dst in _MULTI:
        body = body.replace(src, dst)
    out: list[str] = []
    for ch in body:
        o = ord(ch)
        if ch in "\t\n\r" or 32 <= o <= 126:
            out.append(ch)
            continue
        decomposed = unicodedata.normalize("NFKD", ch)
        ascii_part = decomposed.encode("ascii", "ignore").decode("ascii")
        out.append(ascii_part)
    return "".join(out)


def fold_json_strings(value: Any) -> Any:
    if isinstance(value, str):
        return fold_to_ascii(value)
    if isinstance(value, list):
        return [fold_json_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: fold_json_strings(item) for key, item in value.items()}
    return value
