"""
notify_email.py — send the weekly report via the Resend API (https://resend.com).

Credentials come from the project .env (gitignored — never committed):
  RESEND_API_KEY=re_xxxxxxxx                              (required)
  EMAIL_TO=iamuudit@gmail.com                             (required; comma-sep for many)
  EMAIL_FROM=Weekly Stock Screen <onboarding@resend.dev>  (optional; default below)

Resend free tier: send from `onboarding@resend.dev` to the address you signed up
with, no domain verification needed. To send from your own address later, verify a
domain in the Resend dashboard and change EMAIL_FROM.

Test once your key is in .env:
  ./.venv/bin/python notify_email.py --test
Legal: personal/internal research use only.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_FROM = "Weekly Stock Screen <onboarding@resend.dev>"
API = "https://api.resend.com/emails"


def load_env(path=ENV):
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("RESEND_API_KEY", "EMAIL_TO", "EMAIL_FROM"):  # real env overrides file
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def send(subject, html, text=None, env=None):
    """POST to Resend. Returns the parsed JSON response, or raises RuntimeError."""
    env = env or load_env()
    key = env.get("RESEND_API_KEY")
    to = env.get("EMAIL_TO")
    frm = env.get("EMAIL_FROM") or DEFAULT_FROM
    if not key or not to:
        raise RuntimeError(
            f"RESEND_API_KEY and EMAIL_TO must be set in {ENV} "
            "(copy .env.example -> .env and fill them in).")
    payload = {"from": frm, "to": [t.strip() for t in to.split(",") if t.strip()],
               "subject": subject, "html": html}
    if text:
        payload["text"] = text
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Resend API {e.code}: {e.read().decode()[:400]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error reaching Resend: {e.reason}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="send a one-off test email")
    args = ap.parse_args()
    if args.test:
        try:
            res = send("✅ Weekly stock screen — test email",
                       "<p>If you can read this, Resend + .env are wired up correctly. "
                       "The real weekly report will arrive Saturday mornings.</p>",
                       "Resend + .env wired up correctly.")
            print("Sent OK:", res)
        except Exception as e:
            print("FAILED:", e, file=sys.stderr)
            sys.exit(1)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
