#!/usr/bin/env python3
"""Forced SSH command: accept only an immutable muse2api release."""
import os
from pathlib import Path
import re
import shlex
import sys


def main():
    try:
        args = shlex.split(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    except ValueError:
        args = []
    if (len(args) != 4 or args[0] != "deploy"
            or not re.fullmatch(r"[0-9a-f]{40}", args[1])
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", args[2])
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?", args[3])):
        sys.exit("Only 'deploy <commit-sha> <image-digest> <registry-user>' is permitted")
    script = Path(__file__).resolve().with_name("deploy.sh")
    os.execv("/bin/bash", ["bash", str(script), *args[1:]])


if __name__ == "__main__":
    main()
