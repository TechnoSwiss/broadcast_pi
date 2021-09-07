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
def create_live_event(youtube, title, description, starttime, duration, thumbnail, ward, num_from = None, num_to = None, verbose = False):
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
                "scheduledStartTime": starttime.strftime('%Y-%m-%dT%H:%M:%SZ'),
                "scheduledEndTime": endtime.strftime('%Y-%m-%dT%H:%M:%SZ'),
              },
              "status": {
                "privacyStatus": "unlisted",
                "selfDeclaredMadeForKids": True,
              }
            }
        ).execute()
    except:
        if(verbose): print(traceback.format_exc())
        print("Failed to insert new Broadcast")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to insert new broadcast!", verbose)
        return(None)

    videoID = insert_broadcast['id']

    try:
        update_category = youtube.videos().update(
            part="snippet",
            body={
                "id": videoID,
                "snippet":{
                    "categoryId": 29,
                    "title": title, # this doesn't work if the title isn't included
                    "description": description
                },
            } if(description is not None) else {
                 "id": videoID,
                "snippet":{
                    "categoryId": 29,
                    "title": title # this doesn't work if the title isn't included
                },
            }
        ).execute()
    except:
        if(verbose): print(traceback.format_exc())
        print("Failed to update Catagory")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to update new broadcast catagory!", verbose)
        #return(None) #failure at this point doesn't render the broadcast unsuable, so send error but continue
    
    if(thumbnail is not None and os.path.exists(thumbnail)):
        try:
            set_thumbnail = youtube.thumbnails().set(
                videoId=videoID,
                media_body=MediaFileUpload(thumbnail, mimetype='image/jpeg',chunksize=-1, resumable=True)
            ).execute()
        except:
            if(verbose): print(traceback.format_exc())
            print("Failed to update Thumbnail")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " failed to update thumbnail!", verbose)
            #return(None) #failure at this point doesn't render the broadcast unsuable, so send error but continue
    return(videoID)

# this is going to grab the closest broadcast scheduled based on the time this script gets run. Ideally if only this script is getting used to drive the broadcasts there will only be one broadcast returned, if multiple broadcasts are scheduled for the same time, this will return the first in the list which may not be what you want. The html page will get updated with this link, and assuming you bind the stream based on the ID returned here, everything should still be pointing at the same thing, but results might not be as expected. We would like to avoid including completed videos in this list however, because we can't send a stream out to them.
def get_next_broadcast(youtube, ward, num_from = None, num_to = None, verbose = False):
    videos = {}
    nextPage = 0
    while(nextPage is not None):
        try:
            list_broadcasts = youtube.liveBroadcasts().list(
                part='id,snippet,status',
                broadcastType='all',
                mine=True
            ).execute() if nextPage == 0 else youtube.liveBroadcasts().list(
                part='id,snippet,status',
                broadcastType='all',
                mine=True,
                pageToken=nextPage
            ).execute()
        except:
            if(verbose): print(traceback.format_exc())
            print("Failed to get next broadcast")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " failed to get next broadcast!", verbose)
            return(None)
        
        if(len(list_broadcasts['items']) > 0 and 'nextPageToken' in list_broadcasts.keys()):
            nextPage = list_broadcasts['nextPageToken']
        else:
            nextPage = None

        for video in list_broadcasts['items']:
            # we can only send a stream to a video that's either ready or live
            # however to have better control over which video we're sending the
            # stream to we're should only be binding the broadcast just before
            # we start sending the stream, in this case we're looking for a
            # video thats status is created then we can bind the stream 
            # to that broadcast
            if(video['status']['lifeCycleStatus'] == 'ready' or
               video['status']['lifeCycleStatus'] == 'live' or
               video['status']['lifeCycleStatus'] == 'created'):
                starttime = dateutil.parser.parse(video['snippet']['scheduledStartTime'])
                delta = abs((starttime - dt.datetime.now().replace(tzinfo=tz.tzlocal())).total_seconds())
                videos[video['id']] = delta

    if(len(videos) > 0):
        return(min(videos, key=videos.get))
    else:
        return(None)

def get_live_broadcast(youtube, ward, num_from = None, num_to = None, verbose = False):

    videos = get_broadcasts(youtube, ward, num_from, num_to, verbose)
    live_count = 0
    if(videos is not None):
        for video in videos:
            if(videos[video] == "live"):
                live_id = video
                live_count += 1

    if(live_count > 1):
        print("More than one live  broadcasts")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " more than one live broadcasts!", verbose)
        return(None)

    if(live_count == 1):
        return(live_id)
    else:
        return(None)

def get_broadcasts(youtube, ward, num_from = None, num_to = None, verbose = False):
    videos = {}
    nextPage = 0
    while(nextPage is not None):
        try:
            list_broadcasts = youtube.liveBroadcasts().list(
                part='id,status',
                broadcastStatus='all',
            ).execute() if nextPage == 0 else youtube.liveBroadcasts().list(
                part='id,status',
                broadcastStatus='all',
                pageToken=nextPage
            ).execute()
        except:
            if(verbose): print(traceback.format_exc())
            print("Failed to get list of broadcasts")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " failed to get list of broadcasts!", verbose)
            return(None)
        
        if(len(list_broadcasts['items']) > 0 and 'nextPageToken' in list_broadcasts.keys()):
            nextPage = list_broadcasts['nextPageToken']
        else:
            nextPage = None

        for video in list_broadcasts['items']:
            videos[video['id']] = video['status']['lifeCycleStatus']

    return(videos)

def get_broadcast_status(youtube, videoID, ward, num_from = None, num_to = None, verbose = False):
    try:
        broadcast = youtube.liveBroadcasts().list(
            part='status',
            broadcastType='all',
            id=videoID
        ).execute()
        return(broadcast['items'][0]['status']['lifeCycleStatus'])

    except:
        if(verbose): print(traceback.format_exc())
        print("Failed to get broadcast status")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to get broadcast status!", verbose)
        return(None)

def create_stream(youtube, ward, num_from = None, num_to = None, verbose = False, stream_name = 'Default'):
    try:
        getStream = youtube.liveStreams().insert(
            part="snippet,contentDetails,cdn",
            body={
              "contentDetails": {
                "isReusable": True,
              },
              "snippet": {
                "title": stream_name,
              },
              "cdn": {
                "frameRate": "30fps",
                "resolution": "Variable",
                "ingestionType": "rtmp",
              }
            }
        ).execute()
    except:
        if(verbose): print(traceback.format_exc())
        print("Failed to get Stream")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to get stream!", verbose)
        return(None)
    return(getStream['items'][0]['id'])

# liveStream is the enpoint that ffmpeg is going to be sending the rtsp stream at, this is the target of the stream key from YouTube Studio, but for binding a broadcast we need to use the stream ID not the stream key
def get_stream(youtube, ward, num_from = None, num_to = None, verbose = False, stream_num = 0):
    try:
        getStream = youtube.liveStreams().list(
            part='id',
            mine=True
        ).execute()
    except:
        if(verbose): print(traceback.format_exc())
        print("Failed to get Stream")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to get stream!", verbose)
        return(None)
    #streams seem to be listed in reverse order of creation
    #so the default stream always seems to be last, we're going to
    #reverse the list order so the stream number matches the creation order
    if(stream_num > (len(getStream['items']) -1) or stream_num < 0):
        print("Invalid stream number requested")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " invalid stream number requested!", verbose)
    getStream['items'].reverse()
    return(getStream['items'][stream_num]['id'])

# after we've created a broadcast it will not be ready to receive the stream until we bind a stream to the broadcast. This is done automatically when we open the broadcast in YouTube studio, but to automate it we need to bind it here
def bind_broadcast(youtube, videoID, streamID, ward, num_from = None, num_to = None, verbose = False):
    if(videoID is None):
        print("Missing videoID, can't perform bind")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " missing videoID, can't perform bind!", verbose)
        return()

    try:
        bindBroadcast = youtube.liveBroadcasts().bind(
            id=videoID,
            part='snippet,status',
            streamId=streamID,
        ).execute()
    except:
        if(verbose): print(traceback.format_exc())
        print("Failed to bind broadcasts")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to bind broadcast!", verbose)

# AutoStop is being disabled so that if the stream has problems (like the internet drops for a minute) YouTube won't automatically close out the video. This does mean that we have to manualy close it out when we're done with the broadcast/
def stop_broadcast(youtube, videoID, ward, num_from = None, num_to = None, verbose = False):
    try:
        stopBroadcast = youtube.liveBroadcasts().transition(
            broadcastStatus='complete',
            id=videoID,
            part='snippet,status'
        ).execute()

    except:
        if(verbose): print(traceback.format_exc())
        print("Failed to stop Broadcast")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to stop broadcast!", verbose)

# Gets the current number of viewers of the video specified by videoID, so those numbers can be reported out
def get_concurrent_viewers(youtube, videoID, ward, num_from = None, num_to = None, verbose = False):
    retries = 2
    while(retries > 0):
        try:
            liveDetails = youtube.videos().list(
                part='liveStreamingDetails',
                id=videoID
            ).execute()
            if('concurrentViewers' in liveDetails['items'][0]['liveStreamingDetails']):
                currentViewers = liveDetails['items'][0]['liveStreamingDetails']['concurrentViewers']
            else:
                currentViewers = 0
            retries = 0 # we've successfully gotten concurrent viewers, no need to retry
        except:
            retries = retries - 1
            if(retries > 0):
                continue # we'll retry getting concurrent viewers before we send an error message
            currentViewers = -1
            if(verbose): print(traceback.format_exc())
            print("Failed to get concurrent viewers")
            if(num_from is not None and num_to is not None):
                sms.send_sms(num_from, num_to, ward + " failed to get concurrent viewers!", verbose)

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
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    credentials_file = args.ward.lower() + '.auth'

    #authenticate with YouTube API
    youtube = google_auth.get_authenticated_service(credentials_file, args)

    #print(create_stream(youtube, args.ward))

    #create_live_event(youtube, args.title, starttime, args.run_time, args.thumbnail, args.ward)
    print(get_next_broadcast(youtube, args.ward))
    print(get_broadcasts(youtube, args.video_id, args.ward))
    #print(get_live_broadcast(youtube, args.ward))
    #print(get_stream(youtube, args.ward))
    #bind_broadcast(youtube, args.video_id, "VY-K6BTl3Wjxg61zO9-s0A1599607954801518", args.ward)
    #stop_broadcast(youtube, "5Xngi_F9UIk", args.ward)
    #print(get_concurrent_viewers(youtube, args.video_id, args.ward))
