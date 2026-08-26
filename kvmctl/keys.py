"""Browser-style key-name mapping for KVMD HID (verified: ControlRight, Digit2, Enter)."""

_BASE = {
    "a": "KeyA", "b": "KeyB", "c": "KeyC", "d": "KeyD", "e": "KeyE",
    "f": "KeyF", "g": "KeyG", "h": "KeyH", "i": "KeyI", "j": "KeyJ",
    "k": "KeyK", "l": "KeyL", "m": "KeyM", "n": "KeyN", "o": "KeyO",
    "p": "KeyP", "q": "KeyQ", "r": "KeyR", "s": "KeyS", "t": "KeyT",
    "u": "KeyU", "v": "KeyV", "w": "KeyW", "x": "KeyX", "y": "KeyY",
    "z": "KeyZ",
    "1": "Digit1", "2": "Digit2", "3": "Digit3", "4": "Digit4",
    "5": "Digit5", "6": "Digit6", "7": "Digit7", "8": "Digit8",
    "9": "Digit9", "0": "Digit0",
    "-": "Minus", "=": "Equal", "[": "BracketLeft", "]": "BracketRight",
    ";": "Semicolon", "'": "Quote", "`": "Backquote", "\\": "Backslash",
    ",": "Comma", ".": "Period", "/": "Slash", " ": "Space",
}
_SHIFTED = {
    "!": ("Digit1",), "@": ("Digit2",), "#": ("Digit3",), "$": ("Digit4",),
    "%": ("Digit5",), "^": ("Digit6",), "&": ("Digit7",), "*": ("Digit8",),
    "(": ("Digit9",), ")": ("Digit0",),
    "_": ("Minus",), "+": ("Equal",), "{": ("BracketLeft",),
    "}": ("BracketRight",), ":": ("Semicolon",), '"': ("Quote",),
    "~": ("Backquote",), "|": ("Backslash",), "<": ("Comma",),
    ">": ("Period",), "?": ("Slash",),
}


def char_to_key(ch: str) -> tuple[str, bool]:
    """Return (KVMD key name, needs_shift) for a character."""
    if ch in _BASE:
        return _BASE[ch], False
    if ch in _SHIFTED:
        return _SHIFTED[ch][0], True
    if ch.isupper():
        return _BASE[ch.lower()], True
    raise ValueError(f"no KVMD key mapping for character {ch!r}")
