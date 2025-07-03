import os
import signal
import random
import time
import threading
import psutil

from datetime import datetime

import sms # sms.py local file

killer = None

GD_VIEWS_ROW = 3
GD_TOTAL_ROW = 4

ptz_last_status = None
pts_status_retries = 3
ptz_sms_sent = 0
ptz_sms_max = 5

stop_time = None

sms_fifo = []
sms_missed = 0

save_exceptions_to_file = True
global_lock = threading.Lock()
stream_event = threading.Event()
stream_event_terminate = threading.Event()

current_id = None

audio_recorded = False

def sleep(min_timeout = 0.1, max_timeout = 2):
    time.sleep(random.uniform(min_timeout, max_timeout))
    return

def kill_ffmpeg(calling_process, ward, youtube_key, num_from = None, num_to = None, verbose = None):
    for p in psutil.process_iter(attrs=['pid', 'cmdline']):
        try:
            if 'ffmpeg' in p.info['cmdline'] and youtube_key in ' '.join(p.info['cmdline']):
                os.kill(p.info['pid'], signal.SIGKILL)
                print(f"Killed ffmpeg (PID: {p.info['pid']}) from {calling_process}.")
                if(num_from and num_to):
                    sms.send_sms(num_from, num_to, f"{ward} Ward ffmpeg, PID: {p.info['pid']} killed from {calling_process}.", verbose)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

def log_exception(exception: str, message: str):
    if(save_exceptions_to_file):
        while global_lock.locked():
            sleep(0.01, 0.1)
            continue

        with global_lock:
            exception_file = os.path.join(os.path.abspath(os.path.dirname(__file__)), "logs", "exception_error")
            try:
                os.makedirs(os.path.dirname(exception_file), exist_ok=True)  # Ensure directory exists
                with open(exception_file, 'a') as write_error:
                    write_error.write("\n\n" + message + datetime.now().strftime(" %m/%d/%Y, %H:%M:%S\n"))
                    write_error.write(exception)
            except Exception as e:
                print(f"Failed to open exception file {exception_file}: {e}")
