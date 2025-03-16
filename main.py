#!/usr/bin/env python3
"""
Gmail MCP server for accessing Gmail inbox using the MCP protocol.
"""
import os
import asyncio
import json
import base64
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from mcp.server.fastmcp import FastMCP

# Gmail API scopes
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

script_path = os.path.dirname(os.path.abspath(__file__))
# Path to store the OAuth token
TOKEN_FILE = script_path + '/token.json'
# Path to credentials file (you'll need to download this from Google Cloud Console)
CREDENTIALS_FILE = script_path + '/credentials.json'


def get_gmail_credentials():
    """Get and refresh OAuth credentials for Gmail API."""
    creds = None
    
    # Load existing token if available
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_info(
            json.load(open(TOKEN_FILE)), SCOPES)
    
    # If no valid credentials available or they're expired, authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Make sure credentials.json exists
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Missing {CREDENTIALS_FILE}. Please download it from Google Cloud Console.")
            
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Save the credentials for the next run
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    
    return creds


# Create a FastMCP server
mcp = FastMCP("gmail-mcp")

# Gmail service instance
gmail_service = None

async def setup_gmail():
    """Initialize Gmail API service on startup."""
    global gmail_service
    credentials = get_gmail_credentials()
    gmail_service = build('gmail', 'v1', credentials=credentials)
    print("Gmail API service initialized successfully!")

async def get_threads(limit: int = 20) -> List[Dict[str, Any]]:
    """
    Get recent email threads.
    
    Args:
        limit: Maximum number of threads to return
    
    Returns:
        A list of email thread objects
    """
    results = gmail_service.users().threads().list(
        userId='me', maxResults=limit).execute()
    
    threads = []
    for thread_data in results.get('threads', []):
        thread_id = thread_data['id']
        thread_detail = gmail_service.users().threads().get(
            userId='me', id=thread_id).execute()
        
        # Get the subject from the first message
        first_message = thread_detail['messages'][0]
        headers = {h['name']: h['value'] for h in first_message['payload']['headers']}
        subject = headers.get('Subject', '(No Subject)')
        
        # Calculate unread count
        unread_count = sum(1 for msg in thread_detail['messages'] 
                           if 'UNREAD' in msg.get('labelIds', []))
        
        # Get timestamp and format it
        timestamp = int(thread_detail['messages'][-1]['internalDate']) // 1000  # Convert to seconds
        date_str = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
        
        # Create thread object
        thread_obj = {
            "id": thread_id,
            "subject": subject,
            "preview": subject,  # Using subject as preview
            "unread_count": unread_count,
            "last_updated": date_str,
            "message_count": len(thread_detail['messages'])
        }
        threads.append(thread_obj)
    
    return threads

async def get_messages(thread_id: str) -> List[Dict[str, Any]]:
    """
    Get messages in a thread.
    
    Args:
        thread_id: The ID of the thread to fetch messages from
    
    Returns:
        A list of email message objects
    """
    thread = gmail_service.users().threads().get(
        userId='me', id=thread_id).execute()
    
    messages = []
    for msg_data in thread['messages']:
        msg_id = msg_data['id']
        headers = {h['name']: h['value'] for h in msg_data['payload']['headers']}
        
        # Extract sender information
        from_header = headers.get('From', '')
        sender_name = from_header.split('<')[0].strip()
        if '<' in from_header and '>' in from_header:
            sender_email = from_header.split('<')[1].split('>')[0]
        else:
            sender_email = from_header
        
        # Get message body
        body = ""
        if 'parts' in msg_data['payload']:
            for part in msg_data['payload']['parts']:
                if part['mimeType'] == 'text/plain' and 'data' in part.get('body', {}):
                    body_bytes = base64.urlsafe_b64decode(
                        part['body']['data'].encode('ASCII'))
                    body = body_bytes.decode('utf-8')
                    break
        elif 'body' in msg_data['payload'] and 'data' in msg_data['payload']['body']:
            body_bytes = base64.urlsafe_b64decode(
                msg_data['payload']['body']['data'].encode('ASCII'))
            body = body_bytes.decode('utf-8')
        
        # Format timestamp
        timestamp = int(msg_data['internalDate']) // 1000  # Convert to seconds
        date_str = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
        
        # Create message object
        message = {
            "id": msg_id,
            "thread_id": thread_id,
            "body": body,
            "sender": {
                "name": sender_name,
                "email": sender_email
            },
            "timestamp": date_str,
            "subject": headers.get('Subject', '(No Subject)')
        }
        messages.append(message)
    
    return messages


async def test_gmail_access():
    """Test Gmail API access by fetching and displaying some threads."""
    # Initialize Gmail API manually for testing
    await setup_gmail()
    
    try:
        threads = await get_threads(limit=5)
        print(f"Found {len(threads)} email threads:")
        for thread in threads:
            print(f"Thread: {thread['subject']} (Unread: {thread['unread_count']})")
            
            # Fetch messages for the first thread
            if threads and threads[0]['id']:
                messages = await get_messages(threads[0]['id'])
                print(f"\nMessages in thread '{threads[0]['subject']}':")
                for msg in messages:
                    print(f"From: {msg['sender']['name']} <{msg['sender']['email']}>")
                    print(f"Date: {msg['timestamp']}")
                    print(f"Preview: {msg['body'][:100]}...\n" if msg['body'] else "No body content\n")
                break
    except Exception as e:
        print(f"Error accessing Gmail: {e}")

@mcp.tool()
async def get_gmail_content(limit: int =20) -> str:
    """
    Fetch Gmail threads with a given limit and return as JSON string

    Args:
    limit: The limit of threads you want to fetch
    Returns:
    A JSON string containing all threads and their messages
    """
    # Initialize Gmail API if needed
    global gmail_service
    if gmail_service is None:
        credentials = get_gmail_credentials()
        gmail_service = build('gmail', 'v1', credentials=credentials)
        print("Gmail API service initialized!")
    
    try:
        # Get threads up to the specified limit
        threads = await get_threads(limit=limit)
        
        # Create a dictionary to store all thread data
        result_dict = {"threads": []}
        
        # Process each thread
        for thread in threads:
            thread_id = thread["id"]
            
            # Get all messages in this thread
            messages = await get_messages(thread_id)
            
            # Create a thread object with its messages
            thread_with_messages = {
                "thread_info": thread,
                "messages": messages
            }
            
            # Add this thread to our result
            result_dict["threads"].append(thread_with_messages)
        
        # Convert the dictionary to a JSON string
        return json.dumps(result_dict, ensure_ascii=False, indent=2)
    
    except Exception as e:
        error_message = f"Error accessing Gmail: {str(e)}"
        print(error_message)
        return json.dumps({"error": error_message})


def main():
    """Run the Gmail MCP server with different modes."""
    print("Starting Gmail MCP server...")
    
    # Check if we should run the test mode
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        # Test mode - run the test function
        asyncio.run(test_gmail_access())
    else:
        # Server mode - start the MCP server directly with FastMCP's method
        # This avoids the async loop conflict
        print("Starting MCP server...")
        mcp.run()


if __name__ == "__main__":
    main()
