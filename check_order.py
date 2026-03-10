#!/usr/bin/env python3
"""
Premium Inboxes order status poller.
Sends a Slack notification when an order moves to "Order Done & Delivered".
"""

import os
import requests

SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK"]
PI_API_KEY = os.environ["PI_API_KEY"]
ORDER_ID = os.environ["ORDER_ID"]

BASE_URL = "https://pi-be-prod-cw8xd.ondigitalocean.app/api"


def main():
    r = requests.get(
        f"{BASE_URL}/client/order/{ORDER_ID}",
        headers={"x-api-token": PI_API_KEY},
        timeout=15,
    )
    r.raise_for_status()
    order = r.json()

    status = order.get("status", "Unknown")
    name = order.get("fullName", "")
    domain = order.get("forwardedDomain", "")
    inboxes = order.get("inboxes", {}).get("total", 0)

    if status == "Order Done & Delivered":
        requests.post(SLACK_WEBHOOK, json={"blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "✅ Inbox Order Delivered"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": (
                f"*{domain}*  —  {inboxes} inboxes ready\n"
                f"Status: *{status}*\n"
                f"Order ID: `{ORDER_ID}`"
            )}},
        ]}, timeout=10)
        print(f"DELIVERED: {domain}")
    elif status.startswith("Issue"):
        requests.post(SLACK_WEBHOOK, json={"blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "⚠️ Inbox Order Issue"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": (
                f"*{domain}*  —  check your email\nStatus: *{status}*"
            )}},
        ]}, timeout=10)
        print(f"ISSUE: {domain}")
    else:
        print(f"Still in progress: {status}")


if __name__ == "__main__":
    main()
