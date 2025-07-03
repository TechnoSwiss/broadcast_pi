#!/usr/bin/python3

import argparse
import sys
import signal
import os
import traceback
import subprocess 
import time
import requests


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

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Broadcast Live Ward Meeting to YouTube')
    parser.add_argument('-v','--verbose',default=False,action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    killer = GracefulKiller()

    presets = [
            "http://192.168.108.9/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&247",
            "http://192.168.108.9/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&248",
            "http://192.168.108.9/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&249",
            ]

    while True:
        if(killer.kill_now): break;
        for preset in presets:
            if(killer.kill_now): break;
            print("Calling preset: " + preset)
            try:
                requests.get(preset, timeout=5)
            except requests.RequestException as e:
                print("PTZ Problem:", e)
            for i in range(10):
                sys.stdout.write(str(i + 1) + ' ')
                sys.stdout.flush()
                time.sleep(1)
                if(killer.kill_now): break;
            print('\n')

    try:
        requests.get(f"http://192.168.108.9/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&250", timeout=5)
    except requests.RequestException as e:
        print("PTZ Problem:", e)
