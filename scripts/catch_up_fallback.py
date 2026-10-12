"""One-time fallback for the October 12 catch-up report.

The full card can fail even after the report is generated. If so, send a
short plain-text digest using the same GitHub Actions Feishu webhook secret.
Only sanitized error categories are written to the public Actions page.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import requests


def main() -> int:
    report = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    error = Path(sys.argv[2]).read_text(encoding="utf-8")

    if not report or "[pusher] pushing digest" not in error:
        print("::error::Report generation failed before delivery")
        return 1

    if "Feishu error code=" in error:
        match = re.search(r"Feishu error code=([0-9]+)", error)
        reason = f"Feishu code {match.group(1)}" if match else "Feishu API error"
    elif "[pusher] HTTP " in error:
        match = re.search(r"\[pusher\] HTTP ([0-9]+)", error)
        reason = f"Feishu HTTP {match.group(1)}" if match else "Feishu HTTP error"
    elif "[pusher] request failed" in error:
        reason = "Feishu network request failed"
    elif "no valid FEISHU_WEBHOOK_URL" in error:
        reason = "Feishu webhook is invalid or a placeholder"
    else:
        reason = "full card delivery failed"
    print(f"::warning::Full card delivery failed ({reason}); trying short text")

    excerpt = report[:2800].rsplit("\n", 1)[0] or report[:2800]
    payload = {
        "msg_type": "text",
        "content": {"text": "【AI 早报 11:00 补发】\n" + excerpt},
    }
    try:
        response = requests.post(
            os.environ["FEISHU_WEBHOOK_URL"], json=payload, timeout=15
        )
    except requests.RequestException as exc:
        print(f"::error::Plain-text fallback request failed ({type(exc).__name__})")
        return 1

    if response.status_code != 200:
        print(f"::error::Plain-text fallback HTTP {response.status_code}")
        return 1
    try:
        result = response.json()
    except ValueError:
        print("::error::Plain-text fallback returned a non-JSON response")
        return 1
    code = result.get("code", result.get("StatusCode", 0))
    if code not in (0, None):
        safe_code = str(code) if str(code).isdigit() else "unknown"
        print(f"::error::Plain-text fallback Feishu code {safe_code}")
        return 1

    print("::notice::Short AI news report delivered by plain-text fallback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
