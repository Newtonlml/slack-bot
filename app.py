import os
import csv
import time
import schedule
import random
from datetime import datetime, timedelta
from threading import Thread
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from pathlib import Path
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
import json

__version__ = "0.1.0"

# Schedule birthday greetings daily at 9 AM
HH, MM = 9, 0
TIMEZONE = os.environ.get("TIMEZONE", "America/Santiago")  # Default timezone

# === JOURNAL CLUB PRESENTER FUNCTIONS ===
DATA_DIR = "data/"
PRESENTED_FILE = DATA_DIR + "presented.csv"
MEMBERS_FILE = DATA_DIR + "members.csv"
REMINDER_FILE = DATA_DIR + "reminder.csv"

# === LOAD ENVIRONMENT VARIABLES ===
env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

# Initialize Slack app
app = App(token=os.environ["SLACK_API_TOKEN"])
AUTHORIZED_USER_ID = os.environ["ADMIN_USER_ID"]  # Your Slack user ID
JOURNAL_CHANNEL_ID = os.environ["JOURNAL_CHANNEL_ID"]


CONFIG_FILE = DATA_DIR + "config.json"


# Load config with defaults if file doesn't exist
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {
        "meeting_day": "monday",
        "reminder_day": "thursday",
        "reminder_hour": "23:01"
    }


# Save config to file
def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)


# Load initial config
config = load_config()

# Replace constants with config values
MEETING_DAY = config["meeting_day"]
REMINDER_DAY = config["reminder_day"]
REMINDER_HOUR = config["reminder_hour"]


def reload_schedules():
    """Clear existing reminder jobs and reschedule with new config."""
    global MEETING_DAY, REMINDER_DAY, REMINDER_HOUR
    schedule.clear("weekly_reminder")  # Remove old weekly reminder
    schedule_reminder_for_next(REMINDER_DAY, REMINDER_HOUR, send_journal_reminder)
    print(f"🔄 Scheduler reloaded with: {REMINDER_DAY} at {REMINDER_HOUR}")


@app.command("/configure_meeting")
def handle_configure_meeting(ack, body, say):
    ack()
    user_id = body["user_id"]
    if user_id != AUTHORIZED_USER_ID:
        say(f"Sorry <@{user_id}>, you're not authorized to run this command.")
        return

    # Expect format: /configure_meeting meeting_day reminder_day reminder_hour
    # Example: /configure_meeting monday thursday 15:30
    text = body.get("text", "").strip().lower()
    parts = text.split()

    if len(parts) != 3:
        say("Usage: `/configure_meeting MEETING_DAY REMINDER_DAY REMINDER_HOUR` (e.g. `/configure_meeting monday thursday 15:30`)")
        return

    meeting_day, reminder_day, reminder_hour = parts
    valid_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    if meeting_day not in valid_days or reminder_day not in valid_days:
        say(f"Days must be one of: {', '.join(valid_days)}")
        return

    try:
        time.strptime(reminder_hour, "%H:%M")
    except ValueError:
        say("Reminder hour must be in HH:MM format (24h).")
        return

    # Update config and save
    config["meeting_day"] = meeting_day
    config["reminder_day"] = reminder_day
    config["reminder_hour"] = reminder_hour
    save_config(config)

    # Update globals
    global MEETING_DAY, REMINDER_DAY, REMINDER_HOUR
    MEETING_DAY = meeting_day
    REMINDER_DAY = reminder_day
    REMINDER_HOUR = reminder_hour

    # Reload scheduler so changes take effect immediately
    reload_schedules()

    say(f"✅ Configuration updated!\n- Meeting day: {MEETING_DAY.capitalize()}\n- Reminder day: {REMINDER_DAY.capitalize()}\n- Reminder time: {REMINDER_HOUR}")


@app.message("hello")
def message_hello(message, say):
    # say() sends a message to the channel where the event was triggered
    say(f"Hey there <@{message['user']}>!")


# === BIRTHDAY GREETINGS FUNCTION ===
def check_and_send_birthday_messages():
    if not os.path.exists(MEMBERS_FILE):
        print("Members file not found.")
        return
    today = datetime.now(ZoneInfo(TIMEZONE)).strftime("%m-%d")
    with open(MEMBERS_FILE, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row["date"] == today:
                user_id = row["user_id"]
                name = row["name"]
                channel_id = os.getenv("BIRTHDAY_CHANNEL_ID") or ""
                try:
                    app.client.chat_postMessage(
                        channel=channel_id,
                        text=f"🎉 Happy Birthday <@{user_id}>! Wishing you an amazing day! 🎂"
                    )
                    print(f"Sent birthday message to {name} ({user_id})")
                except Exception as e:
                    print(f"Failed to send birthday message to {name}: {e}")


def get_all_members():
    with open(MEMBERS_FILE, newline='') as csvfile:
        return list(csv.DictReader(csvfile))


def get_presented_members():
    if not os.path.exists(PRESENTED_FILE):
        return []
    with open(PRESENTED_FILE, newline='') as csvfile:
        return list(csv.DictReader(csvfile))


def save_presented_member(member):
    fieldnames = ["name", "user_id", "date"]
    write_header = not os.path.exists(PRESENTED_FILE)
    with open(PRESENTED_FILE, mode='a', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(member)


def reset_presented_list():
    if os.path.exists(PRESENTED_FILE):
        os.remove(PRESENTED_FILE)


def select_random_presenter():
    members = get_all_members()
    presented = get_presented_members()
    presented_ids = {row["user_id"] for row in presented}
    remaining = [m for m in members if m["user_id"] not in presented_ids]

    if not remaining:
        reset_presented_list()
        remaining = members

    selected = random.choice(remaining)
    save_presented_member(selected)

    # Save for reminder
    with open(REMINDER_FILE, "w", newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["name", "user_id", "date"])
        writer.writeheader()
        writer.writerow(selected)

    return selected


# === COMMAND TO SELECT PRESENTER ===
@app.command("/select_presenter")
def handle_select_presenter(ack, body, say):
    ack()
    user_id = body["user_id"]
    if user_id != AUTHORIZED_USER_ID:
        say(f"Sorry <@{user_id}>, you're not authorized to run this command.")
        return

    selected = select_random_presenter()
    say(f"📢 The next journal club presenter is <@{selected['user_id']}>! 🎓")
    print(f"Selected {selected['name']} for journal club.")


@app.command("/get_channel_members")
def handle_get_channel_members(ack, body, say):
    ack()

    user_id = body["user_id"]
    if user_id != AUTHORIZED_USER_ID:
        say(f"Sorry <@{user_id}>, you're not authorized to run this command.")
        return

    text = body.get("text", "").strip()
    if not text:
        say("Please provide the channel ID. Example: `/get_channel_members C12345678`")
        return

    channel_id = text

    try:
        members = []
        cursor = None
        while True:
            response = app.client.conversations_members(channel=channel_id, cursor=cursor)
            members.extend(response["members"])
            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        results = []
        for member_id in members:
            user_info = app.client.users_info(user=member_id)
            user_name = user_info["user"]["real_name"]  # or user_info["user"]["profile"]["display_name"]
            results.append(f"{user_name} (`{member_id}`)")

        say(f"Found {len(results)} members in <#{channel_id}>:\n" + "\n".join(results))

    except Exception as e:
        say(f"Error fetching members: {e}")


# === REMINDER FUNCTION ===
def send_journal_reminder():
    if not os.path.exists(REMINDER_FILE):
        print("No upcoming presenter found.")
        return

    with open(REMINDER_FILE, newline='') as f:
        reader = csv.DictReader(f)
        selected = next(reader, None)

    if selected:
        try:
            app.client.chat_postMessage(
                channel=selected["user_id"],
                text=f"🔔 Reminder: You are presenting in the next journal club on {MEETING_DAY.capitalize()}. "
                     f"Please submit the paper in <#{JOURNAL_CHANNEL_ID}> before then!"
            )
            print(f"Reminder sent to {selected['name']}")
        except Exception as e:
            print(f"Failed to send reminder: {e}")


# === UTILITY TO FIND DATE OF NEXT GIVEN WEEKDAY ===
def schedule_reminder_for_next(day_name, time_str, job_func):
    weekday_number = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"].index(day_name)
    now = datetime.now(ZoneInfo(TIMEZONE))
    days_ahead = (weekday_number - now.weekday() + 7) % 7
    if days_ahead == 0 and now.time() > datetime.strptime(time_str, "%H:%M").time():
        days_ahead = 7
    run_date = now + timedelta(days=days_ahead)
    run_time = run_date.replace(hour=int(time_str.split(":")[0]), minute=int(time_str.split(":")[1]), second=0, microsecond=0)
    delay = (run_time - now).total_seconds()

    def delayed_job():
        time.sleep(delay)
        job_func()
        # Reschedule the reminder for next week
        getattr(schedule.every(), REMINDER_DAY).at(REMINDER_HOUR).do(send_journal_reminder).tag("weekly_reminder")

    Thread(target=delayed_job, daemon=True).start()


def get_server_time_for_santiago(hour, minute):
    santiago_tz = ZoneInfo(TIMEZONE)
    server_tz = datetime.now().astimezone().tzinfo

    now_santiago = datetime.now(santiago_tz)
    target_dt_santiago = now_santiago.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if target_dt_santiago < now_santiago:
        target_dt_santiago += timedelta(days=1)

    target_dt_server = target_dt_santiago.astimezone(server_tz)
    return target_dt_server.strftime("%H:%M")


# === START BOT & SCHEDULER ===
if __name__ == "__main__":
    server_time_str = get_server_time_for_santiago(HH, MM)
    schedule.every().day.at(server_time_str).do(check_and_send_birthday_messages)

    # Schedule reminder for presenter
    schedule_reminder_for_next(REMINDER_DAY, REMINDER_HOUR, send_journal_reminder)

    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(60)

    Thread(target=run_scheduler, daemon=True).start()
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
