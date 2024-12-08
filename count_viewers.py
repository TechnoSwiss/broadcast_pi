#!/usr/bin/python3

import argparse
import signal
import os
import traceback
import time
import threading
import json
import pandas # pip3 install pandas
import matplotlib.pyplot as plt # pip3 install matplotlib

from matplotlib.dates import DateFormatter
from matplotlib.ticker import MultipleLocator, AutoMinorLocator, MaxNLocator
from datetime import datetime, timedelta
from subprocess import check_output

import google_auth # google_auth.py local file
import youtube_api as yt # youtube.py local file
import sms # sms.py local file
import send_email # send_email.py localfile
import global_file as gf # local file for sharing globals between files
import delete_event # local file for deleting broadcast

import gspread # pip3 install gspread==2.0.0

NUM_RETRIES = 2

class GracefulKiller:
  kill_now = False
  def __init__(self):
    signal.signal(signal.SIGINT, self.exit_gracefully)
    signal.signal(signal.SIGTERM, self.exit_gracefully)
    signal.signal(signal.SIGHUP, self.exit_gracefully)

  def exit_gracefully(self,signum, frame):
    print("\n!!Received SIGNAL/{}!!".format(str(signum)))
    self.kill_now = True

def write_viewer_image(viewers_file, graph_file, ward, num_from, num_to, verbose):
    try:
        with open(viewers_file, 'r') as inFile:
            df = pandas.read_csv(viewers_file, delimiter=',', 
                    index_col=0,
                    parse_dates=[0],
                    infer_datetime_format=True,
                    names=['time','concurent viewers'])
            if(not df.empty):
                fig, ax = plt.subplots()
                ax.plot(df.index.to_pydatetime(), df)
                ax.xaxis.set_major_formatter(DateFormatter("%-I:%M:%S %p"))
                ax.yaxis.set_major_locator(MaxNLocator(nbins=40, steps=[1,2,5,10], integer=True, min_n_ticks=1))
                ax.set_ylim(ymin=0)
                ax.grid(axis='x', which='major', linestyle='--')
                ax.grid(axis='y', which='major', linestyle='--')
                if(df.shape[0] > 10):
                    ax.xaxis.set_minor_locator(AutoMinorLocator())
                    ax.tick_params(axis='x',which='minor')
                    ax.grid(axis='x', which='minor', linestyle='--')
                plt.title('Concurrent Viewers')
                plt.xlabel('Time')
                fig.autofmt_xdate()
                fig.set_figwidth(16)
                fig.set_figheight(9)
                plt.savefig(graph_file)
    except:
        if(verbose): print(traceback.format_exc())
        print("Error attempting to write viewer image")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to write viewer image!", verbose)

def write_output(outFile, youtube, videoID, ward, num_from = None, num_to = None, verbose = False, extended = False, statusFile = None, googleDoc = None):
    if(googleDoc is not None):
        sheet, column, insert_row = yt.get_sheet_row_and_column(googleDoc, videoID, ward, num_from, num_to, verbose)

    if(verbose): print("Monitoring viewers...")
    start_time = time.monotonic()
    while not gf.killer.kill_now:
      try:
          # check video id in global file to see if we need to update the video id that we're monitoring
          if(gf.current_id and gf.current_id != videoID):
              print("Live ID doesn't match Current ID, updating count")
              print(videoID + " => " + gf.current_id)
              videoID = gf.current_id
          if(yt.get_broadcast_status(youtube, videoID, ward, num_from, num_to, verbose) == "complete"):
              break
          if extended:
              temp = check_output(['vcgencmd', 'measure_temp']).decode('utf-8').split('=')[-1].rstrip()
              throttled = check_output(['vcgencmd', 'get_throttled']).decode('utf-8').split('=')[-1].rstrip()
              status, description = yt.get_broadcast_health(youtube, videoID, ward, num_from, num_to, verbose)
          numViewers = str(yt.get_concurrent_viewers(youtube, videoID, ward, num_from, num_to, verbose))
          outputData = datetime.now().strftime("%m/%d/%Y %H:%M:%S,") + numViewers + ('\n' if not extended else ("," + temp + "," + throttled + "," + status + "," + description + '\n'))
          outFile.write(outputData)
          outFile.flush()
          if(statusFile is not None):
              statusFile.write(numViewers + '\n')
              statusFile.flush()
          if(googleDoc is not None):
              for retry_num in range(NUM_RETRIES):
                exception = None
                tb = None
                try:
                    sheet.update_cell(insert_row,column, numViewers)
                    insert_row = insert_row + 1
                    break
                except Exception as exc:
                    exception = exc
                    tb = traceback.format_exc()
                    if(verbose): print('!!Write Google Doc Retry!!')
                    # token for writing to Google doc only lasts 60 min., then we have to call get_sheet_row_and_column again to renew it
                    sheet, column, insert_row = yt.get_sheet_row_and_column(googleDoc, videoID, ward, num_from, num_to, verbose)

              if exception:
                if(verbose): print(tb)
                print("Failed to write Google Doc")
                gf.log_exception(tb, "failed to write Google Doc")
                if(num_from is not None and num_to is not None):
                    sms.send_sms(num_from, num_to, ward + " failed to write Google Doc!", verbose)
          if(gf.killer.kill_now):
            break
          time.sleep(30.0 - ((time.monotonic() - start_time) % 30.0))
      # we've been getting exception errors here, that kick us out of the loop and then stop recording viewer data
      # trap the exception, record it, and continue with checking the data
      except:
        tb = traceback.format_exc()
        if(verbose): print(tb)
        print("Error attempting to write viewers file")
        gf.log_exception(tb, "failed to write viewers file")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to write current viewers!", verbose)

def count_viewers(viewers_file, graph_file, youtube, videoID, ward, num_from = None, num_to = None, verbose = False, extended = False, status = None, append = False, googleDoc = None):
    try:
        with open(viewers_file, 'a' if append else 'w') as outFile:
            # status file is used by the browser app to show number of current viewers
            if(status is not None):
                with open(status, 'w') as statusFile:
                    write_output(outFile, youtube, videoID, ward, num_from, num_to, verbose, extended, statusFile, googleDoc)
            else:
                write_output(outFile, youtube, videoID, ward, num_from, num_to, verbose, extended, googleDoc)
        if(not gf.killer.kill_now):
            write_viewer_image(viewers_file, graph_file, ward, num_from, num_to, verbose)
    except:
        tb = traceback.format_exc()
        if(verbose): print(tb)
        print("Error attempting to count viewers")
        gf.log_exception(tb, "failed to count current viewers")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to count current viewers!", verbose)

if __name__ == '__main__':

    # Arguments listed as 'ARGUMENT NOT USED' are included because the args list needs them when passed to the delete_event function, they should not be used and need to pass as None
    parser = argparse.ArgumentParser(description='Save YouTube viewer numbers to file, every 30 seconds.')
    parser.add_argument('-c','--config-file',type=str,help='JSON Configuration file')
    parser.add_argument('-w','--ward',type=str,help='Name of Ward being broadcast')
    parser.add_argument('-i','--title',type=str,help='ARGUMENT NOT USED')
    parser.add_argument('-S','--status-file',type=str,help='ARGUMENT NOT USED')
    parser.add_argument('-n','--thumbnail',type=str,help='ARGUMENT NOT USED')
    parser.add_argument('-o','--host-name',type=str,help='ARGUMENT NOT USED')
    parser.add_argument('-u','--user-name',type=str,help='ARGUMENT NOT USED')
    parser.add_argument('-H','--home-dir',type=str,help='ARGUMENT NOT USED')
    parser.add_argument('-U','--url-filename',type=str,help='ARGUMENT NOT USED')
    parser.add_argument('-L','--html-filename',type=str,help='ARGUMENT NOT USED')
    parser.add_argument('-k','--url-key',type=str,help='ARGUMENT NOT USED')
    parser.add_argument('-s','--start-time',type=str,help='ARGUMENT NOT USED')
    parser.add_argument('-t','--run-time',type=str,help='ARGUMENT NOT USED')
    parser.add_argument('-C','--current-id',type=str,help='YouTube Video ID for the current broadcast')
    parser.add_argument('-d','--delay-after',type=int,default=10,help='Number of min. after broadcast to wait before cleaning up videos.')
    parser.add_argument('--email-send',default=True, action='store_false',help='Should email be sent when deleting video(s)')
    parser.add_argument('-e','--email-from',type=str,help='Account to send email with/from')
    parser.add_argument('-E','--email-to',type=str,help='Accoun tto send CSV fiel email to')
    parser.add_argument('-M','--dkim-private-key',type=str,help='Full path and filename of DKIM private key file')
    parser.add_argument('-m','--dkim-selector',type=str,help='DKIM Domain Selector')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-N','--extended',default=False, action='store_true',help='Included extended data with viewer counts')
    parser.add_argument('-D','--delete-control',type=int,help='Control delete options from command line, bit mapped. delete_current 1 : only delete current is availble from this script')
    parser.add_argument('--append',default=False, action='store_true',help='Append viewers file instead of overwriting')
    parser.add_argument('--test-image',default=False, action='store_true',help='Creates image from existing CSV file and exits')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    ward = args.ward
    current_id = args.current_id
    insert_next_broadcast = False
    email_send = args.email_send
    recurring = False
    googleDoc = 'Broadcast Viewers' # I need to parameterize this at some point...

    delete_current = True # in keeping with guidence not to record sessions, delete the current session

    gf.killer = GracefulKiller()

    if(args.delete_control is not None):
        if(args.delete_control & 0x01):
            if(args.verbose) : print("disable delete current")
            delete_current = False

    if(args.config_file is not None):
        if("/" in args.config_file):
            config_file = args.config_file
        else:
            config_file =  os.path.abspath(os.path.dirname(__file__)) + "/" + args.config_file
    if(args.verbose): print('Config file : ' + config_file)
    if(config_file is not None and os.path.exists(config_file)):
        with open(config_file, "r") as configFile:
            config = json.load(configFile)

            # check for keys in config file
            if 'broadcast_ward' in config:
                ward = config['broadcast_ward']
            if 'delete_time_delay' in config:
                args.delay_after = config['delete_time_delay']
            if 'delete_current' in config:
                delete_current = config['delete_current']
            if 'email_send' in config:
                email_send = config['email_send']
            if 'email_from_account' in config:
                args.email_from = config['email_from_account']
            if 'email_dkim_key' in config:
                args.dkim_private_key = config['email_dkim_key']
            if 'email_dkim_domain' in config:
                args.dkim_selector = config['email_dkim_domain']
            if 'email_viewer_addresses' in config:
                args.email_to = config['email_viewer_addresses']
            if 'notification_text_from' in config:
                args.num_from = config['notification_text_from']
            if 'notification_text_to' in config:
                args.num_to = config['notification_text_to']

    if(ward is None):
        print("!!Ward is a required argument!!")
        exit()
    else:
        args.ward = ward # this argument gets passed in the deletion sub routine

    if(args.email_from is None):
        print("!!Email From is a required argument!!")
        exit()

    if(args.email_to is None):
        print("!!Email To is a required argument!!")
        exit()

    credentials_file = ward.lower() + '.auth'

    viewers_file = ward.lower() + (('_' + current_id) if current_id is not None else '') + '_viewers.csv'
    graph_file = ward.lower() + (('_' + current_id) if current_id is not None else '') + '_viewers.png'

    if(args.test_image):
        write_viewer_image(viewers_file, graph_file, ward, args.num_from, args.num_to, args.verbose)
        exit()

    if(current_id is None):
        print("!!Current ID is a required argument!!")
        exit()


    #authenticate with YouTube API
    youtube = google_auth.get_authenticated_service(credentials_file, args)

    count = threading.Thread(target = count_viewers, args = (viewers_file, graph_file, youtube, current_id, ward, args.num_from, args.num_to, args.verbose, args.extended, None, args.append, googleDoc))
    count.start()
    count.join()

    if(not gf.killer.kill_now):
        print("Broadcast complete")
    else:
        print("Exiting Broadcast")

    numViewers = yt.get_view_count(youtube, current_id, ward, args.num_from, args.num_to, args.verbose)
    if(not gf.killer.kill_now and email_send):
        if(args.email_from is not None and args.email_to is not None):
            send_email.send_viewer_file(viewers_file, graph_file, args.email_from, args.email_to, ward, numViewers, args.dkim_private_key, args.dkim_selector, args.num_from, args.num_to, args.verbose)

    if(googleDoc is not None):
        sheet, column, insert_row = yt.get_sheet_row_and_column(googleDoc, current_id, ward, args.num_from, args.num_to, args.verbose)
        sheet.update_cell(gf.GD_VIEWS_ROW,column, "Views = " + str(numViewers))

    # don't run video deletion if we Ctrl+C out of the process
    if(not gf.killer.kill_now and delete_current):
        run_deletion_time = datetime.now() + timedelta(minutes=int(args.delay_after))
        print("video(s) deletion routine will run at {}".format(run_deletion_time.strftime("%H:%M %Y-%m-%d")))
        delete_event.setup_event_deletion(current_id, numViewers, email_send, recurring, run_deletion_time, args)