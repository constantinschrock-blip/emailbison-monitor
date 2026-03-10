#!/usr/bin/env python3
"""
EmailBison Morning Check
For each workspace, compares emails scheduled for today / tomorrow / day-after-tomorrow
against the total sending capacity (connected accounts × daily limit).
Flags any workspace where scheduled volume falls below capacity on any of those days.
"""

import json
import os
import requests
from datetime import datetime
from pathlib import Path

SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK"]
BASE_URL = "https://send.cleanleadsolution.com"
WORKSPACES = json.loads((Path(__file__).parent / "workspaces.json").read_text())


def headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


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
        data = resp.json()
        campaigns = data.get("data", data) if isinstance(data, dict) else data
        if not campaigns:
            break
        all_campaigns.extend(campaigns)
        if len(campaigns) < 100:
            break
        page += 1
    return [c for c in all_campaigns if c.get("status") == "active" and c.get("type") != "reply_followup"]


def get_sending_schedule(api_key, day):
    """Returns total emails scheduled across all campaigns for a given day."""
    try:
        resp = requests.get(
            f"{BASE_URL}/api/campaigns/sending-schedules",
            headers=headers(api_key),
            json={"day": day},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return sum(item.get("emails_being_sent", 0) for item in data)
    except Exception:
        return None


def get_workspace_capacity(api_key):
    """Sum daily_limit for all connected sender email accounts in the workspace."""
    try:
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
        connected = [a for a in all_accounts if a.get("status") == "Connected"]
        return sum(a.get("daily_limit", 0) or 0 for a in connected)
    except Exception:
        return None


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


def fmt(n):
    return f"{n:,}" if n is not None else "?"


def day_status(scheduled, capacity):
    """Return (display_str, is_low)."""
    if scheduled is None or capacity is None:
        return f"{fmt(scheduled)} / {fmt(capacity)}", False
    is_low = scheduled < capacity
    icon = "🔴" if is_low else "✅"
    return f"{icon} {fmt(scheduled)} / {fmt(capacity)}", is_low


def main():
    workspaces = []
    errors = []

    for ws in WORKSPACES:
        try:
            capacity = get_workspace_capacity(ws["api_key"])
            today     = get_sending_schedule(ws["api_key"], "today")
            tomorrow  = get_sending_schedule(ws["api_key"], "tomorrow")
            dat       = get_sending_schedule(ws["api_key"], "day_after_tomorrow")

            # Per-campaign remaining leads
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
                        "name": detail.get("name", f"Campaign {c['id']}"),
                        "remaining": remaining,
                    })
                except Exception as e:
                    errors.append(f"{ws['name']} / campaign {c.get('id', '?')}: {e}")

            _, low_today    = day_status(today,    capacity)
            _, low_tomorrow = day_status(tomorrow,  capacity)
            _, low_dat      = day_status(dat,        capacity)
            needs_refill = low_today or low_tomorrow or low_dat

            workspaces.append({
                "name": ws["name"],
                "capacity": capacity,
                "today": today,
                "tomorrow": tomorrow,
                "dat": dat,
                "low_today": low_today,
                "low_tomorrow": low_tomorrow,
                "low_dat": low_dat,
                "needs_refill": needs_refill,
                "total_remaining": ws_total_remaining,
                "campaigns": ws_campaigns,
            })
        except Exception as e:
            errors.append(f"{ws['name']}: {e}")

    date_str = datetime.now().strftime("%A, %b %-d")
    any_refill = any(w["needs_refill"] for w in workspaces)
    header_text = f"🔴 Refill needed — {date_str}" if any_refill else f"✅ All good — {date_str}"

    blocks = [{"type": "header", "text": {"type": "plain_text", "text": header_text}}]

    for ws in workspaces:
        if not ws["campaigns"]:
            continue

        icon = "🔴" if ws["needs_refill"] else "✅"
        cap = fmt(ws["capacity"])

        today_str,    _ = day_status(ws["today"],    ws["capacity"])
        tomorrow_str, _ = day_status(ws["tomorrow"],  ws["capacity"])
        dat_str,      _ = day_status(ws["dat"],        ws["capacity"])

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{icon} *{ws['name']}*  —  capacity: *{cap}/day*  |  {ws['total_remaining']:,} leads remaining\n"
                    f"Today: {today_str}  |  Tomorrow: {tomorrow_str}  |  +2 days: {dat_str}"
                ),
            },
        })

        # Campaign breakdown
        lines = [f"• _{c['name']}_  —  *{c['remaining']:,}* leads left" for c in ws["campaigns"]]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})

    if errors:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "⚠️ *Errors:*\n" + "\n".join(f"• {e}" for e in errors)},
        })

    send_slack(blocks)
    refill_count = sum(1 for w in workspaces if w["needs_refill"])
    print(f"Sent report. {refill_count} workspace(s) need refill.")


if __name__ == "__main__":
    main()
