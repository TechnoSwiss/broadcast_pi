#!/usr/bin/python3

import argparse
import os
import traceback
import sys
import json
from datetime import datetime, timedelta

import urllib.request
import mysql.connector
from collections import defaultdict

import sms #sms.py local file
import global_file as gf # local file for sharing globals between files

_db_error_sent = False

def get_public_ip():
    try:
        return urllib.request.urlopen("https://api.ipify.org").read().decode().strip()
    except:
        return "unknown"

def get_conn(viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, ward = None, num_from = None, num_to = None, verbose=False):
    global _db_error_sent  # allow us to suppress repeated messages in this run

    DB_CONFIG = {
        "host": viewer_db_host,
        "user": viewer_db_user,
        "password": viewer_db_password,
        "database": viewer_db_database,
        "port": 3306,
    }

    try:
        return mysql.connector.connect(**DB_CONFIG)

    except mysql.connector.Error as err:
        # Only send ONE text for the entire summarize call
        if not _db_error_sent:
            _db_error_sent = True

            ip_address = get_public_ip()

            print(f"DB connection error, IP: {ip_address} may be missing from access list. Error: {str(err)}")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + f" DB connection error, IP: {ip_address} may be missing from access list!", verbose)

        # Re-raise a generic error so callers know it failed
        raise RuntimeError("Database connection failed") from err

# ---------- Core helpers ----------

def get_unreported_entries_for_video(youtube_id, ward, viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, num_from = None, num_to = None, verbose = False):
    """
    Returns all unreported rows for a given YouTube ID + ward.
    Each row is a dict with: id, ip_address, viewer_count, viewer_name.
    """
    conn = get_conn(viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, ward, num_from, num_to, verbose)
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, ip_address, viewer_count, viewer_name, timestamp
            FROM attendance
            WHERE youtube_id = %s AND ward = %s AND reported = 0
            """,
            (youtube_id, ward),
        )
        rows = cur.fetchall()
        return rows
    finally:
        conn.close()

def find_name_for_ip(ip_address, viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, num_from = None, num_to = None, verbose = False):
    """
    Resolve a name for an IP address.
    If IP is None/empty/unknown, return 'Unknown' without creating an alias.
    """
    # --- NEW CHECK ---
    if not ip_address or ip_address.strip().lower() in ("unknown", "null", "none"):
        return "Unknown"

    conn = get_conn(viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, ward, num_from, num_to, verbose)
    try:
        cur = conn.cursor()

        # 1) Look in attendance table for any known name for this IP
        cur.execute(
            """
            SELECT viewer_name
            FROM attendance
            WHERE ip_address = %s
              AND viewer_name IS NOT NULL
              AND viewer_name <> ''
            ORDER BY timestamp ASC
            LIMIT 1
            """,
            (ip_address,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]

        # 2) Look in aliases table
        cur.execute(
            "SELECT alias_name FROM aliases WHERE ip_address = %s",
            (ip_address,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]

        # 3) Create new alias Visitor N for legitimate IPs
        cur.execute("SELECT COUNT(*) FROM aliases")
        count = cur.fetchone()[0] or 0
        alias_name = f"Visitor {count + 1}"

        cur.execute(
            "INSERT INTO aliases (ip_address, alias_name) VALUES (%s, %s)",
            (ip_address, alias_name),
        )
        conn.commit()

        return alias_name

    finally:
        conn.close()

def mark_entries_reported(entry_ids, viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, value = 1, ward = None, num_from = None, num_to = None, verbose = False):
    """
    Mark all given attendance row IDs as reported = 1.
    """
    if not entry_ids:
        return
    conn = get_conn(viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, ward, num_from, num_to, verbose)
    try:
        cur = conn.cursor()
        # Use IN clause; chunk if needed for very large lists
        placeholders = ",".join(["%s"] * len(entry_ids))
        query = f"UPDATE attendance SET reported = {value} WHERE id IN ({placeholders})"
        cur.execute(query, entry_ids)
        conn.commit()
    finally:
        conn.close()

# ---------- Main public function ----------

from collections import defaultdict

def summarize_viewers_for_broadcast(youtube_id, ward, viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, broadcast_start_time, num_from = None, num_to = None, verbose = False):
    """
    Returns (summary_text, name_counts_dict, total_entries_used).

    Logic:
    - Get all unreported rows for this youtube_id + ward.
    - Group by IP.
    - For each IP, pick the entry with the highest viewer_count
      (including -1 as a valid value).
    - Resolve a name for that IP (row name > prior DB name > aliases).
    - Aggregate counts per name (Taylor: 3, Bowden: 1, etc.).
    - Mark *all* rows we used as reported.
    """
    global _db_error_sent
    _db_error_sent = False

    rows = get_unreported_entries_for_video(youtube_id, ward, viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, num_from, num_to, verbose)
    if not rows:
        return "", {}, 0

    cutoff_time = broadcast_start_time - timedelta(minutes=30)

    early_rows = []
    valid_rows = []

    for r in rows:
        row_time = r["timestamp"]
        if row_time is None:
            early_rows.append(r)
        else:
            if row_time < cutoff_time:
                early_rows.append(r)
            else:
                valid_rows.append(r)

    # Mark early rows as reported but do not include them in summary
    if early_rows:
        mark_entries_reported([r["id"] for r in early_rows], viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, 2, ward, num_from, num_to, verbose)

    # If nothing valid remains, return early
    if not valid_rows:
        return f"No valid submissions ({len(early_rows)} early submissions excluded).", {}, len(early_rows)

    # Continue using valid_rows as your main dataset
    rows = valid_rows

    # NEW: separate lists for handling
    normal_ips = defaultdict(list)
    unknown_ip_rows = []

    # Split rows into valid IP and unknown IP
    for r in rows:
        ip = r["ip_address"]
        name = (r.get("viewer_name") or "").strip()

        if not ip or ip.strip().lower() in ("unknown", "null", "none", ""):
            # UNKNOWN IP CASE
            if name:
                # Named entries get grouped by name
                unknown_ip_rows.append(r)
            else:
                # Anonymous unknown IP entries remain separate viewers
                unknown_ip_rows.append(r)
        else:
            # NORMAL IP CASE
            normal_ips[ip].append(r)

    chosen_rows = []
    all_ids = []

    # Process normal IPs: one row per IP, pick highest viewer_count
    for ip, entries in normal_ips.items():
        for e in entries:
            all_ids.append(e["id"])

        best = max(entries, key=lambda e: e["viewer_count"])
        viewer_name = best.get("viewer_name")
        if not viewer_name:
            viewer_name = find_name_for_ip(ip, viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, num_from, num_to, verbose)

        chosen_rows.append({
            "name": viewer_name,
            "viewer_count": best["viewer_count"],
            "ip_address": ip,
        })

    # Process unknown IP rows
    # RULE:
    #   If viewer_name is present → combine by name
    #   If viewer_name missing → each row is its own viewer named "Unknown"
    grouped_unknown_by_name = defaultdict(list)

    for r in unknown_ip_rows:
        all_ids.append(r["id"])

        name = (r.get("viewer_name") or "").strip()
        if name:
            # group named unknown-IPs by name
            grouped_unknown_by_name[name].append(r)
        else:
            # each completely anonymous row is separate
            chosen_rows.append({
                "name": "Unknown",
                "viewer_count": r["viewer_count"],
                "ip_address": None,
            })

    # Combine named unknown-IP entries
    for name, entries in grouped_unknown_by_name.items():
        best = max(entries, key=lambda e: e["viewer_count"])
        chosen_rows.append({
            "name": name,
            "viewer_count": best["viewer_count"],
            "ip_address": None,
        })

    # AGGREGATE totals by name
    name_counts = defaultdict(int)
    for row in chosen_rows:
        name_counts[row["name"]] += row["viewer_count"]

    # Format aligned breakdown text (same pretty formatting as before)
    names = list(name_counts.keys())
    max_name_len = max(len(n) for n in names)
    num_width = max(len(str(v)) for v in name_counts.values())

    lines = ["Household breakdown:"]
    for name in sorted(names):
        count = name_counts[name]
        lines.append(
            f"{name.ljust(max_name_len)}   {str(count).rjust(num_width)}   viewer(s)"
        )

    # Compute absolute-value total
    total_abs = sum(abs(v) for v in name_counts.values())

    # Add a blank line + total line
    lines.append("")
    lines.append(f"Total: {total_abs}   viewer(s)")

    summary_text = "\n".join(lines)

    # Mark all rows as reported
    mark_entries_reported(all_ids, viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, 1, ward, num_from, num_to, verbose)

    return summary_text, dict(name_counts), len(all_ids)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Viewer Database Connector File')
    parser.add_argument('-c','--config-file',type=str,help='JSON Configuration file')
    parser.add_argument('-w','--ward',type=str,help='Name of Ward being broadcast')
    parser.add_argument('-C','--current-id',type=str,help='ID value for the current broadcast, used if deleting current broadcast is true')
    parser.add_argument('-s','--start-time',type=str,help='Broadcast start time in HH:MM:SS')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    ward = args.ward
    current_id = args.current_id
    num_from = args.num_from
    num_to = args.num_to
    verbose = args.verbose
    start_time = args.start_time
    viewer_db_host = None
    viewer_db_user = None
    viewer_db_password = None
    viewer_db_database = None
    viewer_summary_text = None

    if(current_id is None):
        print("!!YouTube ID is a required argument!!")
        sys.exit("YouTube ID is a required argument")

    if(args.config_file is not None):
        if("/" in args.config_file):
            config_file = args.config_file
        else:
            config_file =  os.path.abspath(os.path.dirname(__file__)) + "/" + args.config_file
    if(config_file is not None and os.path.exists(config_file)):
        with open(config_file, "r") as configFile:
            config = json.load(configFile)

            if 'broadcast_ward' in config:
                ward = config['broadcast_ward']
            if start_time is None and 'broadcast_time' in config:
                start_time = config['broadcast_time']
            if 'viewer_db_host' in config:
                viewer_db_host = config['viewer_db_host']
            if 'viewer_db_user' in config:
                viewer_db_user = config['viewer_db_user']
            if 'viewer_db_password' in config:
                viewer_db_password = config['viewer_db_password']
            if 'viewer_db_database' in config:
                viewer_db_database = config['viewer_db_database']
            if 'notification_text_from' in config:
                num_from = config['notification_text_from']
            if 'notification_text_to' in config:
                num_to = config['notification_text_to']

    if(ward is None):
        print("!!Ward is required argument!!")
        sys.exit("Ward is required argument")

    if(start_time is None):
        start_time = datetime.now()
    else:
        start_time = datetime.strptime(datetime.now().strftime("%m/%d/%Y ") + start_time, "%m/%d/%Y %H:%M:%S")

    print(summarize_viewers_for_broadcast(current_id, ward, viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, start_time, num_from, num_to, verbose)[0])