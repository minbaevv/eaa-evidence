from __future__ import annotations

from pathlib import Path
from urllib.request import urlopen

AXE_VERSION = "4.14.0"
AXE_URL = f"https://cdn.jsdelivr.net/npm/axe-core@{AXE_VERSION}/axe.min.js"
TARGET = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "static"
    / "vendor"
    / "axe.min.js"
)


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(AXE_URL, timeout=30) as response:
        data = response.read()
    if len(data) < 100_000:
        raise SystemExit("Downloaded axe-core file looks too small.")
    TARGET.write_bytes(data)
    print(f"Wrote {TARGET} ({len(data):,} bytes)")


if __name__ == "__main__":
    main()
