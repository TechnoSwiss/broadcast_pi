#!/usr/bin/python3

# this script assume that you have a website hosted somewhere with SSH key based access to the server
# it will grab the first Video ID from YouTube (this assumes that no videos have been uploaded, only Live videos are present)
# and upload an HTML page that will forward to that link

import argparse
import os
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

def update_live_broadcast_link(live_broadcast_id, args):
    link_file = open('link.html', 'w')
    link_file.write('<head>\n')
    link_file.write('  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />\n')
    link_file.write('  <meta http-equiv="Pragma" content="no-cache" />\n')
    link_file.write('  <meta http-equiv="Expires" content="0" />\n')
    link_file.write('  <meta http-equiv="Refresh" content="10; URL=https://www.youtube.com/watch?v=' + live_broadcast_id + '" />\n')
    link_file.write('</head>\n')
    link_file.write('<body>\n')
    link_file.write('  <h1>Redirecting in 10 seconds...</h1>\n')
    link_file.write('  <a href=https://www.youtube.com/watch?v=' + live_broadcast_id + '>Click here if not redirected</a><br><br>\n')
    link_file.write('  The link for the video is: <a href=https://www.youtube.com/watch?v=' + live_broadcast_id + '>https://www.youtube.com/watch?v=' + live_broadcast_id + '</a><br>\n')
    link_file.write('  <h1>Redirigiendo en 10 segundos...</h1>\n')
    link_file.write('  <a href=https://www.youtube.com/watch?v=' + live_broadcast_id + '>Haga clic aqu&iacute; si no se redirige</a><br><br>\n')
    link_file.write('  El enlace del video es: <a href=https://www.youtube.com/watch?v=' + live_broadcast_id + '>https://www.youtube.com/watch?v=' + live_broadcast_id + '</a><br>\n')
    link_file.write('  <br><br><br><br>' + str(datetime.now()))
    link_file.write('</body>\n')
    link_file.close()

    ssh = SSHClient()
    
    if os.path.exists(SSH_RSA_KEY_PASS):
        try:
            with open(SSH_RSA_KEY_PASS, 'r') as f:
                ssh_pass = f.read().replace('\n', '')
                ssh.load_system_host_keys()
                key = paramiko.RSAKey.from_private_key_file(args.home_dir + '/.ssh/id_rsa', password=ssh_pass)
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(args.host_name, username=args.user_name, pkey=key)
                with SCPClient(ssh.get_transport()) as scp:
                    scp.put('link.html', 'public_html/broadcast/' + args.ward.lower() + '_' + args.url_key + '.html')
        except:
            if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward +  " Ward stake website host key failure!")
            print("SSH Host key failure.")
            exit()
    else:
        if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " Ward YouTube SSH Key Required!")
        print("SSH Key Missing for link upload.")
        exit()
    
    ssh.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Update YouTube Link')
    parser.add_argument('-w','--ward',type=str,required=True,help='Name of Ward being broadcast')
    parser.add_argument('-o','--host-name',type=str,help='The address for the web host to upload HTML link forward page to')
    parser.add_argument('-u','--user-name',type=str,help='The username for the web host')
    parser.add_argument('-D','--home-dir',type=str,help='Home directory SSH id_rsa key is stored under')
    parser.add_argument('-k','--url-key',type=str,required=True,help='The 4-digit code at the end of the URL')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    args = parser.parse_args()
  
    credentials_file = args.ward.lower() + '.auth'
  
    youtube = google_auth.get_authenticated_service(credentials_file)
    
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
        print("YouTube Failed to get list of videos!")
        exit()

    #make sure link on hillsborostake.org is current
    update_live_broadcast_link(videos[0], args)