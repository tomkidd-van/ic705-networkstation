#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Small pre-publish secret-pattern scan for this repository."""
from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERNS = [
    re.compile(r"ICOM_PASSWORD\s*=\s*[^<\s]+", re.IGNORECASE),
    re.compile(r"--password\s+['\"]?[^<\s]+", re.IGNORECASE),
    re.compile(r"password\s*=\s*['\"][^'\"]{4,}['\"]", re.IGNORECASE),
]
SKIP_SUFFIXES = {".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".wav", ".raw", ".pcap", ".pcapng", ".zip"}


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    hits: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if "<radio-password>" in line or "your-radio-password" in line:
                continue
            if 'ICOM_PASSWORD="$ICOM_PASSWORD"' in line or "ICOM_PASSWORD='$ICOM_PASSWORD'" in line:
                continue
            if "--host/--user/--password" in line:
                continue
            if "ICOM_PASSWORD" in line and ("May also be set" in line or "--host/--user/--password" in line):
                continue
            for pattern in PATTERNS:
                if pattern.search(line):
                    hits.append(f"{path}:{lineno}: {line.strip()}")
    if hits:
        print("Potential secrets found:")
        for hit in hits:
            print(hit)
        return 1
    print("No configured secret-pattern hits found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
