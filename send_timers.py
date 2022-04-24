#!/usr/bin/python3

import argparse
import os
import traceback

from subprocess import Popen, check_output, PIPE
from datetime import datetime, timedelta

import sms # sms.py local file

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Text systemctl list-timers results to monitor broadcast timers.')
    parser.add_argument('-p','--pc-name',type=str,required=True,help='System name that script is running on.')
    parser.add_argument('-w','--wards',type=str,help='Comma delimited list of wards used to filter timers list.')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    timers = check_output(['systemctl', 'list-timers']).decode('utf-8')
    temp = check_output(['vcgencmd', 'measure_temp']).decode('utf-8').split('=')[-1]
    send_text = 'System: ' + args.pc_name + ', temperature is: ' + temp + '\n\n'

    if os.path.exists(os.path.abspath(os.path.dirname(__file__)) + '/testing'):
        send_text += '!!TESTING ACTIVE!!\n\n\n'

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

    start_ping = datetime.now()
    try:
        ping_results = Popen(['ping', '-c', '2', 'x.rtmp.youtube.com'], stdout=PIPE)
        send_text += '\n\n' + check_output(['grep', '-v', 'rtt'], stdin=ping_results.stdout).decode('utf-8')
        ping_results.wait()
    except:
        send_text += '\n\n !!! PING FAILED !!!'
    ping_delta = datetime.now() - start_ping
    send_text += '\nTotal ping time w/ resolve: ' + str(ping_delta) + '\n\n\n'
    try:
        bandwidth = check_output(['speedtest']).decode('utf-8').splitlines(keepends=True)
        for line in bandwidth:
            if(':' in line and 'URL' not in line):
                send_text += line
    except:
            send_text += '\n\n !!! BANDWIDTH TEST FAILED !!!'

    try:
        CAMERA_IP = '192.168.108.9'
        ping_task = Popen(['ping', '-c', '2', CAMERA_IP], stdout=PIPE)
        ping_results = check_output(['grep', '-v', 'rtt'], stdin=ping_task.stdout).decode('utf-8')
        ping_task.wait()
        send_text += '\n\nPTZ Camera : ' + ('Pass' if ' 0% packet loss' in ping_results  else '!!FAILED!!')
    except:
        if(args.verbose): print(traceback.format_exc())
        send_text += '\n\n !!! CAMERA PING FAILED !!!'

    if(args.num_from is not None and args.num_to is not None):
        print(send_text)
        sms.send_sms(args.num_from, args.num_to, send_text, args.verbose)
    else:
        print(send_text)
        print("\n\nNo numbers given, nothing to send")
