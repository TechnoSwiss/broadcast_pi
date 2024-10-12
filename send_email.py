#!/usr/bin/python3

import sys
import os
import traceback
import argparse
import traceback
import json
from datetime import datetime
import time

import smtplib
import dkim
import ssl
from email.mime.multipart import MIMEMultipart
from email import encoders
from email.message import Message
from email.mime.text import MIMEText

import sms # sms.py local file

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

def send_total_views(email_from, email_to, ward, total_views, dkim_private_key = None, dkim_selector = None, num_from = None, num_to = None, verbose = False):
    sender_domain = email_from.split('@')[-1]
    # added a JSON configuration file that allows creating this as a list
    # so need to be able to turn it into a comma seperated string
    if(type(email_to) == list):
        email_to = ",".join(email_to)
    msg = MIMEText("There were " + total_views + " total view(s) reported by YouTube.", 'plain')
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = datetime.now().strftime("%A %b. ") + ordinal(datetime.now().strftime("%-d")) + " Broadcast final view(s) count - " + ward.replace("_", " ")
    msg["Message-ID"] = "<" + str(time.time()) + "-" + email_from + ">"

    try:
        if(dkim_private_key and dkim_selector):
            with open(dkim_private_key) as fh:
                dkim_private_key = fh.read()
            headers = ['To', 'From', 'Subject']
            sig = dkim.sign(
                message=msg.as_bytes(),
                selector=dkim_selector.encode(),
                domain=sender_domain.encode(),
                privkey=dkim_private_key.encode(),
                include_headers=headers
            )
            msg["DKIM-Signature"] = sig[len("DKIM-Signature: "):].decode()

        port = 465 #for SSL

        # Create a secure SSL context
        context = ssl.create_default_context()

        with smtplib.SMTP_SSL(email_auth[email_from]['server'], port, context=context) as server:
            server.login(email_from, email_auth[email_from]['password'])
            server.sendmail(email_from, email_to.split(','),msg.as_string())
    except:
        if(verbose): print(traceback.format_exc())
        print("Failed to send CSV file email")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to send current viewers!", verbose)

def send_viewer_file(csv_file, email_from, email_to, ward, total_views, dkim_private_key = None, dkim_selector = None, num_from = None, num_to = None, verbose = False):
    sender_domain = email_from.split('@')[-1]
    # added a JSON configuration file that allows creating this as a list
    # so need to be able to turn it into a comma seperated string
    if(type(email_to) == list):
        email_to = ",".join(email_to)
    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = datetime.now().strftime("%A %b. ") + ordinal(datetime.now().strftime("%-d")) + " Broadcast - " + ward.replace("_", " ")
    msg["Message-ID"] = "<" + str(time.time()) + "-" + email_from + ">"
    msg.attach(MIMEText("There were " + total_views + " view(s) reported by YouTube.\nFor breakdown of concurrent viewers during broadcast,\nplease open the attached file in a spreadsheet app. (Excel/Google Docs).", 'plain'))

    try:
        with open(csv_file) as fp:
            attachment = MIMEText(fp.read(), _subtype='csv')

        attachment.add_header("Content-Disposition", "attachment", filename=csv_file)
        msg.attach(attachment)

        if(dkim_private_key and dkim_selector):
            with open(dkim_private_key) as fh:
                dkim_private_key = fh.read()
            headers = ['To', 'From', 'Subject']
            sig = dkim.sign(
                message=msg.as_bytes(),
                selector=dkim_selector.encode(),
                domain=sender_domain.encode(),
                privkey=dkim_private_key.encode(),
                include_headers=headers
            )
            msg["DKIM-Signature"] = sig[len("DKIM-Signature: "):].decode()

        port = 465 #for SSL

        # Create a secure SSL context
        context = ssl.create_default_context()

        with smtplib.SMTP_SSL(email_auth[email_from]['server'], port, context=context) as server:
            server.login(email_from, email_auth[email_from]['password'])
            server.sendmail(email_from, email_to.split(','),msg.as_string())
    except:
        if(verbose): print(traceback.format_exc())
        print("Failed to send CSV file email")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to send current viewers!", verbose)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Email CSV file')
    parser.add_argument('-c','--config-file',type=str,help='JSON Configuration file')
    parser.add_argument('-w','--ward',type=str,help='Name of Ward being broadcast')
    parser.add_argument('-e','--email-from',type=str,help='Account to send email with/from')
    parser.add_argument('-E','--email-to',type=str,help='Accoun tto send CSV fiel email to')
    parser.add_argument('-M','--dkim-private-key',type=str,help='Full path and filename of DKIM private key file')
    parser.add_argument('-m','--dkim-selector',type=str,help='DKIM Domain Selector')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-V','--viewers-file',type=str,default='viewers.csv',help='Filename for Viewers file to send via email')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    verbose = args.verbose
    ward = args.ward

    if(args.config_file is not None):
        if("/" in args.config_file):
            config_file = args.config_file
        else:
            config_file =  os.path.abspath(os.path.dirname(__file__)) + "/" + args.config_file
    if(verbose): print('Config file : ' + config_file)
    if(config_file is not None and os.path.exists(config_file)):
        with open(config_file, "r") as configFile:
            config = json.load(configFile)

            # check for keys in config file
            if 'broadcast_ward' in config:
                ward = config['broadcast_ward']
            if 'email_from_account' in config:
                args.email_from = config['email_from_account']
            if 'email_dkim_key' in config:
                args.dkim_private_key = config['email_dkim_key']
            if 'email_dkim_domain' in config:
                args.dkim_selector = config['email_dkim_domain']
            if 'email_viewer_addresses' in config:
                args.email_to = config['email_viewer_addresses']
            if 'notification_text_from' in config:
                num_from = config['notification_text_from']
            if 'notification_text_to' in config:
                num_to = config['notification_text_to']

    if(ward is None):
        print("!!Ward is a required argument!!")
        exit()

    if(args.email_from is None):
        print("!!Email from is a required argument!!")
        exit()

    if(args.email_to is None):
        print("!!Email To is a required argument!!")
        exit()

    send_total_views(args.email_from, args.email_to, ward, '0', args.dkim_private_key, args.dkim_selector, args.num_from, args.num_to, args.verbose)
    send_viewer_file(args.viewers_file, args.email_from, args.email_to, ward, '0', args.dkim_private_key, args.dkim_selector, args.num_from, args.num_to, args.verbose)
