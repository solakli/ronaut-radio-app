#!/usr/bin/env python3
"""
Discord Notifier for Ronaut Radio
Sends notifications for live events, daily stats, and calendar reminders.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta

import requests

# Discord webhook URL
WEBHOOK_URL = "https://discord.com/api/webhooks/1472307215892353258/yOZTSpu7DfnYEOCuXlkSZxk0Vsbn8SXH5a7CZbcHh97OVCAn70XCZCQ8d3kjee8c5ltq"

# File paths
PLAY_LOG_FILE = "/root/play_log.tsv"
CHAT_DB = "/root/chat.db"
CALENDAR_ICS = "https://calendar.google.com/calendar/ical/ronautradio%40gmail.com/public/basic.ics"
DURATIONS_FILE = "/root/durations.txt"

SITE_URL = "https://ronautradio.la"


def send_discord(content=None, embed=None):
    """Send a message to Discord."""
    payload = {}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed]

    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        return resp.status_code == 204
    except Exception as e:
        print(f"Discord error: {e}")
        return False


def notify_live(dj_name=None):
    """Send notification when going live."""
    embed = {
        "title": "🔴 WE'RE LIVE!",
        "description": f"**{dj_name}** is streaming now!" if dj_name else "Tune in now!",
        "color": 0xFF0000,  # Red
        "fields": [
            {"name": "Listen Now", "value": f"[ronautradio.la]({SITE_URL})", "inline": True}
        ],
        "footer": {"text": "Ronaut Radio • Los Angeles • Vinyl Only"}
    }
    return send_discord(embed=embed)


def notify_end_live(dj_name=None, duration_mins=None):
    """Send notification when live stream ends."""
    desc = "Thanks for tuning in!"
    if dj_name and duration_mins:
        desc = f"**{dj_name}** just finished a {duration_mins} minute set!"

    embed = {
        "title": "📻 Live Stream Ended",
        "description": desc,
        "color": 0x333333,
        "footer": {"text": "Ronaut Radio • See you next time!"}
    }
    return send_discord(embed=embed)


def notify_now_playing(set_name, duration_secs=None):
    """Send notification when a new set starts playing."""
    duration_str = ""
    if duration_secs:
        hours = duration_secs // 3600
        mins = (duration_secs % 3600) // 60
        if hours > 0:
            duration_str = f" ({hours}h {mins}m)"
        else:
            duration_str = f" ({mins}m)"

    embed = {
        "title": "🎵 Now Playing",
        "description": f"**{set_name}**{duration_str}",
        "color": 0x000000,
        "footer": {"text": "Ronaut Radio • 24/7 Vinyl Stream"}
    }
    return send_discord(embed=embed)


def get_daily_stats():
    """Gather stats for the daily summary."""
    now = time.time()
    day_ago = now - 86400

    # Sets played today
    sets_played = []
    try:
        with open(PLAY_LOG_FILE, "r") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 2:
                    ts = int(parts[0])
                    if ts >= day_ago:
                        name = os.path.basename(parts[1]).replace(".mp4", "")
                        sets_played.append(name)
    except:
        pass

    # Load durations
    durations = {}
    try:
        with open(DURATIONS_FILE, "r") as f:
            for line in f:
                parts = line.strip().split("|")
                if len(parts) >= 2:
                    durations[parts[0]] = int(float(parts[1]))
    except:
        pass

    # Calculate total airtime
    total_secs = 0
    for s in sets_played:
        for path, dur in durations.items():
            if s in path:
                total_secs += dur
                break

    # Chat messages today
    chat_count = 0
    try:
        conn = sqlite3.connect(CHAT_DB)
        row = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE timestamp >= ?",
            (day_ago,)
        ).fetchone()
        chat_count = row[0] if row else 0
        conn.close()
    except:
        pass

    return {
        "sets_played": sets_played,
        "sets_count": len(set(sets_played)),  # Unique sets
        "total_hours": round(total_secs / 3600, 1),
        "chat_messages": chat_count,
    }


def send_daily_summary():
    """Send daily stats summary to Discord."""
    stats = get_daily_stats()

    # Build sets list (deduplicated, max 10)
    unique_sets = list(dict.fromkeys(stats["sets_played"]))[:10]
    sets_list = "\n".join([f"• {s}" for s in unique_sets]) if unique_sets else "No sets played"
    if len(stats["sets_played"]) > 10:
        sets_list += f"\n*...and {len(stats['sets_played']) - 10} more*"

    embed = {
        "title": "📊 Daily Ronaut Radio Stats",
        "description": f"Here's what happened in the last 24 hours:",
        "color": 0x5865F2,  # Discord blurple
        "fields": [
            {"name": "🎵 Sets Aired", "value": str(stats["sets_count"]), "inline": True},
            {"name": "⏱️ Total Airtime", "value": f"{stats['total_hours']}h", "inline": True},
            {"name": "💬 Chat Messages", "value": str(stats["chat_messages"]), "inline": True},
            {"name": "📻 Sets Played", "value": sets_list, "inline": False},
        ],
        "footer": {"text": "Ronaut Radio • Los Angeles • Vinyl Only"},
        "timestamp": datetime.utcnow().isoformat()
    }
    return send_discord(embed=embed)


def get_upcoming_events():
    """Fetch upcoming events from Google Calendar."""
    try:
        resp = requests.get(CALENDAR_ICS, timeout=10)
        ics_text = resp.text
    except:
        return []

    events = []
    current_event = {}

    for line in ics_text.split("\n"):
        line = line.strip()
        if line == "BEGIN:VEVENT":
            current_event = {}
        elif line == "END:VEVENT":
            if current_event.get("summary") and current_event.get("dtstart"):
                events.append(current_event)
            current_event = {}
        elif line.startswith("SUMMARY:"):
            current_event["summary"] = line[8:]
        elif line.startswith("DTSTART:"):
            # Parse UTC time
            dt_str = line[8:].replace("Z", "")
            try:
                dt = datetime.strptime(dt_str, "%Y%m%dT%H%M%S")
                # Convert UTC to Pacific (UTC-8)
                dt = dt - timedelta(hours=8)
                current_event["dtstart"] = dt
            except:
                pass

    # Filter to future events only
    now = datetime.now()
    future_events = [e for e in events if e.get("dtstart") and e["dtstart"] > now]
    future_events.sort(key=lambda x: x["dtstart"])

    return future_events


def send_upcoming_reminder():
    """Send reminder for upcoming events in the next 24 hours."""
    events = get_upcoming_events()
    now = datetime.now()
    tomorrow = now + timedelta(hours=24)

    upcoming = [e for e in events if e["dtstart"] <= tomorrow]

    if not upcoming:
        return False

    fields = []
    for event in upcoming[:3]:  # Max 3 events
        dt = event["dtstart"]
        time_str = dt.strftime("%A, %b %d @ %I:%M %p PT")
        fields.append({
            "name": event["summary"],
            "value": time_str,
            "inline": False
        })

    embed = {
        "title": "📅 Upcoming Shows",
        "description": "Don't miss these upcoming live streams!",
        "color": 0x00FF00,  # Green
        "fields": fields,
        "footer": {"text": "Add to your calendar at ronautradio.la"}
    }
    return send_discord(embed=embed)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: discord_notifier.py <command> [args]")
        print("Commands:")
        print("  live [dj_name]     - Notify going live")
        print("  end_live [dj]      - Notify stream ended")
        print("  now_playing <set>  - Notify now playing")
        print("  daily              - Send daily summary")
        print("  upcoming           - Send upcoming events reminder")
        print("  test               - Send test message")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "live":
        dj = sys.argv[2] if len(sys.argv) > 2 else None
        notify_live(dj)
        print("Sent live notification")

    elif cmd == "end_live":
        dj = sys.argv[2] if len(sys.argv) > 2 else None
        notify_end_live(dj)
        print("Sent end live notification")

    elif cmd == "now_playing":
        if len(sys.argv) < 3:
            print("Usage: discord_notifier.py now_playing <set_name>")
            sys.exit(1)
        notify_now_playing(sys.argv[2])
        print("Sent now playing notification")

    elif cmd == "daily":
        send_daily_summary()
        print("Sent daily summary")

    elif cmd == "upcoming":
        send_upcoming_reminder()
        print("Sent upcoming reminder")

    elif cmd == "test":
        send_discord(content="🎧 Test message from Ronaut Radio!")
        print("Sent test message")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
