"""Entry point for the packaged app (PyInstaller) and for running from source: py run.py [--refresh]"""
import sys

from training_export.app import main

if __name__ == "__main__":
    sys.exit(main())
