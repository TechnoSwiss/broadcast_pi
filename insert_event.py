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
import update_status # update_status.py localfile

def insert_event(youtube, title, description, start_time, run_time, thumbnail, ward, num_from = None, num_to = None, verbose = False, language = None, captions = False):
    current_id = yt.create_live_event(youtube, title, description, start_time, run_time, thumbnail, ward, num_from, num_to, verbose, language, captions)

    return(current_id)

def bind_event(youtube, current_id, ward, num_from = None, num_to = None, verbose = False, stream = None):
    stream_id = yt.get_stream(youtube, ward, num_from, num_to, verbose) if stream == None else yt.get_stream(youtube, ward, num_from, num_to, verbose, stream)

    yt.bind_broadcast(youtube, current_id, stream_id, ward, num_from, num_to, verbose)

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
    parser.add_argument('-C','--current-id',type=str,help='ID value for the upcoming broadcast, used to bind / update URL for existing video')
    parser.add_argument('-z','--stream',type=int,help='The stream number to attached to this event, use for multiple concurent broadcasts.')
    parser.add_argument('-s','--start-time',type=str,help='Broadcast start time in HH:MM:SS')
    parser.add_argument('-t','--run-time',type=str,default='1:10:00',help='Broadcast run time in HH:MM:SS')
    parser.add_argument('-A','--start-date',type=str,help='Broadcast run date in MM/DD/YY, use for setting up future broadcasts')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    broadcast_day = None
    description = None

    if(args.config_file is not None and os.path.exists(args.config_file)):
        with open(args.config_file, "r") as configFile:
            config = json.load(configFile)

            # check for keys in config file
            if 'broadcast_ward' in config:
                args.ward = config['broadcast_ward']
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
            if 'notification_text_from' in config:
                args.num_from = config['notification_text_from']
            if 'notification_text_to' in config:
                args.num_to = config['notification_text_to']

    if(args.ward is None):
        print("!!Ward is a required argument!!")
        exit()

    credentials_file = args.ward.lower() + '.auth'

    # if sending in a start date argument, it should override json file
    if(args.start_date is not None):
        broadcast_date = args.start_date
    else:
        import calendar
        days = dict(zip([x.lower() for x in calendar.day_abbr], range(7)));

        try:
            start_time = datetime.now()
            next_days = ((7 + days[broadcast_day[0:3].lower()]) - start_time.weekday()) % 7
            if(next_days == 0): next_days = 7
            broadcast_date = datetime.strftime(start_time + timedelta(days=next_days), '%m/%d/%y')
        except:
            if(args.verbose): print(traceback.format_exc())
            print("Failed to get broadcast date")
            if(args.num_from is not None and args.num_to is not None):
                sms.send_sms(args.num_from, args.num_to, args.ward + " failed to get broadcast date!", args.verbose)

    start_time, stop_time = update_status.get_start_stop(args.start_time, args.run_time, broadcast_date, args.ward, args.num_from, args.num_to, args.verbose)

    #authenticate with YouTube API
    youtube = google_auth.get_authenticated_service(credentials_file, ward, num_from, num_to, 'youtube', 'v3', args.verbose)

    # normally we are only binding videos just before we're ready to send the 
    # stream to them, this allows us to control which broadcast is getting the
    # live stream, any videos in the ready state are already bound and can cause
    # problems, so delete them, unless we're setting up multiple stream
    # broadcasts, which are generally not automated and controlled manually
    # from the YouTube Studio
    if(args.stream is None):
        for video_id, video_status in yt.get_broadcasts(youtube, args.ward, args.num_from, args.num_to, args.verbose).items():
            if(video_status == "ready"):
                youtube.videos().delete(id=video_id).execute()

    if(args.current_id is None):
        current_id = insert_event(youtube, args.title, description, start_time, args.run_time, args.thumbnail, args.ward, args.num_from, args.num_to, args.verbose)
    else:
        current_id = args.current_id

    if(args.stream is not None):
        bind_event(youtube, current_id, args.ward, args.num_from, args.num_to, args.verbose, args.stream)

    if(current_id is None):
        print("Failed to insert new broadcast!")
    else:
        # modified the status-file cli parameter so that it doesn't have a default values here, so we can decide if we want this to update the status file or not at runtime
        if(args.status_file is not None):
            update_status.update("start", start_time, stop_time, args.status_file, args.ward, args.num_from, args.num_to, args.verbose)
        #make sure link on web host is current
        update_link.update_live_broadcast_link(current_id, args, args.ward, args.html_filename, args.url_filename)
