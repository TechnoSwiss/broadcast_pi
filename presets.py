#!/usr/bin/python3

import argparse
import signal
import os
import traceback
import re
import subprocess
import time
import threading
import json

from visca_over_ip.camera import *

import sms # sms.py local file
import global_file as gf

NUM_RETRIES = 5

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

def set_preset(ward, cam_ip, preset_file, preset, num_from = None, num_to = None, verbose = False):

    try:
        if(os.path.exists(preset_file)):
            with open(preset_file, "r") as presetFile:
                presets = json.load(presetFile)
    except:
        if(verbose): print(traceback.format_exc())
        print("Error reading preset file")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " had an error reading the preset filei in set method!", verbose)
        return

    try:
        ptz_cam = Camera(cam_ip)
    except:
        if(verbose): print(traceback.format_exc())
        print("Failure connecting to VISCA Camera")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " had a failure connecting to the VISCA port on the camera in set method!", verbose)
        return

    for preset_name in presets:
        if(presets[preset_name]['preset'] == preset):
            print(preset_name)
            try:
                ptz_cam.pantilt(24, 24, presets[preset_name]['pan'], presets[preset_name]['tilt'])
                ptz_cam.zoom_to(presets[preset_name]['zoom'])
            except:
                if(verbose): print(traceback.format_exc())
                print("Failure setting camera PTZ position")
                if(num_from is not None and num_to is not None):
                    sms.send_sms(num_from, num_to, ward + " had a failure setting camera PTZ position in set method!", verbose)

    last_pan = None
    last_tilt = None
    last_zoom = None
    coordinate_found = None
    while(coordinate_found is None and not gf.killer.kill_now):
        try:
            pan, tilt = ptz_cam.get_pantilt_position()
            zoom = ptz_cam.get_zoom_position()

            if(pan != last_pan or tilt != last_tilt or zoom != last_zoom):
                print("Camera Moving")

            else:
                #need to put stuff here to report or save PTZ
                coordinate_found = True

            last_pan = pan
            last_tilt = tilt
            last_zoom = zoom

            time.sleep(1)
        except:
            if(verbose): print(traceback.format_exc())
            gf.log_exception(traceback.format_exc(), "failure getting camera PTZ")
            print("Failure getting camera PTZ position")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " had a failure getting camera PTZ position in set method!", verbose)
            time.sleep(10)

def record_presets(ward, cam_ip, preset_file, num_from = None, num_to = None, verbose = False):

    try:
        if(os.path.exists(preset_file)):
            with open(preset_file, "r") as presetFile:
                presets = json.load(presetFile)
    except:
        if(verbose): print(traceback.format_exc())
        print("Error reading preset file")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " had an error reading the preset file!", verbose)
        return

    try:
        ptz_cam = Camera(cam_ip)
    except:
        if(verbose): print(traceback.format_exc())
        gf.log_exception(traceback.format_exc(), "failure connecting to the VISCA port on the camera")
        print("Failure connecting to VISCA Camera")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " had a failure connecting to the VISCA port on the camera!", verbose)
        return
    
    last_preset = None
    for preset_name in presets:
        print("Finding coordinates for preset " + preset_name)
        # run through each preset and find it's coordinates
        subprocess.run(["curl", "http://" + cam_ip + "/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&" + str(presets[preset_name]['preset'])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        last_pan = None
        last_tilt = None
        last_zoom = None
        coordinate_found = None
        while(coordinate_found is None and not gf.killer.kill_now):
            try:
                pan, tilt = ptz_cam.get_pantilt_position()
                zoom = ptz_cam.get_zoom_position()

                if(pan != last_pan or tilt != last_tilt or zoom != last_zoom):
                    preset = 0
                    if(preset != last_preset):
                        print("Camera Moving")
                else:
                    preset = presets[preset_name]['preset']
                    presets[preset_name]['pan'] = pan
                    presets[preset_name]['tilt'] = tilt
                    presets[preset_name]['zoom'] = zoom
                    coordinate_found = True

                last_pan = pan
                last_tilt = tilt
                last_zoom = zoom

                time.sleep(1)
            except:
                if(verbose): print(traceback.format_exc())
                print("Failure getting preset coordinates")

    try:
        if(os.path.exists(preset_file)):
            with open(preset_file, "w") as presetFile:
                json.dump(presets, presetFile, indent=4, default=str)
    except:
        if(verbose): print(traceback.format_exc())
        print("Error writing preset file")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " had an error writing the preset file!", verbose)
        return

def report_preset(delay, ward, cam_ip, preset_file, preset_status_file, num_from = None, num_to = None, verbose = False):
    last_preset = None
    last_pan = None
    last_tilt = None
    last_zoom = None

    time.sleep(delay)
    try:
        if(os.path.exists(preset_file)):
            with open(preset_file, "r") as presetFile:
                presets = json.load(presetFile)
    except:
        if(verbose): print(traceback.format_exc())
        print("Error reading preset file")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " had an error reading the preset file!", verbose)
        return

    exception = None
    for retry_num in range(NUM_RETRIES):
        exception = None
        try:
            ptz_cam = Camera(cam_ip)
            break
        except Exception as exc:
            exception = exc
            if(verbose): print('!!VISCA Connection Retry!!')
            gf.sleep(0.5,1)

    if exception:
        if(verbose): print(traceback.format_exc())
        print("Failure connecting to VISCA Camera")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " had a failure connecting to the VISCA port on the camera!", verbose)
        return
    
    while(not gf.killer.kill_now):
        try:
            pan, tilt = ptz_cam.get_pantilt_position()
            zoom = ptz_cam.get_zoom_position()

            if(pan != last_pan or tilt != last_tilt or zoom != last_zoom):
                preset = 0
                if(preset != last_preset):
                    print("Camera Moving")
                    if(preset_status_file is not None):
                        with open(preset_status_file, "w") as presetstatusFile:
                            presetstatusFile.write('0\n')

            else:
                preset = None
                for preset_name in presets:
                    if(pan == presets[preset_name]['pan'] and tilt == presets[preset_name]['tilt'] and zoom == presets[preset_name]['zoom']):
                        preset = presets[preset_name]['preset']
                        if(preset != last_preset):
                            print(preset_name)
                if(preset != last_preset and preset is None):
                    print("Undefined")
                    preset = -1

            if(preset_status_file is not None):
                with open(preset_status_file, "w") as presetstatusFile:
                    presetstatusFile.write(str(preset)+'\n')

            last_preset = preset
            last_pan = pan
            last_tilt = tilt
            last_zoom = zoom

            gf.consecutive_ptz_status_failures = 0

            time.sleep(1)
        except:
            if(verbose): print(traceback.format_exc())
            gf.log_exception(traceback.format_exc(), "failure getting camera PTZ")
            print("Failure getting camera PTZ position")
            gf.consecutive_ptz_status_failures += 1
            # camera PTZ position failures are not a hugh isssue
            # the random wait on retry didn't seem to fix the issue
            # so to prevent constant text messages only send message
            # after several consecutive failures
            if(gf.consecutive_ptz_status_failures >= gf.pts_status_retries):
                # if we've reached this state it's likely the next request
                # will also fail, so zero the counter to prevent double
                # messages
                gf.consecutive_ptz_status_failures = 0
                if(gf.ptz_sms_sent <= gf.ptz_sms_max):
                    gf.ptz_sms_sent += 1
                    if(num_from is not None and num_to is not None):
                        sms.send_sms(num_from, num_to, ward + " had a failure getting camera PTZ position!", verbose)
            time.sleep(1)

    ptz_cam.close_connection()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Report out camera current preset by getting camera positions and verifying against preset locations')
    parser.add_argument('-c','--config-file',type=str,help='JSON Configuration file')
    parser.add_argument('-w','--ward',type=str,help='Name of Ward being broadcast')
    parser.add_argument('-p','--pc-name',type=str,help='System name that script is running on.')
    parser.add_argument('--preset-file',type=str,help='JSON file where camera presets are stored.')
    parser.add_argument('--record-presets',default=False,action='store_true',help='Updates preset positions in preset-file')
    parser.add_argument('--set-preset',type=int,help='Set camera preset to value from preset-file')
    parser.add_argument('-R','--rtsp-stream',type=str,help='Use to specify an RTSP stream on the network to use instead of USB camera')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False,action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    gf.killer = GracefulKiller()

    if(args.config_file is not None and os.path.exists(args.config_file)):
        with open(args.config_file, "r") as configFile:
            config = json.load(configFile)

            # check for keys in config file
            if 'broadcast_ward' in config:
                args.ward = config['broadcast_ward']
            if 'preset_file' in config:
                args.preset_file = config['preset_file']
            if 'source_rtsp_stream' in config:
                args.rtsp_stream = config['source_rtsp_stream']
            if 'preset_status_file' in config:
                preset_status_file = config['preset_status_file']
            if 'notification_text_from' in config:
                args.num_from = config['notification_text_from']
            if 'notification_text_to' in config:
                args.num_to = config['notification_text_to']

    if(args.rtsp_stream is not None):
        ip_pattern = re.compile('''((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)''')
        camera_ip = ip_pattern.search(args.rtsp_stream).group(0)

    if(camera_ip is None and (args.config_file is None or (args.pc_name is None and args.preset_file is None and args.rtsp_stream is None))):
        print("A valid configuration file and rtsp stream are required to monitor presets.")

    if(args.record_presets):
        record_presets(args.ward if args.pc_name is None else args.pc_name, camera_ip, args.preset_file, args.num_from, args.num_to, args.verbose)
        exit()

    if(args.set_preset is not None):
        set_preset(args.ward if args.pc_name is None else args.pc_name, camera_ip, args.preset_file, args.set_preset, args.num_from, args.num_to, args.verbose)
        exit()

    if(camera_ip is not None and (args.ward is not None or args.pc_name is not None) and args.preset_file is not None and preset_status_file is not None):
        report_preset(0, args.ward if args.pc_name is None else args.pc_name, camera_ip, args.preset_file, preset_status_file, args.num_from, args.num_to, args.verbose)

