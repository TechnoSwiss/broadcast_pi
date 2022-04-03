import random
import time

killer = None

consecutive_ptz_status_failures = 0
pts_status_retries = 3
ptz_sms_sent = 0
ptz_sms_max = 5

save_exceptions_to_file = False

def sleep(min_timeout = 0.1, max_timeout = 2):
    time.sleep(random.uniform(min_timeout, max_timeout))
    return
