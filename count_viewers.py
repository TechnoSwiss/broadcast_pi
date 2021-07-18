#!/usr/bin/python3

import argparse
import os
import traceback
import time
import threading

from datetime import datetime, timedelta
from subprocess import check_output

import google_auth # google_auth.py local file
import youtube_api as yt # youtube.py local file
import sms # sms.py local file
import send_email # send_email.py localfile

def count_viewers(filename, youtube, videoID, ward, num_from = None, num_to = None, verbose = False, extended = False):
    try:
        with open(filename, 'w') as outFile:
            while True:
                if(yt.get_broadcast_status(youtube, videoID, ward, num_from, num_to, verbose) == "complete"):
                    break
                if extended:
                    temp = check_output(['vcgencmd', 'measure_temp']).decode('utf-8').split('=')[-1].rstrip()
                    throttled = check_output(['vcgencmd', 'get_throttled']).decode('utf-8').split('=')[-1].rstrip()
                outputData = datetime.now().strftime("%m/%d/%Y %H:%M:%S,") + str(yt.get_concurrent_viewers(youtube, videoID, ward, num_from, num_to, verbose)) + ('\n' if not extended else ("," + temp + "," + throttled + '\n'))
                outFile.write(outputData)
                outFile.flush()
                time.sleep(30)
    except:
        if(verbose): print(traceback.format_exc())
        print("Error attempting to write viewers file")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to write current viewers!", verbose)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Save YouTube viewer numbers to file, every 30 seconds.')
    parser.add_argument('-w','--ward',type=str,required=True,help='Name of Ward being broadcast')
    parser.add_argument('-I','--video-id',type=str,help='YouTube Video ID')
    parser.add_argument('-e','--email-from',type=str,required=True,help='Account to send email with/from')
    parser.add_argument('-E','--email-to',type=str,required=True,help='Accoun tto send CSV fiel email to')
    parser.add_argument('-M','--dkim-private-key',type=str,help='Full path and filename of DKIM private key file')
    parser.add_argument('-m','--dkim-selector',type=str,help='DKIM Domain Selector')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-N','--extended',default=False, action='store_true',help='Included extended data with viewer counts')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    credentials_file = args.ward.lower() + '.auth'

    #authenticate with YouTube API
    youtube = google_auth.get_authenticated_service(credentials_file, args)

    count = threading.Thread(target = count_viewers, args = ('viewers.csv', youtube, args.video_id, args.ward, args.num_from, args.num_to, args.verbose, args.extended))
    count.start()
    count.join()

    if(args.email_from is not None and args.email_to is not None):
        send_email.send_viewer_file('viewers.csv', args.email_from, args.email_to, args.ward, args.dkim_private_key, args.dkim_selector, args.num_from, args.num_to, args.verbose)

