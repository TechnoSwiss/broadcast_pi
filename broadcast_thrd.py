
import os
import sys
import traceback
import re
import subprocess
import time

from subprocess import check_output
import socket

from shlex import split
from datetime import datetime, timedelta
from dateutil import tz # pip install python-dateutil

import youtube_api as yt # youtube.py local file
import sms # sms.py local file
import update_status # update_status.py local file
import local_stream as ls # local_stream.py local file
import global_file as gf # local file for sharing globals between files

EXTEND_TIME_INCREMENT = 5
MAX_PROCESS_POLL_FAILURE = 3
PROCESS_POLL_CLEAR_AFTER = 5

extend_time = 0 # keep track of extend time so we can cap it if needed

def check_extend(extend_file, stop_time, status_file, ward, extend_max = None, num_from = None, num_to = None, verbose = False):
    global extend_time
    if os.path.exists(extend_file):
        extend_time += EXTEND_TIME_INCREMENT
        os.remove(extend_file)
        if(extend_max is None or extend_time <= int(extend_max)):
            stop_time = stop_time + timedelta(minutes=EXTEND_TIME_INCREMENT)
            update_status.update("stop", None, stop_time, status_file, ward, num_from, num_to, verbose)
            print("extending broadcast time by " + str(EXTEND_TIME_INCREMENT) + " min.")
            print("stop_time extended to: " + stop_time.strftime("%d-%b-%Y %H:%M:%S"))
        else:
            print("broadcast time can't be extended")
    return(stop_time)

def broadcast(youtube, current_id, start_time, ward, camera_ip, broadcast_stream, broadcast_downgrade_delay, bandwidth_file, ffmpeg_img, audio_record, audio_record_control, num_from, num_to, args, verbose):
    process = None
    streaming = False
    stream_last = 0
    broadcast_index = 0
    broadcast_index_new = 0
    broadcast_status_length = datetime.now()
    broadcast_status_check = datetime.now()
    process_poll_failure = 0
    process_poll_clear = 0
    ffmpeg_record_audio = False
    ffmpeg_record_audio_last = None
    broadcast_stream_final = list(broadcast_stream)

    while(datetime.now() < gf.stop_time and not gf.killer.kill_now):
        try:
            stream = 0 if os.path.exists(args.control_file) else 1 # stream = 1 means we should be broadcasting the camera feed, stream = 0 means we should be broadcasting the title card "pausing" the video
        except:
            if(verbose): print(traceback.format_exc())
            print("Failure reading control file")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " had a failure reading the control file!", verbose)

        try:
            if(audio_record_control is not None):
                ffmpeg_record_audio = True if os.path.exists(audio_record_control) else False
        except:
            if(verbose): print(traceback.format_exc())
            print("Failure reading audio record control file")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " had a failure reading the audio record control file!", verbose)
        if(audio_record and audio_record_control is not None):
            ffmpeg_record_audio = True
            # touch the audio_record_control file so we can show recording is active in the web interface
            open(audio_record_control, "a").close()

        if(ffmpeg_record_audio != ffmpeg_record_audio_last):
            broadcast_stream_final = list(broadcast_stream)
            if(ffmpeg_record_audio):
                for index in range(len(broadcast_stream)):
                    broadcast_stream_final[index] = broadcast_stream[index] + f" -vn -acodec libmp3lame -q:a 5 -f segment -segment_time 9999999 -strftime 1 \"{args.url_filename if args.url_filename is not None else ward}_%Y-%m-%d_%H-%M-%S.mp3\""
            ffmpeg_record_audio_last = ffmpeg_record_audio

        if(ffmpeg_record_audio): gf.audio_recorded = True

        if(stream == 1 and streaming == False):
          try:
            print(f"main stream {'with' if ffmpeg_record_audio else 'without'} audio recording")
            if(args.use_ptz and stream_last == 0):
                stream_last = stream
                try:
                    subprocess.run(["curl", "http://" + camera_ip + "/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&102"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) # point camera at pulpit before streaming
                except:
                    print("PTZ Problem")
                time.sleep(3) # wait for camera to get in position before streaming, hand count for this is about 3 seconds.
            streaming = True
            process = subprocess.Popen(split(broadcast_stream_final[broadcast_index]), shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            process_terminate = False
            # update status file with current start/stop times (there may be multiple wards in this file, so read/write out any that don't match current ward
            update_status.update("broadcast", start_time, gf.stop_time, args.status_file, ward, num_from, num_to, verbose)
            while process.poll() is None:
                gf.stop_time = check_extend(args.extend_file, gf.stop_time, args.status_file, ward, args.extend_max, num_from, num_to, verbose)
                change_record_status = False
                if(audio_record_control is not None):
                    if((os.path.exists(audio_record_control) and not ffmpeg_record_audio) or (not audio_record and ffmpeg_record_audio and not os.path.exists(audio_record_control))):
                        change_record_status = True
                    if(audio_record):
                        # if audio_record is set to true, recording can't be stopped, if control file is removed by the web interface, add it back in
                        open(audio_record_control, "a").close()
                else:
                    # if we don't have the path defined in the config, check the default location and delete if it exists
                    default_audio_record_path = 'html/status/audio_record'
                    if(os.path.exists(default_audio_record_path)):
                        os.remove(default_audio_record_path)
                if(os.path.exists(args.control_file) or datetime.now() > gf.stop_time or gf.killer.kill_now or change_record_status):
                    process_terminate = True
                    process.terminate()
                    process.wait()
                    break;
                try:
                    if(bandwidth_file is not None and os.path.exists(bandwidth_file)):
                        with open(bandwidth_file, "r") as bandwidthFile:
                            broadcast_index_new = int(bandwidthFile.readline())
                            if(broadcast_index_new < len(broadcast_stream_final) and broadcast_index_new >= 0):
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
                if(broadcast_index < (len(broadcast_stream_final) - 1) and (datetime.now() - broadcast_status_check) > timedelta(minutes=1)):
                    # every minute grab the broadcast status so we can reduce the bandwidth if there's problems
                    # if gf.current_id has been defined, then verify_life_broadcast detected a change in the video id we're using, so we need to update for that
                    if(gf.current_id and gf.current_id != current_id):
                        print("Live ID doesn't match Current ID, updating status")
                        print(current_id + " => " + gf.current_id)
                        current_id = gf.current_id
                    status, status_description = yt.get_broadcast_health(youtube, current_id, ward, num_from, num_to, verbose)
                    broadcast_status_check = datetime.now()
                    if(verbose): print("Status : " + status)
                    # this description contains additional information from YouTube
                    # it's usually empty unless there's a message like "bitrate is missmatched" or "not receiving enough data"
                    if(verbose): print("Status Description : " + status_description)
                    if(status == 'bad' and (datetime.now() - broadcast_status_length) > timedelta(minutes=broadcast_downgrade_delay[broadcast_index])):
                        broadcast_index += 1
                        # !!!! START DEBUG CODE !!!!
                        # if we're going to reduce the broadcast bandwidth, we want to collect some information so we can try to determine why the bandwidth needs to be reduced.
                        url1 = 'top1' + time.strftime("%Y%m%d-%H%M%S")  + '.txt'
                        url2 = 'top2' + time.strftime("%Y%m%d-%H%M%S")  + '.txt'
                        url3 = 'ps' + time.strftime("%Y%m%d-%H%M%S")  + '.txt'
                        top1 = check_output("top -b -n 1 -1 -c -H -w 512 > html/" + url1, shell=True)
                        top2 = check_output("top -b -n 1 -1 -c -w 512 > html/" + url2, shell=True)
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

                        if(broadcast_index >= len(broadcast_stream_final)): broadcast_index = len(broadcast_stream_final)  - 1
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
                    if(process_poll_failure > 0 and process_poll_clear > PROCESS_POLL_CLEAR_AFTER):
                        if(verbose): print("clear process failure")
                        process_poll_failure = 0

            streaming = False
          except SystemExit as e:
              #if we threw a system exit something is wrong bail out
              sys.exit("Caught system exit")
          except:
            tb = traceback.format_exc()
            if(verbose): print(tb)
            gf.log_exception(tb, f"{ward} Live Broadcast failure")
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
            update_status.update("pause", start_time, gf.stop_time, args.status_file, ward, num_from, num_to, verbose)
            while process.poll() is None:
                gf.stop_time = check_extend(args.extend_file, gf.stop_time, args.status_file, ward, args.extend_max, num_from, num_to, verbose)
                if(not os.path.exists(args.control_file) or datetime.now() > gf.stop_time or gf.killer.kill_now):
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
                    process_poll_clear += 1
                    if(process_poll_failure > 0 and process_poll_clear > PROCESS_POLL_CLEAR_AFTER):
                        if(verbose): print("clear process failure")
                        process_poll_failure = 0

            streaming = False
          except SystemExit as e:
              #if we threw a system exit something is wrong bail out
              sys.exit("Caught system exit")
          except:
            tb = traceback.format_exc()
            if(verbose): print(tb)
            gf.log_exception(tb, f"{ward} Live Broadcast failure pause")
            streaming = False
            print("Live broadcast failure pause")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " had a live broadcast failure while paused!", verbose)

        time.sleep(0.1)