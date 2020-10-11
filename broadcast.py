#!/usr/bin/python3

# python script that will grab the latest YouTube Live ID (assumes no uploaded videos are present on channel)
# update a web server over SSH with a page containing a redirect to that Live ID
# then start sending an RTSP stream to the given YouTube key to start a Live Stream that should come up under that link
# the broadcast will run for the amount of time given (or and hour and ten minutes is the default) before waiting for the
# given delay (default is ten minutes) before deleting ALL videos from YouTube channel

import argparse
import os
import re
import pickle
import subprocess
import time

from shlex import split
from datetime import datetime, timedelta

import google_auth # google_auth.py local file
import update_link # update_link.py local file
import sms #sms.py local file
 
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Broadcast Live Ward Meeting to YouTube')
    parser.add_argument('-w','--ward',type=str,required=True,help='Name of Ward being broadcast')
    parser.add_argument('-f','--control-file',type=str,default='pause',help='Path and filename for file used to Delay/Pause video stream')
    parser.add_argument('-p','--pause-image',type=str,default='pause.jpg',help='Path and filename for the JPG image that will be showen when stream is paused')
    parser.add_argument('-o','--host-name',type=str,help='The address for the web host to upload HTML link forward page to')
    parser.add_argument('-u','--user-name',type=str,help='The username for the web host')
    parser.add_argument('-D','--home-dir',type=str,help='Home directory SSH id_rsa key is stored under')
    parser.add_argument('-k','--url-key',type=str,help='The 4-digit code at the end of the URL')
    parser.add_argument('-y','--youtube-key',type=str,required=True,help='YouTube Key')
    parser.add_argument('-a','--audio-delay',type=float,default=0.0,help='Audio Delay in Seconds (decimal)')
    parser.add_argument('-g','--audio-gain',type=int,default=0,help='Audio Gain in dB (integer)')
    parser.add_argument('-t','--run-time',type=str,default='1:10:00',help='Broadcast runtime in HH:MM:SS')
    parser.add_argument('-d','--delay-after',type=int,default=10,help='Number of min. after broadcast to wait before cleaning up videos.')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    args = parser.parse_args()

    start_time = datetime.now()
    H, M, S = args.run_time.split(':')
    stop_time = start_time + timedelta(hours=int(H), minutes=int(M),seconds=int(S))
    if not os.path.exists(args.pause_image):
        if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " no pause image available!")
        print("Pause image not found, image is required for paused stream.")
        exit()

    credentials_file = args.ward.lower() + '.auth'
    
    audio_delay = ''
    video_delay = ''
    if(args.audio_delay < 0):
        video_delay = " -itsoffset " + str(abs(args.audio_delay))
    else:
        audio_delay = " -itsoffset " + str(abs(args.audio_delay))
 
    videos = []
    
    #authenticate with YouTube API
    youtube = google_auth.get_authenticated_service(credentials_file, args)
  
    #attempt to get list of curreny videos from YouTube, the first video in this list should be the next URL used for live broadcasts
    try:
        uploads_playlist_id = update_link.get_my_uploads_list(youtube)
        if uploads_playlist_id:
            videos = update_link.list_my_uploaded_videos(youtube, uploads_playlist_id)
        else:
            if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " Ward YouTube no videos found!")
            exit()
    except ():
        if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " Ward YouTube Failed to get list of videos!")
        exit()

    #grab the ID for the first video, this should be the next URL for live broadcasts
    current_id = videos[0]
    
    #make sure link on web host is current
    update_link.update_live_broadcast_link(current_id, args)

    #kick off broadcast
    ffmpeg = 'ffmpeg -thread_queue_size 2048' + audio_delay + ' -f alsa -guess_layout_max 0 -i default:CARD=Device -thread_queue_size 2048' + video_delay + ' -f v4l2 -framerate 15 -video_size 1920x1080 -c:v h264 -i /dev/video2 -c:v libx264 -pix_fmt yuv420p -preset superfast -g 25 -b:v 3000k -maxrate 3000k -bufsize 1500k -strict experimental -acodec libmp3lame -ar 44100 -threads 4 -q:v 5 -q:a 5 -b:a 64k -af pan="mono: c0=FL" -ac 1 -filter:a "volume=' + str(args.audio_gain) + 'dB" -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key
    ffmpeg_img = 'ffmpeg -f lavfi -i anullsrc -loop 1 -i ' + args.pause_image + ' -c:v libx264 -f flv rtmp://x.rtmp.youtube.com/live2/' + args.youtube_key
#    process = subprocess.run(ffmpeg, shell=True, capture_output=True)
#    if(process.returncode != 0):
#        if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " Ward YouTube process exited with error!")
#        exit()
    process = None
    streaming = False
    while(datetime.now() < stop_time):
        stream = 0 if os.path.exists(args.control_file) else 1 # stream = 1 means we should be broadcasting the camera feed, stream = 0 means we should be broadcasting the title card "pausing" the video

        if(stream == 1 and streaming == False):
            streaming = True
            process = subprocess.Popen(split(ffmpeg), shell=False, stderr=subprocess.DEVNULL)
            while process.poll() is None:
                if os.path.exists(args.control_file) or datetime.now() > stop_time:
                    process.terminate()
                    process.wait()
                time.sleep(1)
            streaming = False
        elif(stream == 0 and streaming == False):
            streaming = True
            process = subprocess.Popen(split(ffmpeg_img), shell=False, stderr=subprocess.DEVNULL)
            while process.poll() is None:
                if not os.path.exists(args.control_file) or datetime.now() > stop_time:
                    process.terminate()
                    process.wait()
                time.sleep(1)
            streaming = False

        time.sleep(1)

    time.sleep(args.delay_after * 60) # wait for X min before deleting video
    
    #get list of videos (at this point it should be the next live video link, and the broadcast we just finished
    try:
        if uploads_playlist_id:
            videos = update_link.list_my_uploaded_videos(youtube, uploads_playlist_id)
        else:
            if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " Ward YouTube no videos found after broadcast!")
            exit()
    except ():
        if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " Ward YouTube Failed to get list of videos after broadcast!")
        exit()
    
    # delete all videos in Live list except video 0 which is the URL for the NEXT live broadcast
    for video in range(1, len(videos)):
        request = youtube.videos().delete(id=videos[video])
        request.execute()
        
    #grab the ID for the first video, this should be the next URL for live broadcasts
    current_id = videos[0]
    
    #make sure link on web host is current
    update_link.update_live_broadcast_link(current_id, args)

    #clean up control file so it's reset for next broadcast
    if os.path.exists(args.control_file):
        os.remote(args.control_file)

