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
        message = client.messages.create(
                         body=sms_message,
                         from_=num_from,
                         to=num_to
                     )
    except:
        if(verbose): print(traceback.format_exc())
        print("SMS Account Auth File Read Failure")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Send SMS Message')
    parser.add_argument('-m','--message',type=str,required=True,help="Message text")
    parser.add_argument('-F','--num-from',type=str,required=True,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,required=True,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()
  
    send_sms(args.num_from, args.num_to, args.message, args.verbose)
