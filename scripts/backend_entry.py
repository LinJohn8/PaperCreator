"""PyInstaller entry point for the self-contained desktop backend."""

from papercreator.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
