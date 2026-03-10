#!/usr/bin/env python3
"""
EmailBison Domain Health Check
Flags email domains where the combined reply rate across all accounts is below 1%.
Only checks domains with enough sent volume to be meaningful (MIN_SENT threshold).
"""

import json
import os
import requests
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_HEALTH") or os.environ["SLACK_WEBHOOK"]
REPLY_RATE_THRESHOLD = 0.01   # 1%
MIN_SENT = 100                 # ignore accounts/domains with fewer sent emails
BASE_URL = "https://send.cleanleadsolution.com"
WORKSPACES = json.loads((Path(__file__).parent / "workspaces.json").read_text())


def headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }


def get_sender_emails(api_key):
    all_accounts = []
    page = 1
    while True:
        resp = requests.get(
            f"{BASE_URL}/api/sender-emails",
            headers=headers(api_key),
            params={"page": page},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", [])
        if not data:
            break
        all_accounts.extend(data)
        last_page = body.get("meta", {}).get("last_page", 1)
        if page >= last_page:
            break
        page += 1
    return all_accounts


def send_slack(blocks):
    requests.post(SLACK_WEBHOOK, json={"blocks": blocks}, timeout=10)


def main():
    date_str = datetime.now().strftime("%A, %b %-d")
    all_flagged = []
    errors = []

    for ws in WORKSPACES:
        try:
            accounts = get_sender_emails(ws["api_key"])

            # Group by domain
            domains = defaultdict(lambda: {"sent": 0, "replied": 0, "accounts": []})
            for acc in accounts:
                email = acc.get("email", "")
                domain = email.split("@")[-1] if "@" in email else "unknown"
                sent = acc.get("emails_sent_count", 0) or 0
                replied = acc.get("unique_replied_count", 0) or 0
                domains[domain]["sent"] += sent
                domains[domain]["replied"] += replied
                domains[domain]["accounts"].append({
                    "email": email,
                    "sent": sent,
                    "replied": replied,
                    "rate": replied / sent if sent >= MIN_SENT else None,
                })

            flagged_domains = []
            for domain, stats in sorted(domains.items()):
                if stats["sent"] < MIN_SENT:
                    continue
                rate = stats["replied"] / stats["sent"]
                if rate < REPLY_RATE_THRESHOLD:
                    flagged_domains.append({
                        "domain": domain,
                        "sent": stats["sent"],
                        "replied": stats["replied"],
                        "rate": rate,
                        "accounts": stats["accounts"],
                    })

            if flagged_domains:
                all_flagged.append({
                    "workspace": ws["name"],
                    "domains": flagged_domains,
                })

        except Exception as e:
            errors.append(f"{ws['name']}: {e}")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔥 Domain Health Check — {date_str}"},
        },
    ]

    if not all_flagged and not errors:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "✅ All domains are above 1% reply rate."},
        })
    else:
        for ws in all_flagged:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{ws['workspace']}*"},
            })
            for d in ws["domains"]:
                rate_pct = f"{d['rate']*100:.2f}%"
                account_lines = []
                for acc in d["accounts"]:
                    if acc["sent"] < MIN_SENT:
                        account_lines.append(f"    ◦ {acc['email']}  — too few sent to measure")
                    else:
                        acc_rate = f"{acc['rate']*100:.2f}%" if acc["rate"] is not None else "—"
                        account_lines.append(f"    ◦ {acc['email']}  — {acc['sent']} sent / {acc['replied']} replied ({acc_rate})")
                accounts_text = "\n".join(account_lines)
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"🔴 *{d['domain']}*  —  {d['sent']} sent / {d['replied']} replied  =  *{rate_pct}*\n"
                            f"{accounts_text}"
                        ),
                    },
                })

    if errors:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "⚠️ *Errors:*\n" + "\n".join(f"• {e}" for e in errors)},
        })

    send_slack(blocks)
    flagged_count = sum(len(w["domains"]) for w in all_flagged)
    print(f"Done. {flagged_count} domain(s) flagged across {len(all_flagged)} workspace(s).")


if __name__ == "__main__":
    main()
