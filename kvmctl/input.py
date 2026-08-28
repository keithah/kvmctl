"""Validated keyboard helpers for the user-facing KVM control surface."""
from __future__ import annotations

from .keys import char_to_key

ALIASES = {
    "ctrl": "ControlLeft", "control": "ControlLeft", "rctrl": "ControlRight",
    "shift": "ShiftLeft", "rshift": "ShiftRight", "alt": "AltLeft",
    "ralt": "AltRight", "cmd": "MetaLeft", "command": "MetaLeft",
    "win": "MetaLeft", "windows": "MetaLeft", "meta": "MetaLeft",
    "esc": "Escape", "escape": "Escape", "enter": "Enter", "return": "Enter",
    "tab": "Tab", "space": "Space", "backspace": "Backspace", "delete": "Delete",
    "del": "Delete", "home": "Home", "end": "End", "up": "ArrowUp",
    "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
    "capslock": "CapsLock", "scrolllock": "ScrollLock",
}
for _i in range(1, 13):
    ALIASES[f"f{_i}"] = f"F{_i}"

MODIFIERS = {"ControlLeft", "ControlRight", "ShiftLeft", "ShiftRight",
             "AltLeft", "AltRight", "MetaLeft", "MetaRight"}


def resolve_key(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("empty key name")
    if name in {"Enter", "Tab", "Space", "Escape", "Delete", "Backspace"}:
        return name
    if name.startswith(("Key", "Digit", "Numpad", "Arrow", "F")):
        return name
    return ALIASES.get(name.lower(), name)


def parse_combo(combo: str) -> tuple[list[str], str]:
    parts = [part.strip() for part in combo.split("+")]
    if not combo or any(not part for part in parts):
        raise ValueError("key combo must be non-empty and '+'-separated")
    keys = [resolve_key(part) for part in parts]
    modifiers, main = keys[:-1], keys[-1]
    if any(mod not in MODIFIERS for mod in modifiers):
        raise ValueError("all keys before the final combo key must be modifiers")
    return modifiers, main


def send_text(client, text: str, *, sleep, inter_char_s: float = 0.01) -> dict:
    sent = 0
    unsupported = []
    for ch in text:
        try:
            key, shifted = char_to_key(ch)
        except ValueError:
            unsupported.append(ch)
            continue
        if shifted:
            client.key_down("ShiftLeft")
        try:
            client.press_key(key)
        finally:
            if shifted:
                client.key_up("ShiftLeft")
        sent += 1
        if inter_char_s:
            sleep(inter_char_s)
    return {"chars": sent, "skipped": unsupported}
