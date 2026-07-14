from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request


def main() -> int:
    if len(sys.argv) < 2:
        return 1

    url = sys.argv[1]
    attempts = int(sys.argv[2]) if len(sys.argv) >= 3 else 40
    interval = float(sys.argv[3]) if len(sys.argv) >= 4 else 0.5

    for _ in range(max(attempts, 1)):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return 0
        except (OSError, urllib.error.URLError):
            pass

        time.sleep(max(interval, 0.0))

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
