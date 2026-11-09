#!/usr/bin/python3

import argparse
import os
import traceback
import subprocess
import json
import re
import socket
import ipaddress
import urllib.request
import urllib.parse

from subprocess import Popen, check_output, PIPE
from datetime import datetime, timedelta
from dateutil import parser as date_parser
from dateutil import tz

import google_auth # google_auth.py local file
import youtube_api as yt # youtube.py local file
import sms # sms.py local file
import global_file as gf # local file for sharing globals between files

import gspread # pip3 install gspread==4.0.0

googleDoc = 'Pi Stats' # I need to parameterize this at some point...
SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, 'logs', 'send_timers.log') # failure details go here instead of SMS
LIMITS = {
    'temp': 60.0,
    'ptz_status': 'Pass',
    'ping_resolve_time': 2.0,
    'ping_max': 20.0,
    'ping_packet_loss': 50.0,
    'speedtest_packet_loss': 5.0,
    'download_mbps': 200.0,
    'upload_mbps': 25.0
}

EXTERNAL_IP_SOURCES = ['https://api.ipify.org', 'https://ifconfig.me/ip']
DDNS_DEFAULT_UPDATE_PATH = '/nic/update' # dyndns2 protocol path, used by most DDNS providers


def check_wifi_ssid(target_ssid, verbose=False):
    """Scan wlan0 for target_ssid. Returns True if found, False if not found, None on scan error."""
    try:
        subprocess.run(['sudo', 'ip', 'link', 'set', 'wlan0', 'up'], capture_output=True)
        result = subprocess.run(
            ['sudo', 'iw', 'dev', 'wlan0', 'scan'],
            capture_output=True, text=True, timeout=15
        )
        found_ssids = re.findall(r'^\s+SSID: (.+)$', result.stdout, re.MULTILINE)
        if verbose: print(f"SSIDs found: {found_ssids}")
        return target_ssid in found_ssids
    except FileNotFoundError:
        if verbose: print("iw not found; install iw")
        return None
    except subprocess.TimeoutExpired:
        if verbose: print("WiFi scan timed out")
        return None
    except Exception as e:
        if verbose: print(f"WiFi scan error: {e}")
        return None

def get_external_ip(timeout=10, verbose=False):
    """Return this site's public IPv4 address as a string, or None if every source fails."""
    for url in EXTERNAL_IP_SOURCES:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'send_timers/1.0'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ip = resp.read().decode().strip()
            return str(ipaddress.IPv4Address(ip)) # validates the response is a bare IPv4 address
        except Exception as e:
            if verbose: print(f"External IP lookup via {url} failed: {e}")
    return None

def resolve_ddns(hostname, resolver=None, verbose=False):
    """Return a sorted list of A records for hostname ([] if none), or None if the lookup itself failed.
    Uses the system resolver unless a specific DNS server is given."""
    try:
        if resolver:
            result = subprocess.run(
                ['dig', '+short', '+time=3', '+tries=2', f'@{resolver}', hostname, 'A'],
                capture_output=True, text=True, timeout=20
            )
            if result.returncode != 0:
                if verbose: print(f"dig @{resolver} {hostname} failed: {result.stderr.strip()}")
                return None
            ips = []
            for line in result.stdout.splitlines(): # dig +short can include CNAME targets, keep only IPv4 lines
                try:
                    ips.append(str(ipaddress.IPv4Address(line.strip())))
                except ValueError:
                    pass
            return sorted(set(ips))
        return sorted({ai[4][0] for ai in socket.getaddrinfo(hostname, None, socket.AF_INET)})
    except socket.gaierror as e:
        if verbose: print(f"DNS lookup for {hostname} returned no address: {e}")
        return []
    except Exception as e:
        if verbose: print(f"DNS lookup for {hostname} failed: {e}")
        return None

def load_ddns_config(path, verbose=False):
    """Load the DDNS JSON config. A bare filename is looked up in the script directory, same as broadcast.py.
    Returns the config dict, or None if it can't be read."""
    if "/" not in path:
        path = os.path.join(SCRIPT_DIR, path)
    try:
        with open(path, 'r') as f:
            config = json.load(f)
        if not isinstance(config, dict):
            raise ValueError("top level of DDNS config must be a JSON object")
        return config
    except Exception as e:
        if verbose: print(f"Could not load DDNS config from {path}: {e}")
        return None

def build_ddns_update_url(update_host, hostname, ip, creds=None, update_path=DDNS_DEFAULT_UPDATE_PATH):
    """Build a dyndns2-style update URL. With creds the login is embedded so one tap updates the record,
    without them the browser prompts for the DDNS username/password."""
    query = urllib.parse.urlencode({'hostname': hostname, 'myip': ip})
    path = '/' + update_path.lstrip('/')
    if creds:
        user = urllib.parse.quote(creds[0], safe='')
        password = urllib.parse.quote(creds[1], safe='')
        return f"https://{user}:{password}@{update_host}{path}?{query}"
    return f"https://{update_host}{path}?{query}"

# systemctl list-timers columns: NEXT | LEFT | LAST | PASSED | UNIT | ACTIVATES
# Match on the timestamp pattern instead of splitting on whitespace: systemd pads
# LEFT/PASSED only to the widest value, so the widest row gets a single space and
# a 2+ space split merges columns (intermittent "No <ward> timer scheduled" failures).
TS_PATTERN = r'(?:\w{3} \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+|n/a|-)'
TIMER_RE = re.compile(rf'^({TS_PATTERN})\s+(.*?)\s+({TS_PATTERN})\s+(.*?)\s+(\S+\.timer)\s+(\S+)$')

def parse_timer_line_minimal(line):
    m = TIMER_RE.match(line.strip())
    if not m or m.group(1) in ('n/a', '-'):
        return None
    return {
        'next_run': m.group(1),
        'time_left_raw': m.group(2).replace('left', '').strip(),
        'unit': m.group(5),
        'activates': m.group(6)
    }

def format_timers_for_sms(timers_list):
    """Compact, phone-friendly timer list: one header per day, then 'time name' lines.
    Avoids the wide column table (wraps badly on phones) and the broadcast@... unit names (get autolinked as emails)."""
    lines = []
    current_day = None
    for t in timers_list:
        name = t['unit'][:-len('.timer')] if t['unit'].endswith('.timer') else t['unit']
        try:
            dt = date_parser.parse(t['next_run'])
        except Exception:
            lines.append(f"{name} {t['next_run']}")
            continue
        day = f"{dt.strftime('%a')} {dt.month}/{dt.day}"
        if day != current_day:
            lines.append(day)
            current_day = day
        time_str = f"{dt.hour % 12 or 12}:{dt.minute:02d}{'a' if dt.hour < 12 else 'p'}"
        lines.append(f" {time_str} {name}")
    return "\n".join(lines)

def write_failure_log(pc_name, failures, timers_list, raw_timers, verbose=False):
    """Append failure details, parsed timers and raw list-timers output to LOG_FILE."""
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, 'a') as f:
            f.write(f"===== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {pc_name} =====\n")
            f.write("Failures:\n")
            for fail in failures:
                f.write(f"  - {fail}\n")
            f.write(f"Parsed timers ({len(timers_list)}):\n")
            for t in timers_list:
                f.write(f"  {t['next_run']} | {t['time_left_raw']} | {t['unit']} | {t['activates']}\n")
            f.write("Raw systemctl list-timers --all:\n")
            f.write(raw_timers.rstrip() + "\n\n")
    except Exception as e:
        print(f"Could not write failure log {LOG_FILE}: {e}")

def get_worksheet_case_insensitive(client, googleDoc, desired_tab_name):
    sh = client.open(googleDoc)
    for ws in sh.worksheets():
        if ws.title.lower() == desired_tab_name.lower():
            return ws
    raise ValueError(f"Worksheet '{desired_tab_name}' not found (case-insensitive match).")

def log_diagnostics_to_sheet(ward, pc_name, googleDoc, diagnostics, failures_str, num_from=None, num_to=None, verbose=False):
    credentials_file = ward.lower() + '.auth'
    client = gspread.authorize(google_auth.get_credentials_google_sheets(credentials_file, ward, num_from, num_to))
    sheet = get_worksheet_case_insensitive(client, 'Pi Stats', pc_name)

    row = [
        diagnostics.get('timestamp', ''),                # String
        pc_name,                                         # String
        float(diagnostics.get('temp', 0)),               # Float
        diagnostics.get('ptz_status', ''),               # String
        float(diagnostics.get('total_ping_time', 0)),    # Float
        float(diagnostics.get('ping_max', 0)),           # Float
        float(diagnostics.get('ping_min', 0)),           # Float
        float(diagnostics.get('ping_avg', 0)),           # Float
        float(diagnostics.get('ping_stddev', 0)),        # Float or 0 if 'N/A'
        float(diagnostics.get('packet_loss', 0)),        # Float
        diagnostics.get('server', ''),                   # String
        diagnostics.get('isp', ''),                      # String
        float(diagnostics.get('latency', 0)),            # Float
        float(diagnostics.get('jitter', 0)),             # Float
        float(diagnostics.get('download_mbps', 0)),      # Float
        float(diagnostics.get('download_data_mb', 0)),   # Float
        float(diagnostics.get('upload_mbps', 0)),        # Float
        float(diagnostics.get('upload_data_mb', 0)),     # Float
        float(diagnostics.get('packet_loss_st', -1)),    # Float (Speedtest packet loss)
        diagnostics.get('result_url', ''),               # String
        failures_str
    ]

    # Insert new data in second row (index=3), pushing down older data
    sheet.insert_row(row, index=3, value_input_option='USER_ENTERED')

    FORMULAS = {
        'C2': '=AVERAGE(FILTER(C3:C, ISNUMBER(C3:C)))',
        'E2': '=AVERAGE(FILTER(E3:E, ISNUMBER(E3:E)))',
        'F2': '=AVERAGE(FILTER(F3:F, ISNUMBER(F3:F)))',
        'G2': '=AVERAGE(FILTER(G3:G, ISNUMBER(G3:G)))',
        'H2': '=AVERAGE(FILTER(H3:H, ISNUMBER(H3:H)))',
        'I2': '=AVERAGE(FILTER(I3:I, ISNUMBER(I3:I)))',
        'J2': '=AVERAGE(FILTER(J3:J, ISNUMBER(J3:J)))',
        'M2': '=AVERAGE(FILTER(M3:M, ISNUMBER(M3:M)))',
        'N2': '=AVERAGE(FILTER(N3:N, ISNUMBER(N3:N)))',
        'O2': '=AVERAGE(FILTER(O3:O, ISNUMBER(O3:O)))',
        'P2': '=AVERAGE(FILTER(P3:P, ISNUMBER(P3:P)))',
        'Q2': '=AVERAGE(FILTER(Q3:Q, ISNUMBER(Q3:Q)))',
        'R2': '=AVERAGE(FILTER(R3:R, ISNUMBER(R3:R)))',
        'S2': '=AVERAGE(FILTER(S3:S, ISNUMBER(S3:S)))'
        # Add more if needed
    }

    requests = []
    for cell, formula in FORMULAS.items():
        requests.append({
            'updateCells': {
                'range': {
                    'sheetId': sheet.id,
                    'startRowIndex': int(cell[1:]) - 1,
                    'endRowIndex': int(cell[1:]),
                    'startColumnIndex': ord(cell[0].upper()) - ord('A'),
                    'endColumnIndex': ord(cell[0].upper()) - ord('A') + 1
                },
                'rows': [{
                    'values': [{
                        'userEnteredValue': {
                            'formulaValue': formula
                        }
                    }]
                }],
                'fields': 'userEnteredValue'
            }
        })

    sheet.spreadsheet.batch_update({'requests': requests})

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Text systemctl list-timers results to monitor broadcast timers.')
    parser.add_argument('-p','--pc-name',type=str,required=True,help='System name that script is running on.')
    parser.add_argument('-w','--wards',type=str,help='Comma delimited list of wards used to filter timers list.')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    parser.add_argument('-W','--wifi-ssid',type=str,default=None,help='Check that this WiFi SSID is visible (e.g. Liahona)')
    parser.add_argument('-l','--ddns-config',type=str,default=None,help='JSON file with the DDNS check settings (ddns_hostname, ddns_update_host, ddns_username, ddns_password, ddns_resolver)')
    args = parser.parse_args()

    num_from = args.num_from
    num_to = args.num_to
    verbose = args.verbose

    send_text = ""
    console_text = ""
    failure_text = ""
    ddns_sms_text = "" # kept out of console/log/sheet output since it can contain the DDNS key
    diagnostics = {}
    now = datetime.now()
    latest_start = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0, tzinfo=tz.tzlocal())

    try:
        result = subprocess.run(['vcgencmd', 'measure_temp'], capture_output=True, text=True, check=True)
        temp_str  = result.stdout.strip().split('=')[-1]
        temp = float(re.sub(r"[^\d\.]", "", temp_str))
    except subprocess.CalledProcessError:
        temp = 0.0
    diagnostics['temp'] = temp
    send_text += 'System: ' + args.pc_name + ', temperature is: ' + str(temp) + '°C\n\n'

    testing_active = os.path.exists(os.path.join(SCRIPT_DIR, 'testing'))
    if testing_active:
        send_text += '!!TESTING ACTIVE!!\n\n'

    timers_list = []
    result = subprocess.run(['systemctl', 'list-timers', '--all'], capture_output=True, text=True, check=True)
    raw_timers = result.stdout
    if(args.wards is not None):
        wards = [w.strip() for w in args.wards.split(',')]
        send_text += f"{'NEXT':<28} {'LEFT':<9} {'UNIT':<20} ACTIVATES\n"
        for line in raw_timers.splitlines():
            if not any(ward in line for ward in wards):
                continue
            timer = parse_timer_line_minimal(line)
            if timer:
                timers_list.append(timer)
                send_text += f"{timer['next_run']:<28} {timer['time_left_raw']:<9} {timer['unit']:<20} {timer['activates']}\n"

    else:
        send_text += raw_timers

    start_ping = datetime.now()
    try:
        result = subprocess.run(['ping', '-c', '2', 'x.rtmp.youtube.com'], capture_output=True, text=True)
        lines = result.stdout.splitlines()
        # Filter out 'rtt' lines in Python instead of piping to `grep`
        ping_output = "\n".join([line for line in result.stdout.splitlines() if 'rtt' not in line])
        console_text += '\n\n' + ping_output
        if result.stderr.strip():
            diagnostics['ping_error'] = result.stderr.strip()
            console_text += '\n' + result.stderr.strip()
    except:
        console_text += '\n\n !!! PING FAILED !!!'
    ping_delta = datetime.now() - start_ping
    console_text += '\nTotal ping time w/ resolve: ' + str(ping_delta) + '\n\n\n'
    try:
        diagnostics['total_ping_time'] = ping_delta.total_seconds()
        ping_times = []
        rtt_found = False
        for line in lines:
            if 'packet loss' in line:
                match = re.search(r'(\d+)% packet loss', line)
                diagnostics['packet_loss'] = match.group(1)

            if 'time=' in line:
                match = re.search(r'time=([\d\.]+)\s*ms', line)
                if match:
                    ping_times.append(float(match.group(1)))

            if 'rtt min/avg/max/mdev' in line:
                rtt_found = True
                match = re.search(r'=\s([\d\.]+)/([\d\.]+)/([\d\.]+)/([\d\.]+)\s*ms', line)
                if match:
                    diagnostics['ping_min'] = match.group(1)
                    diagnostics['ping_avg'] = match.group(2)
                    diagnostics['ping_max'] = match.group(3)
                    diagnostics['ping_stddev'] = match.group(4)

        if not rtt_found and ping_times:
            # Fallback: calculate manually if no rtt summary
            diagnostics['ping_min'] = min(ping_times)
            diagnostics['ping_avg'] = sum(ping_times)/len(ping_times)
            diagnostics['ping_max'] = max(ping_times)
            diagnostics['ping_stddev'] = 'N/A'
    except Exception:
        diagnostics['total_ping_time'] = 'Fail'
        diagnostics['packet_loss'] = 'Fail'
        diagnostics['ping_min'] = 'Fail'
        diagnostics['ping_avg'] = 'Fail'
        diagnostics['ping_max'] = 'Fail'
        diagnostics['ping_stddev'] = 'Fail'

    try:
        result = subprocess.run(['speedtest', '--format=json'], capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        server = data['server']
        isp = data['isp']
        latency = data['ping']['latency']
        jitter = data['ping']['jitter']
        packet_loss = data.get('packetLoss', 0)

        download_mbps = data['download']['bandwidth'] * 8 / 1e6
        download_data_mb = data['download']['bytes'] / 1e6

        upload_mbps = data['upload']['bandwidth'] * 8 / 1e6
        upload_data_mb = data['upload']['bytes'] / 1e6

        # Print classic style output
        console_text += f"     Server : {server['name']} - {server['location']} (id = {server['id']})\n"
        console_text += f"        ISP : {isp}\n"
        console_text += f"    Latency : {latency:.2f} ms   ({jitter:.2f} ms jitter)\n"
        console_text += f"   Download : {download_mbps:.2f} Mbps (data used: {download_data_mb:.1f} MB)\n"
        console_text += f"     Upload : {upload_mbps:.2f} Mbps (data used: {upload_data_mb:.1f} MB)\n"
        console_text += f"Packet Loss : {packet_loss:.1f}%\n"

        diagnostics['server'] = f"{server['name']} - {server['location']} (id = {server['id']})"
        diagnostics['isp'] = isp
        diagnostics['latency'] = latency
        diagnostics['jitter'] = jitter
        diagnostics['download_mbps'] = download_mbps
        diagnostics['download_data_mb'] = download_data_mb
        diagnostics['upload_mbps'] = upload_mbps
        diagnostics['upload_data_mb'] = upload_data_mb
        diagnostics['packet_loss_st'] = packet_loss
        diagnostics['result_url'] = data['result']['url']

    except subprocess.CalledProcessError as e:
        console_text += '\n\n !!! BANDWIDTH TEST FAILED !!!'

    CAMERA_IP = '192.168.108.9'
    try:
        result = subprocess.run(['ping', '-c', '2', '-W', '2', CAMERA_IP], capture_output=True, text=True)
        status = 'Pass' if ' 0% packet loss' in result.stdout else '!!FAILED!!'
        diagnostics['ptz_status'] = status
    except Exception as e:
        if(verbose): print(f"Exception: {e}")
        status = f'!!! CAMERA PING FAILED !!!'
        diagnostics['ptz_status'] = 'Fail'
    console_text += f'\n\n PTZ Camera : {status}\n'

    if args.wifi_ssid:
        wifi_result = check_wifi_ssid(args.wifi_ssid, verbose)
        if wifi_result is True:
            console_text += f'\n WiFi SSID "{args.wifi_ssid}": Found\n'
            diagnostics['wifi_ssid_status'] = 'Found'
        elif wifi_result is False:
            console_text += f'\n WiFi SSID "{args.wifi_ssid}": !!NOT FOUND!!\n'
            diagnostics['wifi_ssid_status'] = 'Not Found'
        else:
            console_text += f'\n WiFi SSID "{args.wifi_ssid}": Scan Failed\n'
            diagnostics['wifi_ssid_status'] = 'Scan Failed'

    # DDNS check: does the hostname resolve to this site's current public IP?
    external_ip = None
    ddns_ips = None
    ddns_status = None
    ddns_config = None
    ddns_hostname = None
    DDNS_UPDATE_HOST = None
    if args.ddns_config:
        ddns_config = load_ddns_config(args.ddns_config, verbose)
        if ddns_config is not None:
            ddns_hostname = ddns_config.get('ddns_hostname')
            DDNS_UPDATE_HOST = ddns_config.get('ddns_update_host')
        if ddns_config is None:
            ddns_status = 'Config Unreadable'
        elif not ddns_hostname:
            ddns_status = 'Config Missing ddns_hostname'
        else:
            external_ip = get_external_ip(verbose=verbose)
            ddns_ips = resolve_ddns(ddns_hostname, ddns_config.get('ddns_resolver'), verbose)
        if ddns_status is not None:
            pass # config problem, nothing to compare
        elif external_ip is None:
            ddns_status = 'External IP Lookup Failed'
        elif ddns_ips is None:
            ddns_status = 'DNS Lookup Failed'
        elif not ddns_ips:
            ddns_status = 'No A Record'
        elif external_ip in ddns_ips:
            ddns_status = 'Pass'
        else:
            ddns_status = 'Mismatch'
        diagnostics['external_ip'] = external_ip or 'Fail'
        diagnostics['ddns_ips'] = ', '.join(ddns_ips) if ddns_ips else ''
        diagnostics['ddns_status'] = ddns_status
        console_text += (f"\n DDNS {ddns_hostname or '-'} : {', '.join(ddns_ips) if ddns_ips else '-'}"
                         f" | External IP : {external_ip or '-'} | {ddns_status}\n")

    diagnostics['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    failures = []

    for ward in (args.wards.split(',') if args.wards else []):
        ward = ward.strip()
        ward_timer_found = False

        for timer in timers_list:
            if ward.lower() in timer['unit'].lower():
                next_run_time = date_parser.parse(timer['next_run'])
                if next_run_time <= latest_start:
                    ward_timer_found = True
                    break  # Found valid timer for this ward

        if not ward_timer_found:
            failures.append(f"No {ward} timer scheduled before latest start tomorrow!")

    if diagnostics['temp'] >= LIMITS['temp']:
        failures.append(f"Temperature High: {diagnostics['temp']}°C")

    if diagnostics['ptz_status'] != LIMITS['ptz_status']:
        failures.append(f"PTZ Camera: {diagnostics['ptz_status']}")

    if args.wifi_ssid:
        wifi_status = diagnostics.get('wifi_ssid_status')
        if wifi_status and wifi_status != 'Found':
            failures.append(f'WiFi SSID "{args.wifi_ssid}": {wifi_status}')

    if args.ddns_config and ddns_status != 'Pass':
        if ddns_status in ('Mismatch', 'No A Record'):
            dns_shown = ', '.join(ddns_ips) if ddns_ips else 'no A record'
            failures.append(f"DDNS needs update: {ddns_hostname} is {dns_shown}, external IP is {external_ip}")
            if DDNS_UPDATE_HOST:
                username = ddns_config.get('ddns_username')
                password = ddns_config.get('ddns_password')
                creds = (username, password) if username and password else None
                # the link goes only in the SMS: failures are also written to the log file and the Google Sheet
                ddns_sms_text = ("\nTap to update DDNS:\n"
                                 + build_ddns_update_url(DDNS_UPDATE_HOST, ddns_hostname, external_ip, creds,
                                                         ddns_config.get('ddns_update_path', DDNS_DEFAULT_UPDATE_PATH))
                                 + "\n")
        else:
            failures.append(f"DDNS check {ddns_hostname or args.ddns_config}: {ddns_status}")

    if not isinstance(diagnostics.get('total_ping_time'), float):
        failures.append(f"Ping Resolve Time: {diagnostics.get('total_ping_time')}")
    elif diagnostics['total_ping_time'] >= LIMITS['ping_resolve_time']:
        failures.append(f"Ping Resolve Time: {diagnostics['total_ping_time']:.2f}s")

    try:
        if float(diagnostics.get('ping_max', 0)) >= LIMITS['ping_max']:
            failures.append(f"Ping Max: {diagnostics['ping_max']}ms")
    except ValueError:
        failures.append(f"Ping Max Unavailable: {diagnostics.get('ping_max')}")

    if float(diagnostics.get('packet_loss', 0)) > LIMITS['ping_packet_loss']:
        ping_error = diagnostics.get('ping_error', '')
        if ping_error:
            failures.append(f"Ping Packet Loss: {diagnostics['packet_loss']}% ({ping_error})")
        else:
            failures.append(f"Ping Packet Loss: {diagnostics['packet_loss']}%")

    st_packet_loss = diagnostics.get('packet_loss_st', None)

    if st_packet_loss is not None:
        if float(st_packet_loss) >= LIMITS['speedtest_packet_loss']:
            failures.append(f"Speedtest Packet Loss: {st_packet_loss}%")
    else:
        failures.append("Speedtest Packet Loss: Unavailable")

    if diagnostics.get('download_mbps', 0) <= LIMITS['download_mbps']:
        failures.append(f"Download Speed Low: {diagnostics.get('download_mbps', 0):.2f} Mbps")

    if diagnostics.get('upload_mbps', 0) <= LIMITS['upload_mbps']:
        failures.append(f"Upload Speed Low: {diagnostics.get('upload_mbps', 0):.2f} Mbps")

    if failures:
        failure_text += "\nFAILURES DETECTED:\n"
        for fail in failures:
            failure_text += f"- {fail}\n"
        write_failure_log(args.pc_name, failures, timers_list, raw_timers, verbose)

    failures_str = "\n".join(failures) if failures else "All checks passed"

    if(args.wards is not None):
        log_diagnostics_to_sheet(args.wards.split(',')[0].strip(), args.pc_name, googleDoc, diagnostics, failures_str, num_from, num_to, verbose)
    else:
        print("!!Can't Log Diagnostic Data with a Ward!!")

    # console never shows the credentialed link, only a version without the login
    ddns_console_text = ""
    if ddns_sms_text:
        ddns_console_text = ("\nDDNS update link (login removed): "
                             + build_ddns_update_url(DDNS_UPDATE_HOST, ddns_hostname, external_ip, None,
                                                     ddns_config.get('ddns_update_path', DDNS_DEFAULT_UPDATE_PATH))
                             + "\n")

    if(num_from is not None and num_to is not None):
        print(send_text + console_text + failure_text + ddns_console_text)
        if(args.wards is not None):
            # compact layout for phones, the console keeps the full table
            sms_text = f"System: {args.pc_name}, temperature: {temp}C\n"
            if testing_active:
                sms_text += "!!TESTING ACTIVE!!\n"
            sms_text += "\n" + (format_timers_for_sms(timers_list) or "No timers found") + "\n" + failure_text
        else:
            sms_text = send_text + failure_text
        sms.send_sms(num_from, num_to, sms_text, verbose) # sms.py makes the text GSM-7 safe
        if ddns_sms_text:
            # separate message so the link is easy to tap and the status message stays short
            sms.send_sms(num_from, num_to, ddns_sms_text.strip(), verbose)
    else:
        print(send_text + console_text + failure_text + ddns_console_text)
        print("\n\nNo numbers given, nothing to send")
