"""Convert a shell-style env file to Docker's ``--env-file`` format.

The input may contain ``export NAME=value`` or ``NAME=value`` lines. Values
are copied without evaluating shell syntax and are never printed.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def normalize(source: str | Path, destination: str | Path) -> int:
    output: list[str] = []
    for raw in Path(source).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            raise ValueError(f"invalid env line: {line.split('=', 1)[0]}")
        name, value = match.groups()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        output.append(f"{name}={value}")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(output) + ("\n" if output else ""), encoding="utf-8")
    return len(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize an env file for Docker")
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    count = normalize(args.source, args.destination)
    print(f"normalized {count} variables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
