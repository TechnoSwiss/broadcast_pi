#!/usr/bin/python3

import argparse
import os
import traceback

from shlex import split
from datetime import datetime, timedelta
from dateutil import tz # pip install python-dateutil

import google_auth # google_auth.py local file
import update_link # update_link.py local file
import youtube_api as yt # youtube.py local file
import sms # sms.py local file
import update_status # update_status.py local file

def update(update_type, start_time, stop_time, status_file, ward, num_from = None, num_to = None, verbose = False):
    # update_type should be one of the folllowing:
    # broadcast - updating the status because we're starting the broadcast
    # start - updating the status because we're creating an even in the future
    # stop = updating the broadcast because we're extending the runtime
    try:
        if not os.path.exists(status_file):
            with open(status_file, 'w'): pass
        with open(status_file, 'r+') as fp_status:
            status = fp_status.readlines()
            fp_status.seek(0)
            fp_status.truncate()
            for line in status:
                action, timestamp, unit = line.replace('\n', '').split(',')
                if(unit.lower() != ward.lower()): #for all cases if unit doesn't match ward, write out the line and continue
                    fp_status.write(line)
                if(unit.lower() == ward.lower() and update_type == "stop"):
                    if(action.lower() == "stop"):
                        fp_status.write("stop," + stop_time.replace(tzinfo=tz.tzlocal()).astimezone(tz.tzutc()).strftime('%Y-%m-%dT%H:%M:%SZ') + "," + ward.lower() + '\r')
                    else:
                        fp_status.write(line)
            if(update_type == "broadcast" or update_type == "start" or update_type == "pause"):
                if(update_type == "broadcast"):
                    fp_status.write("broadcast," + start_time.replace(tzinfo=tz.tzlocal()).astimezone(tz.tzutc()).strftime('%Y-%m-%dT%H:%M:%SZ') + "," + ward.lower() + '\n')
                elif(update_type == "pause"):
                    fp_status.write("pause," + start_time.replace(tzinfo=tz.tzlocal()).astimezone(tz.tzutc()).strftime('%Y-%m-%dT%H:%M:%SZ') + "," + ward.lower() + '\n')
                else:
                    fp_status.write("start," + start_time.replace(tzinfo=tz.tzlocal()).astimezone(tz.tzutc()).strftime('%Y-%m-%dT%H:%M:%SZ') + "," + ward.lower() + '\n')
                fp_status.write("stop," + stop_time.replace(tzinfo=tz.tzlocal()).astimezone(tz.tzutc()).strftime('%Y-%m-%dT%H:%M:%SZ') + "," + ward.lower() + '\n')
    except:
        if(verbose): print(traceback.format_exc())
        print("Failed to update status file - " + update_type)
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to update status file " + update_type + "!", verbose)

def get_start_stop(start_time, duration, start_date = None, ward = None, num_from = None, num_to = None, verbose = False):
    try:
        if(start_date is not None):
            start_date = start_date + ' '
        else:
            start_date = datetime.now().strftime('%m/%d/%y ')
        if(start_time is not None):
            try:
                start_time = datetime.strptime(start_date + start_time, '%m/%d/%y %H:%M:%S') # this is a 2 digit year
            except ValueError:
                start_time = datetime.strptime(start_date + start_time, '%m/%d/%Y %H:%M:%S') # this is a 4 digit year
        else:
            start_time = datetime.now()
            start_time = start_time - timedelta(seconds=start_time.second, microseconds=start_time.microsecond) + timedelta(minutes=1) # we had been rounding down to the nearest 5 min., but that cause problems now that we are inserting YT broadcasts and including the start and run-time, because YT won't let you add events in the past.

        H, M, S = duration.split(':')
        stop_time = start_time + timedelta(hours=int(H), minutes=int(M),seconds=int(S))

    except:
        if(verbose): print(traceback.format_exc())
        print("Error getting start/stop times")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " error getting start/stop times!", verbose)

    return start_time, stop_time

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Update Status file with broadcast information (start/stop/broadcast times).')
    parser.add_argument('-w','--ward',type=str,required=True,help='Name of Ward being broadcast')
    parser.add_argument('-S','--status-file',type=str,default='status',help='Path and fineame for file used to write out Start/Stop time status.')
    parser.add_argument('-s','--start-time',type=str,help='Broadcast start time in HH:MM:SS')
    parser.add_argument('-t','--run-time',type=str,default='1:10:00',help='Broadcast run time in HH:MM:SS')
    parser.add_argument('-A','--start-date',type=str,help='Broadcast run date in MM/DD/YY, use for setting up future broadcasts')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    ward = args.ward
    num_from = args.num_from
    num_to = args.num_to
    verbose = args.verbose

    start_time, stop_time = update_status.get_start_stop(args.start_time, args.run_time, args.start_date, ward, num_from, num_to, verbose)

    update("start", start_time, stop_time, args.status_file, ward, num_from, num_to, verbose)
    
