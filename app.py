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

__version__ = "0.1.0"

# === CONFIGURABLE CONSTANTS ===
MEETING_DAY = "monday"      # Day of the week when journal club happens
REMINDER_DAY = "thursday"   # Day of the week to send the reminder
REMINDER_HOUR = "23:01"     # Time (24hr) to send the reminder

# === LOAD ENVIRONMENT VARIABLES ===
env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

# Initialize Slack app
app = App(token=os.environ["SLACK_API_TOKEN"])
AUTHORIZED_USER_ID = os.environ["ADMIN_USER_ID"]  # Your Slack user ID
JOURNAL_CHANNEL_ID = os.environ["JOURNAL_CHANNEL_ID"]

@app.message("hello")
def message_hello(message, say):
    # say() sends a message to the channel where the event was triggered
    say(f"Hey there <@{message['user']}>!")

# === BIRTHDAY GREETINGS FUNCTION ===
def check_and_send_birthday_messages():
    today = datetime.now().strftime("%m-%d")
    with open("birthdays.csv", newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row["date"] == today:
                user_id = row["user_id"]
                name = row["name"]
                channel_id = os.getenv("BIRTHDAY_CHANNEL_ID")
                try:
                    app.client.chat_postMessage(
                        channel=channel_id,
                        text=f"🎉 Happy Birthday <@{user_id}>! Wishing you an amazing day! 🎂"
                    )
                    print(f"Sent birthday message to {name} ({user_id})")
                except Exception as e:
                    print(f"Failed to send birthday message to {name}: {e}")

# === JOURNAL CLUB PRESENTER FUNCTIONS ===
PRESENTED_FILE = "presented.csv"
MEMBERS_FILE = "members.csv"
REMINDER_FILE = "reminder.csv"

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
    now = datetime.now()
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

# === START BOT & SCHEDULER ===
if __name__ == "__main__":
    # Schedule birthday greetings daily at 9 AM
    schedule.every().day.at("09:00").do(check_and_send_birthday_messages)

    # Schedule reminder for presenter
    schedule_reminder_for_next(REMINDER_DAY, REMINDER_HOUR, send_journal_reminder)

    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(60)

    Thread(target=run_scheduler, daemon=True).start()
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
