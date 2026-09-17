#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only operational checks for the FSTDD coordination hub."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8788")
    args = ap.parse_args()
    url = args.url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read())
            if response.status != 200 or payload.get("ok") is not True:
                print(json.dumps(payload, ensure_ascii=False))
                return 1
            print(json.dumps(payload, ensure_ascii=False))
            return 0
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"hub healthcheck failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
