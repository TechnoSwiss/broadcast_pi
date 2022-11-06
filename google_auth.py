#!/usr/bin/python3

# Will store Google API authentication to a file (this is a manual process as you are required to authorize the access via a webpage)
# once authantication is stored to the file, the auth file can be used to establish API access from an automated script

import argparse
import os
import traceback
import pickle

import sms #sms.py local file

import google.oauth2.credentials # pip3 install google-auth-oauthlib
import google_auth_oauthlib.flow
from googleapiclient.discovery import build # pip3 install google-api-python-client
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow

# The CLIENT_SECRETS_FILE variable specifies the name of a file that contains
# the OAuth 2.0 information for this application, including its client_id and
# client_secret. You can acquire an OAuth 2.0 client ID and client secret from
# the {{ Google Cloud Console }} at
# {{ https://cloud.google.com/console }}.
# Please ensure that you have enabled the YouTube Data API for your project.
# For more information about using OAuth2 to access the YouTube Data API, see:
#   https://developers.google.com/youtube/v3/guides/authentication
# For more information about the client_secrets.json file format, see:
#   https://developers.google.com/api-client-library/python/guide/aaa_client_secrets
CLIENT_SECRETS_FILE = 'client_secret.json'

# This OAuth 2.0 access scope allows for read-only access to the authenticated
# user's account, but not other types of account access.
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'

def store_authenticated_service(credentials_file):
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
    print("Before using URL setup tunnel from PC to broadcast system port L8088 localhost:8088")

    credentials = flow.run_local_server(
            host='localhost',
            port=8088,
            authorization_prompt_message='Please visit this URL: {url}',
            success_message='The auth flow is complete; you may close this window.',
            open_browser=False)

    with open(credentials_file, 'wb') as f:
        pickle.dump(credentials, f)

def get_authenticated_service(credentials_file, args):
    # Authorize the request and store authorization credentials.
    if os.path.exists(credentials_file):
        with open(credentials_file, 'rb') as f:
            credentials = pickle.load(f)
    else:
        if(args.num_from is not None): sms.send_sms(args.num_from, args.num_to, args.ward + " Ward YouTube Authentication Required!")
        print("YouTube Authorization Required, please run google_auth.py")
        exit()

    return build(API_SERVICE_NAME, API_VERSION, credentials = credentials)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Store YouTube Authentication')
    parser.add_argument('-w','--ward',type=str,required=True,help="Name of Ward storing authentication for")
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()
  
    credentials_file = args.ward.lower() + '.auth'
  
    store_authenticated_service(credentials_file)
