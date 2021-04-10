#!/usr/bin/python3

import argparse
import os
import traceback

import google_auth # google_auth.py local file
import update_link # update_link.py local file
import youtube_api as yt # youtube.py local file
import sms # sms.py local file
import update_status # update_status.py localfile

def insert_event(youtube, title, start_time, run_time, thumbnail, ward, num_from = None, num_to = None, verbose = False, stream = None):
    current_id = yt.create_live_event(youtube, title, start_time, run_time, thumbnail, ward, num_from, num_to, verbose)

    stream_id = yt.get_stream(youtube, ward, num_from, num_to, verbose) if stream == None else yt.get_stream(youtube, ward, num_from, num_to, verbose, stream)

    yt.bind_broadcast(youtube, current_id, stream_id, ward, num_from, num_to, verbose)

    return(current_id)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Insert Live Broadcast in YouTube Live list.')
    parser.add_argument('-w','--ward',type=str,required=True,help='Name of Ward being broadcast')
    parser.add_argument('-i','--title',type=str,default='Live Stream',help='Broadcast Title')
    parser.add_argument('-S','--status-file',type=str,help='Path and fineame for file used to write out Start/Stop time status.')
    parser.add_argument('-n','--thumbnail',type=str,default='thumbnail.jpg',help='Path and filename for the JPG image that will be the video thumbnail')
    parser.add_argument('-o','--host-name',type=str,help='The address for the web host to upload HTML link forward page to')
    parser.add_argument('-u','--user-name',type=str,help='The username for the web host')
    parser.add_argument('-D','--home-dir',type=str,help='Home directory SSH id_rsa key is stored under')
    parser.add_argument('-U','--url-filename',type=str,help='Use this for web filename instead of Unit name.')
    parser.add_argument('-K','--html-filename',type=str,help='Override default upload path and filename, this must be full path and filename for target webserver')
    parser.add_argument('-k','--url-key',type=str,help='The 4-digit code at the end of the URL')
    parser.add_argument('-z','--stream',type=int,help='The stream number to attached to this event, use for multiple concurent broadcasts.')
    parser.add_argument('-s','--start-time',type=str,help='Broadcast start time in HH:MM:SS')
    parser.add_argument('-t','--run-time',type=str,default='1:10:00',help='Broadcast run time in HH:MM:SS')
    parser.add_argument('-A','--start-date',type=str,help='Broadcast run date in MM/DD/YY, use for setting up future broadcasts')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    credentials_file = args.ward.lower() + '.auth'

    start_time, stop_time = update_status.get_start_stop(args.start_time, args.run_time, args.start_date, args.ward, args.num_from, args.num_to, args.verbose)

    #authenticate with YouTube API
    youtube = google_auth.get_authenticated_service(credentials_file, args)

    # any existing videos in the ready state are going to cause problems for the newly inserted video (because we bind the stream at the same time) so delete and videos in the ready state before inserting a new one, for single stream for multi stream we can leave them
    if(args.stream == None):
        for video_id, video_status in yt.get_broadcasts(youtube, args.ward, args.num_from, args.num_to, args.verbose).items():
            if(video_status == "ready"):
                youtube.videos().delete(id=video_id).execute()

    current_id = insert_event(youtube, args.title, start_time, args.run_time, args.thumbnail, args.ward, args.num_from, args.num_to, args.verbose)

    if(current_id is None):
        print("Failed to insert new broadcast!")
    else:
        # modified the status-file cli parameter so that it doesn't have a default values here, so we can decide if we want this to update the status file or not at runtime
        if(args.status_file is not None):
            update_status.update("start", start_time, stop_time, args.status_file, args.ward, args.num_from, args.num_to, args.verbose)
        #make sure link on web host is current
        update_link.update_live_broadcast_link(current_id, args, args.html_filename, args.url_filename)
