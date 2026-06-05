from pathlib import Path
from urllib.parse import unquote


def normalize_input_path(raw_path: str) -> str:
    decoded = str(raw_path or "").strip()
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return str(Path(decoded).expanduser().resolve())
