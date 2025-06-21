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
import subprocess
import time
import threading
import json
import random

from subprocess import check_output
import socket

from shlex import split
from datetime import datetime, timedelta
from dateutil import tz # pip install python-dateutil
from mutagen.mp3 import MP3 # pip install mutagen

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
import delete_event # local file for deleting broadcast
import broadcast_thrd as bt # broadcast_thrd.py local file

import gspread # pip3 install gspread==4.0.0

NUM_RETRIES = 5

class GracefulKiller:
  kill_now = False
  def __init__(self):
    signal.signal(signal.SIGINT, self.exit_gracefully)
    signal.signal(signal.SIGTERM, self.exit_gracefully)
    signal.signal(signal.SIGHUP, self.exit_gracefully)

  def exit_gracefully(self,signum, frame):
    print("\n!!Received SIGNAL/{}!!".format(str(signum)))
    self.kill_now = True

# this was the original Ctrl+C handler, this shoudln't be getting called anywhere now that GracefulKiller is being used
def signal_handler(sig, frame):
  print('\n!!Exiting from Ctrl+C or SIGTERM!!')
  sys.exit(0)

# sigfault happening and seemed to be re-segfaulting trying to send SMS alert
# exit with a non-zero code and handle notification / restart outside script
def signal_segfault(sig, frame):
#    print("!!SEGFAULT!!")
#    faulthandler.dump_traceback(file=sys.stdout, all_threads=True)
#    if(num_from is not None and num_to is not None):
#        sms.send_sms(num_from, num_to, ward + " SEGFAULT occured!", verbose)
#    gf.killer.kill_now = True
#   os._exit(1) 
    sys.exit("Caught SegFault, exit and handle notification and restart from outside script")

def get_active_instances(service_template_prefix):
    try:
        output = subprocess.check_output(
            ["systemctl", "list-units", "--type=service", "--state=running", "--no-legend"],
            stderr=subprocess.DEVNULL,
            text=True
        )
        return [
            line.split()[0]
            for line in output.splitlines()
            if line.startswith(service_template_prefix)
        ]
    except subprocess.CalledProcessError:
        return []

def stop_instance(unit_name):
    try:
        print(f"Stopping: {unit_name}")
        subprocess.run(
            ["sudo", "systemctl", "stop", unit_name],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print(f"Stopped: {unit_name}")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, f"{ward} stopping existing broadcast {unit_name}", verbose)
    except subprocess.CalledProcessError as e:
        print(f"Failed to stop {unit_name}: {e.stderr.decode().strip()}")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, f"{ward} failed to stop {unit_name} : {e.stderr.decode().strip()}", verbose)

def stop_other_instances(service_template_prefix, exclude_instance=None):
    running = get_active_instances(service_template_prefix)
    for unit in running:
        if exclude_instance and unit == exclude_instance:
            continue
        stop_instance(unit)

def check_report_missed_sms(ward, num_from = None, num_to = None, verbose = None):
    if(gf.sms_missed > 0):
        print("There were (" + str(gf.sms_missed) + ") missed SMS messages.")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " broadcast had (" + str(gf.sms_missed) + ") missed SMS messages!", verbose)

def verify_live_broadcast(youtube, ward, args, current_id, html_filename, url_filename, num_from = None, num_to = None, verbose = False):
     # want to make sure that the current live broadcast is the one we think it is 
    time.sleep(15) # it takes a few seconds for the video to go live, so wait before we start checking
    verify_broadcast = False
    start_verify = datetime.now()
    start_failure_sms_sent = False
    while(not verify_broadcast and not gf.killer.kill_now):
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
            update_link.update_live_broadcast_link(live_id, args, ward, html_filename, url_filename)
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " link ID updated!", verbose)
            current_id = live_id
            gf.current_id = current_id # setting this will allow updating the current_id in other threads

        if(current_id == live_id):
            verify_broadcast = True
        time.sleep(5)

    print("Live broadcast ID has been verified.")

if __name__ == '__main__':
  verbose = False
  num_from = None
  num_to = None
  audio_record = False
  pause_music = None
  ward = "Undefined"

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

    verbose = args.verbose
    num_from = args.num_from
    num_to = args.num_to
    ward = args.ward

    gf.killer = GracefulKiller()
    signal.signal(signal.SIGSEGV, signal_segfault)

    if "INVOCATION_ID" in os.environ:
        print("Started by systemd.")
    else:
        print("Started manually or by another process.")

    # # I now believe this was added to set the gf.sleep function and was left in by accident, so commenting this out for now to see if it causes any problems
    # # belive this was added so that is the script is restarted by systemd we don't end up crashing and restarting instantly over and over again
    # for n in range(6):
        # print("sleep number {}".format(str(n)))
        # gf.sleep(1, 3)

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
    audio_record_control = None
    local_stream = None
    local_stream_output = None
    local_stream_control = None
    preset_file = None
    preset_status_file = None
    googleDoc = 'Broadcast Viewers' # I need to parameterize this at some point...

    testing = True if os.path.exists(os.path.abspath(os.path.dirname(__file__)) + '/testing') else False

    if(args.config_file is not None):
        if("/" in args.config_file):
            config_file = args.config_file
        else:
            config_file =  os.path.abspath(os.path.dirname(__file__)) + "/" + args.config_file
    if(config_file is not None and os.path.exists(config_file)):
        with open(config_file, "r") as configFile:
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
            if 'audio_record' in config:
                audio_record = config['audio_record']
            if 'audio_record_control' in config:
                audio_record_control = config['audio_record_control']
            if 'pause_music' in config:
                pause_music = config['pause_music']
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
        sys.exit("Audio Gain and Audio Gate are mutually exclusive")
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

    viewers_file = ward.lower() + '_viewers.csv'
    graph_file = ward.lower() + '_viewers.png'

    start_time, gf.stop_time = update_status.get_start_stop(args.start_time, args.run_time, None, ward, num_from, num_to, verbose)

    update_start_stop = False
    if(datetime.now() >= gf.stop_time):
        print("Stop time is less than current time!")
        # most common cause for this error is trying to run broadcast for testing
        # we want this to go ahead and proceed so adjust timing to allow for testing
        update_start_stop = True

    H, M, S = map(int, args.run_time.split(':'))
    # adding 1 minute to the duration to allow for some error in start time
    # so we're not tripping this by accident if a timer starts a little early
    if((gf.stop_time - datetime.now()) > timedelta(hours=int(H), minutes=int(M) + 1,seconds=int(S))):
        print("Duration is longer than requested run time!")
        # most common cause for this error is trying to run the broadcast for testing
        # we want this to go ahead and proceed so adjust timing to allow for testing
        update_start_stop = True

    if(update_start_stop):
        start_time = datetime.now() #start time also needs to be update since YouTube will not create broadcasts in the past if a broadcast needs to be created
        gf.stop_time = start_time + timedelta(hours=int(H), minutes=int(M),seconds=int(S))
        print("stop_time updated to: " + gf.stop_time.strftime("%d-%b-%Y %H:%M:%S"))
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " stop time was less than start time! stop time update to: " + gf.stop_time.strftime("%d-%b-%Y %H:%M:%S"), verbose)
    else:
        print("stop_time : " + gf.stop_time.strftime("%d-%b-%Y %H:%M:%S"))

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
            match = ip_pattern.search(args.rtsp_stream)
            if match:
                camera_ip = match.group(0)
            else:
                tb = traceback.format_exc()
                if(verbose): print(tb)
                gf.log_exception(tb, "failed getting camera IP address")
                print("Failed getting camera IP address")
                if(num_from is not None and num_to is not None):
                    sms.send_sms(num_from, num_to, ward + " failed getting camera IP address!", verbose)

    # remove extend file when we first start so we don't accidently extend the broadcast at the begining
    if os.path.exists(args.extend_file):
        os.remove(args.extend_file)

    # remove any audio recordings or partial recordings so they don't get concatinated into the final result if we do a recording
    mp3_base_path = os.path.abspath(os.path.dirname(__file__))
    mp3_base_filename = args.url_filename if args.url_filename is not None else ward
    if(verbose): print(f"MP3 Base Path : {mp3_base_path}")
    try:
        pattern = re.compile(rf"^{re.escape(mp3_base_filename)}_.*\.mp3$")
        remove_old_recordings = sorted(f for f in os.listdir(mp3_base_path) if f.endswith(".mp3") and pattern.match(f))
        if(len(remove_old_recordings) > 0):
            print("MP3 file(s) exists, removing")
            if(verbose): print(f"MP3 Files for removal: {remove_old_recordings}")
        for remove in remove_old_recordings:
            os.remove(remove)
    except:
        tb = traceback.format_exc()
        if(verbose): print(tb)
        gf.log_exception(tb, "failed removing existing MP3 files")
        print("Failed failed removing existing MP3 files")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed removing existing MP3 files!", verbose)

    try:
        current_instance = f"broadcast@{os.path.basename(config_file)}.service"
        instances = get_active_instances("broadcast@")
        if len(instances) > 1:
            print(f"Found running instances: {instances}")
            stop_other_instances("broadcast@", exclude_instance=current_instance)
    except:
        tb = traceback.format_exc()
        if(verbose): print(tb)
        gf.log_exception(tb, "failed checking for other active broadcasts")
        print("Failed checking for other active broadcasts")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed checking for other broadcasts!", verbose)

    #authenticate with YouTube API
    exception = None
    for retry_num in range(NUM_RETRIES):
        exception = None
        tb = None
        try:
            youtube = google_auth.get_authenticated_service(credentials_file, ward, num_from, num_to, 'youtube', 'v3', verbose)
        except Exception as exc:
            exception = exc
            tb = traceback.format_exc()
            if(verbose): print(f"!!YouTube Authentication Failure!! retry({retry_num + 1} of {NUM_RETRIES})")
            gf.sleep(0.5, 2)
    if(exception):
        print(tb)
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
    broadcast_status = yt.get_broadcast_status(youtube, current_id, ward, num_from, num_to, verbose)
    print(f"Broadcast status : {broadcast_status} for broadcast ID {current_id}")
    if(broadcast_status == 'created'):
        insert_event.bind_event(youtube, current_id, ward, num_from, num_to, verbose)

    #make sure link on web host is current
    update_link.update_live_broadcast_link(current_id, args, ward, args.html_filename, args.url_filename)

    # check if we have music defined to play during the pause
    pause_audio_cmd = "-f lavfi -i anullsrc"
    if(pause_music is not None):
        print("Pause music defined, create list")
        random.shuffle(pause_music)
        
        # get MP3 file durations
        durations = [(f, int(MP3(f).info.length)) for f in pause_music]
        if(verbose): print(f"Durations : {durations}")
        playlist = []
        H, M, S = map(int, args.run_time.split(':'))
        target_seconds = timedelta(hours=H, minutes=M, seconds=S).total_seconds() + (int(args.extend_max) * 60)
        if(verbose): print(f"Target Minutes : {str(target_seconds / 60)}")
        total_time = 0
        index = 0
        while total_time < target_seconds and durations:
            f, dur = durations[index % len(durations)]
            playlist.append(f)
            total_time += dur
            index += 1

        with open(mp3_base_path + "/mp3_play_list", "w") as f:
            f.writelines("file '" + os.path.join(mp3_base_path, fn.replace("'", "'\\''")) + "'\n" for fn in playlist)
        if(verbose): print(f"Pause music playlist : {playlist}")
        pause_audio_cmd = "-f concat -safe 0 -re -i mp3_play_list"

    #kick off broadcast
    ffmpeg = 'ffmpeg -thread_queue_size 2048' + audio_delay + ' -f alsa -guess_layout_max 0 -i default:CARD=Device -thread_queue_size 2048' + video_delay + camera_parameters + ' -c:v libx264 -profile:v high -pix_fmt yuv420p -preset superfast -g 7 -bf 2 -b:v 4096k -maxrate 4096k -bufsize 2048k -strict experimental -threads 4 -crf 18 -acodec aac -ar 44100 -b:a 128k -ac 1' + audio_parameters + ' -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key
    ffmpeg_lbw = 'ffmpeg -thread_queue_size 2048' + audio_delay + ' -f alsa -guess_layout_max 0 -i default:CARD=Device -thread_queue_size 2048' + video_delay + camera_parameters_lbw + ' -c:v libx264 -profile:v high -pix_fmt yuv420p -preset superfast -g 7 -bf 2 -b:v 1024k -maxrate 1024k -bufsize 2048k -strict experimental -threads 4 -crf 18 -acodec aac -ar 44100 -b:a 128k -ac 1' + audio_parameters + ' -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key if camera_parameters_lbw is not None else ffmpeg
    ffmpeg_audio = 'ffmpeg -thread_queue_size 2048' + audio_delay + ' -f alsa -guess_layout_max 0 -i default:CARD=Device -thread_queue_size 2048 -framerate 4 -loop 1 -i ' + audio_only_image + ' -r 4 -c:v libx264 -vf "fps=4, drawtext=font=calibri-bold:fontsize=12:fontcolor=#85200C:borderw=3:bordercolor=white:text=\\\'%{pts\:gmtime\:0\:%#M\\\\\:%S}\\\':x=(w-text_w)/2:y=185" -g 2 -threads 4 -crf 18 -acodec aac -ar 44100 -b:a 128k -ac 1' + audio_parameters + ' -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key if audio_only_image is not None else ffmpeg
    ffmpeg_img = 'ffmpeg -thread_queue_size 2048 ' + pause_audio_cmd + ' -thread_queue_size 2048 -framerate 4 -loop 1 -i ' + args.pause_image + ' -r 4 -c:v libx264 -vf "fps=4, drawtext=font=calibri-bold:fontsize=56:fontcolor=#85200C:borderw=3:bordercolor=white:text=\\\'%{pts\:gmtime\:0\:%#M\\\\\:%S}\\\':x=(w-text_w)/2:y=835" -g 2' + (' -acodec aac -ar 44100 -b:a 128k -ac 1 -shortest ' if pause_music is not None else ' ') + '-f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key

    broadcast_stream = [ffmpeg, ffmpeg_lbw, ffmpeg_audio]
    broadcast_downgrade_delay = [2, 4, 4] # how long to we wait before step from one broadcast stream to the next?

    for ffmpeg_command in broadcast_stream:
        if(verbose): print("\nffmpeg command : " + ffmpeg_command + "\n")
    if(verbose): print("\nffmpeg pause command : " + ffmpeg_img + "\n")

    count_viewers_thrd = threading.Thread(target = count_viewers.count_viewers, args = (credentials_file, viewers_file, graph_file, youtube, current_id, ward, num_from, num_to, verbose, args.extended, broadcast_watching_file, False, googleDoc), name="CountViewerThread")
    count_viewers_thrd.daemon = True #set this as a daemon thread so it will end when the script does (instead of keeping script open)
    count_viewers_thrd.start()
    print("Starting stream...")
    verify_broadcast = threading.Thread(target = verify_live_broadcast, args = (youtube, ward, args, current_id, args.html_filename, args.url_filename, num_from, num_to, verbose), name="VerifyLiveBroadcastThread")
    verify_broadcast.daemon = True
    verify_broadcast.start()
    if(local_stream is not None and local_stream_output is not None and local_stream_control is not None):
        stream_local = threading.Thread(target = ls.local_stream_process, args = (ward, local_stream, local_stream_output, local_stream_control, num_from, num_to, verbose), name="LocalStreamThread")
        stream_local.daemon = True
        stream_local.start()
    if(camera_ip is not None and preset_file is not None and preset_status_file is not None):
        preset_report = threading.Thread(target = presets.report_preset, args = (5, ward, camera_ip, preset_file, preset_status_file, num_from, num_to, verbose), name="PresetReportThread")
        preset_report.daemon = True
        preset_report.start()

    broadcast_start = datetime.now()

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

    broadcast_thrd = threading.Thread(target = bt.broadcast, args = (youtube, current_id, start_time, ward, camera_ip, broadcast_stream, broadcast_downgrade_delay, bandwidth_file, ffmpeg_img, audio_record, audio_record_control, num_from, num_to, args, verbose), name="BroadcastThread")
    broadcast_thrd.daemon = True #set this as a daemon thread so it will end when the script does (instead of keeping script open)
    broadcast_thrd.start()

    while(datetime.now() < gf.stop_time and not gf.killer.kill_now):
        try:
            if(not broadcast_thrd.is_alive()):
                print("**Broadcast Thread Died**")
                if(num_from is not None and num_to is not None):
                    sms.send_sms(num_from, num_to, ward + " broadcast thread died.", verbose)
                print("**Restarting Broadcast Thread**")
                broadcast_thrd = threading.Thread(target = bt.broadcast, args = (youtube, current_id, start_time, ward, camera_ip, broadcast_stream, broadcast_downgrade_delay, bandwidth_file, ffmpeg_img, audio_record, audio_record_control, num_from, num_to, args, verbose), name="BroadcastThread")
                broadcast_thrd.daemon = True #set this as a daemon thread so it will end when the script does (instead of keeping script open)
                broadcast_thrd.start()
        except:
            tb = traceback.format_exc()
            if(verbose): print(tb)
            gf.log_exception(tb, "failed restarting broadcast thread")
            print("**Failed restarting broadcast thread**")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " failed restarting broadcast thread!", verbose)
            time.sleep(15) # something has gone wrong here, we need to figure out how to fix this, in the meantime add some delay so we don't spam the error log

        try:
            if(not count_viewers_thrd.is_alive()):
                print("**Count Viewers Thread Died**")
                if(num_from is not None and num_to is not None):
                    sms.send_sms(num_from, num_to, ward + " count viewers thread died.", verbose)
                print("**Restarting Count Viewers Thread**")
                count_viewers_thrd = threading.Thread(target = count_viewers.count_viewers, args = (credentials_file, viewers_file, graph_file, youtube, current_id, ward, num_from, num_to, verbose, args.extended, broadcast_watching_file, True, googleDoc), name="CountViewerThread")
                count_viewers_thrd.daemon = True #set this as a daemon thread so it will end when the script does (instead of keeping script open)
                count_viewers_thrd.start()
        except:
            tb = traceback.format_exc()
            if(verbose): print(tb)
            gf.log_exception(tb, "failed restarting count viewer thread")
            print("**Failed restarting count viewer thread**")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " failed restarting count viewer thread!", verbose)
            time.sleep(15) # something has gone wrong here, we need to figure out how to fix this, in the meantime add some delay so we don't spam the error log

        time.sleep(4.1)

    try:
        #if we're force killing this, lets take a look at where the broadcast and count viewers threads are (because we've been having issues with these getting stuck)
        if(gf.killer.kill_now):
            frames = sys._current_frames()
            for thread in threading.enumerate():
                if thread.name != "MainThread":
                    frame = frames.get(thread.ident)
                    if frame:
                        print(f"Thread '{thread.name}' is at:")
                        traceback.print_stack(frame)
    except:
        tb = traceback.format_exc()
        if(verbose): print(tb)
        gf.log_exception(tb, "failed printing current thread location")
        print("Failed printing current thread location")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed printing current thread location!", verbose)

    #moved these to before the ffmpeg test to give local stream time to shutdown
    #clean up control file so it's reset for next broadcast, do this twice in case somebody inadvertently hits pause after the broadcast ends
    try:
        if(args.control_file is not None and os.path.exists(args.control_file)):
            os.remove(args.control_file)
        if(local_stream_control is not None and os.path.exists(local_stream_control)):
            os.remove(local_stream_control)
        if(audio_record_control is not None and os.path.exists(audio_record_control)):
            os.remove(audio_record_control)
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
    update_status.update("start", start_time, gf.stop_time, args.status_file, ward, num_from, num_to, verbose)

    if(not gf.killer.kill_now): # don't wait if we're trying to kill the process now
        # this time while we wait before deleting the video is to allow people
        # time to finish watching the stream if they started late because of this
        # we want to continue getting viewer updates, so don't email until time runs out
        run_deletion_time = datetime.now() + timedelta(minutes=int(args.delay_after))
        print("video(s) deletion routine will run at {}".format(run_deletion_time.strftime("%H:%M %Y-%m-%d")))
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
        if 'email_send' in config:
            email_send = config['email_send']

    uploaded_url = None
    if(gf.audio_recorded):

        #authenticate with Google Drive API
        exception = None
        for retry_num in range(NUM_RETRIES):
            exception = None
            tb = None
            try:
                google_drive = google_auth.get_authenticated_service(credentials_file, ward, num_from, num_to, 'drive', 'v3', verbose)
            except Exception as exc:
                exception = exc
                tb = traceback.format_exc()
                if(verbose): print(f"!!Google Drive Authentication Failure!! retry({retry_num + 1} of {NUM_RETRIES})")
                gf.sleep(0.5, 2)
        if(exception):
            print(tb)
            print("Google Drive authentication failure.")
            if(num_from is not None): sms.send_sms(num_from, num_to, ward +  " Ward Google Drive authentication failure!", verbose)
        else:
            try:
                print("Concatenating MP3 files")

                pattern = re.compile(rf"^{re.escape(mp3_base_filename)}_\d{{4}}-\d{{2}}-\d{{2}}_\d{{2}}-\d{{2}}-\d{{2}}\.mp3$")
                mp3_files = sorted(f for f in os.listdir(mp3_base_path) if f.endswith(".mp3") and pattern.match(f))
                if(verbose): print(mp3_files)

                with open(mp3_base_path + "/mp3_file_list", "w") as f:
                    for mp3 in mp3_files:
                        f.write(f"file '{os.path.join(mp3_base_path, mp3)}'\n")

                outputfile = f"{mp3_base_filename}_{datetime.now().strftime('%Y-%m-%d')}.mp3"

                subprocess.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", mp3_base_path + "/mp3_file_list", "-c", "copy", outputfile],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE)

                if(os.path.exists(outputfile)):
                    uploaded_url = yt.upload_to_drive(google_drive, ward, outputfile, num_from, num_to, verbose)

                    print("Cleaning up temporary MP3 files")
                    for mp3 in mp3_files:
                        os.remove(os.path.join(mp3_base_path, mp3))
                    os.remove(mp3_base_path + "/mp3_file_list")
                else:
                    print(f"No MP3 Files Concatinated")
                    if(num_from is not None and num_to is not None):
                        sms.send_sms(num_from, num_to, ward + " no MP3 files concatinated!", verbose)
            except:
                tb = traceback.format_exc()
                if(verbose): print(tb)
                gf.log_exception(tb, "failed concatenating MP3 files")
                print("Failed concatenating MP3 files")
                if(num_from is not None and num_to is not None):
                    sms.send_sms(num_from, num_to, ward + " failed concatenating MP3 files!", verbose)

    # testing automation scripts can generate emails with invalid data
    # and a number of those email can be generated, look for a control
    # file and don't send emails if control file is present
    numViewers = yt.get_view_count(youtube, current_id, ward, num_from, num_to, verbose)
    if(not testing and email_send):
        print("e-mail concurrent viewer file")
        if(args.email_from is not None and args.email_to is not None):
            count_viewers.write_viewer_image(viewers_file, graph_file, ward, num_from, num_to, verbose)
            send_email.send_viewer_file(viewers_file, graph_file, args.email_from, args.email_to, ward, numViewers, datetime.now(), uploaded_url, args.dkim_private_key, args.dkim_selector, num_from, num_to, verbose)

    if(googleDoc is not None):
        sheet, column, insert_row = yt.get_sheet_row_and_column(credentials_file, googleDoc, current_id, ward, num_from, num_to, verbose)
        sheet.update_cell(gf.GD_VIEWS_ROW,column, "Views = " + str(numViewers))

    # schedule video deletion task
    # don't setup deletion if forcibly killing process
    if(not gf.killer.kill_now):
        delete_event.setup_event_deletion(current_id, numViewers, email_send, recurring, run_deletion_time, args)

    #clean up control file so it's reset for next broadcast, do this twice in case somebody inadvertently hits pause after the broadcast ends
    try:
        if(args.control_file is not None and os.path.exists(args.control_file)):
            os.remove(args.control_file)
        if(local_stream_control is not None and os.path.exists(local_stream_control)):
            os.remove(local_stream_control)
        if(audio_record_control is not None and os.path.exists(audio_record_control)):
            os.remove(audio_record_control)
        if(os.path.exists("mp3_play_list")):
            os.remove("mp3_play_list")
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
        print(f"ffmpeg still running from current broadcast {ward} ward.")
        if(num_from is not None): sms.send_sms(num_from, num_to, ward +  " Ward ffmpeg still running from current broadcast!", verbose)

    check_report_missed_sms(ward, num_from, num_to, verbose)

    # leave terminal in a working state on exit but only if running from command line
    if sys.stdin and sys.stdin.isatty():
        os.system('stty sane')

  #except SystemExit:

  except:
    if(verbose): print(traceback.format_exc())
    gf.log_exception(traceback.format_exc(), f"crashed out of broadcast.py : {ward} ward")
    print(f"Crashed out of broadcast.py : {ward} ward")
    check_report_missed_sms(ward, num_from, num_to, verbose)
    if(num_from is not None and num_to is not None):
        sms.send_sms(num_from, num_to, ward + " crashed out of broadcast.py!", verbose)    
