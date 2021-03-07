#!/usr/bin/python3

# python script that will grab the latest YouTube Live ID (assumes no uploaded videos are present on channel)
# update a web server over SSH with a page containing a redirect to that Live ID
# then start sending an RTSP stream to the given YouTube key to start a Live Stream that should come up under that link
# the broadcast will run for the amount of time given (or and hour and ten minutes is the default) before waiting for the
# given delay (default is ten minutes) before deleting ALL videos from YouTube channel

import argparse
import os
import traceback
import re
import pickle
import subprocess
import time
import threading

from shlex import split
from datetime import datetime, timedelta
from dateutil import tz # pip install python-dateutil

import google_auth # google_auth.py local file
import update_link # update_link.py local file
import youtube_api as yt # youtube.py local file
import sms # sms.py local file
import send_email # send_email.py localfile
import update_status # update_status.py localfile
import insert_event # insert_event.py localfile
import count_viewers # count_viewers.py localfile

def check_extend(extend_file, stop_time, status_file, ward, num_from = None, num_to = None):
    global extend_time
    if os.path.exists(extend_file):
        extend_time += 5
        os.remove(extend_file)
        if(args.extend_max is None or extend_time <= args.extend_max):
            stop_time = stop_time + timedelta(minutes=5)
            update_status.update("stop", None, stop_time, status_file, ward, num_from, num_to, args.verbose)
    return(stop_time)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Broadcast Live Ward Meeting to YouTube')
    parser.add_argument('-w','--ward',type=str,required=True,help='Name of Ward being broadcast')
    parser.add_argument('-i','--title',type=str,default='Live Stream',help='Broadcast Title')
    parser.add_argument('-f','--control-file',type=str,default='pause',help='Path and filename for file used to Delay/Pause video stream')
    parser.add_argument('-p','--pause-image',type=str,default='pause.jpg',help='Path and filename for the JPG image that will be shown when stream is paused')
    parser.add_argument('-x','--extend-file',type=str,default='extend',help='Path and filename for file used to Extend broadcast by 5 min.')
    parser.add_argument('-X','--extend-max',type=int,help='Maximum time broadcast can be extended in minutes')
    parser.add_argument('-S','--status-file',type=str,default='status',help='Path and fineame for file used to write out Start/Stop time status.')
    parser.add_argument('-n','--thumbnail',type=str,help='Path and filename for the JPG image that will be the video thumbnail')
    parser.add_argument('-o','--host-name',type=str,help='The address for the web host to upload HTML link forward page to')
    parser.add_argument('-u','--user-name',type=str,help='The username for the web host')
    parser.add_argument('-D','--home-dir',type=str,help='Home directory SSH id_rsa key is stored under')
    parser.add_argument('-U','--url-filename',type=str,help='Use this for web filename instead of Unit name.')
    parser.add_argument('-K','--html-filename',type=str,help='Override default upload path and filename, this must be full path and filename for target webserver')
    parser.add_argument('-k','--url-key',type=str,help='The 4-digit code at the end of the URL')
    parser.add_argument('-y','--youtube-key',type=str,required=True,help='YouTube Key')
    parser.add_argument('-a','--audio-delay',type=float,default=0.0,help='Audio Delay in Seconds (decimal)')
    parser.add_argument('-g','--audio-gain',type=int,default=0,help='Audio Gain in dB (integer)')
    parser.add_argument('-s','--start-time',type=str,help='Broadcast start time in HH:MM:SS')
    parser.add_argument('-t','--run-time',type=str,default='1:10:00',help='Broadcast run time in HH:MM:SS')
    parser.add_argument('-d','--delay-after',type=int,default=10,help='Number of min. after broadcast to wait before cleaning up videos.')
    parser.add_argument('-e','--email-from',type=str,help='Account to send email with/from')
    parser.add_argument('-E','--email-to',type=str,help='Accoun tto send CSV fiel email to')
    parser.add_argument('-M','--dkim-private-key',type=str,help='Full path and filename of DKIM private key file')
    parser.add_argument('-m','--dkim-selector',type=str,help='DKIM Domain Selector')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    delete_current = True # in keeping with guidence not to record sessions, delete the current session
    delete_ready = True # script will create a new broadcast endpoint after the delete process, and existing ready broadcasts will interfere since we're not creating seperate endpoints for each broadcast, so delete any ready broadcasts to prevent problems
    delete_complete = True # in most cases there shouldn't be any completed broadcasts (they should have gotten deleted at the end of the broadcast), however some units are uploading broadcasts that we may want to save so this could be switched to False

    extend_time = 0 # keep track of extend time so we can cap it if needed

    start_time, stop_time = update_status.get_start_stop(args.start_time, args.run_time, None, args.ward, args.num_from, args.num_to, args.verbose)

    if not os.path.exists(args.pause_image):
        if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " no pause image available!", args.verbose)
        print("Pause image not found, image is required for paused stream.")
        exit()

    credentials_file = args.ward.lower() + '.auth'
    
    audio_delay = ''
    video_delay = ''
    if(args.audio_delay < 0):
        video_delay = " -itsoffset " + str(abs(args.audio_delay))
    else:
        audio_delay = " -itsoffset " + str(abs(args.audio_delay))

    # remove extend file when we first start so we don't accidently extend the broadcast at the begining
    if os.path.exists(args.extend_file):
        os.remove(args.extend_file)
 
    #authenticate with YouTube API
    youtube = google_auth.get_authenticated_service(credentials_file, args)
  
    #get next closest broadcast endpoint from youtube (should only be one)
    current_id = yt.get_next_broadcast(youtube, args.ward, args.num_from, args.num_to, args.verbose)
    if(current_id is None):
        print("No broadcast found, attempting to create broadcast.")
        current_id = insert_event.insert_event(youtube, args.title, start_time, args.run_time, args.thumbnail, args.ward, args.num_from, args.num_to, args.verbose)
        if(current_id is None):
            print("Failed to get current broadcast, and broadcast creation also failed!")
            if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " Ward failed to get current broadcast, and broadcast creation also failed!", args.verbose)
            exit()

    #make sure link on web host is current
    update_link.update_live_broadcast_link(current_id, args, args.html_filename, args.url_filename)

    #kick off broadcast
    ffmpeg = 'ffmpeg -thread_queue_size 2048' + audio_delay + ' -f alsa -guess_layout_max 0 -i default:CARD=Device -thread_queue_size 2048' + video_delay + ' -f v4l2 -framerate 15 -video_size 1920x1080 -c:v h264 -i /dev/video2 -c:v libx264 -pix_fmt yuv420p -preset superfast -g 25 -b:v 3000k -maxrate 3000k -bufsize 1500k -strict experimental -acodec libmp3lame -ar 44100 -threads 4 -q:v 5 -q:a 5 -b:a 64k -af pan="mono: c0=FL" -ac 1 -filter:a "volume=' + str(args.audio_gain) + 'dB" -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key
    ffmpeg_img = 'ffmpeg -f lavfi -i anullsrc -loop 1 -i ' + args.pause_image + ' -c:v libx264 -filter:v fps=fps=4 -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key
#    process = subprocess.run(ffmpeg, shell=True, capture_output=True)
#    if(process.returncode != 0):
#        if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " Ward YouTube process exited with error!")
#        exit()
    process = None
    streaming = False
    count_viewers = threading.Thread(target = count_viewers.count_viewers, args = (args.ward.lower() + '_viewers.csv', youtube, current_id, args.ward, args.num_from, args.num_to, args.verbose))
    count_viewers.daemon = True #set this as a daemon thread so it will end when the script does (instead of keeping script open)
    count_viewers.start()
    if(datetime.now() >= stop_time):
        print("Stop time is less than start time!")
        if(args.num_from is not None and args.num_to is not None):
            sms.send_sms(args.num_from, args.num_to, args.ward + " stop time is less than start time!", args.verbose)
    print("Starting stream...")
    while(datetime.now() < stop_time):
        try:
            stream = 0 if os.path.exists(args.control_file) else 1 # stream = 1 means we should be broadcasting the camera feed, stream = 0 means we should be broadcasting the title card "pausing" the video
        except:
            if(args.verbose): print(traceback.format_exc())
            print("Failure reading control file")
            if(args.num_from is not None and args.num_to is not None):
                sms.send_sms(args.num_from, args.num_to, args.ward + " had a failure reading the control file!", args.verbose)

        if(stream == 1 and streaming == False):
          try:
            print("main stream")
            streaming = True
            process = subprocess.Popen(split(ffmpeg), shell=False, stderr=subprocess.DEVNULL)
            # update status file with current start/stop times (there may be multiple wards in this file, so read/write out any that don't match current ward
            update_status.update("broadcast", start_time, stop_time, args.status_file, args.ward, args.num_from, args.num_to, args.verbose)
            while process.poll() is None:
                stop_time = check_extend(args.extend_file, stop_time, args.status_file, args.ward, args.num_from, args.num_to)
                if os.path.exists(args.control_file) or datetime.now() > stop_time:
                    process.terminate()
                    process.wait()
                    break;
                time.sleep(1)
            streaming = False
          except:
            if(args.verbose): print(traceback.format_exc())
            print("Live broadcast failure")
            if(args.num_from is not None and args.num_to is not None):
                sms.send_sms(args.num_from, args.num_to, args.ward + " had a live broadcast failure!", args.verbose)
        elif(stream == 0 and streaming == False):
          try:
            print("pause stream")
            streaming = True
            process = subprocess.Popen(split(ffmpeg_img), shell=False, stderr=subprocess.DEVNULL)
            # update status file with current start/stop times (there may be multiple wards in this file, so read/write out any that don't match current ward
            update_status.update("pause", start_time, stop_time, args.status_file, args.ward, args.num_from, args.num_to, args.verbose)
            while process.poll() is None:
                stop_time = check_extend(args.extend_file, stop_time, args.status_file, args.ward, args.num_from, args.num_to)
                if not os.path.exists(args.control_file) or datetime.now() > stop_time:
                    process.terminate()
                    process.wait()
                    break;
                time.sleep(1)
            streaming = False
          except:
            if(args.verbose): print(traceback.format_exc())
            print("Live broadcast failure pause")
            if(args.num_from is not None and args.num_to is not None):
                sms.send_sms(args.num_from, args.num_to, args.ward + " had a live broadcast failure while paused!", args.verbose)

        time.sleep(0.1)

    print("Finished stream...")

    yt.stop_broadcast(youtube, current_id, args.ward, args.num_from, args.num_to, args.verbose)
    # change status back to start so webpage isn't left thinking we're still broadcasting/paused
    update_status.update("start", start_time, stop_time, args.status_file, args.ward, args.num_from, args.num_to, args.verbose)

    #clean up control file so it's reset for next broadcast, do this twice in case somebody inadvertently hits pause after the broadcast ends
    if os.path.exists(args.control_file):
        os.remove(args.control_file)

    time.sleep(args.delay_after * 60) # wait for X min before deleting video

    print("e-mail concurrent viewer file")
    if(args.email_from is not None and args.email_to is not None):
        send_email.send_viewer_file(args.ward.lower() + '_viewers.csv', args.email_from, args.email_to, args.ward, args.dkim_private_key, args.dkim_selector, args.num_from, args.num_to, args.verbose)

    print("Delete broadcast(s)")
    try:
        # delete the recording we just finished
        if(delete_current): youtube.videos().delete(id=current_id).execute()

        # delete all completed videos in Live list
        # delete all ready videos as they will cause problems for the new broadcast we will insert at the end of the script
        for video_id, video_status in yt.get_broadcasts(youtube, args.ward, args.num_from, args.num_to, args.verbose).items():
            if((delete_complete and video_status == "complete")
                or (delete_ready and video_status == "ready")):
                youtube.videos().delete(id=video_id).execute()
    except:
        if(args.verbose): print(traceback.format_exc())
        print("Failed to delete broadcasts")
        if(args.num_from is not None and args.num_to is not None):
            sms.send_sms(args.num_from, args.num_to, args.ward + " failed to delete broadcasts!", args.verbose)

    print("Create next weeks broadcast")
    # create a broadcast endpoint for next weeks video
    start_time, stop_time = update_status.get_start_stop(datetime.strftime(start_time, '%H:%M:%S'), args.run_time, datetime.strftime(start_time + timedelta(days=7), '%m/%d/%y'), args.ward, args.num_from, args.num_to, args.verbose)
    current_id = insert_event.insert_event(youtube, args.title, start_time, args.run_time, args.thumbnail, args.ward, args.num_from, args.num_to, args.verbose)

 # update status file with next start/stop times (there may be multiple wards in this file, so read/write out any that don't match current ward
    update_status.update("start", start_time, stop_time, args.status_file, args.ward, args.num_from, args.num_to, args.verbose)

    if(current_id is None):
        print("Failed to create new broadcast for next week")
        if(args.num_from is not None and args.num_to is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " failed to create broadcast for next week!", args.verbose)

    # make sure link on web host is current
    update_link.update_live_broadcast_link(current_id, args, args.html_filename, args.url_filename)

    #clean up control file so it's reset for next broadcast, do this twice in case somebody inadvertently hits pause after the broadcast ends
    if os.path.exists(args.control_file):
        os.remove(args.control_file)

