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

    if not all([viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database]):
        print("Missing DB configuration values!")
        sys.exit(1)

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
                sms.send_sms(num_from, num_to, f"{ward or 'Unknown Ward'} DB connection error, IP: {ip_address} may be missing from access list!", verbose)

        # Re-raise a generic error so callers know it failed
        raise RuntimeError("Database connection failed") from err

# ---------- Core helpers ----------

def get_unreported_entries_for_video(youtube_id, ward, viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, num_from = None, num_to = None, verbose = False, test = False):
    """
    Returns all unreported rows for a given YouTube ID + ward.
    Each row is a dict with: id, ip_address, viewer_count, viewer_name.
    """
    conn = get_conn(viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, ward, num_from, num_to, verbose)
    try:
        cur = conn.cursor(dictionary=True)
        condition = "AND reported = 0" if not test else ""
        cur.execute(
            f"""
            SELECT id, device_id, ip_address, viewer_count, viewer_name, timestamp
            FROM attendance
            WHERE youtube_id = %s AND ward = %s {condition}
            """,
            (youtube_id, ward),
        )
        rows = cur.fetchall()
        return rows
    finally:
        conn.close()

def normalize_name(name):
    return (name or "").strip()

def is_multi_word_name(name):
    # split on whitespace, ignore empty chunks
    return len([p for p in name.split() if p]) >= 2

def strip_suffix(name):
    return name.split("#", 1)[0]

def resolve_name(device_id, ip_address, viewer_db_host, viewer_db_user, viewer_db_password,
                 viewer_db_database, ward=None, num_from=None, num_to=None, verbose=False):
    """
    Resolve a name using:
      1) attendance by device_id (if present)
      2) attendance by ip_address (if present)
      3) aliases by device_id
      4) aliases by ip_address
      5) create alias (store device_id and/or ip_address)
    """
    def is_bad(s):
        return (not s) or (str(s).strip().lower() in ("unknown", "null", "none", ""))

    # If neither identifier is usable, don't create alias
    if is_bad(device_id) and is_bad(ip_address):
        return "Unknown"

    conn = get_conn(viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, ward, num_from, num_to, verbose)
    try:
        cur = conn.cursor()

        # 1) attendance by device_id
        if not is_bad(device_id):
            cur.execute(
                """
                SELECT viewer_name
                FROM attendance
                WHERE device_id = %s
                  AND viewer_name IS NOT NULL
                  AND viewer_name <> ''
                ORDER BY timestamp ASC
                LIMIT 1
                """,
                (device_id,),
            )
            row = cur.fetchone()
            if row and row[0]:
                return row[0]

        # 2) attendance by ip_address
        if not is_bad(ip_address):
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

        # 3) aliases by device_id
        if not is_bad(device_id):
            cur.execute("SELECT alias_name FROM aliases WHERE device_id = %s LIMIT 1", (device_id,))
            row = cur.fetchone()
            if row and row[0]:
                return row[0]

        # 4) aliases by ip_address
        if not is_bad(ip_address):
            cur.execute("SELECT alias_name FROM aliases WHERE ip_address = %s LIMIT 1", (ip_address,))
            row = cur.fetchone()
            if row and row[0]:
                return row[0]

        # 5) Create new alias ONLY if we have at least one usable identifier

        usable_device = not is_bad(device_id)
        usable_ip = not is_bad(ip_address)

        if not (usable_device or usable_ip):
            # No stable identity possible
            return "Unknown"

        cur.execute(
            "INSERT INTO aliases (device_id, ip_address) VALUES (%s, %s)",
            (
                device_id if usable_device else None,
                ip_address if usable_ip else None,
            )
        )
        new_id = cur.lastrowid
        alias_name = f"Visitor {new_id}"
        cur.execute("UPDATE aliases SET alias_name=%s WHERE id=%s", (alias_name, new_id))
        conn.commit()
        return alias_name

    finally:
        conn.close()

def mark_entries_reported(entry_ids, viewer_db_host, viewer_db_user, viewer_db_password,
                          viewer_db_database, value=1, ward=None, num_from=None,
                          num_to=None, verbose=False):
    """
    Mark all given attendance row IDs as reported = <value>.
    """
    if not entry_ids:
        return

    conn = get_conn(viewer_db_host, viewer_db_user, viewer_db_password,
                    viewer_db_database, ward, num_from, num_to, verbose)
    try:
        cur = conn.cursor()

        # Build a placeholder list: %s,%s,%s,...
        placeholders = ", ".join(["%s"] * len(entry_ids))

        # Use the placeholders in the query
        query = f"UPDATE attendance SET reported = %s WHERE id IN ({placeholders})"

        # First param is 'value', then each id is its own %s
        params = [value] + entry_ids

        cur.execute(query, params)
        conn.commit()

    finally:
        conn.close()

# ---------- Main public function ----------

from collections import defaultdict

def summarize_viewers_for_broadcast(youtube_id, ward, viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, broadcast_start_time, num_from = None, num_to = None, verbose = False, view_only = False, test = False):
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

    If view_only=True:
        - No DB rows are marked reported.
        - Early rows are listed in the output.
    """
    global _db_error_sent
    _db_error_sent = False

    rows = get_unreported_entries_for_video(
        youtube_id, ward,
        viewer_db_host, viewer_db_user,
        viewer_db_password, viewer_db_database,
        num_from, num_to, verbose, test
    )

    if not rows:
        return "", {}, 0

    cutoff_time = broadcast_start_time - timedelta(minutes=90)

    early_rows = []
    valid_rows = []

    # --- Separate early vs valid ---
    for r in rows:
        row_time = r["timestamp"]
        if row_time is None or row_time < cutoff_time:
            early_rows.append(r)
        else:
            valid_rows.append(r)

    # --- Mark early rows as reported=2 unless view_only ---
    if early_rows and (not view_only and not test):
        mark_entries_reported(
            [r["id"] for r in early_rows],
            viewer_db_host, viewer_db_user,
            viewer_db_password, viewer_db_database,
            value=2,
            ward=ward,
            num_from=num_from, num_to=num_to,
            verbose=verbose
        )

    # If only early rows existed
    if not valid_rows:
        if (view_only or test):
            # Include an early-row listing
            lines = ["Early submissions (excluded):"]
            for r in early_rows:
                ip = r["ip_address"] or "Unknown"
                vc = r["viewer_count"]
                lines.append(f"{ip.ljust(15)} {str(vc).rjust(3)} viewer(s)")
            return "\n".join(lines), {}, len(early_rows)

        return f"No valid submissions ({len(early_rows)} early submissions excluded).", {}, len(early_rows)

    # --- Continue processing valid rows ---
    rows = valid_rows

    def norm(s):
        return (s or "").strip()

    def is_bad(s):
        return (not s) or (str(s).strip().lower() in ("unknown", "null", "none", ""))

    def dedupe_preserve_order_case_insensitive(names):
        seen = set()
        out = []
        for n in names:
            key = n.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(n.strip())
        return out

    # 1) Group rows by identity (device_id preferred, else ip, else per-row)
    groups_by_ident = defaultdict(list)
    all_ids = []

    for r in rows:
        all_ids.append(r["id"])

        device_id = r.get("device_id")
        ip = r.get("ip_address")

        if not is_bad(device_id):
            ident_key = ("device", device_id)
        elif not is_bad(ip):
            ident_key = ("ip", ip)
        else:
            ident_key = ("unknown", r["id"])

        groups_by_ident[ident_key].append(r)

    # 2) For each identity group:
    #    - viewer_count = max(viewer_count)
    #    - name display = all distinct provided names joined by " / "
    #      OR resolved via resolve_name() if none provided
    ident_results = []  # list of (ident_key, display_name, max_count)

    for ident_key, entries in groups_by_ident.items():
        counts = [e["viewer_count"] for e in entries]
        max_count = max(counts)

        provided = [norm(e.get("viewer_name")) for e in entries if norm(e.get("viewer_name"))]
        uniq_names = dedupe_preserve_order_case_insensitive(provided)

        if uniq_names:
            display_name = " / ".join(uniq_names)
        else:
            any_device = next((e.get("device_id") for e in entries if not is_bad(e.get("device_id"))), None)
            any_ip = next((e.get("ip_address") for e in entries if not is_bad(e.get("ip_address"))), None)

            display_name = resolve_name(
                any_device, any_ip,
                viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database,
                ward=ward, num_from=num_from, num_to=num_to, verbose=verbose
            )

        ident_results.append((ident_key, display_name, max_count))

    # 3) Optional merge ACROSS identities:
    #    - Only if display_name is a SINGLE multi-word name (no " / ")
    #    - Never merge single-word names across identities
    #    - Never merge multi-name labels like "Tom / Tom Smith" across identities
    name_counts = defaultdict(float)

    for ident_key, display_name, count in ident_results:
        base_name = normalize_name(display_name)

        if (" / " not in base_name) and is_multi_word_name(base_name):
            # Safe to combine across identities
            out_key = base_name
        else:
            # Keep scoped per identity
            out_key = f"{base_name}#{ident_key[0]}:{ident_key[1]}"

        prev = name_counts.get(out_key)

        if prev is None:
            # First value wins, even if it's -1
            name_counts[out_key] = count
        else:
            # If either value is positive, take the larger
            if count >= 0 or prev >= 0:
                name_counts[out_key] = max(prev, count)
            else:
                # both are negative (-1), keep -1
                name_counts[out_key] = -1

    # 4) Formatting helpers
    def strip_suffix(name):
        # Here suffix includes #device:XYZ or #ip:1.2.3.4
        return name.split("#", 1)[0]

    def fmt_num(x):
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
        return f"{x:.1f}"

    items = list(name_counts.items())

    # Disambiguate duplicate display names (after stripping suffix)
    base_counts = defaultdict(int)
    for k, _ in items:
        base_counts[strip_suffix(k)] += 1

    base_seen = defaultdict(int)
    display_rows = []
    for k, v in sorted(items, key=lambda kv: strip_suffix(kv[0]).lower()):
        base = strip_suffix(k)
        if base_counts[base] > 1:
            base_seen[base] += 1
            display = f"{base} ({base_seen[base]})"
        else:
            display = base
        display_rows.append((display, v))

    max_name_len = max(len(d) for d, _ in display_rows) if display_rows else 10
    num_width = max(len(fmt_num(v)) for _, v in display_rows) if display_rows else 1

    lines = []

    # Early rows list (view_only/test)
    if (view_only or test) and early_rows:
        lines.append("Early submissions (excluded):")
        for r in early_rows:
            ip_disp = r["ip_address"] or "Unknown"
            vc = r["viewer_count"]
            lines.append(f"{ip_disp.ljust(max_name_len)}   {str(vc).rjust(num_width)}   viewer(s)   [EARLY]")
        lines.append("")

    lines.append("Household breakdown:")
    for display, val in display_rows:
        lines.append(f"{display.ljust(max_name_len)}   {fmt_num(val).rjust(num_width)}   viewer(s)")

    total_abs = sum(abs(v) for v in name_counts.values())
    lines.append("")
    lines.append(f"Total: {fmt_num(total_abs)}   viewer(s)")

    average_abs = total_abs / float(len(display_rows)) if display_rows else 0.0
    rounded = round(average_abs * 2) / 2
    lines.append(f"Ave. per Household: {rounded:.1f}   viewer(s)")

    summary_text = "\n".join(lines)

    # Mark rows as reported unless view_only/test
    if (not view_only and not test):
        mark_entries_reported(
            all_ids,
            viewer_db_host, viewer_db_user,
            viewer_db_password, viewer_db_database,
            value=1,
            ward=ward,
            num_from=num_from, num_to=num_to,
            verbose=verbose
        )

    return summary_text, {k: float(v) for k, v in name_counts.items()}, len(all_ids)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Viewer Database Connector File')
    parser.add_argument('-c','--config-file',type=str,help='JSON Configuration file')
    parser.add_argument('-w','--ward',type=str,help='Name of Ward being broadcast')
    parser.add_argument('-C','--current-id',type=str,help='ID value for the current broadcast, used if deleting current broadcast is true')
    parser.add_argument('-s','--start-time',type=str,help='Broadcast start time in HH:MM:SS or YYYY-MM-DD HH:MM:SS')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('--test',default=False, action='store_true',help='Test run for debugging')
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
                if(not ward):
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

    if start_time is None:
        start_time = datetime.now()
    else:
        start_time = start_time.strip()

        if "/" in start_time or "-" in start_time:
            try:
                start_time = datetime.strptime(start_time, "%m/%d/%Y %H:%M:%S")
            except:
                start_time = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        else:
            # Time only: HH:MM:SS → use today's date
            today = datetime.now().strftime("%Y-%m-%d ")
            start_time = datetime.strptime(today + start_time, "%Y-%m-%d %H:%M:%S")

    print(summarize_viewers_for_broadcast(current_id, ward, viewer_db_host, viewer_db_user, viewer_db_password, viewer_db_database, start_time, num_from, num_to, verbose, True, args.test)[0])