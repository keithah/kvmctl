"""Single operation catalog shared by CLI and MCP front ends."""

TOOL_SPEC = [
    {"name": "capabilities", "description": "Report device capabilities and identity.", "read_only": True},
    {"name": "snapshot", "description": "Capture a JPEG snapshot; returns bytes/SHA-256.", "read_only": True,
     "params": {"preview_max_width": "int"}},
    {"name": "ocr", "description": "OCR the screen (or a provided image); returns text.", "read_only": True,
     "params": {"image_b64": "str (optional base64 image)"}},
    {"name": "verify", "description": "Verify which machine is on screen.", "read_only": True,
     "params": {"machine": "str", "policy": "none|frame_change|ocr_identity|prompt_pattern"}},
    {"name": "select", "description": "Switch KVM port to a named machine (held-key recipe).", "write_gate": True,
     "params": {"machine": "str", "verify_policy": "str", "rearm": "bool", "settle_s": "float"}},
    {"name": "hid_reset", "description": "Reset the HID subsystem.", "write_gate": True},
    {"name": "rearm_otg", "description": "OTG gadget bounce to re-arm hotkey engine.", "write_gate": True},
    {"name": "exec_command", "description": "Run an allowlisted command over SSH; requires explicit SSH transport.",
     "write_gate": True, "params": {"command": "str", "transport": "ssh"}},
]

OPERATION_NAMES = frozenset(item["name"] for item in TOOL_SPEC)
