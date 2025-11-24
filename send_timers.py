#!/usr/bin/python3

import argparse
import os
import traceback
import subprocess
import json
import re

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


def parse_timer_line_minimal(line):
    parts = re.split(r'\s{2,}', line.strip())

    # systemctl list-timers order:
    # NEXT | LEFT | LAST | PASSED | UNIT | ACTIVATES
    if len(parts) >= 6 and parts[0] != "NEXT":
        try:
            time_left_val = float(parts[1].replace('left', '').replace('h', '').strip())
        except ValueError:
            #time_left_val = 0.0  # or you could choose to skip this entry
            return None

        return {
            'next_run': parts[0],
            'time_left_raw': parts[1].replace('left', '').strip(),
            'time_left': time_left_val,
            'unit': parts[4],
            'activates': parts[5]
        }
    else:
        return None

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
    args = parser.parse_args()

    num_from = args.num_from
    num_to = args.num_to
    verbose = args.verbose

    send_text = ""
    console_text = ""
    failure_text = ""
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

    if os.path.exists(os.path.abspath(os.path.dirname(__file__)) + '/testing'):
        send_text += '!!TESTING ACTIVE!!\n\n\n'

    timers_list = []
    result = subprocess.run(['systemctl', 'list-timers'], capture_output=True, text=True, check=True)
    timers = result.stdout
    if(args.wards is not None):
        timers = timers.splitlines()
        wards = args.wards.split(',') + ['ACTIVATES'] # adding this so we get the header
        send_text += f"\n{'NEXT':<28} {'LEFT':<9} {'UNIT':<20} ACTIVATES\n"
        for line in timers:
            if line.strip().startswith("NEXT"):
                continue
            for ward in wards:
                if(ward in line):
                    timer = parse_timer_line_minimal(line)
                    if timer:
                        timers_list.append(timer)
                        send_text += f"{timer['next_run']:<28} {timer['time_left_raw']:<9} {timer['unit']:<20} {timer['activates']}\n"

    else:
        send_text += timers

    start_ping = datetime.now()
    try:
        result = subprocess.run(['ping', '-c', '2', 'x.rtmp.youtube.com'], capture_output=True, text=True)
        lines = result.stdout.splitlines()
        # Filter out 'rtt' lines in Python instead of piping to `grep`
        ping_output = "\n".join([line for line in result.stdout.splitlines() if 'rtt' not in line])
        console_text += '\n\n' + ping_output
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
    console_text += f'\n\n PTZ Camera : {status}'

    diagnostics['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    failures = []

    for ward in args.wards.split(','):
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

    if diagnostics['total_ping_time'] >= LIMITS['ping_resolve_time']:
        failures.append(f"Ping Resolve Time: {diagnostics['total_ping_time']:.2f}s")

    try:
        if float(diagnostics.get('ping_max', 0)) >= LIMITS['ping_max']:
            failures.append(f"Ping Max: {diagnostics['ping_max']}ms")
    except ValueError:
        failures.append(f"Ping Max Unavailable: {diagnostics.get('ping_max')}")

    if float(diagnostics.get('packet_loss', 0)) > LIMITS['ping_packet_loss']:
        failures.append(f"Ping Packet Loss: {diagnostics['packet_loss']}%")

    st_packet_loss = diagnostics.get('packet_loss_st', None)

    if st_packet_loss is not None:
        if float(st_packet_loss) >= LIMITS['speedtest_packet_loss']:
            failures.append(f"Speedtest Packet Loss: {st_packet_loss}%")
    else:
        failures.append("Speedtest Packet Loss: Unavailable")

    if diagnostics.get('download_mbps', 0) <= LIMITS['download_mbps']:
        failures.append(f"Download Speed Low: {diagnostics['download_mbps']:.2f} Mbps")

    if diagnostics.get('upload_mbps', 0) <= LIMITS['upload_mbps']:
        failures.append(f"Upload Speed Low: {diagnostics['upload_mbps']:.2f} Mbps")

    if failures:
        failure_text += "\n\nFAILURES DETECTED:\n"
        for fail in failures:
            failure_text += f"- {fail}\n"

    failures_str = "\n".join(failures) if failures else "All checks passed"

    if(args.wards is not None):
        log_diagnostics_to_sheet(args.wards.split(',')[0].strip(), args.pc_name, googleDoc, diagnostics, failures_str, num_from, num_to, verbose)
    else:
        print("!!Can't Log Diagnostic Data with a Ward!!")

    if(num_from is not None and num_to is not None):
        print(send_text + console_text + failure_text)
        sms.send_sms(num_from, num_to, send_text + failure_text, verbose)
    else:
        print(send_text + console_text + failure_text)
        print("\n\nNo numbers given, nothing to send")
