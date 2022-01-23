#!/usr/bin/python3

import argparse
import signal
import os
import traceback
import subprocess
import time
import threading
import json

from visca_over_ip.camera import *

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
        print("Failure connecting to VISCA Camera")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " had a failure connection to the VISCA port on the camera!", verbose)
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

    try:
        ptz_cam = Camera(cam_ip)
    except:
        if(verbose): print(traceback.format_exc())
        print("Failure connecting to VISCA Camera")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " had a failure connection to the VISCA port on the camera!", verbose)
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

            time.sleep(1)
        except:
            if(verbose): print(traceback.format_exc())
            print("Failure getting camera PTZ position")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " had a failure getting camera PTZ position!", verbose)
            time.sleep(10)

    ptz_cam.close_connection()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Report out camera current preset by getting camera positions and verifying against preset locations')
    parser.add_argument('-c','--config-file',type=str,help='JSON Configuration file')
    parser.add_argument('-v','--verbose',default=False,action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    gf.killer = GracefulKiller()

    #report_preset("evergreen", "192.168.108.9", "presets.json", "html/status/preset", None, None, True)
    record_presets("rpi", "192.168.108.9", "presets.json", None, None, True)

