#!/usr/bin/python3

import argparse
import os
import traceback
import json
import sys
import time

from subprocess import Popen, check_output, PIPE
from datetime import datetime, timedelta

import sms # sms.py local file

FAILURES_TO_RESTART = 0
ATTEMPTS_PER_HOUR = 2
ATTEMPTS_PER_DAY  = 6
DELAY_AFTER_RESTART = 20

SERVER_IP = "192.168.1.1"

def is_vpn_connected(pc_name, num_from = None, num_to = None, verbose = False):
    try:
        ip_addr = check_output(['ip', 'addr']).decode('utf-8')

        if('tun' not in ip_addr):
            if(verbose): print("VPN Connection not present")
            return False
        else:
            response = os.system("ping -c 1 -w2 " + SERVER_IP + " > /dev/null 2>&1")
            if(response > 0):
                if(verbose): print("Can't ping server")
                return False
            else:
                if(verbose): print("VPN connection up")
                return True
    except:
        if(verbose): print(traceback.format_exc())
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, pc_name + " failed to verify or restart VPN!", verbose)
        sys.exit()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Restart VPN if not running.')
    parser.add_argument('-p','--pc-name',type=str,required=True,help='System name that script is running on.')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    restart_attempts = os.path.abspath(os.path.dirname(__file__)) + '/vpn_restart'

    vpn_connected = is_vpn_connected(args.pc_name, args.num_from, args.num_to, args.verbose)

    if(not vpn_connected):
        if(os.path.exists(restart_attempts)):
            with open(restart_attempts, "r") as attemptsFile:
                try:
                    attempts = json.load(attemptsFile)
                except:
                    if(args.verbose): print(traceback.format_exc())
                    attempts = {}
        else:
            attempts = {}
        # this is the number of times this script has been run and is_vpn_connected returned a FALSE since the last time is_vpn_connected returned TRUE
        failure_num = attempts['num_failures'] if 'num_failures' in attempts else 1
        # this is the number of times that an attempt has been made to restart the VPN
        attempt_num = attempts['num_attempts'] if 'num_attempts' in attempts else 0
        attempt_first = datetime.fromisoformat(attempts['first_attempt'])  if 'first_attempt' in attempts else datetime.now()
        notification_sent = attempts['notification_sent'] if 'notification_sent' in attempts else False
        time_diff = datetime.now() - attempt_first
        max_attempts_hour = (time_diff.seconds//3600 + 1) * ATTEMPTS_PER_HOUR
        max_attempts_day = (time_diff.days + 1) * ATTEMPTS_PER_DAY
        if(args.verbose): print("Failure Num: " + str(failure_num))
        if(args.verbose): print("Attempts per Hour: " + str(max_attempts_hour))
        if(args.verbose): print("Attempts per Day: " + str(max_attempts_day))
        if(args.verbose): print("Attempt Num: " + str(attempt_num))
        vpn_restart = False
        if(failure_num > FAILURES_TO_RESTART and attempt_num < max_attempts_hour and attempt_num < max_attempts_day):
            vpn_restart = True
            #if(args.num_from is not None and args.num_to is not None):
            #    sms.send_sms(args.num_from, args.num_to, args.pc_name + " VPN down, attempting to restart!", args.verbose)
            print("Restarting VPN.")
            # user account must have passwordless sudo access to restart the OpenVPN service for this to work (ie update sudoers 'ALL=NOPASSWD: /bin/systemctl restart openvpn')
            systemctl_results = check_output(['sudo', 'systemctl', 'restart', 'openvpn'])
            attempt_num += 1
        if(vpn_restart):
            # we want to verify the VPN came back up, and only text about a problem if it did not
            restart_time = datetime.now()
            while((datetime.now() - restart_time).seconds < DELAY_AFTER_RESTART):
                time.sleep(1)
                if(args.verbose): print('Seconds since restart : ' + str((datetime.now() - restart_time).seconds), end='\r')
            if(not is_vpn_connected(args.pc_name, args.num_from, args.num_to, args.verbose)):
                if(args.num_from is not None and args.num_to is not None and notification_sent == False):
                    attempts['notification_sent'] = True
                    sms.send_sms(args.num_from, args.num_to, args.pc_name + " VPN failed to restart!", args.verbose)
                print("VPN failed to restart.")
        with open(restart_attempts, "w") as attemptsFile:
            failure_num += 1
            attempts['num_failures'] = failure_num
            attempts['num_attempts'] = attempt_num
            attempts['first_attempt'] = attempt_first
            json.dump(attempts, attemptsFile, indent=4, sort_keys=True, default=str)
    else:
        # VPN is up so clean up VPN restart status file
        if os.path.exists(restart_attempts):
            with open(restart_attempts, "r") as attemptsFile:
                try:
                    attempts = json.load(attemptsFile)
                except:
                    if(args.verbose): print(traceback.format_exc())
                    attempts = {}
            notification_sent = attempts['notification_sent'] if 'notification_sent' in attempts else False
            if(notification_sent == True):
                if(args.num_from is not None and args.num_to is not None):
                    sms.send_sms(args.num_from, args.num_to, args.pc_name + " VPN back up :D", args.verbose)
            print("VPN back up.")
            os.remove(restart_attempts)

