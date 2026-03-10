#!/usr/bin/env python3
"""
EmailBison Morning Check
Alerts via Slack when active campaigns have fewer than THRESHOLD leads remaining.
"""

import json
import os
import requests
from datetime import datetime
from pathlib import Path

SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK"]
THRESHOLD = 1500
BASE_URL = "https://send.cleanleadsolution.com"
WORKSPACES = json.loads((Path(__file__).parent / "workspaces.json").read_text())


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


def get_sending_schedule(api_key, day="today"):
    """Fetch emails_being_sent for all campaigns on a given day. Returns {campaign_id: count}."""
    try:
        resp = requests.get(
            f"{BASE_URL}/api/campaigns/sending-schedules",
            headers=headers(api_key),
            json={"day": day},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return {item["campaign_id"]: item.get("emails_being_sent", 0) for item in data}
    except Exception:
        return {}


def get_workspace_capacity(api_key):
    """Sum daily_limit across all sender email accounts in the workspace."""
    try:
        all_accounts = []
        page = 1
        while True:
            resp = requests.get(
                f"{BASE_URL}/api/sender-emails",
                headers=headers(api_key),
                params={"per_page": 100, "page": page},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                break
            all_accounts.extend(data)
            if len(data) < 100:
                break
            page += 1
        return sum(a.get("daily_limit", 0) or 0 for a in all_accounts)
    except Exception:
        return None


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
            today_schedule = get_sending_schedule(ws["api_key"], "today")
            dat_schedule = get_sending_schedule(ws["api_key"], "day_after_tomorrow")
            ws_capacity = get_workspace_capacity(ws["api_key"])

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
                        "emails_today": today_schedule.get(c["id"]),
                        "emails_dat": dat_schedule.get(c["id"]),
                    })
                except Exception as e:
                    errors.append(f"{ws['name']} / campaign {c.get('id', '?')}: {e}")

            ws_dat_total = sum(c["emails_dat"] or 0 for c in ws_campaigns if c["emails_dat"] is not None)
            low_dat = ws_capacity is not None and ws_dat_total < ws_capacity - 100
            needs_refill = ws_total_remaining < THRESHOLD or low_dat
            workspaces.append({
                "name": ws["name"],
                "total_remaining": ws_total_remaining,
                "campaigns": ws_campaigns,
                "needs_refill": needs_refill,
                "ws_dat_total": ws_dat_total,
                "ws_capacity": ws_capacity,
                "low_dat": low_dat,
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
        cap_str = f"{ws['ws_capacity']:,}" if ws.get("ws_capacity") is not None else "?"
        dat_str = f"{ws['ws_dat_total']:,}"
        refill_note = f"  🔴 *only {dat_str} / {cap_str} capacity scheduled day after tomorrow*" if ws.get("low_dat") else ""
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{icon} *{ws['name']}*  —  {ws['total_remaining']:,} leads remaining{refill_note}"},
        })
        lines = []
        for c in ws["campaigns"]:
            flag = " ⚠️" if c["remaining"] == 0 else ""
            today = f"{c['emails_today']:,}" if c.get("emails_today") is not None else "?"
            dat = f"{c['emails_dat']:,}" if c.get("emails_dat") is not None else "?"
            lines.append(
                f"• _{c['campaign']}_  —  *{c['remaining']:,}* leads left{flag}  |  today: *{today}*  /  day after tomorrow: *{dat}*"
            )
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
