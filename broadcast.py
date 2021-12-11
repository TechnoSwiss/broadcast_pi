#!/usr/bin/python3

# python script that will grab the latest YouTube Live ID (assumes no uploaded videos are present on channel)
# update a web server over SSH with a page containing a redirect to that Live ID
# then start sending an RTSP stream to the given YouTube key to start a Live Stream that should come up under that link
# the broadcast will run for the amount of time given (or and hour and ten minutes is the default) before waiting for the
# given delay (default is ten minutes) before deleting ALL videos from YouTube channel

import argparse
import signal
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

VARIABLES_LOCAL = 'variables.local'

class GracefulKiller:
  kill_now = False
  def __init__(self):
    signal.signal(signal.SIGINT, self.exit_gracefully)
    signal.signal(signal.SIGTERM, self.exit_gracefully)

  def exit_gracefully(self,signum, frame):
    print('\n!!Received SIGINT or SIGTERM!!')
    self.kill_now = True

def signal_handler(sig, frame):
  print('\n!!Exiting from Ctrl+C or SIGTERM!!')
  sys.exit(0)


def check_extend(extend_file, stop_time, status_file, ward, num_from = None, num_to = None):
    global extend_time
    if os.path.exists(extend_file):
        extend_time += 5
        os.remove(extend_file)
        if(args.extend_max is None or extend_time <= args.extend_max):
            stop_time = stop_time + timedelta(minutes=5)
            update_status.update("stop", None, stop_time, status_file, ward, num_from, num_to, args.verbose)
    return(stop_time)

def verify_live_broadcast(youtube, ward, args, current_id, html_filename, url_filename, num_from = None, num_to = None, verbose = False):
     # want to make sure that the current live broadcast is the one we think it is 
    time.sleep(15) # it takes a few seconds for the video to go live, so wait before we start checking
    verify_broadcast = False
    while(not verify_broadcast):
        live_id = yt.get_live_broadcast(youtube, ward, num_from, num_to, verbose)
        if(live_id is None):
            print("No live broadcast found")
        if(live_id is not None and live_id != current_id):
            print("Live ID doesnt't match current ID, updating link")
            update_link.update_live_broadcast_link(live_id, args, html_filename, url_filename)
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " link ID updated!", verbose)
            current_id = live_id
        if(current_id == live_id):
            verify_broadcast = True
        time.sleep(5)

    print("Live broadcast ID has been verified.")

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
    parser.add_argument('-H','--home-dir',type=str,help='Home directory SSH id_rsa key is stored under')
    parser.add_argument('-U','--url-filename',type=str,help='Use this for web filename instead of Unit name.')
    parser.add_argument('-L','--html-filename',type=str,help='Override default upload path and filename, this must be full path and filename for target webserver')
    parser.add_argument('-k','--url-key',type=str,help='The 4-digit code at the end of the URL')
    parser.add_argument('-y','--youtube-key',type=str,required=True,help='YouTube Key')
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

    if(args.audio_gain is not None and args.audio_gate is not None):
        print("!!Audio Gain and Audio Gate are mutually exclusive!!")
        exit()

    killer = GracefulKiller()

    delete_current = True # in keeping with guidence not to record sessions, delete the current session
    delete_ready = True # script will create a new broadcast endpoint after the delete process, an existing ready broadcasts will interfere since we're not creating seperate endpoints for each broadcast, so delete any ready broadcasts to prevent problems
    delete_complete = True # in most cases there shouldn't be any completed broadcasts (they should have gotten deleted at the end of the broadcast), however some units are uploading broadcasts that we may want to save so this could be switched to False

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

    testing = True if os.path.exists(os.path.abspath(os.path.dirname(__file__)) + '/testing') else False
    if(testing):
        print("!!testing is active!!")
        if(args.num_from is not None and args.num_to is not None):
            sms.send_sms(args.num_from, args.num_to, args.ward + " testing is active!", args.verbose)
    start_time, stop_time = update_status.get_start_stop(args.start_time, args.run_time, None, args.ward, args.num_from, args.num_to, args.verbose)

    description = None
    if os.path.exists(VARIABLES_LOCAL):
        try:
            with open(VARIABLES_LOCAL, 'r') as f:
                description = f.readline().replace('\n', '')
                print(description)
        except:
            print("Failure reading local variables, defaults will be used.")
            if(args.num_from is not None and args.num_to is not None):
                sms.send_sms(args.num_from, args.num_to, args.ward + " failure reading local variables, defaults will be used!", args.verbose)
    else:
        print("No local variables file, defaults will be used.")

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
        if(args.num_from is not None and args.num_to is not None):
            sms.send_sms(args.num_from, args.num_to, args.ward + " stop time was less than start time! stop time update to: " + stop_time.strftime("%d-%b-%Y %H:%M:%S"), args.verbose)
    else:
        print("stop_time : " + stop_time.strftime("%d-%b-%Y %H:%M:%S"))

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

    audio_parameters = ''
    if(args.audio_gain is not None):
        audio_parameters = ' -af "volume=' + str(args.audio_gain) + 'dB"'
    if(args.audio_gate is not None):
        audio_parameters = ' -filter_complex agate=makeup=' + str(args.audio_gate)

    camera_parameters = ' -f v4l2 -framerate 15 -video_size 1920x1080 -c:v h264 -i /dev/video2'
    if(args.rtsp_stream is not None): # we're going to use an RTSP stream instead of the USB camera
        camera_parameters = ' -c:v h264 -rtsp_transport tcp -i "rtsp://' + args.rtsp_stream + '" -vf fps=fps=15'
        if(args.use_ptz):
            ip_pattern = re.compile('''((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)''')
            camera_ip = ip_pattern.search(args.rtsp_stream).group(0)

    # remove extend file when we first start so we don't accidently extend the broadcast at the begining
    if os.path.exists(args.extend_file):
        os.remove(args.extend_file)
 
    #authenticate with YouTube API
    youtube = google_auth.get_authenticated_service(credentials_file, args)
  
    #get next closest broadcast endpoint from youtube (should only be one)
    current_id = yt.get_next_broadcast(youtube, args.ward, args.num_from, args.num_to, args.verbose)
    if(current_id is None):
        print("No broadcast found, attempting to create broadcast.")
        current_id = insert_event.insert_event(youtube, args.title, description, start_time, args.run_time, args.thumbnail, args.ward, args.num_from, args.num_to, args.verbose)
        if(current_id is None):
            print("Failed to get current broadcast, and broadcast creation also failed!")
            if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " Ward failed to get current broadcast, and broadcast creation also failed!", args.verbose)
            exit()
    
    # if we have a broadcast status of created (which is what we should be getting here each time) then we need to bind the broadcast before we can use it
    if(yt.get_broadcast_status(youtube, current_id, args.ward, args.num_from, args.num_to, args.verbose == 'created')):
        insert_event.bind_event(youtube, current_id, args.ward, args.num_from, args.num_to, args.verbose)

    #make sure link on web host is current
    update_link.update_live_broadcast_link(current_id, args, args.html_filename, args.url_filename)

    #kick off broadcast
    ffmpeg = 'ffmpeg -thread_queue_size 2048' + audio_delay + ' -f alsa -guess_layout_max 0 -i default:CARD=Device -thread_queue_size 2048' + video_delay + camera_parameters + ' -c:v libx264 -profile:v high -pix_fmt yuv420p -preset superfast -g 7 -bf 2 -b:v 4096k -maxrate 4096k -bufsize 2048k -strict experimental -acodec libmp3lame -ar 44100 -threads 4 -crf 18 -b:a 128k -ac 1' + audio_parameters + ' -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key
    #ffmpeg = 'ffmpeg -thread_queue_size 2048' + audio_delay + ' -f alsa -guess_layout_max 0 -i default:CARD=Device -thread_queue_size 2048' + video_delay + ' -c:v h264 -rtsp_transport tcp -i "rtsp://admin:LDSadmin@192.168.1.109:554/cam/realmonitor?channel=1&subtype=0&unicast=true&proto=Onvif" -c:v libx264 -pix_fmt yuv420p -preset superfast -g 25 -b:v 3000k -maxrate 3000k -bufsize 1500k -strict experimental -acodec libmp3lame -ar 44100 -threads 4 -q:v 5 -q:a 5 -b:a 64k -af pan="mono: c0=FL" -ac 1 -filter:a "volume=' + str(args.audio_gain) + 'dB" -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key
    ffmpeg_img = 'ffmpeg -f lavfi -i anullsrc -loop 1 -i ' + args.pause_image + ' -c:v libx264 -filter:v fps=fps=4 -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key
#    process = subprocess.run(ffmpeg, shell=True, capture_output=True)
#    if(process.returncode != 0):
#        if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " Ward YouTube process exited with error!")
#        exit()
    process = None
    streaming = False
    count_viewers = threading.Thread(target = count_viewers.count_viewers, args = (args.ward.lower() + '_viewers.csv', youtube, current_id, args.ward, args.num_from, args.num_to, args.verbose, args.extended))
    count_viewers.daemon = True #set this as a daemon thread so it will end when the script does (instead of keeping script open)
    count_viewers.start()
    print("Starting stream...")
    verify_broadcast = threading.Thread(target = verify_live_broadcast, args = (youtube, args.ward, args, current_id, args.html_filename, args.url_filename, args.num_from, args.num_to, args.verbose))
    verify_broadcast.daemon = True
    verify_broadcast.start()
    broadcast_start = datetime.now()
    while(datetime.now() < stop_time and not killer.kill_now):
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
            if(args.use_ptz):
                try:
                    os.system('curl "http://192.168.108.9/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&2"') # point camera at pulpit before streaming
                except:
                    print("PTZ Problem")
                time.sleep(3) # wait for camera to get in position before streaming, hand count for this is about 3 seconds.
            streaming = True
            process = subprocess.Popen(split(ffmpeg), shell=False, stderr=subprocess.DEVNULL)
            # update status file with current start/stop times (there may be multiple wards in this file, so read/write out any that don't match current ward
            update_status.update("broadcast", start_time, stop_time, args.status_file, args.ward, args.num_from, args.num_to, args.verbose)
            while process.poll() is None:
                stop_time = check_extend(args.extend_file, stop_time, args.status_file, args.ward, args.num_from, args.num_to)
                if(os.path.exists(args.control_file) or datetime.now() > stop_time or killer.kill_now):
                    process.terminate()
                    process.wait()
                    break;
                time.sleep(1)
            streaming = False
          except:
            if(args.verbose): print(traceback.format_exc())
            streaming = False
            print("Live broadcast failure")
            if(args.num_from is not None and args.num_to is not None):
                sms.send_sms(args.num_from, args.num_to, args.ward + " had a live broadcast failure!", args.verbose)
        elif(stream == 0 and streaming == False):
          try:
            print("pause stream")
            if(args.use_ptz):
                try:
                    os.system('curl "http://192.168.108.9/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&250"') # point camera at wall to signal streaming as paused
                except:
                    print("PTZ Problem")
            streaming = True
            process = subprocess.Popen(split(ffmpeg_img), shell=False, stderr=subprocess.DEVNULL)
            # update status file with current start/stop times (there may be multiple wards in this file, so read/write out any that don't match current ward
            update_status.update("pause", start_time, stop_time, args.status_file, args.ward, args.num_from, args.num_to, args.verbose)
            while process.poll() is None:
                stop_time = check_extend(args.extend_file, stop_time, args.status_file, args.ward, args.num_from, args.num_to)
                if(not os.path.exists(args.control_file) or datetime.now() > stop_time or killer.kill_now):
                    process.terminate()
                    process.wait()
                    break;
                time.sleep(1)
            streaming = False
          except:
            if(args.verbose): print(traceback.format_exc())
            streaming = False
            print("Live broadcast failure pause")
            if(args.num_from is not None and args.num_to is not None):
                sms.send_sms(args.num_from, args.num_to, args.ward + " had a live broadcast failure while paused!", args.verbose)

        time.sleep(0.1)

    if(args.use_ptz):
        os.system('curl "http://192.168.108.9/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&250"') # point camera at wall to signal streaming as stopped

    print("Finished stream...")
    yt.stop_broadcast(youtube, current_id, args.ward, args.num_from, args.num_to, args.verbose)

    # change status back to start so webpage isn't left thinking we're still broadcasting/paused
    update_status.update("start", start_time, stop_time, args.status_file, args.ward, args.num_from, args.num_to, args.verbose)

    #clean up control file so it's reset for next broadcast, do this twice in case somebody inadvertently hits pause after the broadcast ends
    if os.path.exists(args.control_file):
        os.remove(args.control_file)

    if(not killer.kill_now): # don't wait if we're trying to kill the process now
        # this time while we wait before deleting the video is to allow people
        # time to finish watching the stream if they started late because of this
        # we want to continue getting viewer updates, so don't email until time runs out
        print("video will be deleted at {}".format((datetime.now() + timedelta(minutes=int(args.delay_after))).strftime("%d-%b-%Y %H:%M:%S")))
        # wait for X min before deleting video
        delete_time = datetime.now() + timedelta(minutes=args.delay_after)
        while(datetime.now() < delete_time and not killer.kill_now):
            time.sleep(1)
    else:
        # keep hitting a crash here with the viewers script, adding a delay for this case to prevent possble race condition
        time.sleep(5)

    # testing automation scripts can generate emails with invalid data
    # and a number of those email can be generated, look for a control
    # file and don't send emails if control file is present
    if(not testing):
        print("e-mail concurrent viewer file")
        if(args.email_from is not None and args.email_to is not None):
            send_email.send_viewer_file(args.ward.lower() + '_viewers.csv', args.email_from, args.email_to, args.ward, args.dkim_private_key, args.dkim_selector, args.num_from, args.num_to, args.verbose)

    print("Delete broadcast(s)")
    try:
        # delete the recording we just finished
        # if forcibly killing process, don't delete video
        if(delete_current and not killer.kill_now): youtube.videos().delete(id=current_id).execute()

        # delete all completed videos in Live list
        # delete all ready videos as they will cause problems for the new broadcast we will insert at the end of the script
        for video_id, video_status in yt.get_broadcasts(youtube, args.ward, args.num_from, args.num_to, args.verbose).items():
            if((delete_complete and video_status == "complete")
                or (delete_ready and (video_status == "ready"))): # if the broadcast got created but not bound it will be in created instead of ready state, since an un-bound broadcast can't unexpectedly accept a stream we'll leave these 
                if(video_id != current_id): # if current_id is still in list, then we've skipped deleting it above, so don't delete now.
                    youtube.videos().delete(id=video_id).execute()
    except:
        if(args.verbose): print(traceback.format_exc())
        print("Failed to delete broadcasts")
        if(args.num_from is not None and args.num_to is not None):
            sms.send_sms(args.num_from, args.num_to, args.ward + " failed to delete broadcasts!", args.verbose)

    print("Create next weeks broadcast")
    # create a broadcast endpoint for next weeks video
    start_time, stop_time = update_status.get_start_stop(args.start_time, args.run_time, datetime.strftime(start_time + timedelta(days=7), '%m/%d/%y'), args.ward, args.num_from, args.num_to, args.verbose)
    current_id = insert_event.insert_event(youtube, args.title, description, start_time, args.run_time, args.thumbnail, args.ward, args.num_from, args.num_to, args.verbose)

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

