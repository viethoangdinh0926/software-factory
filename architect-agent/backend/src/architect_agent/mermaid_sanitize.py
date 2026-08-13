from __future__ import annotations

import re

_FENCE_RE = re.compile(r"^```(?:mermaid)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
# Only quote rectangle/diamond labels — do NOT treat (...) as shapes (that mangles
# parentheses that appear inside already-quoted [] / {} labels).
_SHAPE_RES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"(\b[A-Za-z][\w-]*\s*)\[(?!\")([^\]\n]*?)\]"), "[", "]"),
    (re.compile(r"(\b[A-Za-z][\w-]*\s*)\{(?!\")([^\}\n]*?)\}"), "{", "}"),
)
_EDGE_TEXT_RE = re.compile(r"(--\s*)([^>\"\n]+?)(\s*-->)")
_EDGE_PIPE_RE = re.compile(r"(\|)([^|\n]+?)(\|)")
_SPECIAL_RE = re.compile(r"[()[\]{}|/\\@#%&=+]")
_STYLE_LINE_RE = re.compile(r"^(\s*style\s+\S+\s+)(.+)$", re.IGNORECASE)
_CLASSDEF_LINE_RE = re.compile(r"^(\s*classDef\s+\S+\s+)(.+)$", re.IGNORECASE)
_FILL_RE = re.compile(r"(?:^|,)\s*fill\s*:\s*(#[0-9a-fA-F]{3,8}|[a-zA-Z]+)")
_NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
}


def sanitize_mermaid(raw: str) -> str:
    """Quote Mermaid labels and ensure style fills get contrasting text colors."""
    source = raw.strip()
    if not source:
        return source
    source = re.sub(r"^```(?:mermaid)?\s*", "", source, flags=re.IGNORECASE)
    source = re.sub(r"\s*```$", "", source)

    # Edges first so later shape passes don't touch edge text.
    source = _EDGE_TEXT_RE.sub(_edge_text, source)
    source = _EDGE_PIPE_RE.sub(_edge_pipe, source)

    for pattern, open_ch, close_ch in _SHAPE_RES:
        source = pattern.sub(
            lambda m, o=open_ch, c=close_ch: _quote_shape(m, o, c),
            source,
        )

    source = "\n".join(
        _rewrite_classdef_contrast(_rewrite_style_contrast(line)) for line in source.splitlines()
    )
    return source


def _needs_quotes(label: str) -> bool:
    if not label:
        return False
    if label.startswith('"') and label.endswith('"'):
        return False
    return bool(_SPECIAL_RE.search(label))


def _escape_quotes(label: str) -> str:
    return label.replace('"', "#quot;")


def _quote_shape(match: re.Match[str], open_ch: str, close_ch: str) -> str:
    node_id, label = match.group(1), match.group(2)
    if not _needs_quotes(label):
        return f"{node_id}{open_ch}{label}{close_ch}"
    return f'{node_id}{open_ch}"{_escape_quotes(label)}"{close_ch}'


def _edge_text(match: re.Match[str]) -> str:
    pre, label, post = match.group(1), match.group(2).strip(), match.group(3)
    if not label or label.startswith("|") or label.startswith('"'):
        return match.group(0)
    if not _needs_quotes(label):
        return match.group(0)
    return f'{pre}"{_escape_quotes(label)}"{post}'


def _edge_pipe(match: re.Match[str]) -> str:
    inner = match.group(2)
    if inner.startswith('"') or not _needs_quotes(inner):
        return match.group(0)
    return f'|"{_escape_quotes(inner)}"|'


def _rewrite_style_contrast(line: str) -> str:
    match = _STYLE_LINE_RE.match(line)
    if not match:
        return line
    return f"{match.group(1)}{_normalize_fill_props(match.group(2))}"


def _rewrite_classdef_contrast(line: str) -> str:
    match = _CLASSDEF_LINE_RE.match(line)
    if not match:
        return line
    return f"{match.group(1)}{_normalize_fill_props(match.group(2))}"


def _normalize_fill_props(raw_props: str) -> str:
    props = raw_props.strip().rstrip(";").strip()
    fill_match = _FILL_RE.search(props)
    if not fill_match:
        return raw_props.strip()
    fill = _ensure_light_fill(fill_match.group(1))
    text_color = "#000000"
    parts = [
        p.strip()
        for p in props.split(",")
        if p.strip() and not re.match(r"(fill|color|font-weight)\s*:", p.strip(), re.I)
    ]
    props = f"fill:{fill}" + ("," + ",".join(parts) if parts else "")
    if "stroke:" not in props.lower():
        props = f"{props},stroke:#4a5a70"
    return f"{props},color:{text_color},font-weight:bold"


def _ensure_light_fill(fill: str) -> str:
    rgb = _parse_css_color(fill)
    if rgb is None:
        return "#b8d4f0"
    if _relative_luminance(rgb) >= 0.55:
        return _to_hex(rgb)
    # Dark fills → light tint of same hue so black text stays readable.
    lightened = (
        int(round(rgb[0] + (255 - rgb[0]) * 0.72)),
        int(round(rgb[1] + (255 - rgb[1]) * 0.72)),
        int(round(rgb[2] + (255 - rgb[2]) * 0.72)),
    )
    return _to_hex(lightened)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for c in rgb:
        s = c / 255.0
        channels.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _to_hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{max(0, min(255, c)):02x}" for c in rgb)


def _parse_css_color(value: str) -> tuple[int, int, int] | None:
    lower = value.lower()
    if lower in _NAMED_COLORS:
        return _NAMED_COLORS[lower]
    hex_value = value.lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", hex_value):
        return (
            int(hex_value[0] * 2, 16),
            int(hex_value[1] * 2, 16),
            int(hex_value[2] * 2, 16),
        )
    if re.fullmatch(r"[0-9a-fA-F]{6}", hex_value) or re.fullmatch(r"[0-9a-fA-F]{8}", hex_value):
        return (
            int(hex_value[0:2], 16),
            int(hex_value[2:4], 16),
            int(hex_value[4:6], 16),
        )
    return None
