#!/usr/bin/env python3
"""
EmailBison Daily Reply Rate Reporter

Uses the line-area-chart-stats endpoint to get yesterday's sent/replied
counts directly — no snapshots needed.
"""

import json
import os
import requests
from datetime import date, timedelta
from pathlib import Path

SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK"]
BASE_URL = "https://send.cleanleadsolution.com"
WORKSPACES = json.loads((Path(__file__).parent / "workspaces.json").read_text())


def headers(api_key):
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def get_daily_stats(api_key, target_date):
    """Fetch sent and replied counts for a specific date."""
    resp = requests.get(
        f"{BASE_URL}/api/workspaces/v1.1/line-area-chart-stats",
        headers=headers(api_key),
        params={"start_date": str(target_date), "end_date": str(target_date)},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    sent, replied = 0, 0
    for series in data:
        if series.get("label") == "Sent":
            sent = sum(v for _, v in series.get("dates", []))
        elif series.get("label") == "Replied":
            replied = sum(v for _, v in series.get("dates", []))
    return sent, replied


def get_active_campaigns(api_key):
    all_campaigns = []
    page = 1
    while True:
        resp = requests.get(
            f"{BASE_URL}/api/campaigns",
            headers=headers(api_key),
            params={"per_page": 100, "page": page},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            break
        all_campaigns.extend(data)
        if len(data) < 100:
            break
        page += 1
    return [c for c in all_campaigns if c.get("status") == "active" and c.get("type") != "reply_followup"]


def get_campaign_daily_stats(api_key, campaign_id, target_date):
    """Fetch sent and replied counts for a specific campaign and date."""
    resp = requests.post(
        f"{BASE_URL}/api/campaigns/{campaign_id}/stats",
        headers={**headers(api_key), "Content-Type": "application/json"},
        json={"start_date": str(target_date), "end_date": str(target_date)},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    sent = data.get("emails_sent", 0) or 0
    replied = data.get("unique_replies_per_contact", 0) or 0
    return sent, replied


def send_slack(blocks):
    requests.post(SLACK_WEBHOOK, json={"blocks": blocks}, timeout=10)


def main():
    yesterday = date.today() - timedelta(days=1)
    date_str = yesterday.strftime("%A, %b %-d")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📊 Daily Reply Rates — {date_str}"},
        },
    ]

    errors = []

    for ws in WORKSPACES:
        try:
            ws_sent, ws_replied = get_daily_stats(ws["api_key"], yesterday)

            if ws_sent == 0:
                continue

            ws_rate = ws_replied / ws_sent
            ws_rate_str = f"{ws_rate*100:.1f}%"
            ws_flag = " 🔴" if ws_rate < 0.02 else ""

            # Per-campaign breakdown
            campaign_lines = []
            try:
                campaigns = get_active_campaigns(ws["api_key"])
                for c in campaigns:
                    try:
                        c_sent, c_replied = get_campaign_daily_stats(ws["api_key"], c["id"], yesterday)
                        if c_sent > 0:
                            rate = c_replied / c_sent
                            rate_str = f"{rate*100:.1f}%"
                            flag = " 🔴" if rate < 0.02 else ""
                            campaign_lines.append(
                                f"• _{c['name']}_  —  {c_sent} sent / {c_replied} replied  =  *{rate_str}*{flag}"
                            )
                    except Exception as e:
                        errors.append(f"{ws['name']} / campaign {c.get('id')}: {e}")
            except Exception as e:
                errors.append(f"{ws['name']} campaigns: {e}")

            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{ws['name']}*  —  {ws_sent} sent / {ws_replied} replied  =  *{ws_rate_str}*{ws_flag}"},
            })
            if campaign_lines:
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "\n".join(campaign_lines)},
                })

        except Exception as e:
            errors.append(f"{ws['name']}: {e}")

    if errors:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "⚠️ *Errors:*\n" + "\n".join(f"• {e}" for e in errors)},
        })

    send_slack(blocks)
    print("Daily reply rate report sent.")


if __name__ == "__main__":
    main()
