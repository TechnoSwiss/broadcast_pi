#!/usr/bin/python3

import argparse
import os
import traceback
import json

from datetime import datetime, timedelta

import google_auth # google_auth.py local file
import update_link # update_link.py local file
import youtube_api as yt # youtube.py local file
import sms # sms.py local file
import send_email # send_email.py local file
import update_status # update_status.py localfile
import insert_event # insert_event.py local file
import global_file as gf # local file for sharing globals between files

import gspread # pip install gspread==2.0.0

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Insert Live Broadcast in YouTube Live list.')
    parser.add_argument('-c','--config-file',type=str,help='JSON Configuration file')
    parser.add_argument('-w','--ward',type=str,help='Name of Ward being broadcast')
    parser.add_argument('-i','--title',type=str,default='Live Stream',help='Broadcast Title')
    parser.add_argument('-S','--status-file',type=str,help='Path and fineame for file used to write out Start/Stop time status.')
    parser.add_argument('-n','--thumbnail',type=str,default='thumbnail.jpg',help='Path and filename for the JPG image that will be the video thumbnail')
    parser.add_argument('-o','--host-name',type=str,help='The address for the web host to upload HTML link forward page to')
    parser.add_argument('-u','--user-name',type=str,help='The username for the web host')
    parser.add_argument('-H','--home-dir',type=str,help='Home directory SSH id_rsa key is stored under')
    parser.add_argument('-U','--url-filename',type=str,help='Use this for web filename instead of Unit name.')
    parser.add_argument('-L','--html-filename',type=str,help='Override default upload path and filename, this must be full path and filename for target webserver')
    parser.add_argument('-k','--url-key',type=str,help='The 4-digit code at the end of the URL')
    parser.add_argument('-s','--start-time',type=str,help='Broadcast start time in HH:MM:SS')
    parser.add_argument('-t','--run-time',type=str,default='1:10:00',help='Broadcast run time in HH:MM:SS')
    parser.add_argument('-D','--delete-control',type=int,help='Control delete options from command line, bit mapped. delete_current 1 : delete_ready 2 : delete_complete 4')
    parser.add_argument('-C','--current-id',type=str,help='ID value for the current broadcast, used if deleting current broadcast is true')
    parser.add_argument('-I','--insert-next-broadcast',default=False, action='store_true',help='Insert next broadcast, this should only be used if calling from broadcast.py')
    parser.add_argument('--email-send',default=False, action='store_true',help='Should email be sent when deleting video(s)')
    parser.add_argument('-e','--email-from',type=str,help='Account to send email with/from')
    parser.add_argument('-E','--email-to',type=str,help='Account to send CSV fiel email to')
    parser.add_argument('-M','--dkim-private-key',type=str,help='Full path and filename of DKIM private key file')
    parser.add_argument('-m','--dkim-selector',type=str,help='DKIM Domain Selector')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    verbose = args.verbose
    num_from = args.num_from
    num_to = args.num_to
    ward = args.ward
    current_id = args.current_id
    insert_next_broadcast = args.insert_next_broadcast
    email_send = args.email_send
    googleDoc = 'Broadcast Viewers' # I need to parameterize this at some point...

    delete_current = True # in keeping with guidence not to record sessions, delete the current session
    delete_ready = True # script will create a new broadcast endpoint after the delete process, an existing ready broadcasts will interfere since we're not creating seperate endpoints for each broadcast, so delete any ready broadcasts to prevent problems
    delete_complete = True # in most cases there shouldn't be any completed broadcasts (they should have gotten deleted at the end of the broadcast), however some units are uploading broadcasts that we may want to save so this could be switched to Fals

    if(args.delete_control is not None):
        if(args.delete_control & 0x01):
            if(verbose) : print("disable delete current")
            delete_current = False
        if(args.delete_control & 0x02):
            if(verbose) : print("disable delete ready")
            delete_ready = False
        if(args.delete_control & 0x04):
            if(verbose) : print("disable delete complete")
            delete_complete = False

    testing = True if os.path.exists(os.path.abspath(os.path.dirname(__file__)) + '/testing') else False

    if(args.config_file is not None):
        if("/" in args.config_file):
            config_file = args.config_file
        else:
            config_file =  os.path.abspath(os.path.dirname(__file__)) + "/" + args.config_file
    if(verbose): print('Config file : ' + config_file)
    if(config_file is not None and os.path.exists(config_file)):
        with open(config_file, "r") as configFile:
            config = json.load(configFile)

            # check for keys in config file
            if 'testing' in config:
                testing |= config['testing']
            if 'broadcast_ward' in config:
                ward = config['broadcast_ward']
            if 'broadcast_title' in config:
                args.title = config['broadcast_title']
            if 'broadcast_title_card' in config:
                args.thumbnail = config['broadcast_title_card']
            if 'broadcast_day' in config:
                broadcast_day = config['broadcast_day']
            if 'broadcast_time' in config:
                args.start_time = config['broadcast_time']
            if 'broadcast_length' in config:
                args.run_time = config['broadcast_length']
            if 'broadcast_description' in config:
                description = config['broadcast_description']
            if 'delete_current' in config:
                delete_current = config['delete_current']
            if 'delete_completed' in config:
                delete_complete = config['delete_completed']
            if 'delete_ready' in config:
                delete_ready = config['delete_ready']
            if 'url_key' in config:
                args.url_key = config['url_key']
            if 'url_name' in config:
                args.url_filename = config['url_name']
            if 'url_ssh_host' in config:
                args.host_name = config['url_ssh_host']
            if 'url_ssh_username' in config:
                args.user_name = config['url_ssh_username']
            if 'url_ssh_key_dir' in config:
                args.home_dir = config['url_ssh_key_dir']
            if 'broadcast_status' in config:
                args.status_file = config['broadcast_status']
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
                num_from = config['notification_text_from']
            if 'notification_text_to' in config:
                num_to = config['notification_text_to']

    if(ward is None):
        print("!!Ward is a required argument!!")
        exit()

    if(testing):
        #print("!!testing is active!!")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " testing is active!", verbose)

    start_time, stop_time = update_status.get_start_stop(args.start_time, args.run_time, None, ward, num_from, num_to, verbose)

    credentials_file = ward.lower() + '.auth'

    #authenticate with YouTube API
    youtube = google_auth.get_authenticated_service(credentials_file, args)

    # if current_id isn't defined, we can't delete that video
    if(current_id is None):
        delete_current = False
    else:
        numViewers = yt.get_view_count(youtube, current_id, ward, num_from, num_to, verbose)
        if(not testing and email_send):
            print("e-mail total views")
            if(args.email_from is not None and args.email_to is not None):
                send_email.send_total_views(args.email_from, args.email_to, ward, numViewers, args.dkim_private_key, args.dkim_selector, num_from, num_to, verbose)
        if(googleDoc is not None):
            sheet, column, insert_row = yt.get_sheet_row_and_column(googleDoc, current_id, ward, num_from, num_to, verbose)
            sheet.update_cell(insert_row,column, "Total Views")
            sheet.update_cell(insert_row,column+1, numViewers)

    try:
        # delete the recording we just finished
        # if forcibly killing process, don't delete video
        if(delete_current):
            if(verbose) : print("Delete current broadcast")
            youtube.videos().delete(id=current_id).execute()

        # delete all completed videos in Live list
        # delete all ready videos as they will cause problems for the new broadcast we will insert at the end of the script
        broadcasts = yt.get_broadcasts(youtube, ward, num_from, num_to, verbose)
        if(broadcasts is not None):
            if(verbose):
                print("Broadcasts :")
                print(broadcasts)
            for video_id, video_status in broadcasts.items():
                if((delete_complete and video_status == "complete")
                    or (delete_ready and (video_status == "ready"))): # if the broadcast got created but not bound it will be in created instead of ready state, since an un-bound broadcast can't unexpectedly accept a stream we'll leave these
                    if(video_id != current_id): # if current_id is still in list, then we've skipped deleting it above, so don't delete now.
                        try:
                            if(video_status == "complete"):
                                if(verbose) : print("Delete complete broadcast " + video_id)
                            if(video_status == "ready"):
                                if(verbose) : print("Delete ready broadcast " + video_id)
                            youtube.videos().delete(id=video_id).execute()
                        except:
                            if(verbose): print(traceback.format_exc())
                            gf.log_exception(traceback.format_exc(), "failed to delete complete/ready broadcast(s)")
                            print("Failed to delete complete/ready broadcast " + video_id)
                            if(num_from is not None and num_to is not None):
                                sms.send_sms(num_from, num_to, ward + " failed to delete complete/ready broadcast " + video_id + "!", verbose)
    except:
        if(verbose) : print(traceback.format_exc())
        gf.log_exception(traceback.format_exc(), "failed to delete broadcast")
        print("Failed to delete broadcast " + video_id)
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to delete broadcast " + video_id + "!", verbose)

    # should only be calling this from the at command, but if we are, then we might need to also create the next broadcast
    if(insert_next_broadcast):
        if(verbose) : print("Create next weeks broadcast")
        if(broadcast_day is None):
            next_date = datetime.strftime(start_time + timedelta(days=7), '%m/%d/%y')
        else:
            import calendar
            days = dict(zip([x.lower() for x in calendar.day_abbr], range(7)));

            try:
                next_days = ((7 + days[broadcast_day[0:3].lower()]) - start_time.weekday()) % 7
                if(next_days == 0) : next_days = 7
                next_date = datetime.strftime(start_time + timedelta(days=next_days), '%m/%d/%y')
            except:
                if(verbose): print(traceback.format_exc())
                print("Failed to get next broadcast date")
                if(num_from is not None and num_to is not None):
                    sms.send_sms(num_from, num_to, ward + " failed to get next broadcast date!", verbose)
        # create a broadcast endpoint for next weeks video
        start_time, stop_time = update_status.get_start_stop(args.start_time, args.run_time, next_date, ward, num_from, num_to, verbose)
        current_id = insert_event.insert_event(youtube, args.title, description, start_time, args.run_time, args.thumbnail, ward, num_from, num_to, verbose)

         # update status file with next start/stop times (there may be multiple wards in this file, so read/write out any that don't match current ward
        update_status.update("start", start_time, stop_time, args.status_file, ward, num_from, num_to, verbose)

        if(current_id is None):
            print("Failed to create new broadcast for next week")
            if(num_from is not None and num_to is not None): sms.send_sms(num_from, num_to, ward + " failed to create broadcast for next week!", verbose)

        # make sure link on web host is current
        update_link.update_live_broadcast_link(current_id, args, ward, args.html_filename, args.url_filename, verbose)
