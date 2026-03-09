#!/usr/bin/env python3
"""
EmailBison Morning Check
Alerts via Slack when active campaigns have fewer than THRESHOLD leads remaining.
"""

import os
import requests
from datetime import datetime

SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK"]
THRESHOLD = 1500
BASE_URL = "https://send.cleanleadsolution.com"

WORKSPACES = [
    {"name": "All Pro Cleaning Systems | Atlanta, GA", "api_key": "7|VIaEykaex8XWu553UTRgiM11rsvi8WlmiBhq4FMl6ea3b721"},
    {"name": "EdenSpokane",                            "api_key": "10|6qqezrdvL0wRtF6D6qO2djbUdjr2koWLJmhtUhaB513c71e3"},
    {"name": "HealthPoint",                            "api_key": "11|KV6Apy8YtBtSAzc32NhvPeape1PQ2JFsW8mkUCAxefc515e8"},
    {"name": "eMop - Dublin",                          "api_key": "15|Z4A5VPHBY7YhLFMiOheJwQqUi2eiWqhOy8UEeC4wcd504acb"},
    {"name": "On Point Pressure Washing",              "api_key": "16|B2ClBHxtXLbLehYXbO0OUTrSBAH1hOHt6VF7oHYla39a9ae7"},
    {"name": "Well-Polished",                          "api_key": "20|VfwL9TxskQ47NCp5vdcieV4iCF8kWTBrf0L8So4753191c75"},
    {"name": "eMop",                                   "api_key": "21|dkGsbKloL6LmeLn7VVW54EugWkR1dLoTJualVEOHa044232e"},
]


def headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def get_active_campaigns(api_key):
    """Fetch all campaigns and filter active ones client-side (handles pagination)."""
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
        data = resp.json()
        campaigns = data.get("data", data) if isinstance(data, dict) else data
        if not campaigns:
            break
        all_campaigns.extend(campaigns)
        # stop if we got fewer than a full page
        if len(campaigns) < 100:
            break
        page += 1
    return [c for c in all_campaigns if c.get("status") == "active" and c.get("type") != "reply_followup"]


def get_campaign_detail(api_key, campaign_id):
    resp = requests.get(
        f"{BASE_URL}/api/campaigns/{campaign_id}",
        headers=headers(api_key),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", data) if isinstance(data, dict) else data


def send_slack(blocks):
    requests.post(SLACK_WEBHOOK, json={"blocks": blocks}, timeout=10)


def main():
    workspaces = []
    errors = []

    for ws in WORKSPACES:
        try:
            campaigns = get_active_campaigns(ws["api_key"])
            ws_campaigns = []
            ws_total_remaining = 0

            for c in campaigns:
                try:
                    detail = get_campaign_detail(ws["api_key"], c["id"])
                    total = detail.get("total_leads", 0)
                    contacted = detail.get("total_leads_contacted", 0)
                    remaining = max(total - contacted, 0)
                    ws_total_remaining += remaining
                    ws_campaigns.append({
                        "campaign": detail.get("name", f"Campaign {c['id']}"),
                        "remaining": remaining,
                    })
                except Exception as e:
                    errors.append(f"{ws['name']} / campaign {c.get('id', '?')}: {e}")

            workspaces.append({
                "name": ws["name"],
                "total_remaining": ws_total_remaining,
                "campaigns": ws_campaigns,
                "needs_refill": ws_total_remaining < THRESHOLD,
            })
        except Exception as e:
            errors.append(f"{ws['name']}: {e}")

    date_str = datetime.now().strftime("%A, %b %-d")
    needs_refill = [w for w in workspaces if w["needs_refill"]]
    header_text = f"🔴 Refill needed — {date_str}" if needs_refill else f"✅ All good — {date_str}"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header_text},
        },
    ]

    for ws in workspaces:
        if not ws["campaigns"]:
            continue
        icon = "🔴" if ws["needs_refill"] else "✅"
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{icon} *{ws['name']}*  —  {ws['total_remaining']:,} total leads remaining"},
        })
        lines = []
        for c in ws["campaigns"]:
            flag = " ⚠️" if c["remaining"] == 0 else ""
            lines.append(f"• _{c['campaign']}_  —  *{c['remaining']:,}* leads left{flag}")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(lines)},
        })

    if errors:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "⚠️ *Errors:*\n" + "\n".join(f"• {e}" for e in errors)},
        })

    send_slack(blocks)
    print(f"Sent daily report. {len(needs_refill)} workspace(s) need refill.")


if __name__ == "__main__":
    main()
