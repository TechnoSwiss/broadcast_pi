#!/usr/bin/python3

# Will store Google API authentication to a file (this is a manual process as you are required to authorize the access via a webpage)
# once authantication is stored to the file, the auth file can be used to establish API access from an automated script

import argparse
import os
import traceback
import json

from tzlocal import get_localzone # pip3 install tzlocal
from datetime import datetime

import sms #sms.py local file

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials # pip3 install google-auth-oauthlib
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build # pip3 install google-api-python-client
from google.auth.exceptions import RefreshError


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
# this OAuth 2.0 is for the application, not a specific account, you'll authenticate
# this against the account as part of the store_authenticated_service function
CLIENT_SECRETS_FILE = 'client_secret.json'

# This OAuth 2.0 access scope allows for read-only access to the authenticated
# user's account, but not other types of account access.
SCOPES = [
    'https://www.googleapis.com/auth/youtube.force-ssl',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/drive.file'
    ]
API_SERVICE_YOUTUBE = 'youtube'
API_SERVICE_GDRIVE = 'drive'
API_VERSION = 'v3'

def store_authenticated_service(credentials_file, ward, num_from, num_to, scopes = SCOPES, verbose = False):
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes)
    print("Before using URL setup tunnel from PC to broadcast system port L8088 localhost:8088")

    credentials = flow.run_local_server(
            host='localhost',
            port=8088,
            authorization_prompt_message='Please visit this URL: {url}',
            success_message='The auth flow is complete; you may close this window.',
            open_browser=False)

    with open(credentials_file, 'w') as f:
        f.write(credentials.to_json())

    local_tz = get_localzone()
    local_expiry = credentials.expiry.astimezone(local_tz)
    print(f"✅ Credentials stored. Token expires at (local time): {local_expiry.strftime('%Y-%m-%d %H:%M:%S %Z')}")

def refresh_if_needed(credentials, credentials_file, ward, num_from, num_to, verbose=False):
    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            # Save the refreshed credentials
            with open(credentials_file, 'w') as f:
                f.write(credentials.to_json())
            if verbose:
                local_tz = get_localzone()
                local_expiry = credentials.expiry.astimezone(local_tz)
                print(f"✅ Token refreshed; valid until {local_expiry.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        except RefreshError as e:
            print("❌ Token refresh failed:", str(e))
            if(num_from is not None and num_to is not None): sms.send_sms(num_from, num_to, ward + " Ward Google API Refresh Token failed!")
            raise
    return credentials

def get_authenticated_service(credentials_file, ward, num_from, num_to, api_service = API_SERVICE_YOUTUBE, api_version = API_VERSION, verbose = False):
    # Authorize the request and store authorization credentials.
    if os.path.exists(credentials_file):
        with open(credentials_file, 'r') as f:
            credentials = Credentials.from_authorized_user_info(json.load(f), SCOPES)
        credentials = refresh_if_needed(credentials, credentials_file, ward, num_from, num_to, verbose)
    else:
        if(num_from is not None and num_to is not None): sms.send_sms(num_from, num_to, ward + " Ward Google API Authentication Required!")
        print("Google API Authorization Required, please run google_auth.py")
        exit()

    return build(api_service, api_version, credentials = credentials)

def get_credentials_google_sheets(credentials_file, ward, num_from, num_to, verbose = False):
    # Authorize the request and store authorization credentials.
    if os.path.exists(credentials_file):
        with open(credentials_file, 'r') as f:
            credentials = Credentials.from_authorized_user_info(json.load(f), SCOPES)
        credentials = refresh_if_needed(credentials, credentials_file, ward, num_from, num_to, verbose)
    else:
        if(num_from is not None and num_to is not None): sms.send_sms(num_from, num_to, ward + " Ward Google API Authentication Required!")
        print("Google API Authorization Required, please run google_auth.py")
        exit()

    return credentials

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Store YouTube Authentication')
    parser.add_argument('-c','--config-file',type=str,help='JSON Configuration file')
    parser.add_argument('-w','--ward',type=str,help="Name of Ward storing authentication for")
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    ward = args.ward
    num_from = args.num_from
    num_to = args.num_to
    verbose = args.verbose

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
            if 'notification_text_from' in config:
                num_from = config['notification_text_from']
            if 'notification_text_to' in config:
                num_to = config['notification_text_to']

    credentials_file = ward.lower() + '.auth'

    store_authenticated_service(credentials_file, ward, num_from, num_to, SCOPES, verbose)
