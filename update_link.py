#!/usr/bin/python3

# this script assume that you have a website hosted somewhere with SSH key based access to the server
# it will grab the first Video ID from YouTube (this assumes that no videos have been uploaded, only Live videos are present)
# and upload an HTML page that will forward to that link

import argparse
import os
import traceback
import re
import pickle

import google_auth # google_auth.py local file
import sms #sms.py local file

import paramiko
from paramiko import SSHClient
from scp import SCPClient
from datetime import datetime

#SSH id_rsa key decryption password file (for SSH upload of HTML file to website for sharing YouTube Unlisted URL
#file is just a single ascii line plain text password
SSH_RSA_KEY_PASS = 'ssh.pass'

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

def update_live_broadcast_link(live_broadcast_id, args, path_filename = None, filename = None, verbose = False):
    if(args.host_name is None):
        print("Nothing to update.")
        return()

    if(live_broadcast_id is None):
        print("No BroadcastID, can't update.")
        return()

    link_file = open('link.html', 'w')
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

    ssh = SSHClient()
    
    if os.path.exists(SSH_RSA_KEY_PASS) and os.path.exists(args.home_dir + '/.ssh/id_rsa'):
        try:
            with open(SSH_RSA_KEY_PASS, 'r') as f:
                ssh_pass = f.read().replace('\n', '')
                ssh.load_system_host_keys()
                key = paramiko.RSAKey.from_private_key_file(args.home_dir + '/.ssh/id_rsa', password=ssh_pass)
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(args.host_name, username=args.user_name, pkey=key)
                with SCPClient(ssh.get_transport()) as scp:
                    if(path_filename is not None):
                        scp.put('link.html', path_filename)
                    else:
                        scp.put('link.html', 'public_html/broadcast/' + (filename if (filename is not None) else args.ward.lower()) + (('_' + args.url_key) if (args.url_key is not None) else '')  + '.html')
        except:
            if(verbose): print(traceback.format_exc())
            print("SSH Host key failure.")
            if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward +  " Ward stake website host key failure!", verbose)
            exit()
    else:
        if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " Ward YouTube SSH Key and Password files are required!", verbose)
        print("SSH Key or Password file is missing for link upload.")
        exit()
    
    ssh.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Update YouTube Link')
    parser.add_argument('-w','--ward',type=str,required=True,help='Name of Ward being broadcast')
    parser.add_argument('-o','--host-name',type=str,help='The address for the web host to upload HTML link forward page to')
    parser.add_argument('-u','--user-name',type=str,help='The username for the web host')
    parser.add_argument('-D','--home-dir',type=str,help='Home directory SSH id_rsa key is stored under')
    parser.add_argument('-U','--url-filename',type=str,help='Use this for web filename instead of Unit name.')
    parser.add_argument('-K','--html-filename',type=str,help='Override default upload path and filename, this must be full path and filename for target webserver')
    parser.add_argument('-k','--url-key',type=str,help='A 4-digit code added after the ward name at the end of the URL')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()
 
    if(args.host_name is not None or args.user_name is not None or args.home_dir is not None):
        if(args.host_name is None or args.user_name is None or args.home_dir is None):
            print("If host-name, user-name, or home-dir parameter is defined, all 3 parameters must be defined.")
            exit()
    if(args.num_from is not None or args.num_to is not None):
        if(args.num_from is None or args.num_to is None):
            print("If SMS notification numbers are defined, both From and To numbers must be defined.")
            exit()

    credentials_file = args.ward.lower() + '.auth'
  
    youtube = google_auth.get_authenticated_service(credentials_file, args)
    
    #while we're at it, lets go ahead and setup the link too
    videos = []
  
    #attempt to get list of curreny videos from YouTube, the first video in this list should be the next URL used for live broadcasts
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
    update_live_broadcast_link(videos[0], args, args.html_filename)
