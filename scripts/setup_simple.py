#!/usr/bin/env python3
"""Compatibility entry point for the canonical GreenRoute database setup."""

import sys

from init_db import main


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
