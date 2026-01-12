#!/usr/bin/env python3

import argparse
import os
import sys
import subprocess
import traceback
import json
from datetime import datetime, date, time, timedelta
from pathlib import Path

import sms # sms.py local file

SYSTEMD_DIR = "/etc/systemd/system"
DELETE_AFTER = 30

def require_root():
    if os.geteuid() != 0:
        print("ERROR: This script must be run as root (use sudo).", file=sys.stderr)
        sys.exit(1)

def time_to_minutes(timestr: str) -> int:
    h, m, s = (timestr.split(":") + ["0"])[:3]  # allow HH:MM
    td = timedelta(hours=int(h), minutes=int(m), seconds=int(s))
    return int(td.total_seconds() // 60)

def parse_time_string(ts: str) -> time:
    """
    Parse time as HH:MM or HH:MM:SS (24h).
    """
    parts = ts.strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Invalid time format '{ts}', expected HH:MM or HH:MM:SS")
    hour = int(parts[0])
    minute = int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    return time(hour=hour, minute=minute, second=second)

def normalize_dow(dow: str) -> str:
    """
    Normalize day-of-week string to systemd-compatible 3-letter form: Mon, Tue, Wed, Thu, Fri, Sat, Sun.
    Accepts full names or abbreviations, any case.
    """
    dow = dow.strip().lower()
    mapping = {
        "mon": "Mon", "monday": "Mon",
        "tue": "Tue", "tues": "Tue", "tuesday": "Tue",
        "wed": "Wed", "weds": "Wed", "wednesday": "Wed",
        "thu": "Thu", "thur": "Thu", "thurs": "Thu", "thursday": "Thu",
        "fri": "Fri", "friday": "Fri",
        "sat": "Sat", "saturday": "Sat",
        "sun": "Sun", "sunday": "Sun",
    }
    if dow not in mapping:
        raise ValueError(f"Unrecognized day of week: '{dow}'")
    return mapping[dow]

def compute_next_date_for_dow(target_dow_str: str) -> date:
    """
    Given a day-of-week like 'Mon', compute the date of the *next* such day.
    If today is that day, return one week from today.
    """
    short_to_idx = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
    target_dow = short_to_idx[target_dow_str]
    today = date.today()
    today_idx = today.weekday()  # Monday = 0
    days_ahead = (target_dow - today_idx) % 7
    if days_ahead == 0:
        days_ahead = 7  # always "next", never today
    return today + timedelta(days=days_ahead)


def write_timer_file(timer_unit: str, on_calendar: str, service_name: str, description: str):
    timer_path = Path(SYSTEMD_DIR) / timer_unit
    content = f"""[Unit]
Description={description}

[Timer]
OnCalendar={on_calendar}
AccuracySec=1
Persistent=true
Unit={service_name}

[Install]
WantedBy=timers.target
"""
    with timer_path.open("w") as f:
        f.write(content)
    print(f"Created timer: {timer_path}")


def systemctl(*args):
    cmd = ["systemctl", *args]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def schedule_cleanup_with_at(timer_unit: str, cleanup_time: datetime):
    """
    Schedule an at job that will stop, disable, and delete the timer, and reload systemd.
    """
    at_time_str = cleanup_time.strftime("%Y%m%d%H%M")  # [[CC]YY]MMDDhhmm
    cleanup_script = f"""#!/bin/sh
systemctl stop {timer_unit} || true
systemctl disable {timer_unit} || true
rm -f {SYSTEMD_DIR}/{timer_unit}
systemctl daemon-reload
"""

    print(f"Scheduling cleanup for {timer_unit} at {cleanup_time} (at -t {at_time_str})")

    proc = subprocess.Popen(
        ["at", "-t", at_time_str],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = proc.communicate(cleanup_script)

    if proc.returncode != 0:
        print("WARNING: Failed to schedule cleanup with 'at':", file=sys.stderr)
        print(stderr.strip(), file=sys.stderr)
    else:
        print(stdout.strip())


if __name__ == "__main__":
    require_root()

    parser = argparse.ArgumentParser(description="Create and start a systemd timer for a broadcast (recurring or non-recurring).")
    parser.add_argument('-c','--config-file',type=str,required=True,help='JSON Configuration file')
    parser.add_argument('-w','--ward',type=str,help='Name of Ward being broadcast')
    parser.add_argument('-X','--extend-max',type=int,help='Maximum time broadcast can be extended in minutes')
    parser.add_argument('-s','--start-time',type=str,help='Broadcast start time in HH:MM:SS')
    parser.add_argument('-t','--run-time',type=str,default='1:10:00',help='Broadcast run time in HH:MM:SS')
    parser.add_argument('-A','--start-date',type=str,help='Broadcast run date in MM/DD/YY, use for setting up future broadcasts')
    parser.add_argument('-I','--insert-next-broadcast',default=False, action='store_true',help='Insert next broadcast, this should only be used if calling from broadcast.py')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('--timer-name',help="Base name of the systemd timer (without .timer). Default: basename of json config (e.g. alderbrook.json -> alderbrook.timer).")
    parser.add_argument('--delete-after',type=int,default=DELETE_AFTER,help='Number of minutes to wait after broadcast ends to delete non-recurring events')
    parser.add_argument('--timer-description',type=str,help='Description for the unit field of the timer')
    parser.add_argument('-v','--verbose',default=False,action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    ward = args.ward
    num_from = args.num_from
    num_to = args.num_to
    verbose = args.verbose
    extend_max = args.extend_max
    start_time = args.start_time
    run_time = args.run_time
    start_date = args.start_date
    recurring = args.insert_next_broadcast
    broadcast_day = None
    timer_description = args.timer_description

    if(args.config_file is not None):
        if("/" in args.config_file):
            config_file = args.config_file
        else:
            config_file =  os.path.abspath(os.path.dirname(__file__)) + "/" + args.config_file
        if(verbose): print('Config file : ' + config_file)
    if(config_file is not None and os.path.exists(config_file)):
        with open(config_file, "r") as configFile:
            config = json.load(configFile)

            # check for keys in config file
            if 'broadcast_ward' in config:
                ward = config['broadcast_ward']
            if 'broadcast_title' in config:
                timer_description = config['broadcast_title']
            if 'broadcast_day' in config:
                broadcast_day = config['broadcast_day']
            if 'broadcast_time' in config:
                start_time = config['broadcast_time']
            if 'broadcast_length' in config:
                run_time = config['broadcast_length']
            if 'max_extend_minutes' in config:
                extend_max = config['max_extend_minutes']
            if 'broadcast_recurring' in config:
                recurring = config['broadcast_recurring']
            if 'notification_text_from' in config:
                num_from = config['notification_text_from']
            if 'notification_text_to' in config:
                num_to = config['notification_text_to']

    # Parse and normalize basic fields
    try:
        start_time_obj = parse_time_string(start_time)
        dow_short = normalize_dow(broadcast_day)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    json_path = Path(config_file)
    if not json_path.exists():
        print(f"WARNING: JSON config file '{json_path}' does not exist (continuing anyway).", file=sys.stderr)

    json_base = json_path.stem

    timer_base = args.timer_name if args.timer_name else json_base
    timer_unit = f"{timer_base}.timer"

    service_name = f"broadcast@{json_base}.json.service"

    if(timer_description):
        description = f"{timer_description} Timer"
    else:
        description = f"Broadcast timer for {json_base}"

    # Build OnCalendar
    if recurring:
        # Weekly recurring: e.g. "Sun 09:00"
        on_calendar = f"{dow_short} {start_time}"
        print(f"Creating RECURRING timer '{timer_unit}' with OnCalendar='{on_calendar}'")
        write_timer_file(timer_unit, on_calendar, service_name, description)

        # Enable + start timer
        systemctl("daemon-reload")
        systemctl("enable", timer_unit)
        systemctl("start", timer_unit)

    else:
        # Non-recurring (one-shot)
        if start_date:
            try:
                broadcast_date = datetime.strptime(start_date, "%m/%d/Y").date()
            except ValueError:
                print("ERROR: --date must be in MM/DD/YYYY format.", file=sys.stderr)
                sys.exit(1)
        else:
            broadcast_date = compute_next_date_for_dow(dow_short)

        start_dt = datetime.combine(broadcast_date, start_time_obj)
        on_calendar = f"{broadcast_date.isoformat()} {start_time}"

        print(f"Creating ONE-SHOT timer '{timer_unit}' with OnCalendar='{on_calendar}'")
        write_timer_file(timer_unit, on_calendar, service_name, description)

        # Enable + start timer
        systemctl("daemon-reload")
        systemctl("enable", timer_unit)
        systemctl("start", timer_unit)

        # Schedule cleanup DELETE_AFTER minutes after broadcast end
        total_minutes = time_to_minutes(run_time) + extend_max + args.delete_after
        cleanup_time = start_dt + timedelta(minutes=total_minutes)
        schedule_cleanup_with_at(timer_unit, cleanup_time)

    print("Done.")
