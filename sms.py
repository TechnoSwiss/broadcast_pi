#!/usr/bin/python3

# Python script that sends SMS message using the Twilio SMS API https://www.twilio.com/

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
        print("SMS Account Auth File Read Failure")
        exit()

client = Client(account_sid, auth_token)

def send_sms(num_from, num_to, sms_message):
    message = client.messages.create(
                     body=sms_message,
                     from_=num_from,
                     to=num_to
                 )

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Send SMS Message')
    parser.add_argument('-m','--message',type=str,required=True,help="Message text")
    parser.add_argument('-F','--num-from',type=str,required=True,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,required=True,help='SMS number to send notification to')
    args = parser.parse_args()
  
    send_sms(args.num_from, args.num_to, args.message)
