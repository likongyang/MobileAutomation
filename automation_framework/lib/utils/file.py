"""File system utilities."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any


def ensure_dir(path: str | Path) -> Path:
    """Create directory and all parents. Return Path object."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_name(name: str) -> str:
    """Convert a string (e.g. device id or pytest nodeid) to a safe directory name."""
    return re.sub(r'[^\w\-]', '_', name).strip('_')


def write_json(path: str | Path, data: Any, *, indent: int = 2) -> None:
    """Write data as JSON to path, creating parent dirs as needed."""
    p = Path(path)
    ensure_dir(p.parent)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)


def read_json(path: str | Path) -> Any:
    """Read and parse JSON file."""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def append_jsonl(path: str | Path, data: Any) -> None:
    """Append a JSON line to a JSON Lines file."""
    p = Path(path)
    ensure_dir(p.parent)
    with open(p, 'a', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False) + '\n')


def copy_file(src: str | Path, dst: str | Path) -> None:
    """Copy a file, creating destination directory if needed."""
    dst_path = Path(dst)
    ensure_dir(dst_path.parent)
    shutil.copy2(str(src), str(dst))
