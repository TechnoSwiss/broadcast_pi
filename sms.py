#!/usr/bin/python3

# Python script that sends SMS message using the Twilio SMS API https://www.twilio.com/

# Twilio API has a free trial that will probably work for a long time if the volume of text messages is low, however it's $1/month plus per-use cost if you go past the free trial
# There may be other APIs that could be used instead with better cost structures
# There's also the possibility to use email to send SMS messages using the carriers email endpoints for SMS
# Set that up something like:
#carriers = {
#    'att':    '@txt.att.net',
#    'tmobile':' @tmomail.net',
#    'verizon':  '@vtext.com',
#}
# and then in code the num_to would be setup like
# num_to = '{0}{}'.format(num_to, carriers['att'])

import argparse
import os
import traceback
import json
from twilio.rest import Client # pip3 install twilio

#Twilio account sid and auth token
#two line ascii text file, first line account sid, second line auth token
TWILIO_AUTH = 'twilio.auth'

if os.path.exists(TWILIO_AUTH):
    try:
        with open(TWILIO_AUTH, 'r') as f:
           account_sid = f.readline().replace('\n', '')
           auth_token = f.readline().replace('\n', '')
    except:
        #print(traceback.format_exc())
        print("SMS Account Auth File Read Failure")
        exit()

client = Client(account_sid, auth_token)

def send_sms(num_from, num_to, sms_message, verbose = False):
    try:
        if(type(num_to) != list):
            num_to = [num_to]

        for number in num_to:
            message = client.messages.create(
                             body=sms_message,
                             from_=num_from,
                             to=number
                         )
    except:
        if(verbose): print(traceback.format_exc())
        print("SMS Send Failure")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Send SMS Message')
    parser.add_argument('-c','--config-file',type=str,help='JSON Configuration file')
    parser.add_argument('-m','--message',required=True,type=str,help="Message text")
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()
  
    verbose = args.verbose
    ward = None
    num_from = args.num_from
    num_to = args.num_to

    if(args.config_file is not None):
        if("/" in args.config_file):
            config_file = args.config_file
        else:
            config_file =  os.path.exists(os.path.abspath(os.path.dirname(__file__)) + "/" + args.config_file)
    if(args.config_file is not None and os.path.exists(args.config_file)):
        with open(args.config_file, "r") as configFile:
            config = json.load(configFile)

            # check for keys in config file
            if 'broadcast_ward' in config:
                ward = config['broadcast_ward']
            if 'notification_text_from' in config:
                num_from = config['notification_text_from']
            if 'notification_text_to' in config:
                num_to = config['notification_text_to']

    if(num_from is None):
        print("!!Number From is a required argument!!")
        exit()
    if(num_to is None):
        print("!!Number To is a required argument!!")
        exit()

    if(ward is not None):
        args.message = ward + " : " + args.message
    send_sms(num_from, num_to, args.message, verbose)
8
