#!/usr/bin/python3

# this script assume that you have a website hosted somewhere with SSH key based access to the server
# it will grab the first Video ID from YouTube (this assumes that no videos have been uploaded, only Live videos are present)
# and upload an HTML page that will forward to that link

import argparse
import os
import traceback
import re
import json

import google_auth # google_auth.py local file
import sms #sms.py local file
import global_file as gf # local file for sharing globals between files

import paramiko
from paramiko import SSHClient
from scp import SCPClient
from datetime import datetime
from pathlib import Path

NUM_RETRIES = 5

#SSH id_rsa key decryption password file (for SSH upload of HTML file to website for sharing YouTube Unlisted URL
#file is just a single ascii line plain text password
SSH_RSA_KEY_PASS = 'ssh.pass'

def update_ward_name(html_path, ward_name, num_from, num_to, verbose):
    try:
        p = Path(html_path)
        text = p.read_text(encoding="utf-8")

        # Replace:
        # const WARD_NAME = "anything_here"
        new_text = re.sub(
            r'const\s+WARD_NAME\s*=\s*"[^"]*"',
            f'const WARD_NAME = "{ward_name}"',
            text
        )

        p.write_text(new_text, encoding="utf-8")
    except:
        if(verbose): print(traceback.format_exc())
        print("Failed to update ward in link plage")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to update ward in link page!", verbose)

def get_my_uploads_list(youtube):
    # Retrieve the contentDetails part of the channel resource for the
    # authenticated user's channel.
    channels_response = youtube.channels().list(
        mine=True,
        part='contentDetails'
    ).execute()

    for channel in channels_response['items']:
        # From the API response, extract the playlist ID that identifies the list
        # of videos uploaded to the authenticated user's channel.
        return channel['contentDetails']['relatedPlaylists']['uploads']

    return None

def list_my_uploaded_videos(youtube, uploads_playlist_id):
    video_list = []
    # Retrieve the list of videos uploaded to the authenticated user's channel.
    playlistitems_list_request = youtube.playlistItems().list(
        playlistId=uploads_playlist_id,
        part='snippet',
        maxResults=5
    )

    while playlistitems_list_request:
        playlistitems_list_response = playlistitems_list_request.execute()

        # Print information about each video.
        for playlist_item in playlistitems_list_response['items']:
            title = playlist_item['snippet']['title']
            video_id = playlist_item['snippet']['resourceId']['videoId']
            video_list.append(video_id)

        playlistitems_list_request = youtube.playlistItems().list_next(
            playlistitems_list_request, playlistitems_list_response)
    return video_list

def update_live_broadcast_link(live_broadcast_id, args, ward, num_from, num_to, path_filename = None, filename = None, link_page = None, youtube_id_path = None, verbose = False):
    if(args.host_name is None):
        print("Nothing to update.")
        return()

    if(live_broadcast_id is None):
        print("No BroadcastID, can't update.")
        return()

    ssh = SSHClient()

    # regardless of the file that we're going to be uploading, we need to determine what that filename is going to be
    link_file_path = None
    upload_link_file = False
    if(path_filename is not None):
        link_file_path = path_filename
    else:
        link_file_path = 'public_html/broadcast/' + (filename if (filename is not None) else ward.lower()) + (('_' + args.url_key) if (args.url_key is not None) else '')  + '.html'

    youtube_id_file_path = None
    if(youtube_id_path is not None):
        p = Path(link_file_path).with_suffix('')
        youtube_id_file_path = p.parent / youtube_id_path / p.name
        if(verbose):
            print(f"YouTube ID file target: {youtube_id_file_path}")
        youtube_id_file = open('youtube_id', 'w')
        youtube_id_file.write(live_broadcast_id)
        youtube_id_file.close()

    local_link_file = 'link.html'
    if(link_page is None):
        upload_link_file = True
        link_file = open(local_link_file, 'w')
        link_file.write('<head>\n')
        link_file.write('  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />\n')
        link_file.write('  <meta http-equiv="Pragma" content="no-cache" />\n')
        link_file.write('  <meta http-equiv="Expires" content="0" />\n')
        link_file.write('  <meta http-equiv="Refresh" content="10; URL=https://www.youtube.com/watch?v=' + live_broadcast_id + '" />\n')
        link_file.write('</head>\n')
        link_file.write('<body>\n')
        link_file.write('  <h1>Redirecting in 10 seconds...  /  Redirigiendo en 10 segundos...</h1>\n')
        link_file.write('  <a href=https://www.youtube.com/watch?v=' + live_broadcast_id + '>Click here if not redirected  /  Haga clic aqu&iacute; si no se redirige</a><br><br>\n')
        link_file.write('  The link for the video is  /  El enlace del video es: <br><a href=https://www.youtube.com/watch?v=' + live_broadcast_id + '>https://www.youtube.com/watch?v=' + live_broadcast_id + '</a><br>\n')
        link_file.write('  <br><br>' + str(datetime.now()))
        link_file.write('</body>\n')
        link_file.close()

    if os.path.exists(SSH_RSA_KEY_PASS) and os.path.exists(args.home_dir + '/.ssh/id_rsa'):
        def remote_exists(sftp, path):
            """Return True if remote file exists, False otherwise."""
            try:
                sftp.stat(path)
                return True
            except FileNotFoundError:
                return False
            except IOError:
                return False

        for retry_num in range(NUM_RETRIES):
            exception = None
            tb = None
            try:
                with open(SSH_RSA_KEY_PASS, 'r') as f:
                    ssh_pass = f.read().replace('\n', '')
                    ssh.load_system_host_keys()
                    key = paramiko.RSAKey.from_private_key_file(args.home_dir + '/.ssh/id_rsa', password=ssh_pass)
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(args.host_name, username=args.user_name, pkey=key)

                    if(link_page is not None):
                        sftp = ssh.open_sftp()
                        if remote_exists(sftp, link_file_path):
                            if(verbose):
                                print(f"Remote file already exists: {link_file_path}")
                        else:
                            upload_link_file = True
                            local_link_file = link_page
                            update_ward_name(link_page, ward, num_from, num_to, verbose)
                        sftp.close()
                    with SCPClient(ssh.get_transport()) as scp:
                        if(upload_link_file):
                            scp.put(local_link_file, link_file_path)
                        if(youtube_id_path is not None):
                            scp.put('youtube_id', str(youtube_id_file_path))
                break
            except Exception as exc:
                exception = exc
                tb = traceback.format_exc()
                if(verbose): print('!!Host Key Failure!!')
                gf.sleep(0.5,2)

        if exception:
            if(verbose): print(tb)
            print("SSH Host key failure.")
            if(num_from is not None): sms.send_sms(num_from, num_to, ward +  " Ward stake website host key failure!", verbose)
    else:
        if(num_from is not None): sms.send_sms(num_from, num_to, ward + " Ward YouTube SSH Key and Password files are required!", verbose)
        print("SSH Key or Password file is missing for link upload.")
        exit()
    
    ssh.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Update YouTube Link')
    parser.add_argument('-c','--config-file',type=str,help='JSON Configuration file')
    parser.add_argument('-w','--ward',type=str,help='Name of Ward being broadcast')
    parser.add_argument('-o','--host-name',type=str,help='The address for the web host to upload HTML link forward page to')
    parser.add_argument('-u','--user-name',type=str,help='The username for the web host')
    parser.add_argument('-H','--home-dir',type=str,help='Home directory SSH id_rsa key is stored under')
    parser.add_argument('-U','--url-filename',type=str,help='Use this for web filename instead of Unit name.')
    parser.add_argument('-L','--html-filename',type=str,help='Override default upload path and filename, this must be full path and filename for target webserver')
    parser.add_argument('-k','--url-key',type=str,help='A 4-digit code added after the ward name at the end of the URL')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    ward = args.ward
    num_from = args.num_from
    num_to = args.num_to
    verbose = args.verbose
    link_page = None
    youtube_id_path = None

    if(args.host_name is not None or args.user_name is not None or args.home_dir is not None):
        if(args.host_name is None or args.user_name is None or args.home_dir is None):
            print("If host-name, user-name, or home-dir parameter is defined, all 3 parameters must be defined.")
            exit()
    if(num_from is not None or num_to is not None):
        if(num_from is None or num_to is None):
            print("If SMS notification numbers are defined, both From and To numbers must be defined.")
            exit()

    if(args.config_file is not None):
        if("/" in args.config_file):
            config_file = args.config_file
        else:
            config_file =  os.path.abspath(os.path.dirname(__file__)) + "/" + args.config_file
    if(config_file is not None and os.path.exists(config_file)):
        with open(config_file, "r") as configFile:
            config = json.load(configFile)

            if 'broadcast_ward' in config:
                ward = config['broadcast_ward']
            if 'link_page' in config:
                link_page = config['link_page']
            if 'youtube_id_path' in config:
                youtube_id_path = config['youtube_id_path']
            if 'url_ssh_host' in config:
                args.host_name = config['url_ssh_host']
            if 'url_ssh_username' in config:
                args.user_name = config['url_ssh_username']
            if 'url_ssh_key_dir' in config:
                args.home_dir = config['url_ssh_key_dir']
            if 'notification_text_from' in config:
                num_from = config['notification_text_from']
            if 'notification_text_to' in config:
                num_to = config['notification_text_to']

    if(ward is None):
        print("!!Ward is required argument!!")
        sys.exit("Ward is required argument")

    credentials_file = ward.lower() + '.auth'

    youtube = google_auth.get_authenticated_service(credentials_file, ward, num_from, num_to, 'youtube', 'v3', verbose)

    #while we're at it, lets go ahead and setup the link too
    videos = []

    #attempt to get list of current videos from YouTube, the first video in this list should be the next URL used for live broadcasts
    try:
        uploads_playlist_id = get_my_uploads_list(youtube)
        if uploads_playlist_id:
            videos = list_my_uploaded_videos(youtube, uploads_playlist_id)
        else:
            print("No Videos Found!")
            exit()
    except ():
        #print(traceback.format_exc())
        print("YouTube Failed to get list of videos!")
        exit()

    #make sure link on hillsborostake.org is current
    update_live_broadcast_link(videos[0], args, ward, num_from, num_to, args.html_filename, None, link_page, youtube_id_path, verbose)
