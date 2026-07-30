"""Small, stable entry point used by PyInstaller."""

from zettellinker.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
