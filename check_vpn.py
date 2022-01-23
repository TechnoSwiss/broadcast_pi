#!/usr/bin/python3

import argparse
import os
import traceback
import json

from subprocess import Popen, check_output, PIPE
from datetime import datetime, timedelta

import sms # sms.py local file

ATTEMPTS_PER_HOUR = 2
ATTEMPTS_PER_DAY  = 6

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Restart VPN if not running.')
    parser.add_argument('-p','--pc-name',type=str,required=True,help='System name that script is running on.')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    restart_attempts = os.path.abspath(os.path.dirname(__file__)) + '/vpn_restart'

    try:
        ip_addr = check_output(['ip', 'addr']).decode('utf-8')

        if('tun' not in ip_addr):
            if(os.path.exists(restart_attempts)):
                with open(restart_attempts, "r") as attemptsFile:
                    try:
                        attempts = json.load(attemptsFile)
                    except:
                        if(args.verbose): print(traceback.format_exc())
                        attempts = {}
            else:
                attempts = {}
            attempt_num = attempts['num_attempts'] if 'num_attempts' in attempts else 0
            attempt_first = datetime.fromisoformat(attempts['first_attempt'])  if 'first_attempt' in attempts else datetime.now()
            time_diff = datetime.now() - attempt_first
            max_attempts_hour = (time_diff.seconds//3600 + 1) * ATTEMPTS_PER_HOUR
            max_attempts_day = (time_diff.days + 1) * ATTEMPTS_PER_DAY
            if(args.verbose): print("Attempts per Hour: " + str(max_attempts_hour))
            if(args.verbose): print("Attempts per Day: " + str(max_attempts_day))
            if(args.verbose): print("Attempt Num: " + str(attempt_num))
            if(attempt_num < max_attempts_hour and attempt_num < max_attempts_day):
                with open(restart_attempts, "w") as attemptsFile:
                    if(args.num_from is not None and args.num_to is not None):
                        sms.send_sms(args.num_from, args.num_to, args.pc_name + " VPN down, attempting to restart!", args.verbose)
                    print("Restarting VPN.")
                    # user account must have passwordless sudo access to restart the OpenVPN service for this to work (ie update sudoers 'ALL=NOPASSWD: /bin/systemctl restart openvpn')
                    systemctl_results = check_output(['sudo', 'systemctl', 'restart', 'openvpn'])
                    attempt_num += 1
                    attempts['num_attempts'] = attempt_num
                    attempts['first_attempt'] = attempt_first
                    json.dump(attempts, attemptsFile, indent=4, sort_keys=True, default=str)
        else:
            # VPN is up so clean up VPN restart status file
            if os.path.exists(restart_attempts):
                os.remove(restart_attempts)
    except:
        if(args.verbose): print(traceback.format_exc())
        if(args.num_from is not None and args.num_to is not None):
            sms.send_sms(args.num_from, args.num_to, args.pc_name + " failed to verify or restart VPN!", args.verbose)

