#!/usr/bin/python3

import argparse
import signal
import os
import traceback
import subprocess
import time
import threading
import json

from shlex import split

import sms # sms.py local file
import global_file as gf

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


def local_stream_process(ward, local_stream, local_stream_output, local_stream_control, num_from = None, num_to = None, verbose = False):

    local_stream_process = "ffmpeg -thread_queue_size 2048 -c:v h264 -rtsp_transport tcp -i rtsp://" + local_stream + " -vf fps=fps=3 -update 1 " + local_stream_output + " -y"
    while(not gf.killer.kill_now):
        try:
            if(os.path.exists(local_stream_control)):
                print("Starting Local Stream")
                process = subprocess.Popen(split(local_stream_process), shell=False, stderr=subprocess.DEVNULL)
                while process.poll() is None:
                    if(not os.path.exists(local_stream_control)):
                        print("Stopping Local Stream")
                        process.terminate()
                        process.wait()
                        break;
                    time.sleep(1)
            time.sleep(1)
        except:
            if(verbose): print(traceback.format_exc())
            print("Local Stream Failure")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " had a local stream failure!", verbose)
    print("Local Stream Finished")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Update local server with JPG stream from camera for displaying on webpage.')
    parser.add_argument('-c','--config-file',type=str,help='JSON Configuration file')
    parser.add_argument('-w','--ward',type=str,help='Name of Ward being broadcast')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    gf.killer = GracefulKiller()

    if(args.config_file is not None and os.path.exists(args.config_file)):
        with open(args.config_file, "r") as configFile:
            config = json.load(configFile)

            # check for keys in config file
            if 'broadcast_ward' in config:
                args.ward = config['broadcast_ward']
            if 'local_stream' in config:
                local_stream = config['local_stream']
            if 'local_stream_output' in config:
                local_stream_output = config['local_stream_output']
            if 'local_stream_control' in config:
                local_stream_control = config['local_stream_control']
            if 'notification_text_from' in config:
                args.num_from = config['notification_text_from']
            if 'notification_text_to' in config:
                args.num_to = config['notification_text_to']

    if(local_stream is not None and local_stream_output is not None and local_stream_control is not None):
        stream_local = threading.Thread(target = local_stream_process, args = (args.ward, local_stream, local_stream_output, local_stream_control, args.num_from, args.num_to, args.verbose))
        stream_local.daemon = True
        stream_local.start()

    while(not gf.killer.kill_now):
        time.sleep(1)
