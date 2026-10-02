"""Build the native ZettelLinker executable for the current operating system."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or newer is required to build ZettelLinker")
    if importlib.util.find_spec("PyInstaller") is None:
        raise SystemExit(
            "PyInstaller is not installed. Run: python -m pip install -e '.[build]'"
        )
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--onefile",
        "--console",
        "--name",
        "zettellinker",
        "--specpath",
        str(ROOT / "build"),
        "--paths",
        str(ROOT / "src"),
        "--collect-all",
        "sentence_transformers",
        "--hidden-import",
        "transformers.models.bert.configuration_bert",
        "--hidden-import",
        "transformers.models.bert.modeling_bert",
        "--hidden-import",
        "zettellinker.ui_server",
        "--hidden-import",
        "zettellinker.gui_app",
        str(ROOT / "packaging" / "zettellinker_launcher.py"),
    ]
    if importlib.util.find_spec("usearch") is not None:
        hidden = command.index("--hidden-import")
        command[hidden:hidden] = ["--collect-all", "usearch"]
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(ROOT / "build" / "pyinstaller-config")
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    executable = ROOT / "dist" / ("zettellinker.exe" if sys.platform == "win32" else "zettellinker")
    subprocess.run([str(executable), "--version"], check=True)
    print(f"Executable ready: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
