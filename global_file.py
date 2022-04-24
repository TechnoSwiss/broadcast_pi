import random
import time
import threading

from datetime import datetime

killer = None

consecutive_ptz_status_failures = 0
pts_status_retries = 3
ptz_sms_sent = 0
ptz_sms_max = 5

save_exceptions_to_file = True
global_lock = threading.Lock()

def sleep(min_timeout = 0.1, max_timeout = 2):
    time.sleep(random.uniform(min_timeout, max_timeout))
    return

def log_exception(exception: str, message: str):
    if(save_exceptions_to_file):
        while global_lock.locked():
            sleep(0.01, 0.1)
            continue

        global_lock.acquire()

        with open("exception_error", 'a') as write_error:
            write_error.write("\n\n" + message + datetime.now().strftime(" %m/%d/%Y, %H:%M:%S\n"))
            write_error.write(exception)

        global_lock.release()
