#!/usr/bin/env python3
"""
EmailBison Daily Snapshot & Reply Rate Reporter

Saves today's cumulative campaign stats to snapshots/YYYY-MM-DD.json.
If yesterday's snapshot exists, calculates day-over-day deltas and
sends a Slack report with daily reply rates per client.
"""

import json
import os
import requests
from datetime import date, timedelta
from pathlib import Path

SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK"]
BASE_URL = "https://send.cleanleadsolution.com"
SNAPSHOTS_DIR = Path(__file__).parent / "snapshots"

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
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def get_all_campaigns(api_key):
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
    # Only active campaigns matter for daily tracking
    return [c for c in all_campaigns if c.get("status") == "active" and c.get("type") != "reply_followup"]


def get_campaign_detail(api_key, campaign_id):
    resp = requests.get(
        f"{BASE_URL}/api/campaigns/{campaign_id}",
        headers=headers(api_key),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("data", {})


def take_snapshot():
    """Collect current stats for all workspaces and return as dict."""
    snapshot = {"date": str(date.today()), "workspaces": {}}
    errors = []

    for ws in WORKSPACES:
        try:
            campaigns = get_all_campaigns(ws["api_key"])
            ws_data = {"campaigns": {}}
            for c in campaigns:
                try:
                    detail = get_campaign_detail(ws["api_key"], c["id"])
                    ws_data["campaigns"][str(c["id"])] = {
                        "name": detail.get("name", ""),
                        "sent": detail.get("emails_sent", 0) or 0,
                        "replied": detail.get("replied", 0) or 0,
                        "unique_replied": detail.get("unique_replies", 0) or 0,
                        "contacted": detail.get("total_leads_contacted", 0) or 0,
                    }
                except Exception as e:
                    errors.append(f"{ws['name']} / campaign {c.get('id')}: {e}")
            snapshot["workspaces"][ws["name"]] = ws_data
        except Exception as e:
            errors.append(f"{ws['name']}: {e}")

    return snapshot, errors


def load_snapshot(target_date):
    path = SNAPSHOTS_DIR / f"{target_date}.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def save_snapshot(snapshot):
    SNAPSHOTS_DIR.mkdir(exist_ok=True)
    path = SNAPSHOTS_DIR / f"{snapshot['date']}.json"
    path.write_text(json.dumps(snapshot, indent=2))
    print(f"Snapshot saved to {path}")


def calc_delta(today_val, yesterday_val):
    return max((today_val or 0) - (yesterday_val or 0), 0)


def send_slack(blocks):
    requests.post(SLACK_WEBHOOK, json={"blocks": blocks}, timeout=10)


def build_report(today_snapshot, yesterday_snapshot):
    today = today_snapshot["date"]
    yesterday = yesterday_snapshot["date"] if yesterday_snapshot else None

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📊 Daily Reply Rates — {today}"},
        },
    ]

    if not yesterday_snapshot:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_Baseline snapshot saved. Daily rates will appear from tomorrow._"},
        })
        return blocks

    global_sent = 0
    global_replied = 0
    all_campaign_lines = []

    for ws_name, ws_today in today_snapshot["workspaces"].items():
        ws_yesterday = yesterday_snapshot.get("workspaces", {}).get(ws_name, {})
        yesterday_campaigns = ws_yesterday.get("campaigns", {})
        today_campaigns = ws_today.get("campaigns", {})

        for cid, c_today in today_campaigns.items():
            c_yest = yesterday_campaigns.get(cid, {})
            sent_delta = calc_delta(c_today["sent"], c_yest.get("sent", 0))
            replied_delta = calc_delta(c_today["unique_replied"], c_yest.get("unique_replied", 0))
            global_sent += sent_delta
            global_replied += replied_delta

            if sent_delta > 0:
                rate = replied_delta / sent_delta
                rate_str = f"{rate*100:.1f}%"
                flag = " 🔴" if rate < 0.02 else ""
                all_campaign_lines.append(
                    f"• [{ws_name}]  _{c_today['name']}_  —  {sent_delta} sent / {replied_delta} replied  =  *{rate_str}*{flag}"
                )

    global_rate = global_replied / global_sent if global_sent > 0 else 0
    global_rate_str = f"{global_rate*100:.1f}%"
    global_flag = " 🔴" if global_sent > 0 and global_rate < 0.02 else ""

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*Overall:*  {global_sent} sent / {global_replied} replied  =  *{global_rate_str}*{global_flag}"},
    })

    if all_campaign_lines:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(all_campaign_lines)},
        })

    return blocks


def main():
    today = date.today()
    yesterday = today - timedelta(days=1)

    print("Taking snapshot...")
    today_snapshot, errors = take_snapshot()
    save_snapshot(today_snapshot)

    yesterday_snapshot = load_snapshot(yesterday)
    if not yesterday_snapshot:
        print("No yesterday snapshot found — this is the baseline. Report will start tomorrow.")

    blocks = build_report(today_snapshot, yesterday_snapshot)

    if errors:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "⚠️ *Errors:*\n" + "\n".join(f"• {e}" for e in errors)},
        })

    send_slack(blocks)
    print("Report sent.")


if __name__ == "__main__":
    main()
