#!/usr/bin/python3

import sys
import os
import traceback
import argparse
import traceback
from datetime import datetime

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email import encoders
from email.message import Message
from email.mime.text import MIMEText

EMAIL_PASS = 'email.pass'

email_auth = {}
if os.path.exists(EMAIL_PASS):
    try:
        with open(EMAIL_PASS, 'r') as f:
            for line in f.readlines():
                line = line.replace('\n', '')
                account, password, server = line.split(' ')
                email_auth[account] = {}
                email_auth[account]['password'] = password
                email_auth[account]['server']= server
    except:
        #print(traceback.format_exc())
        print("Email Password File Read Failure")
        exit()

def ordinal(n):
    n = int(n)
    suffix = ['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]
    if 11 <= (n % 100) <= 13:
        suffix = 'th'
    return str(n) + suffix

def send_viewer_file(csv_file, email_from, email_to, ward, num_from = None, num_to = None):
    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = datetime.now().strftime("%A %b. ") + ordinal(datetime.now().strftime("%-d")) + " Broadcast"
    msg.attach(MIMEText("Please open the attached file in a spreadsheet (Excel/Google Docs).", 'plain'))

    try:
        fp = open(csv_file)
        attachment = MIMEText(fp.read(), _subtype='csv')
        fp.close()

        attachment.add_header("Content-Disposition", "attachment", filename=csv_file)
        msg.attach(attachment)

        port = 465 #for SSL

        # Create a secure SSL context
        context = ssl.create_default_context()

        with smtplib.SMTP_SSL(email_auth[email_from]['server'], port, context=context) as server:
            server.login(email_from, email_auth[email_from]['password'])
            server.sendmail(email_from, email_to.split(','),msg.as_string())
    except:
        #print(traceback.format_exc())
        print("Failed to send CSV file email")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to write current viewers!")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Email CSV file')
    parser.add_argument('-w','--ward',type=str,required=True,help='Name of Ward being broadcast')
    parser.add_argument('-e','--email-from',type=str,required=True,help='Account to send email with/from')
    parser.add_argument('-E','--email-to',type=str,required=True,help='Accoun tto send CSV fiel email to')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    args = parser.parse_args()

    send_viewer_file('viewers.csv', args.email_from, args.email_to, args.ward)

