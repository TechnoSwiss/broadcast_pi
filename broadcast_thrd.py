
import os
import sys
import traceback
import re
import subprocess
import time
import signal
import psutil # sudo apt install python3-psutil
import threading
import requests

from subprocess import check_output
from collections import deque
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
NO_DATA_TIMEOUT_SEC = 10
YOUTUBE_RECEIVING_SEC = 10

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

def safe_kill_ffmpeg(process, ward, num_from = None, num_to = None, verbose = False):
    try:
        pgid = os.getpgid(process.pid)
        os.killpg(pgid, signal.SIGTERM)
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print("Graceful kill failed, forcing...")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " ffmpeg graceful kill failed, forcing!", verbose)
        os.killpg(pgid, signal.SIGKILL)
        process.wait()
    except ProcessLookupError:
        print("Process already exited.")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " ffmpeg process already exited!", verbose)
    finally:
        try:
            if process.stderr:
                process.stderr.close()
        except:
            pass

def ffmpeg_tree_alive(process, ward, num_from=None, num_to=None, verbose=False):
    try:
        parent = psutil.Process(process.pid)
        if parent.is_running() and parent.status() != psutil.STATUS_ZOMBIE:
            return True
        for child in parent.children(recursive=True):
            if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                return True
    except psutil.NoSuchProcess:
        pass
    return False

def log_ffmpeg_process_tree(process):
    try:
        parent = psutil.Process(process.pid)
        print(f"Main ffmpeg PID: {parent.pid} ({parent.name()})")
        for child in parent.children(recursive=True):
            print(f"  └─ Child PID: {child.pid} ({child.name()})")
    except Exception as e:
        print(f"Could not fetch process tree: {e}")

def handle_poll_failure(process, process_terminate, poll_failure, index, audio_index, ward, stream_type, stderr_buffer,num_from=None, num_to=None, verbose=False):
    try:
        if not process_terminate and process.poll() is not None:
            exit_code = process.poll()
            print(f"⚠️ {stream_type} stream exited with code: {exit_code}")

            err_output = "\n".join(stderr_buffer).lower()
            poll_failure += 1

            if(exit_code == -15): # this is SIGTERM and means we requested the process stop
                return poll_failure, index
            #elif(exit_code == -9): # this is SIGKILL and means we forced the process stop, which means we're likely trying to trigger an event below
            else:
                stderr_summary = "🔍 Last 5 stderr lines before poll failure:\n"
                stderr_summary += '\n'.join("   " + line.strip() for line in list(stderr_buffer)[-5:])
                if(verbose): print(stderr_summary)
                gf.log_exception(stderr_summary, "ffmpeg poll failure context")

            if "invalid data" in err_output:
                print("!!Issue with data from camera!!")
                if num_from and num_to:
                    sms.send_sms(num_from, num_to, f"{ward} {stream_type.lower()} issue with data from camera!", verbose)

            if "connection timed out" in err_output:
                print("!!Connection to camera timed out!!")
                if num_from and num_to:
                    sms.send_sms(num_from, num_to, f"{ward} {stream_type.lower()} connection to camera timeout!", verbose)

            if "no route to host" in err_output:
                print("!!Network issue reaching camera!!")
                if num_from and num_to:
                    sms.send_sms(num_from, num_to, f"{ward} {stream_type.lower()} network issue reaching camera!", verbose)

            # if broadcast is dying then issue is most likely with the camera not streaming data to YouTube
            # I've seen that happen for the specific issues tested for above, but also with no discernable reason in the ffmpeg output
            if audio_index and index != audio_index:
                if poll_failure > 1:
                    poll_failure = 0
                    print("!!forcing broadcast to audio only!!")
                    index = audio_index
                    if(num_from is not None and num_to is not None):
                        sms.send_sms(num_from, num_to, ward + " forcing broadcast to audio only!", verbose)

            if poll_failure > MAX_PROCESS_POLL_FAILURE:
                print(f"!!{stream_type} Stream Died, max failure reached, exiting!!")
                if num_from and num_to:
                    sms.send_sms(num_from, num_to, f"{ward} {stream_type.lower()} stream died, max failure reached, exiting!", verbose)
                sys.exit(f"{stream_type} Stream max failure reached")
            else:
                print(f"!!{stream_type} Stream Died!! ({poll_failure})")
                if num_from and num_to:
                    sms.send_sms(num_from, num_to, f"{ward} {stream_type.lower()} stream died! ({poll_failure})", verbose)
    except:
        tb = traceback.format_exc()
        if verbose: print(tb)
        gf.log_exception(tb, " exception in poll failure handler")
        print(f"!!{stream_type} exception in poll failure handler!!")
        if num_from and num_to:
            sms.send_sms(num_from, num_to, f"{ward} {stream_type.lower()} exception in poll failure handler!", verbose)

    return poll_failure, index

def drain_stderr(pipe, buffer, verbose=False, log_filename=None):
    log_file = None

    if pipe is None:
        if(verbose): print("stderr pipe is None, skipping drain_stderr")
        return

    # Attempt to open the log file if a path is provided
    if log_filename:
        logfile_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), log_filename)
        try:
            os.makedirs(os.path.dirname(logfile_path), exist_ok=True)  # Ensure directory exists
            log_file = open(logfile_path, "a", buffering=1)
        except Exception as e:
            if verbose: print(f"Failed to open log file {logfile_path}: {e}")
            log_file = None

    try:
        for line in iter(lambda: pipe.readline(), b''):
            if not line:
                break
            decoded = line.decode(errors='ignore').strip()
            if decoded:
                buffer.append(decoded)
                if log_file:
                    try:
                        log_file.write(decoded + "\n")
                        log_file.flush()
                    except Exception as e:
                        if verbose: print(f"Error writing to log file: {e}")
    except:
        print(f"stderr reader error")
        tb = traceback.format_exc()
        if(verbose): print(tb)
        gf.log_exception(tb, f"stderr reader error")
    finally:
        if log_file:
            try:
                log_file.close()
            except Exception as e:
                if verbose: print(f"Error closing log file: {e}")

def broadcast(youtube, current_id, start_time, ward, camera_ip, broadcast_stream, broadcast_downgrade_delay, bandwidth_file, ffmpeg_img, audio_record, audio_record_control, num_from, num_to, args, verbose):
    process = None
    streaming = False
    youtube_receiving = False
    stream_last = 0
    broadcast_index = 0
    broadcast_index_new = 0
    broadcast_status_length = datetime.now()
    broadcast_status_check = datetime.now()
    process_poll_failure = 0
    ffmpeg_record_audio = False
    ffmpeg_record_audio_last = None
    broadcast_stream_final = list(broadcast_stream)
    log_filename = None
    cam_present = False

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


        script_dir = os.path.abspath(os.path.dirname(__file__))
        tmp_dir = os.path.join(script_dir, "tmp")
        mp3_base_filename = os.path.join(tmp_dir, (args.url_filename if args.url_filename is not None else ward) + "_%Y-%m-%d_%H-%M-%S.mp3")
        if(verbose): print(f"MP3 Output File : {mp3_base_filename}")
        if(ffmpeg_record_audio != ffmpeg_record_audio_last):
            broadcast_stream_final = list(broadcast_stream)
            if(ffmpeg_record_audio):
                for index in range(len(broadcast_stream)):
                    broadcast_stream_final[index] = broadcast_stream[index] + f" -vn -acodec libmp3lame -q:a 5 -f segment -segment_time 9999999 -strftime 1 \"{mp3_base_filename}\""
            ffmpeg_record_audio_last = ffmpeg_record_audio

        if(ffmpeg_record_audio): gf.audio_recorded = True

        # at this point no other instance of ffmpeg should be running, other than the one for the web preview
        # if we find any that are sending data to YouTube we need to kill them so we don't end up with a double ingestion error
        gf.kill_ffmpeg("broadcast_thrd", ward, args.youtube_key, num_from, num_to, verbose)

        if(stream == 1 and streaming == False):
          try:
            print(f"main stream {'with' if ffmpeg_record_audio else 'without'} audio recording")
            if(args.use_ptz):
                try:
                    result = subprocess.run(['ping', '-c', '2', '-W', '2', camera_ip], capture_output=True, text=True)
                    cam_present = True if ' 0% packet loss' in result.stdout else False
                except Exception as e:
                    cam_present = False
                if(not cam_present):
                    print("!!Camera not responding!!")
                    broadcast_index = (len(broadcast_stream_final) - 1)
                    print("!!Forcing Audio Only Broadcast!!")
                    if num_from and num_to:
                        sms.send_sms(num_from, num_to, f"{ward} camera not responding, forcing audio only broadcast!", verbose)
                    try:
                        if(bandwidth_file is not None):
                            with open(bandwidth_file, "w") as bandwidthFile:
                                bandwidthFile.write(str(broadcast_index))
                    except:
                        if(verbose): print(traceback.format_exc())
                        print("Failure writing bandwidth file in camera not responding")
                        if(num_from is not None and num_to is not None):
                            sms.send_sms(num_from, num_to, ward + " had a failure writing the bandwidth file in camera not responding!", verbose)
            if(cam_present and stream_last == 0):
                stream_last = stream
                try:
                    requests.get(f"http://{camera_ip}/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&102", timeout=5)
                except requests.RequestException as e:
                    print("PTZ Problem:", e)
                time.sleep(3) # wait for camera to get in position before streaming, hand count for this is about 3 seconds.
            streaming = True
            if broadcast_index is None or broadcast_index >= len(broadcast_stream_final):
                print(f"Invalid broadcast index: {broadcast_index}, defaulting to 0")
                broadcast_index = 0
            stderr_buffer = deque(maxlen=1000)
            process = subprocess.Popen(split(broadcast_stream_final[broadcast_index]), shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, preexec_fn=os.setsid)
            youtube_receiving = False
            #log_filename = f"logs/ffmpeg_main_{datetime.now():%Y%m%d-%H%M%S}.log"
            stderr_thread = threading.Thread(target=drain_stderr, args=(process.stderr, stderr_buffer, verbose, log_filename), daemon=True)
            stderr_thread.start()
            broadcast_status_length = datetime.now()
            if(verbose): print(f"Spawned PID: {process.pid}")
            process_terminate = False
            # update status file with current start/stop times (there may be multiple wards in this file, so read/write out any that don't match current ward
            update_status.update("broadcast", start_time, gf.stop_time, args.status_file, ward, num_from, num_to, verbose)
            while process.poll() is None or ffmpeg_tree_alive(process, ward, num_from, num_to, verbose):
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
                    print("stopping main stream")
                    process_terminate = True
                    safe_kill_ffmpeg(process, ward, num_from, num_to, verbose)
                    process = None
                    break
                try:
                    if(bandwidth_file is not None and os.path.exists(bandwidth_file)):
                        with open(bandwidth_file, "r") as bandwidthFile:
                            broadcast_index_new = int(bandwidthFile.readline())
                            if(broadcast_index_new < len(broadcast_stream_final) and broadcast_index_new >= 0):
                                if(broadcast_index != broadcast_index_new):
                                    broadcast_index = broadcast_index_new
                                    poll_failure = 0 # if we're changing the broadcast_index, lets reset the poll_failure counter as well
                                    print("Setting broadcast to index " + str(broadcast_index))
                                    process_terminate = True
                                    safe_kill_ffmpeg(process, ward, num_from, num_to, verbose)
                                    process = None
                                    break
                            else:
                                raise Exception("Invalid broadcast index")
                except:
                    if(verbose): print(traceback.format_exc())
                    print("Failure reading bandwidth file")
                    if(num_from is not None and num_to is not None):
                        sms.send_sms(num_from, num_to, ward + " had a failure reading the bandwidth file!", verbose)
                if(not youtube_receiving and (datetime.now() - broadcast_status_check) > timedelta(seconds=2)):
                    status, status_description = yt.get_broadcast_health(youtube, current_id, ward, num_from, num_to, verbose)
                    if(verbose or status != 'good'): print(f"Status : {status}" + ("" if status_description == "" else f" => {status_description}"))
                    broadcast_status_check = datetime.now()
                    if(status == 'noData' and (datetime.now() - broadcast_status_length) > timedelta(seconds=NO_DATA_TIMEOUT_SEC)):
                        gf.kill_ffmpeg("broadcast_thrd_main", ward, args.youtube_key, num_from, num_to, verbose)
                        broadcast_status_length = datetime.now()
                    elif(status != 'noData' and  (datetime.now() - broadcast_status_length) > timedelta(seconds=YOUTUBE_RECEIVING_SEC)):
                        youtube_receiving = True
                        broadcast_status_length = datetime.now()
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
                    if(verbose or status != 'good'): print(f"Status : {status}" + ("" if status_description == "" else f" => {status_description}"))
                    # if for some reason we have multiple streams going into YouTube kill this stream to reset the system
                    if("More than one ingestion" in status_description):
                        print("!!Multiple streams being ingested by YouTube, resetting!!")
                        broadcast_status_length = datetime.now()
                        if(num_from is not None and num_to is not None):
                            sms.send_sms(num_from, num_to, ward + " multiple streams being ingested by YouTube!", verbose)
                        process_terminate = True
                        safe_kill_ffmpeg(process, ward, num_from, num_to, verbose)
                        process = None
                        break
                    if(status == 'noData' and (datetime.now() - broadcast_status_length) > timedelta(seconds=NO_DATA_TIMEOUT_SEC)):
                        gf.kill_ffmpeg("broadcast_thrd_main", ward, args.youtube_key, num_from, num_to, verbose)
                        broadcast_status_length = datetime.now()
                    if(status == 'bad' and (datetime.now() - broadcast_status_length) > timedelta(minutes=broadcast_downgrade_delay[broadcast_index])):
                        broadcast_index += 1
                        poll_failure = 0 # if we're changing the broadcast_index, lets reset the poll_failure counter as well
                        # !!!! START DEBUG CODE !!!!
                        # if we're going to reduce the broadcast bandwidth, we want to collect some information so we can try to determine why the bandwidth needs to be reduced.
                        os.makedirs("html/debug", exist_ok=True)

                        url1 = 'debug/top1' + time.strftime("%Y%m%d-%H%M%S")  + '.txt'
                        url2 = 'debug/top2' + time.strftime("%Y%m%d-%H%M%S")  + '.txt'
                        url3 = 'debug/ps' + time.strftime("%Y%m%d-%H%M%S")  + '.txt'
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
                        safe_kill_ffmpeg(process, ward, num_from, num_to, verbose)
                        process = None
                        break
                    if(status != 'bad' and status != 'noData'):
                        broadcast_status_length = datetime.now()
                time.sleep(1)
                # if something is using the camera after the sleep we'll
                # end up with process.poll() != None and we'll get stuck
                # in an endless loop here
                process_poll_failure, broadcast_index_tmp = handle_poll_failure(
                    process,
                    process_terminate,
                    process_poll_failure,
                    broadcast_index,
                    (len(broadcast_stream_final) - 1),
                    ward,
                    stream_type="Main",
                    stderr_buffer=stderr_buffer,
                    num_from=num_from,
                    num_to=num_to,
                    verbose=verbose
                )
                if(broadcast_index != broadcast_index_tmp):
                    broadcast_index = broadcast_index_tmp
                    print("Audio only broadcast, switching to index " + str(broadcast_index))
                    try:
                        if(bandwidth_file is not None):
                            with open(bandwidth_file, "w") as bandwidthFile:
                                bandwidthFile.write(str(broadcast_index))
                    except:
                        if(verbose): print(traceback.format_exc())
                        print("Failure writing bandwidth file")
                        if(num_from is not None and num_to is not None):
                            sms.send_sms(num_from, num_to, ward + " had a failure writing the bandwidth file!", verbose)
                    break
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
            # need to make sure the thread is dead here or we'll end up with duplicate ffmpegs feeding YouTube
            safe_kill_ffmpeg(process, ward, num_from, num_to, verbose)
            process = None
        elif(stream == 0 and streaming == False):
          try:
            print("pause stream")
            if(cam_present):
                stream_last = stream
                try:
                    requests.get(f"http://{camera_ip}/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&250", timeout=5)
                except requests.RequestException as e:
                    print("PTZ Problem:", e)
            streaming = True
            stderr_buffer = deque(maxlen=1000)
            process = subprocess.Popen(split(ffmpeg_img), shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, preexec_fn=os.setsid)
            youtube_receiving = False
            #log_filename = f"logs/ffmpeg_pause_{datetime.now():%Y%m%d-%H%M%S}.log"
            stderr_thread = threading.Thread(target=drain_stderr, args=(process.stderr, stderr_buffer, verbose, log_filename), daemon=True)
            stderr_thread.start()
            broadcast_status_length = datetime.now()
            if(verbose): print(f"Spawned PID: {process.pid}")
            process_terminate = False
            # update status file with current start/stop times (there may be multiple wards in this file, so read/write out any that don't match current ward
            update_status.update("pause", start_time, gf.stop_time, args.status_file, ward, num_from, num_to, verbose)
            while process.poll() is None or ffmpeg_tree_alive(process, ward, num_from, num_to, verbose):
                gf.stop_time = check_extend(args.extend_file, gf.stop_time, args.status_file, ward, args.extend_max, num_from, num_to, verbose)
                if(not os.path.exists(args.control_file) or datetime.now() > gf.stop_time or gf.killer.kill_now):
                    print("stopping pause stream")
                    process_terminate = True
                    safe_kill_ffmpeg(process, ward, num_from, num_to, verbose)
                    process = None
                    break
                if(not youtube_receiving and (datetime.now() - broadcast_status_check) > timedelta(seconds=2)):
                    status, status_description = yt.get_broadcast_health(youtube, current_id, ward, num_from, num_to, verbose)
                    if(verbose or status != 'good'): print(f"Status : {status}" + ("" if status_description == "" else f" => {status_description}"))
                    broadcast_status_check = datetime.now()
                    if(status == 'noData' and (datetime.now() - broadcast_status_length) > timedelta(seconds=NO_DATA_TIMEOUT_SEC)):
                        gf.kill_ffmpeg("broadcast_thrd_main", ward, args.youtube_key, num_from, num_to, verbose)
                        broadcast_status_length = datetime.now()
                    elif(status != 'noData' and  (datetime.now() - broadcast_status_length) > timedelta(seconds=YOUTUBE_RECEIVING_SEC)):
                        youtube_receiving = True
                        broadcast_status_length = datetime.now()
                if((datetime.now() - broadcast_status_check) > timedelta(minutes=1)):
                    # every minute grab the broadcast status so we can act on any issues
                    status, status_description = yt.get_broadcast_health(youtube, current_id, ward, num_from, num_to, verbose)
                    broadcast_status_check = datetime.now()
                    if(verbose or status != 'good'): print(f"Status : {status}" + ("" if status_description == "" else f" => {status_description}"))
                    # if for some reason we have multiple streams going into YouTube kill this stream to reset the system
                    if("More than one ingestion" in status_description):
                        print("!!Multiple streams being ingested by YouTube, resetting!!")
                        broadcast_status_length = datetime.now()
                        if(num_from is not None and num_to is not None):
                            sms.send_sms(num_from, num_to, ward + " multiple streams being ingested by YouTube!", verbose)
                        process_terminate = True
                        safe_kill_ffmpeg(process, ward, num_from, num_to, verbose)
                        process = None
                        break
                    if(status == 'noData' and (datetime.now() - broadcast_status_length) > timedelta(seconds=NO_DATA_TIMEOUT_SEC)):
                        gf.kill_ffmpeg("broadcast_thrd_pause", ward, args.youtube_key, num_from, num_to, verbose)
                        broadcast_status_length = datetime.now()
                    if(status != 'bad' and status != 'noData'):
                        broadcast_status_length = datetime.now()
                time.sleep(1)
                # if something is using the camera after the sleep we'll
                # end up with process.poll() != None and we'll get stuck
                # in an endless loop here
                process_poll_failure, broadcast_index = handle_poll_failure(
                    process,
                    process_terminate,
                    process_poll_failure,
                    broadcast_index,
                    None, # audio_only index
                    ward,
                    stream_type="Pause",
                    stderr_buffer=stderr_buffer,
                    num_from=num_from,
                    num_to=num_to,
                    verbose=verbose
                )

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
            # need to make sure the thread is dead here or we'll end up with duplicate ffmpegs feeding YouTube
            safe_kill_ffmpeg(process, ward, num_from, num_to, verbose)
            process = None

        time.sleep(0.1)
