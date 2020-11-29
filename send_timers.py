#!/usr/bin/python3

import argparse
import os
import traceback

from subprocess import check_output
from datetime import datetime, timedelta

import sms # sms.py local file

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Text systemctl list-timers results to monitor broadcast timers.')
    parser.add_argument('-p','--pc-name',type=str,required=True,help='System name that script is running on.')
    parser.add_argument('-w','--wards',type=str,help='Comma delimited list of wards used to filter timers list.')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    args = parser.parse_args()

    timers = check_output(['systemctl', 'list-timers']).decode('utf-8')
    send_text = 'System: ' + args.pc_name + '\n\n'

    if(args.wards is not None):
        timers = timers.splitlines(keepends=True)
        wards = args.wards + ',ACTIVATES' # adding this so we get the header
        wards = wards.split(',')
        for line in timers:
            for ward in wards:
                if(ward in line):
                    send_text += line
    else:
        send_text += timers

    send_text += '\nStart: ' + datetime.strftime(datetime.now(), '%H:%M:%S.%f')
    send_text += '\n\n' + check_output(['ping', '-c', '2', 'x.rtmp.youtube.com']).decode('utf-8')
    send_text += '\nFinish: ' + datetime.strftime(datetime.now(), '%H:%M:%S.%f')
    if(args.num_from is not None and args.num_to is not None):
        print(send_text)
        sms.send_sms(args.num_from, args.num_to, send_text)
    else:
        print(send_text)
        print("No numbers given, nothing to send")
