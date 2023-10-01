#!/usr/bin/python3

# python script that will grab the latest YouTube Live ID (assumes no uploaded videos are present on channel)
# update a web server over SSH with a page containing a redirect to that Live ID
# then start sending an RTSP stream to the given YouTube key to start a Live Stream that should come up under that link
# the broadcast will run for the amount of time given (or and hour and ten minutes is the default) before waiting for the
# given delay (default is ten minutes) before deleting ALL videos from YouTube channel

import argparse
import signal
import os
import sys
import traceback
import faulthandler
import re
import pickle
import subprocess
import time
import threading
import json

from subprocess import check_output
import socket

from shlex import split
from datetime import datetime, timedelta
from dateutil import tz # pip install python-dateutil

import google_auth # google_auth.py local file
import update_link # update_link.py local file
import youtube_api as yt # youtube.py local file
import sms # sms.py local file
import send_email # send_email.py local file
import update_status # update_status.py local file
import insert_event # insert_event.py local file
import count_viewers # count_viewers.py local file
import presets # presets.py local file
import local_stream as ls # local_stream.py local file
import global_file as gf # local file for sharing globals between files

NUM_RETRIES = 5
EXTEND_TIME_INCREMENT = 5
MAX_PROCESS_POLL_FAILURE = 3
PROCESS_POLL_CLEAR_AFTER = 5

class GracefulKiller:
  kill_now = False
  def __init__(self):
    signal.signal(signal.SIGINT, self.exit_gracefully)
    signal.signal(signal.SIGTERM, self.exit_gracefully)

  def exit_gracefully(self,signum, frame):
    print('\n!!Received SIGINT or SIGTERM!!')
    self.kill_now = True

# this was the original Ctrl+C handler, this shoudln't be getting called anywhere now that GracefulKiller is being used
def signal_handler(sig, frame):
  print('\n!!Exiting from Ctrl+C or SIGTERM!!')
  sys.exit(0)

# sigfault happening and need to at least be alerted when application fails because of this so we can restart it
def signal_sigfault(sig, frame):
    print("!!SEGFAULT!!")
    faulthandler.dump_traceback(file=sys.stdout, all_threads=True)
    if(num_from is not None and num_to is not None):
        sms.send_sms(num_from, num_to, ward + " SEGFAULT occured!", verbose)
    sys.exit(0)

def check_extend(extend_file, stop_time, status_file, ward, num_from = None, num_to = None):
    global extend_time
    if os.path.exists(extend_file):
        extend_time += EXTEND_TIME_INCREMENT
        os.remove(extend_file)
        if(args.extend_max is None or extend_time <= args.extend_max):
            stop_time = stop_time + timedelta(minutes=EXTEND_TIME_INCREMENT)
            update_status.update("stop", None, stop_time, status_file, ward, num_from, num_to, verbose)
            print("extending broadcast time by " + str(EXTEND_TIME_INCREMENT) + " min.")
            print("stop_time extended to: " + stop_time.strftime("%d-%b-%Y %H:%M:%S"))
        else:
            print("broadcast time can't be extended")
    return(stop_time)

def verify_live_broadcast(youtube, ward, args, current_id, html_filename, url_filename, num_from = None, num_to = None, verbose = False):
     # want to make sure that the current live broadcast is the one we think it is 
    time.sleep(15) # it takes a few seconds for the video to go live, so wait before we start checking
    verify_broadcast = False
    start_verify = datetime.now()
    start_failure_sms_sent = False
    while(not verify_broadcast):
        live_id = yt.get_live_broadcast(youtube, ward, num_from, num_to, verbose)
        if(live_id is None):
            print("No live broadcast found")
            START_DELAY = 2
            if((datetime.now() - start_verify) > timedelta(minutes=START_DELAY)):
                if(not start_failure_sms_sent):
                    start_failure_sms_sent = True
                    print("Broadcast has failed to start within " + str(START_DELAY))
                    if(num_from is not None and num_to is not None):
                        sms.send_sms(num_from, num_to, ward + " broadcast has failed to start!", verbose)
        if(live_id is not None and live_id != current_id):
            print("Live ID doesnt't match current ID, updating link")
            print(current_id + " => " + live_id)
            update_link.update_live_broadcast_link(live_id, args, html_filename, url_filename)
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " link ID updated!", verbose)
            current_id = live_id
            gf.current_id = current_id # setting this will allow updating the current_id in other threads

        if(current_id == live_id):
            verify_broadcast = True
        time.sleep(5)

    print("Live broadcast ID has been verified.")

if __name__ == '__main__':
  try:
    parser = argparse.ArgumentParser(description='Broadcast Live Ward Meeting to YouTube')
    parser.add_argument('-c','--config-file',type=str,help='JSON Configuration file')
    parser.add_argument('-w','--ward',type=str,help='Name of Ward being broadcast')
    parser.add_argument('-i','--title',type=str,default='Live Stream',help='Broadcast Title')
    parser.add_argument('-f','--control-file',type=str,default='pause',help='Path and filename for file used to Delay/Pause video stream')
    parser.add_argument('-p','--pause-image',type=str,default='pause.jpg',help='Path and filename for the JPG image that will be shown when stream is paused')
    parser.add_argument('-x','--extend-file',type=str,default='extend',help='Path and filename for file used to Extend broadcast by 5 min.')
    parser.add_argument('-X','--extend-max',type=int,help='Maximum time broadcast can be extended in minutes')
    parser.add_argument('-S','--status-file',type=str,default='status',help='Path and fineame for file used to write out Start/Stop time status.')
    parser.add_argument('-n','--thumbnail',type=str,help='Path and filename for the JPG image that will be the video thumbnail')
    parser.add_argument('-o','--host-name',type=str,help='The address for the web host to upload HTML link forward page to')
    parser.add_argument('-u','--user-name',type=str,help='The username for the web host')
    parser.add_argument('-H','--home-dir',type=str,help='Home directory SSH id_rsa key is stored under')
    parser.add_argument('-U','--url-filename',type=str,help='Use this for web filename instead of Unit name.')
    parser.add_argument('-L','--html-filename',type=str,help='Override default upload path and filename, this must be full path and filename for target webserver')
    parser.add_argument('-k','--url-key',type=str,help='The 4-digit code at the end of the URL')
    parser.add_argument('-y','--youtube-key',type=str,help='YouTube Key')
    parser.add_argument('-a','--audio-delay',type=float,default=0.0,help='Audio Delay in Seconds (decimal)')
    parser.add_argument('-g','--audio-gain',type=int,help='Audio Gain in dB (integer)')
    parser.add_argument('-G','--audio-gate',type=int,help='Audio Gate post-gain, can not be used with Audio Gain')
    parser.add_argument('-R','--rtsp-stream',type=str,help='Use to specify an RTSP stream on the network to use instead of USB camera')
    parser.add_argument('-P','--use-ptz',default=False,action='store_true',help='Use PTZ function on camera to set start/end/pause positions of camera')
    parser.add_argument('-s','--start-time',type=str,help='Broadcast start time in HH:MM:SS')
    parser.add_argument('-t','--run-time',type=str,default='1:10:00',help='Broadcast run time in HH:MM:SS')
    parser.add_argument('-d','--delay-after',type=int,default=10,help='Number of min. after broadcast to wait before cleaning up videos.')
    parser.add_argument('-e','--email-from',type=str,help='Account to send email with/from')
    parser.add_argument('-E','--email-to',type=str,help='Account to send CSV fiel email to')
    parser.add_argument('-M','--dkim-private-key',type=str,help='Full path and filename of DKIM private key file')
    parser.add_argument('-m','--dkim-selector',type=str,help='DKIM Domain Selector')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-N','--extended',default=False,action='store_true',help='Include extended data with viewer counts')
    parser.add_argument('-D','--delete-control',type=int,help='Control delete options from command line, bit mapped. delete_current 1 : delete_ready 2 : delete_complete 4')
    parser.add_argument('-v','--verbose',default=False,action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    gf.killer = GracefulKiller()
    signal.signal(signal.SIGSEGV, signal_sigfault)

    delete_current = True # in keeping with guidence not to record sessions, delete the current session
    delete_ready = True # script will create a new broadcast endpoint after the delete process, an existing ready broadcasts will interfere since we're not creating seperate endpoints for each broadcast, so delete any ready broadcasts to prevent problems
    delete_complete = True # in most cases there shouldn't be any completed broadcasts (they should have gotten deleted at the end of the broadcast), however some units are uploading broadcasts that we may want to save so this could be switched to False

    verbose = args.verbose
    num_from = args.num_from
    num_to = args.num_to
    ward = args.ward

    if(args.delete_control is not None):
        if(args.delete_control & 0x01):
            print("disable delete current")
            delete_current = False
        if(args.delete_control & 0x02):
            print("disable delete ready")
            delete_ready = False
        if(args.delete_control & 0x04):
            print("disable delete complete")
            delete_complete = False

    extend_time = 0 # keep track of extend time so we can cap it if needed
    recurring = True # is this a recurring broadcast, then create a new broadcast for next week
    broadcast_day = None # for recurring broadcasts, what day of the week is the broadcast
    audio_only_image = None # image that gets used for audio only broadcast, this is a super low bandwidth broadcast option and is only avaialble if image is provided
    rtsp_stream_lowbandwidth = None # if available, a low bandwidth option for streaming
    source_usb_cam = None # allows for setting USB camera
    url_randomize = False # allows for some security to control access to the stream URL
    email_send = True # sets if emails will be sent out
    email_url_addresses = [] # email addresses to sent randomized URL to
    description = None
    bandwidth_file = None
    broadcast_watching_file = None
    broadcast_reset_file = None
    local_stream = None
    local_stream_output = None
    local_stream_control = None
    preset_file = None
    preset_status_file = None

    testing = True if os.path.exists(os.path.abspath(os.path.dirname(__file__)) + '/testing') else False

    if(args.config_file is not None):
        if("/" in args.config_file):
            config_file = args.config_file
        else:
            config_file =  os.path.exists(os.path.abspath(os.path.dirname(__file__)) + "/" + args.config_file)
    if(args.config_file is not None and os.path.exists(args.config_file)):
        with open(args.config_file, "r") as configFile:
            config = json.load(configFile)

            # check for keys in config file
            if 'testing' in config:
                testing |= config['testing']
            if 'broadcast_ward' in config:
                ward = config['broadcast_ward']
                # some scripts still get information passed via the args dictionary so we need to make sure it's up-to-date
                args.ward = ward
            if 'broadcast_title' in config:
                args.title = config['broadcast_title']
            if 'broadcast_title_card' in config:
                args.thumbnail = config['broadcast_title_card']
            if 'broadcast_pause_card' in config:
                args.pause_image = config['broadcast_pause_card']
            if 'broadcast_audio_card' in config:
                audio_only_image = config['broadcast_audio_card']
            if 'broadcast_recurring' in config:
                recurring = config['broadcast_recurring']
            if 'broadcast_day' in config:
                broadcast_day = config['broadcast_day']
            if 'broadcast_time' in config:
                args.start_time = config['broadcast_time']
            if 'broadcast_length' in config:
                args.run_time = config['broadcast_length']
            if 'broadcast_description' in config:
                description = config['broadcast_description']
            if 'local_stream' in config:
                local_stream = config['local_stream']
            if 'local_stream_output' in config:
                local_stream_output = config['local_stream_output']
            if 'preset_file' in config:
                preset_file = config['preset_file']
            if 'youtube_key' in config:
                args.youtube_key = config['youtube_key']
            if 'delete_time_delay' in config:
                args.delay_after = config['delete_time_delay']
            if 'delete_current' in config:
                delete_current = config['delete_current']
            if 'delete_completed' in config:
                delete_complete = config['delete_completed']
            if 'delete_ready' in config:
                delete_ready = config['delete_ready']
            if 'source_rtsp_stream' in config:
                args.rtsp_stream = config['source_rtsp_stream']
            if 'source_rtsp_lowbandwidth' in config:
                rtsp_stream_lowbandwidth = config['source_rtsp_lowbandwidth']
            if 'source_usb_cam' in config:
                source_usb_cam = config['source_usb_cam']
            if 'source_ptz_enable' in config:
                args.use_ptz = config['source_ptz_enable']
            if 'url_key' in config:
                args.url_key = config['url_key']
            if 'url_randomize' in config:
                url_randomize = config['url_randomize']
            if 'url_name' in config:
                args.url_filename = config['url_name']
            if 'url_ssh_host' in config:
                args.host_name = config['url_ssh_host']
            if 'url_ssh_username' in config:
                args.user_name = config['url_ssh_username']
            if 'url_ssh_key_dir' in config:
                args.home_dir = config['url_ssh_key_dir']
            if 'audio_delay' in config:
                args.audio_delay = config['audio_delay']
            if 'audio_gain' in config:
                args.audio_gain = config['audio_gain']
            if 'audio_gate' in config:
                args.audio_gate = config['audio_gate']
            if 'broadcast_status' in config:
                args.status_file = config['broadcast_status']
            if 'broadcast_pause_control' in config:
                args.control_file = config['broadcast_pause_control']
            if 'broadcast_bandwidth_control' in config:
                bandwidth_file = config['broadcast_bandwidth_control']
            if 'broadcast_watching_status' in config:
                broadcast_watching_file = config['broadcast_watching_status']
            if 'broadcast_reset_status' in config:
                broadcast_reset_file = config['broadcast_reset_status']
            if 'local_stream_control' in config:
                local_stream_control = config['local_stream_control']
            if 'preset_status_file' in config:
                preset_status_file = config['preset_status_file']
            if 'max_extend_minutes' in config:
                args.extend_max = config['max_extend_minutes']
            if 'max_extend_control' in config:
                args.extend_file = config['max_extend_control']
            if 'email_extended_data' in config:
                args.extended = config['email_extended_data']
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
            if 'email_url_addresses' in config:
                email_url_addresses = config['email_url_addresses']
            if 'notification_text_from' in config:
                num_from = config['notification_text_from']
                # some scripts still get information passed via the args dictionary so we need to make sure it's up-to-date
                args.num_from = num_from
            if 'notification_text_to' in config:
                num_to = config['notification_text_to']
                # some scripts still get information passed via the args dictionary so we need to make sure it's up-to-date
                args.num_to = num_to

    if(args.audio_gain is not None and args.audio_gate is not None):
        print("!!Audio Gain and Audio Gate are mutually exclusive!!")
        sys.exit("Audio Fain and Audio Gate are mutually exclusive")
    if(ward is None):
        print("!!Ward is required argument!!")
        sys.exit("Ward is required argument")
    if(args.youtube_key is None):
        print("!!YouTube Key is a required argument!!")
        sys.exit("YouTube Key is a required argument")

    if(testing):
        print("!!testing is active!!")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " testing is active!", verbose)

    start_time, stop_time = update_status.get_start_stop(args.start_time, args.run_time, None, ward, num_from, num_to, verbose)

    update_start_stop = False
    if(datetime.now() >= stop_time):
        print("Stop time is less than current time!")
        # most common cause for this error is trying to run broadcast for testing
        # we want this to go ahead and proceed so adjust timing to allow for testing
        update_start_stop = True

    H, M, S = args.run_time.split(':')
    # adding 1 minute to the duration to allow for some error in start time
    # so we're not tripping this by accident if a timer starts a little early
    if((stop_time - datetime.now()) > timedelta(hours=int(H), minutes=int(M) + 1,seconds=int(S))):
        print("Duration is longer than requested run time!")
        # most common cause for this error is trying to run the broadcast for testing
        # we want this to go ahead and proceed so adjust timing to allow for testing
        update_start_stop = True

    if(update_start_stop):
        start_time = datetime.now() #start time also needs to be update since YouTube will not create broadcasts in the past if a broadcast needs to be created
        stop_time = start_time + timedelta(hours=int(H), minutes=int(M),seconds=int(S))
        print("stop_time updated to: " + stop_time.strftime("%d-%b-%Y %H:%M:%S"))
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " stop time was less than start time! stop time update to: " + stop_time.strftime("%d-%b-%Y %H:%M:%S"), verbose)
    else:
        print("stop_time : " + stop_time.strftime("%d-%b-%Y %H:%M:%S"))

    if not os.path.exists(args.pause_image):
        if(num_from is not None): sms.send_sms(num_from, num_to, ward + " no pause image available!", verbose)
        print("Pause image not found, image is required for paused stream.")
        sys.exit("Pause image not found and is require for paused stream")

    credentials_file = ward.lower() + '.auth'
    
    audio_delay = ''
    video_delay = ''
    if(args.audio_delay < 0):
        video_delay = " -itsoffset " + str(abs(args.audio_delay))
    else:
        audio_delay = " -itsoffset " + str(abs(args.audio_delay))

    audio_parameters = ''
    if(args.audio_gain is not None):
        audio_parameters = ' -af "volume=' + str(args.audio_gain) + 'dB"'
    if(args.audio_gate is not None):
        audio_parameters = ' -filter_complex agate=makeup=' + str(args.audio_gate)

    camera_parameters = ' -f v4l2 -framerate 15 -video_size 1920x1080 -c:v h264 -i /dev/video2'
    camera_parameters_lbw = ' -f v4l2 -framerate 15 -video_size 854x480 -c:v h264 -i /dev/video2'
    if(args.rtsp_stream is not None): # we're going to use an RTSP stream instead of the USB camera
        camera_parameters = ' -c:v h264 -rtsp_transport tcp -i "rtsp://' + args.rtsp_stream + '" -vf fps=fps=15'
        camera_parameters_lbw = ' -c:v h264 -rtsp_transport tcp -i "rtsp://' + rtsp_stream_lowbandwidth + '" -vf fps=fps=15' if rtsp_stream_lowbandwidth is not None else None
        if(args.use_ptz):
            ip_pattern = re.compile('''((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)''')
            camera_ip = ip_pattern.search(args.rtsp_stream).group(0)

    # remove extend file when we first start so we don't accidently extend the broadcast at the begining
    if os.path.exists(args.extend_file):
        os.remove(args.extend_file)

    # check if ffmpeg is already running, which would signal that previous broadcast didn't end correctly, and bad things will happen
    for line in os.popen("ps aux | grep ffmpeg | grep -v grep"):
        fields = line.split()
        pid = fields[1]

        os.kill(int(pid), signal.SIGKILL)

        print("ffmpeg still running from previous broadcast with pid : " + pid + " killing process")
        if(num_from is not None): sms.send_sms(num_from, num_to, ward +  " Ward ffmpeg still running from previous broadcast with pid : " + pid + "! Killing process.", verbose)
 
    #authenticate with YouTube API
    exception = None
    for retry_num in range(NUM_RETRIES):
        exception = None
        try:
            youtube = google_auth.get_authenticated_service(credentials_file, args)
        except Exception as exc:
            exception = ecx
            if(verbose): print('!!YouTube Authentication Failure!!')
            gf.sleep(0.5, 2)
    if(exception):
        print(traceback.format_exc())
        print("YouTube authentication failure.")
        if(num_from is not None): sms.send_sms(num_from, num_to, ward +  " Ward YouTube authentication failure!", verbose)
        # we can't continue if not authenticated to YouTube so exit out
        sys.exit("Can't authenticate to YouTube")
  
    #get next closest broadcast endpoint from youtube (should only be one)
    current_id = yt.get_next_broadcast(youtube, ward, num_from, num_to, verbose)
    if(current_id is None):
        print("No broadcast found, attempting to create broadcast.")
        current_id = insert_event.insert_event(youtube, args.title, description, start_time, args.run_time, args.thumbnail, ward, num_from, num_to, verbose)
        if(current_id is None):
            print("Failed to get current broadcast, and broadcast creation also failed!")
            if(num_from is not None): sms.send_sms(num_from, num_to, ward + " Ward failed to get current broadcast, and broadcast creation also failed!", verbose)
            sys.exit("No current broadcast, and new broadcast creation failed")
    
    # if we have a broadcast status of created (which is what we should be getting here each time) then we need to bind the broadcast before we can use it
    if(yt.get_broadcast_status(youtube, current_id, ward, num_from, num_to, verbose == 'created')):
        insert_event.bind_event(youtube, current_id, ward, num_from, num_to, verbose)

    #make sure link on web host is current
    update_link.update_live_broadcast_link(current_id, args, args.html_filename, args.url_filename)

    #kick off broadcast
    ffmpeg = 'ffmpeg -thread_queue_size 2048' + audio_delay + ' -f alsa -guess_layout_max 0 -i default:CARD=Device -thread_queue_size 2048' + video_delay + camera_parameters + ' -c:v libx264 -profile:v high -pix_fmt yuv420p -preset superfast -g 7 -bf 2 -b:v 4096k -maxrate 4096k -bufsize 2048k -strict experimental -acodec libmp3lame -ar 44100 -threads 4 -crf 18 -b:a 128k -ac 1' + audio_parameters + ' -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key
    ffmpeg_lbw = 'ffmpeg -thread_queue_size 2048' + audio_delay + ' -f alsa -guess_layout_max 0 -i default:CARD=Device -thread_queue_size 2048' + video_delay + camera_parameters_lbw + ' -c:v libx264 -profile:v high -pix_fmt yuv420p -preset superfast -g 7 -bf 2 -b:v 1024k -maxrate 1024k -bufsize 2048k -strict experimental -acodec libmp3lame -ar 44100 -threads 4 -crf 18 -b:a 128k -ac 1' + audio_parameters + ' -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key if camera_parameters_lbw is not None else ffmpeg
    ffmpeg_audio = 'ffmpeg -thread_queue_size 2048' + audio_delay + ' -f alsa -guess_layout_max 0 -i default:CARD=Device -thread_queue_size 2048 -loop 1 -i ' + audio_only_image + ' -c:v libx264 -filter:v fps=fps=4 -g 7 -acodec libmp3lame -ar 44100 -threads 4 -crf 18 -b:a 128k -ac 1' + audio_parameters + ' -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key if audio_only_image is not None else ffmpeg
    ffmpeg_img = 'ffmpeg -thread_queue_size 2048 -f lavfi -i anullsrc -thread_queue_size 2048 -loop 1 -i ' + args.pause_image + ' -c:v libx264 -filter:v fps=fps=4 -g 7 -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key

    broadcast_stream = [ffmpeg, ffmpeg_lbw, ffmpeg_audio]
    broadcast_index = 0
    broadcast_index_new = 0
    broadcast_downgrade_delay = [2, 4, 4] # how long to we wait before step from one broadcast stream to the next?
    broadcast_status_length = datetime.now()
    broadcast_status_check = datetime.now()
    process_poll_failure = 0
    process_poll_clear = 0

    for ffmpeg_command in broadcast_stream:
        if(verbose): print("\nffmpeg command : " + ffmpeg_command + "\n")

    process = None
    streaming = False
    count_viewers = threading.Thread(target = count_viewers.count_viewers, args = (ward.lower() + '_viewers.csv', youtube, current_id, ward, num_from, num_to, verbose, args.extended, broadcast_watching_file))
    count_viewers.daemon = True #set this as a daemon thread so it will end when the script does (instead of keeping script open)
    count_viewers.start()
    print("Starting stream...")
    verify_broadcast = threading.Thread(target = verify_live_broadcast, args = (youtube, ward, args, current_id, args.html_filename, args.url_filename, num_from, num_to, verbose))
    verify_broadcast.daemon = True
    verify_broadcast.start()
    if(local_stream is not None and local_stream_output is not None and local_stream_control is not None):
        stream_local = threading.Thread(target = ls.local_stream_process, args = (ward, local_stream, local_stream_output, local_stream_control, num_from, num_to, verbose))
        stream_local.daemon = True
        stream_local.start()
    if(camera_ip is not None and preset_file is not None and preset_status_file is not None):
        preset_report = threading.Thread(target = presets.report_preset, args = (5, ward, camera_ip, preset_file, preset_status_file, num_from, num_to, verbose))
        preset_report.daemon = True
        preset_report.start()

    broadcast_start = datetime.now()
    stream_last = 0

    #if reset file exists clean it up here, we want to wait till we get this far so hopefully we've bypassed the issue that's causing the reset to be attempted
    try:
        if(broadcast_reset_file is not None and os.path.exists(broadcast_reset_file)):
            os.remove(broadcast_reset_file)
            print("Removed reset file")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " removed reset file.", verbose)
    except:
        if(verbose): print(traceback.format_exc())
        print("Failed cleaning up reset file")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed cleaning up reset file!", verbose)

    while(datetime.now() < stop_time and not gf.killer.kill_now):
        try:
            stream = 0 if os.path.exists(args.control_file) else 1 # stream = 1 means we should be broadcasting the camera feed, stream = 0 means we should be broadcasting the title card "pausing" the video
        except:
            if(verbose): print(traceback.format_exc())
            print("Failure reading control file")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " had a failure reading the control file!", verbose)

        if(stream == 1 and streaming == False):
          try:
            print("main stream")
            if(args.use_ptz and stream_last == 0):
                stream_last = stream
                try:
                    subprocess.run(["curl", "http://" + camera_ip + "/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) # point camera at pulpit before streaming
                except:
                    print("PTZ Problem")
                time.sleep(3) # wait for camera to get in position before streaming, hand count for this is about 3 seconds.
            streaming = True
            process = subprocess.Popen(split(broadcast_stream[broadcast_index]), shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            process_terminate = False
            # update status file with current start/stop times (there may be multiple wards in this file, so read/write out any that don't match current ward
            update_status.update("broadcast", start_time, stop_time, args.status_file, ward, num_from, num_to, verbose)
            while process.poll() is None:
                stop_time = check_extend(args.extend_file, stop_time, args.status_file, ward, num_from, num_to)
                if(os.path.exists(args.control_file) or datetime.now() > stop_time or gf.killer.kill_now):
                    process_terminate = True
                    process.terminate()
                    process.wait()
                    break;
                try:
                    if(bandwidth_file is not None and os.path.exists(bandwidth_file)):
                        with open(bandwidth_file, "r") as bandwidthFile:
                            broadcast_index_new = int(bandwidthFile.readline())
                            if(broadcast_index_new < len(broadcast_stream) and broadcast_index_new >= 0):
                                if(broadcast_index != broadcast_index_new):
                                    broadcast_index = broadcast_index_new
                                    print("Setting broadcast to index " + str(broadcast_index))
                                    process_terminate = True
                                    process.terminate()
                                    process.wait()
                                    break;
                            else:
                                raise Exception("Invalid broadcast index")
                except:
                    if(verbose): print(traceback.format_exc())
                    print("Failure reading bandwidth file")
                    if(num_from is not None and num_to is not None):
                        sms.send_sms(num_from, num_to, ward + " had a failure reading the bandwidth file!", verbose)
                # if we're already at the lowest bandwidth broadcast there's no reason to keep checking to see if we need to reduce bandwidth
                if(broadcast_index < (len(broadcast_stream) - 1) and (datetime.now() - broadcast_status_check) > timedelta(minutes=1)):
                    # every minute grab the broadcast status so we can reduce the bandwidth if there's problems
                    # if gf.current_id has been defined, then verify_life_broadcast detected a change in the video id we're using, so we need to update for that
                    if(gf.current_id and gf.current_id != current_id):
                        print("Live ID doesn't match Current ID, updating status")
                        print(current_id + " => " + gf.current_id)
                        current_id = gf.current_id
                    status, status_description = yt.get_broadcast_health(youtube, current_id, ward, num_from, num_to, verbose)
                    broadcast_status_check = datetime.now()
                    if(verbose): print(status)
                    if(verbose): print(status_description)
                    if(status == 'bad' and (datetime.now() - broadcast_status_length) > timedelta(minutes=broadcast_downgrade_delay[broadcast_index])):
                        broadcast_index += 1
                        # !!!! START DEBUG CODE !!!!
                        # if we're going to reduce the broadcast bandwidth, we want to collect some information so we can try to determine why the bandwidth needs to be reduced.
                        url1 = 'top1' + time.strftime("%Y%m%d-%H%M%S")  + '.txt'
                        url2 = 'top2' + time.strftime("%Y%m%d-%H%M%S")  + '.txt'
                        url3 = 'ps' + time.strftime("%Y%m%d-%H%M%S")  + '.txt'
                        top1 = check_output("top -b -n 1 -1 -c -H -w 512 > html/" + url1, shell=True)
                        top1 = check_output("top -b -n 1 -1 -c -w 512 > html/" + url2, shell=True)
                        ps = check_output("ps auxf > html/" + url3, shell=True)
                        temp = check_output(['vcgencmd', 'measure_temp']).decode('utf-8').split('=')[-1]
                        freq = check_output(['vcgencmd', 'measure_clock arm']).decode('utf-8').split('=')[-1]
                        throttled = check_output(['vcgencmd', 'get_throttled']).decode('utf-8').split('=')[-1]
                        send_text = 'Ward: ' + ward + ' temperature is: ' + temp + ' and CPU freq.: ' + freq + ' with throttled: ' + throttled + '\n\n'
                        try:
                            bandwidth = check_output(['speedtest']).decode('utf-8').splitlines(keepends=True)
                            for line in bandwidth:
                                if(':' in line and 'URL' not in line):
                                    send_text += line
                        except:
                            send_text += '\n\n !!! BANDWIDTH TEST FAILED !!!'

                        send_text += '\n http://' + socket.gethostname() + '.hos-conf.local/' + url1
                        send_text += '\n http://' + socket.gethostname() + '.hos-conf.local/' + url2
                        send_text += '\n http://' + socket.gethostname() + '.hos-conf.local/' + url3

                        if(num_from is not None and num_to is not None):
                            print(send_text)
                            sms.send_sms(num_from, num_to, send_text, verbose)
                        # !!!! END DEBUG CODE !!!!

                        if(broadcast_index >= len(broadcast_stream)): broadcast_index = len(broadcast_stream - 1)
                        print("Reducing broadcast bandwidth, switching to index " + str(broadcast_index))
                        try:
                            if(bandwidth_file is not None):
                                with open(bandwidth_file, "w") as bandwidthFile:
                                    bandwidthFile.write(str(broadcast_index))
                        except:
                            if(verbose): print(traceback.format_exc())
                            print("Failure writing bandwidth file")
                            if(num_from is not None and num_to is not None):
                                sms.send_sms(num_from, num_to, ward + " had a failure writing the bandwidth file!", verbose)
                        broadcast_status_length = datetime.now()
                        if(num_from is not None and num_to is not None):
                            sms.send_sms(num_from, num_to, ward + " reducing broadcast bandwidth to index " + str(broadcast_index) + "!", verbose)
                        process_terminate = True
                        process.terminate()
                        process.wait()
                        break;
                    if(status != 'bad'):
                        broadcast_status_length = datetime.now()
                time.sleep(1)
                # if something is using the camera after the sleep we'll
                # end up with process.poll() != None and we'll get stuck
                # in an endless loop here
                if(not process_terminate and process.poll() is not None):
                    process_poll_failure += 1
                    process_poll_clear = 0
                    if(process_poll_failure > MAX_PROCESS_POLL_FAILURE):
                        print("!!Main Stream Died, max failure reached, exiting!! (check camera)")
                        if(num_from is not None and num_to is not None):
                            sms.send_sms(num_from, num_to, ward + " main stream died, max failure reached, exiting! (check camera)", verbose)
                        sys.exit("Main Stream max failure reached")

                    print("!!Main Stream Died!! (" + str(process_poll_failure) + ")")
                    if(num_from is not None and num_to is not None):
                        sms.send_sms(num_from, num_to, ward + " main stream died! (" + str(process_poll_failure) + ")", verbose)
                else:
                    process_poll_clear += 1
                    if(process_poll_clear > PROCESS_POLL_CLEAR_AFTER):
                        if(verbose): print("clear process failure")
                        process_poll_failure = 0

            streaming = False
          except SystemExit as e:
              #if we threw a system exit something is wrong bail out
              sys.exit("Caught system exit")
          except:
            if(verbose): print(traceback.format_exc())
            streaming = False
            print("Live broadcast failure")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " had a live broadcast failure!", verbose)
        elif(stream == 0 and streaming == False):
          try:
            print("pause stream")
            if(args.use_ptz):
                stream_last = stream
                try:
                    subprocess.run(["curl", "http://" + camera_ip + "/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&250"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) # point camera at wall to signal streaming as paused
                except:
                    print("PTZ Problem")
            streaming = True
            process = subprocess.Popen(split(ffmpeg_img), shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            process_terminate = False
            # update status file with current start/stop times (there may be multiple wards in this file, so read/write out any that don't match current ward
            update_status.update("pause", start_time, stop_time, args.status_file, ward, num_from, num_to, verbose)
            while process.poll() is None:
                stop_time = check_extend(args.extend_file, stop_time, args.status_file, ward, num_from, num_to)
                if(not os.path.exists(args.control_file) or datetime.now() > stop_time or gf.killer.kill_now):
                    process_terminate = True
                    process.terminate()
                    process.wait()
                    break;
                time.sleep(1)
                # if something is using the camera after the sleep we'll
                # end up with process.poll() != None and we'll get stuck
                # in an endless loop here
                if(not process_terminate and process.poll() is not None):
                    process_poll_failure += 1
                    process_poll_clear = 0
                    if(process_poll_failure > MAX_PROCESS_POLL_FAILURE):
                        print("!!Pause Stream Died, max failure reached, exiting!!")
                        if(num_from is not None and num_to is not None):
                            sms.send_sms(num_from, num_to, ward + " main stream died, max failure reached, exiting!", verbose)
                        sys.exit("Pause Stream max failure reached")

                    print("!!Pause Stream Died!! (" + str(process_poll_failure) + ")")
                    if(num_from is not None and num_to is not None):
                        sms.send_sms(num_from, num_to, ward + " pause stream died! (" + str(process_poll_failure) + ")", verbose)
                else:
                    if(process_poll_clear > PROCESS_POLL_CLEAR_AFTER):
                        if(verbose): print("clear process failure")
                        process_poll_failure = 0

            streaming = False
          except SystemExit as e:
              #if we threw a system exit something is wrong bail out
              sys.exit("Caught system exit")
          except:
            if(verbose): print(traceback.format_exc())
            streaming = False
            print("Live broadcast failure pause")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " had a live broadcast failure while paused!", verbose)

        time.sleep(0.1)

    #moved these to before the ffmpeg test to give local stream time to shutdown
    #clean up control file so it's reset for next broadcast, do this twice in case somebody inadvertently hits pause after the broadcast ends
    try:
        if(args.control_file is not None and os.path.exists(args.control_file)):
            os.remove(args.control_file)
        if(local_stream_control is not None and os.path.exists(local_stream_control)):
            os.remove(local_stream_control)
    except:
        if(verbose): print(traceback.format_exc())
        print("Failed cleaning up control files")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed cleaning up control files!", verbose)

    # if gf.current_id has been defined, then verify_live_broadcast detected a change in the video id we're using, so we need to update for that
    if(gf.current_id and gf.current_id != current_id):
        print("Live ID doesn't match Current ID, updating cleanup")
        print(current_id + " => " + gf.current_id)
        current_id = gf.current_id

    # if we forced killed this broadcast there's a good chance we don't want
    # to move the camera to the off position  that will keep us from messing
    # up an ongoing broadcast if this broadcast is the one we're killing 
    # because there are multiples running only worry about this if it's
    # a force kill, not normal finished time
    if(args.use_ptz and not gf.killer.kill_now):
        subprocess.run(["curl", "http://" + camera_ip + "/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&250"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) # point camera at wall to signal streaming as stopped

    # stop the local_stream thread if it's running
    # move this to later in the script so camera as time to face the back wall
    # so the last image sent to the web page isn't the stand
    # we'll set the strem_event here after moving the camera to off, and then
    # set the terminate event after the camera has reached the off position
    gf.stream_event.set()

    print("Finished stream...")
    # if we're forcibly killing the stream we're doing something manually
    # leave the stream endpoint running on youtube in case we need to re-start
    # the broadcast so we can continue using the same endpoint, this means
    # we'll need to manually stop the broadcast on youtube studio
    if(not gf.killer.kill_now):
        print("Stopping YT broadcast...")
        yt.stop_broadcast(youtube, current_id, ward, num_from, num_to, verbose)

    # change status back to start so webpage isn't left thinking we're still broadcasting/paused
    update_status.update("start", start_time, stop_time, args.status_file, ward, num_from, num_to, verbose)

    if(not gf.killer.kill_now): # don't wait if we're trying to kill the process now
        # this time while we wait before deleting the video is to allow people
        # time to finish watching the stream if they started late because of this
        # we want to continue getting viewer updates, so don't email until time runs out
        print("video(s) deletion routine will run at {}".format((datetime.now() + timedelta(minutes=int(args.delay_after))).strftime("%d-%b-%Y %H:%M:%S")))
        # wait for X min before deleting video
        delete_time = datetime.now() + timedelta(minutes=args.delay_after)
        while(datetime.now() < delete_time and not gf.killer.kill_now):
            time.sleep(1)
    else:
        # keep hitting a crash here with the viewers script, adding a delay for this case to prevent possble race condition
        time.sleep(5)

    # in the event that the camera didn't read the off position terminate the 
    # local stream now
    gf.stream_event_terminate.set()

    # read config file so we can pull in changes for testing and delete
    # that may have happened while script was running
    testing |= True if os.path.exists(os.path.abspath(os.path.dirname(__file__)) + '/testing') else False
    with open(args.config_file, "r") as configFile:
        config = json.load(configFile)

        if 'testing' in config:
            testing |= config['testing']
        if 'delete_current' in config:
            delete_current = config['delete_current']
        if 'delete_completed' in config:
            delete_complete = config['delete_completed']
        if 'delete_ready' in config:
            delete_ready = config['delete_ready']
        if 'email_send' in config:
            email_send = config['email_send']

    # testing automation scripts can generate emails with invalid data
    # and a number of those email can be generated, look for a control
    # file and don't send emails if control file is present
    if(not testing and send_email):
        print("e-mail concurrent viewer file")
        if(args.email_from is not None and args.email_to is not None):
            send_email.send_viewer_file(ward.lower() + '_viewers.csv', args.email_from, args.email_to, ward, args.dkim_private_key, args.dkim_selector, num_from, num_to, verbose)

    try:
        # delete the recording we just finished
        # if forcibly killing process, don't delete video
        if(delete_current and not gf.killer.kill_now):
            youtube.videos().delete(id=current_id).execute()
            print("Delete current broadcast")

        # delete all completed videos in Live list
        # delete all ready videos as they will cause problems for the new broadcast we will insert at the end of the script
        broadcasts = yt.get_broadcasts(youtube, ward, num_from, num_to, verbose)
        if(broadcasts is not None and not gf.killer.kill_now):
            for video_id, video_status in broadcasts.items():
                if((delete_complete and video_status == "complete")
                    or (delete_ready and (video_status == "ready"))): # if the broadcast got created but not bound it will be in created instead of ready state, since an un-bound broadcast can't unexpectedly accept a stream we'll leave these
                    if(video_id != current_id): # if current_id is still in list, then we've skipped deleting it above, so don't delete now.
                        try:
                            if(video_status == "complete"):
                                print("Delete complete broadcast " + video_id)
                            if(video_status == "ready"):
                                print("Delete ready broadcast " + video_id)
                            youtube.videos().delete(id=video_id).execute()
                        except:
                            if(verbose): print(traceback.format_exc())
                            gf.log_exception(traceback.format_exc(), "failed to delete complete/ready broadcast(s)")
                            print("Failed to delete complete/ready broadcast " + video_id)
                            if(num_from is not None and num_to is not None):
                                sms.send_sms(num_from, num_to, ward + " failed to delete complete/ready broadcast " + video_id + "!", verbose)
    except:
        if(verbose): print(traceback.format_exc())
        gf.log_exception(traceback.format_exc(), "failed to delete broadcast")
        print("Failed to delete broadcast " + video_id)
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to delete broadcast " + video_id + "!", verbose)

    # create next weeks broadcast if recurring
    # don't create a new broadcast is forcibly killing process
    if(recurring and not gf.killer.kill_now):
        print("Create next weeks broadcast")
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
        update_link.update_live_broadcast_link(current_id, args, args.html_filename, args.url_filename)

    #clean up control file so it's reset for next broadcast, do this twice in case somebody inadvertently hits pause after the broadcast ends
    try:
        if(args.control_file is not None and os.path.exists(args.control_file)):
            os.remove(args.control_file)
        if(local_stream_control is not None and os.path.exists(local_stream_control)):
            os.remove(local_stream_control)
    except:
        if(verbose): print(traceback.format_exc())
        print("Failed cleaning up control files")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed cleaning up control files!", verbose)
    # set broadcast back to standard (write 0 to broadcast file) so next broadcast starts in high-bandwidth mode
    try:
        if(bandwidth_file is not None):
            with open(bandwidth_file, "w") as bandwidthFile:
                bandwidthFile.write("0")
    except:
        if(verbose): print(traceback.format_exc())
        print("Failure clearing bandwidth file")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " had a failure clearing the bandwidth file!", verbose)

    # check if ffmpeg is still running, which would signal that broadcast didn't end correctly, and bad things will happen
    ps_aux = check_output(['ps', 'aux']).decode('utf-8')
    if 'ffmpeg' in ps_aux:
        # ffmpeg is still running, we don't want to handle this here, so just send a notification
        print("ffmpeg still running from current broadcast.")
        if(num_from is not None): sms.send_sms(num_from, num_to, ward +  " Ward ffmpeg still running from current broadcast!", verbose)

    # leave terminal in a working state on exit but only if running from command line
    if sys.stdin and sys.stdin.isatty():
        os.system('stty sane')

  except:
    if(verbose): print(traceback.format_exc())
    gf.log_exception(traceback.format_exc(), "crashed out of broadcast.py")
    print("Crashed out of broadcast.py")
    if(num_from is not None and num_to is not None):
        sms.send_sms(num_from, num_to, ward + " crashed out of broadcast.py!", verbose)    
