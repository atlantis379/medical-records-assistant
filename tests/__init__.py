"""Tests run with the login switched off unless a test is about the login itself (tests/test_auth.py turns it on)."""
import os

os.environ.setdefault("BINGLI_AUTH", "0")
