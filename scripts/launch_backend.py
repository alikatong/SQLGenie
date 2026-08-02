"""Launch the SQLGenie backend in a process detached from the caller's console."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_PACKAGES = ROOT / ".python_packages"
BACKEND_LOG = ROOT / "backend.log"
BACKEND_ERR_LOG = ROOT / "backend.err.log"


def main() -> int:
    environment = os.environ.copy()
    python_path = [str(PYTHON_PACKAGES), str(ROOT)]
    if environment.get("PYTHONPATH"):
        python_path.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(python_path)
    environment.setdefault("PYTHONIOENCODING", "utf-8")

    creation_flags = 0
    backend_python = Path(sys.executable)
    if os.name == "nt":
        # Keep the server alive after the .cmd window is closed and do not create a console.
        creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        pythonw = backend_python.with_name("pythonw.exe")
        if pythonw.exists():
            backend_python = pythonw

    with BACKEND_LOG.open("a", encoding="utf-8") as stdout, BACKEND_ERR_LOG.open(
        "a", encoding="utf-8"
    ) as stderr:
        process = subprocess.Popen(
            [str(backend_python), str(ROOT / "scripts" / "run_backend.py")],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=creation_flags,
        )

    print(f"[sqlGenie] Backend launched (PID={process.pid}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
