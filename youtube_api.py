#!/usr/bin/python3

import argparse
import os
import traceback
import sys
import datetime as dt
import dateutil.parser # pip install python-dateutil
from dateutil import tz # pip install python-dateutil

import googleapiclient.discovery
import googleapiclient.errors
from googleapiclient.http import MediaFileUpload

import google_auth # google_auth.py local file
import sms #sms.py local file

# YouTube no longer has a default broadcast, so the only ways to have a new broadcast created are to use the GoLive button in YouTube Studio, or to use the API and create a new live event. AutoStart is selected so the broadcast goes live as soon as you start streaming data to it. AutoStop is turned off so that if something causs a hiccup in the stream, YouTube won't close out the video before you're ready (had that happen on a few occasions) Because this stream is going out to families, including children I've set this to mark the videos as made for children. This causes many things to be tuned off (like monatization, personalized ads, comments and live chat) but I don't think any of those effect what we're trying to accomplish here.
def create_live_event(youtube, title, starttime, duration, thumbnail, ward, num_from = None, num_to = None):
    starttime = starttime.replace(tzinfo=tz.tzlocal())
    starttime = starttime.astimezone(tz.tzutc())
    duration = dt.datetime.strptime(duration,'%H:%M:%S')
    endtime = starttime + dt.timedelta(hours=duration.hour, minutes=duration.minute, seconds=duration.second)
    try:
        insert_broadcast = youtube.liveBroadcasts().insert(
            part="snippet,contentDetails,status",
            body={
              "contentDetails": {
                "enableClosedCaptions": False,
                "enableContentEncryption": True,
                "enableDvr": True,
                "enableAutoStart": True,
                "enableAutoStop": False,
              },
              "snippet": {
                "title": title,
                "scheduledStartTime": starttime.strftime('%Y-%m-%d %H:%M:%SZ'),
                "scheduledEndTime": endtime.strftime('%Y-%m-%d %H:%M:%SZ'),
              },
              "status": {
                "privacyStatus": "unlisted",
                "selfDeclaredMadeForKids": True,
              }
            }
        ).execute()
    except:
        #print(traceback.format_exc())
        print("Failed to insert new Broadcast")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to insert new broadcast!")
        return(None)

    videoID = insert_broadcast['id']

    try:
        update_category = youtube.videos().update(
            part="snippet",
            body={
                "id": videoID,
                "snippet":{
                    "categoryId": 29,
                    "title": title # this doesn't work if the title isn't included
                },
            }
        ).execute()
    except:
        #print(traceback.format_exc())
        print("Failed to update Catagory")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to update new broadcast catagory!")
        return(None)
    
    if(thumbnail is not None and os.path.exists(thumbnail)):
        try:
            set_thumbnail = youtube.thumbnails().set(
                videoId=videoID,
                media_body=MediaFileUpload(thumbnail, mimetype='image/jpeg',chunksize=-1, resumable=True)
            ).execute()
        except:
            #print(traceback.format_exc())
            print("Failed to update Thumbnail")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " failed to update thumbnail!")
            return(None)
    return(videoID)

# this is going to grab the closest broadcast scheduled based on the time this script gets run. Ideally if only this script is getting used to drive the broadcasts there will only be one broadcast returned, if multiple broadcasts are scheduled for the same time, this will return the first in the list which may not be what you want. The html page will get updated with this link, and assuming you bind the stream based on the ID returned here, everything should still be pointing at the same thing, but results might not be as expected. We would like to avoid including completed videos in this list however, because we can't send a stream out to them.
def get_next_broadcast(youtube, ward, num_from = None, num_to = None):
    try:
        list_broadcasts = youtube.liveBroadcasts().list(
            part='id,snippet,status',
            broadcastType='all',
            mine=True
        ).execute()
    except:
        #print(traceback.format_exc())
        print("Failed to get list of upcoming broadcasts")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to get next broadcast!")
        return(None)

    videos = {}
    for video in list_broadcasts['items']:
        # we'll only be able to send a stream to a broadcast that is ready or live, so no reason to list any videos that aren't in either of those states
        if(video['status']['lifeCycleStatus'] != 'ready' and video['status']['lifeCycleStatus'] != 'live'):
            continue
        starttime = dateutil.parser.parse(video['snippet']['scheduledStartTime'])
        # the default GoLive broadcast can be determined by its start date of 1970-01-01T00:00:00Z
        #if(starttime.strftime('%Y-%m-%dT%H:%M:%SZ') == '1970-01-01T00:00:00Z'):
        #    return(video['id'])
        delta = abs((starttime - dt.datetime.now().replace(tzinfo=tz.tzlocal())).total_seconds())
        videos[video['id']] = delta

    if(len(videos) > 0):
        return(min(videos, key=videos.get))
    else:
        return(None)

def get_broadcasts(youtube, ward, num_from = None, num_to = None):
    try:
        list_broadcasts = youtube.liveBroadcasts().list(
            part='id,status',
            mine=True
        ).execute()
    except:
        #print(traceback.format_exc())
        print("Failed to get list of broadcasts")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to get next broadcast!")
        return(None)

    videos = {}
    for video in list_broadcasts['items']:
        videos[video['id']] = video['status']['lifeCycleStatus']

    return(videos)

def get_broadcast_status(youtube, videoID, ward, num_from = None, num_to = None):
    try:
        broadcast = youtube.liveBroadcasts().list(
            part='status',
            broadcastType='all',
            id=videoID
        ).execute()
        return(broadcast['items'][0]['status']['lifeCycleStatus'])

    except:
        #print(traceback.format_exc())
        print("Failed to get broadcasts status")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to get next broadcast!")
        return(None)

# liveStream is the enpoint that ffmpeg is going to be sending the rtsp stream at, this is the target of the stream key from YouTube Studio, but for binding a broadcast we need to use the stream ID not the stream key
def get_stream(youtube, ward, num_from = None, num_to = None):
    try:
        getStream = youtube.liveStreams().list(
            part='id',
            mine=True
        ).execute()
    except:
        #print(traceback.format_exc())
        print("Failed to get Stream")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to get stream!")
        return(None)
    return(getStream['items'][0]['id'])

# after we've created a broadcast it will not be ready to receive the stream until we bind a stream to the broadcast. This is done automatically when we open the broadcast in YouTube studio, but to automate it we need to bind it here
def bind_broadcast(youtube, videoID, streamID, ward, num_from = None, num_to = None):
    try:
        bindBroadcast = youtube.liveBroadcasts().bind(
            id=videoID,
            part='snippet,status',
            streamId=streamID,
        ).execute()
    except:
        #print(traceback.format_exc())
        print("Failed to bind broadcasts")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to bind broadcast!")

# AutoStop is being disabled so that if the stream has problems (like the internet drops for a minute) YouTube won't automatically close out the video. This does mean that we have to manualy close it out when we're done with the broadcast/
def stop_broadcast(youtube, videoID, ward, num_from = None, num_to = None):
    try:
        stopBroadcast = youtube.liveBroadcasts().transition(
            broadcastStatus='complete',
            id=videoID,
            part='snippet,status'
        ).execute()

    except:
        #print(traceback.format_exc())
        print("Failed to stop Broadcast")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to stop broadcast!")

# Gets the current number of viewers of the video specified by videoID, so those numbers can be reported out
def get_concurrent_viewers(youtube, videoID, ward, num_from = None, num_to = None):
    try:
        liveDetails = youtube.videos().list(
            part='liveStreamingDetails',
            id=videoID
        ).execute()
        if('concurrentViewers' in liveDetails['items'][0]['liveStreamingDetails']):
            currentViewers = liveDetails['items'][0]['liveStreamingDetails']['concurrentViewers']
        else:
            currentViewers = 0
    except:
        currentViewers = -1
        #print(traceback.format_exc())
        print("Unable to get concurrent viewers")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to get current viewers!")

    return(currentViewers)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='YouTube API Interaction')
    parser.add_argument('-w','--ward',type=str,required=True,help='Name of Ward being broadcast')
    parser.add_argument('-i','--title',type=str,default='Live Stream',help='Broadcast Title')
    parser.add_argument('-n','--thumbnail',type=str,default='thumbnail.jpg',help='Path and filename for the JPG image that will be the video thumbnail')
    parser.add_argument('-t','--run-time',type=str,default='1:10:00',help='Broadcast runtime in HH:MM:SS')
    parser.add_argument('-I','--video-id',type=str,help='YouTube Video ID')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    args = parser.parse_args()

    credentials_file = args.ward.lower() + '.auth'

    #authenticate with YouTube API
    youtube = google_auth.get_authenticated_service(credentials_file, args)

    #create_live_event(youtube, args.title, starttime, args.run_time, args.thumbnail, args.ward)
    #print(get_next_broadcast(youtube, args.ward))
    #print(get_broadcasts(youtube, args.video_id, args.ward))
    #print(get_stream(youtube, args.ward))
    #bind_broadcast(youtube, args.video_id, "VY-K6BTl3Wjxg61zO9-s0A1599607954801518", args.ward)
    #start_broadcast(youtube, args.video_id, args.ward)
    #print(get_concurrent_viewers(youtube, args.video_id, args.ward))
