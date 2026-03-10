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


def get_campaign_capacity(api_key, campaign_id):
    """Sum daily_limit across all sender emails assigned to a campaign."""
    try:
        resp = requests.get(
            f"{BASE_URL}/api/campaigns/{campaign_id}/sender-emails",
            headers=headers(api_key),
            timeout=15,
        )
        resp.raise_for_status()
        accounts = resp.json().get("data", [])
        return sum(a.get("daily_limit", 0) or 0 for a in accounts)
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

            for c in campaigns:
                try:
                    detail = get_campaign_detail(ws["api_key"], c["id"])
                    total = detail.get("total_leads", 0)
                    contacted = detail.get("total_leads_contacted", 0)
                    remaining = max(total - contacted, 0)
                    ws_total_remaining += remaining
                    emails_today = today_schedule.get(c["id"])
                    emails_dat = dat_schedule.get(c["id"])
                    capacity = get_campaign_capacity(ws["api_key"], c["id"])
                    low_leads = (capacity is not None and emails_dat is not None
                                 and emails_dat < capacity - 100)
                    ws_campaigns.append({
                        "campaign": detail.get("name", f"Campaign {c['id']}"),
                        "remaining": remaining,
                        "emails_today": emails_today,
                        "emails_dat": emails_dat,
                        "capacity": capacity,
                        "low_leads": low_leads,
                    })
                except Exception as e:
                    errors.append(f"{ws['name']} / campaign {c.get('id', '?')}: {e}")

            needs_refill = ws_total_remaining < THRESHOLD or any(c["low_leads"] for c in ws_campaigns)
            workspaces.append({
                "name": ws["name"],
                "total_remaining": ws_total_remaining,
                "campaigns": ws_campaigns,
                "needs_refill": needs_refill,
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
            today = f"{c['emails_today']:,}" if c.get("emails_today") is not None else "?"
            dat = f"{c['emails_dat']:,}" if c.get("emails_dat") is not None else "?"
            cap = f"{c['capacity']:,}" if c.get("capacity") is not None else "?"
            refill_flag = " 🔴 *refill needed*" if c.get("low_leads") else ""
            lines.append(
                f"• _{c['campaign']}_  —  *{c['remaining']:,}* leads left{flag}\n"
                f"  Today: *{today}*  |  Day after tomorrow: *{dat}* / {cap} capacity{refill_flag}"
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
