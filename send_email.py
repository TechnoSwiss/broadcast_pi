#!/usr/bin/python3

import sys
import os
import traceback
import argparse
import traceback
import json
from datetime import datetime, timedelta
import time

import smtplib
import imaplib
import dkim
import ssl
from email import encoders
from email.message import Message
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart

import sms # sms.py local file
import count_viewers # count_viewers.py local file
import delete_event # local file for deleting broadcast

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

def send_total_views(email_from, email_to, ward, total_views, total_previous_views = 0, broadcast_time = None, dkim_private_key = None, dkim_selector = None, num_from = None, num_to = None, verbose = False):
    try:
        if(verbose): print("creating total viewers email")
        sender_domain = email_from.split('@')[-1]
        # added a JSON configuration file that allows creating this as a list
        # so need to be able to turn it into a comma seperated string
        if(type(email_to) == list):
            email_to = ",".join(email_to)
        msg = MIMEText("There were " + str(total_views) + " total view(s) reported by YouTube." + (" An additional " + str(total_views - total_previous_views) + " view(s) since the live broadcast.") if total_previous_views is not None else "", 'plain')
        msg["From"] = email_from
        msg["To"] = email_to
        if(broadcast_time is None) :
            broadcast_time = datetime.now()
        msg["Subject"] = broadcast_time.strftime("%A %b. ") + ordinal(broadcast_time.strftime("%-d")) + " Broadcast final view(s) count - " + ward.replace("_", " ")
        msg["Message-ID"] = "<" + str(time.time()) + "-" + email_from + ">"

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

        try:
            # save copy of message to mail server
            imap = imaplib.IMAP4_SSL(email_auth[email_from]['server'], 993)
            imap.login(email_from, email_auth[email_from]['password'])
            imap.append('INBOX.Sent', '\\Seen', imaplib.Time2Internaldate(time.time()), msg.as_string().encode('UTF-8'))
            imap.logout()
        except:
            if(verbose): print(traceback.format_exc())
            print("Failed to save final viewers email to sent folder")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " failed to save final viewers to sent folder!", verbose)

    except:
        if(verbose): print(traceback.format_exc())
        print("Failed to send final viewers email")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to send final viewers!", verbose)

def send_viewer_file(csv_file, png_file, email_from, email_to, ward, total_views, broadcast_time = None, dkim_private_key = None, dkim_selector = None, num_from = None, num_to = None, verbose = False):
    try:
        if(verbose): print("creating concurrent viewers file email")
        sender_domain = email_from.split('@')[-1]
        # added a JSON configuration file that allows creating this as a list
        # so need to be able to turn it into a comma seperated string
        if(type(email_to) == list):
            email_to = ",".join(email_to)
        msg = MIMEMultipart()
        msg["From"] = email_from
        msg["To"] = email_to
        if(broadcast_time is None) :
            broadcast_time = datetime.now()
        msg["Subject"] = broadcast_time.strftime("%A %b. ") + ordinal(broadcast_time.strftime("%-d")) + " Broadcast - " + ward.replace("_", " ")
        msg["Message-ID"] = "<" + str(time.time()) + "-" + email_from + ">"

        # Encapsulate the plain and HTML versions of the message body in an
        # 'alternative' part, so message agents can decide which they want to display.
        #msgAlternative = MIMEMultipart('alternative')
        #msgRoot.attach(msgAlternative)

        msg.attach(MIMEText("There were " + str(total_views) + " view(s) reported by YouTube.\nFor breakdown of concurrent viewers during broadcast,\nplease open the attached file in a spreadsheet app. (Excel/Google Docs).", 'plain'))

        with open(csv_file) as fp:
            attachment = MIMEText(fp.read(), _subtype='csv')

        attachment.add_header('Content-Disposition', 'attachment', filename=csv_file)
        msg.attach(attachment)

        if(os.path.exists(png_file)):
            with open(png_file, 'rb') as fp:
                image = MIMEImage(fp.read(), _subtype='png')
            image.add_header('Content-ID', '<%s>' % 1)
            image.add_header('Content-Disposition', 'attachment', filename=png_file)
            msg.attach(image)

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

        try:
            # save copy of message to mail server
            imap = imaplib.IMAP4_SSL(email_auth[email_from]['server'], 993)
            imap.login(email_from, email_auth[email_from]['password'])
            imap.append('INBOX.Sent', '\\Seen', imaplib.Time2Internaldate(time.time()), msg.as_string().encode('UTF-8'))
            imap.logout()
        except:
            if(verbose): print(traceback.format_exc())
            print("Failed to save CSV file email to sent folder")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " failed to save current viewers to sent folder!", verbose)

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
    parser.add_argument('-V','--viewers-file',type=str,default=None,help='Filename for Viewers file to send via email')
    parser.add_argument('--image-file',type=str,default=None,help='Filename for Image file to send via email')
    parser.add_argument('--broadcast-time',type=str, default=None,help='Broadcast tim to use in email of final viewer numbers format of "YYYY-MM-DD HH:MM:SS"')
    parser.add_argument('--broadcast-date',type=str, default=None,help='Broadcast date to use in email of final viewer numbers format of "YYYY-MM-DD"')
    parser.add_argument('--num-viewers',type=int,default=0,help='Number of viewers recorded previously, used to calculate number of new viewers since broadcast was live')
    parser.add_argument('-C','--current-id',type=str,help='ID value for the current broadcast, used if deleting current broadcast is true')
    parser.add_argument('-D','--delete-control',type=int,help='Control delete options from command line, bit mapped. delete_current 1 : delete_ready 2 : delete_complete 4')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    verbose = args.verbose
    ward = args.ward
    delete_current = False
    email_send = True
    recurring = True # is this a recurring broadcast, then create a new broadcast for next week

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
                args.ward = ward # this value gets passed to the deletion routine
            if 'broadcast_title' in config:
                args.title = config['broadcast_title'] # this value gets passed to the deletion routine
            if 'broadcast_title_card' in config:
                args.thumbnail = config['broadcast_title_card'] # this value gets passed to the deletion routine
            if 'broadcast_recurring' in config:
                recurring = config['broadcast_recurring'] # this value gets passed to the deletion routine
            if 'broadcast_status' in config:
                args.status_file = config['broadcast_status'] # this value gets passed to the deletion routine
            if 'url_ssh_host' in config:
                args.host_name = config['url_ssh_host'] # this value gets passed to the deletion routine
            if 'url_ssh_username' in config:
                args.user_name = config['url_ssh_username'] # this value gets passed to the deletion routine
            if 'url_ssh_key_dir' in config:
                args.home_dir = config['url_ssh_key_dir'] # this value gets passed to the deletion routine
            if 'url_name' in config:
                args.url_filename = config['url_name'] # this value gets passed to the deletion routine
            else:
                args.url_filename = None
            args.html_filename = None # this value gets passed to the deletion routine
            if 'url_key' in config:
                args.url_key = config['url_key'] # this value gets passed to the deletion routine
            else:
                args.url_key = None
            if 'broadcast_time' in config:
                args.start_time = config['broadcast_time'] # this value gets passed to the deletion routine
            if 'broadcast_length' in config:
                args.run_time = config['broadcast_length'] # this value gets passed to the deletion routine
            if 'email_send' in config:
                email_send = config['email_send'] # this value gets passed to the deletion routine
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
                args.num_from = num_from # this value gets passed to the deletion routine
            if 'notification_text_to' in config:
                num_to = config['notification_text_to']
                args.num_to = num_to # this value gets passed to the deletion routine
            if 'delete_current' in config:
                delete_current = config['delete_current']
            if 'delete_time_delay' in config:
                args.delay_after = config['delete_time_delay']

    if(ward is None):
        print("!!Ward is a required argument!!")
        exit()

    if(args.email_from is None):
        print("!!Email from is a required argument!!")
        exit()

    if(args.email_to is None):
        print("!!Email To is a required argument!!")
        exit()

    if(args.current_id is not None and args.broadcast_time is None):
        print("!!Delete broadcast setup requires using --broadcast-time!!")
        exit()

    if(args.broadcast_date is not None):
        broadcast_time = datetime.strptime(args.broadcast_date + " 12:00:00", "%Y-%m-%d %H:%M:%S")
    elif(args.broadcast_time is not None):
        broadcast_time = datetime.strptime(args.broadcast_time, "%Y-%m-%d %H:%M:%S")
    else:
        broadcast_time = None

    if(args.viewers_file is not None and args.image_file is None):
        args.image_file = ward.lower() + '_viewers.png'
        count_viewers.write_viewer_image(args.viewers_file, args.image_file, ward, num_from, num_to, verbose)

    if(args.viewers_file is not None and args.image_file is not None):
        send_viewer_file(args.viewers_file, args.image_file, args.email_from, args.email_to, ward, args.num_viewers, broadcast_time, args.dkim_private_key, args.dkim_selector, args.num_from, args.num_to, args.verbose)
        if(delete_current and args.current_id is not None):
            if(args.verbose) : print("Setup event deletion")
            run_deletion_time = broadcast_time + timedelta(minutes=int(args.delay_after))
            delete_event.setup_event_deletion(args.current_id, args.num_viewers, email_send, recurring, run_deletion_time, args)
    else:
        send_total_views(args.email_from, args.email_to, ward, args.num_viewers, 0, broadcast_time, args.dkim_private_key, args.dkim_selector, args.num_from, args.num_to, args.verbose)
