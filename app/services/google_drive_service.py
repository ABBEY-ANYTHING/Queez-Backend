"""
Google Drive Service for uploading videos to central Queez folder
Uses Service Account authentication
"""
import os
import json
import mimetypes
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from io import BytesIO

# Configuration
SCOPES = ['https://www.googleapis.com/auth/drive.file']
QUEEZ_FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID', '16DdrQsK0_m_jgeSAlxBUryzulHQEvyCB')

# Load credentials from environment variable or file
def _get_credentials_info():
    # First try environment variable (JSON string)
    creds_json = os.getenv('GOOGLE_DRIVE_CREDENTIALS')
    if creds_json:
        return json.loads(creds_json)
    
    # Fallback to file
    creds_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 
                              'credentials', 'google_drive_service_account.json')
    if os.path.exists(creds_path):
        with open(creds_path, 'r') as f:
            return json.load(f)
    
    return None


def get_drive_service():
    """Get authenticated Google Drive service using service account"""
    try:
        creds_info = _get_credentials_info()
        if not creds_info:
            print("No Google Drive credentials found. Set GOOGLE_DRIVE_CREDENTIALS env var or provide credentials file.")
            return None
        
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=SCOPES
        )
        service = build('drive', 'v3', credentials=credentials)
        return service
    except Exception as e:
        print(f"Error creating Drive service: {e}")
        return None


def upload_video_to_drive(file_content: bytes, filename: str, title: str = None) -> dict:
    """
    Upload a video file to the Queez Google Drive folder
    
    Args:
        file_content: The video file content as bytes
        filename: Original filename (used for mime type detection)
        title: Display title for the video (optional, defaults to filename)
    
    Returns:
        dict with fileId and shareableLink, or None on failure
    """
    try:
        service = get_drive_service()
        if not service:
            return None
        
        # Determine mime type
        mime_type, _ = mimetypes.guess_type(filename)
        if not mime_type:
            mime_type = 'video/mp4'  # Default to mp4
        
        # Use title or filename
        display_name = title if title else filename
        
        # File metadata
        file_metadata = {
            'name': display_name,
            'parents': [QUEEZ_FOLDER_ID]
        }
        
        # Upload file
        media = MediaIoBaseUpload(
            BytesIO(file_content),
            mimetype=mime_type,
            resumable=True
        )
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, name, webViewLink, webContentLink'
        ).execute()
        
        file_id = file.get('id')
        
        # Set file to be publicly viewable (anyone with link)
        service.permissions().create(
            fileId=file_id,
            body={
                'type': 'anyone',
                'role': 'reader'
            }
        ).execute()
        
        # Get the shareable link
        shareable_link = f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
        
        return {
            'fileId': file_id,
            'shareableLink': shareable_link,
            'name': display_name,
            'webViewLink': file.get('webViewLink'),
            'webContentLink': file.get('webContentLink')
        }
        
    except Exception as e:
        print(f"Error uploading to Drive: {e}")
        return None


def delete_video_from_drive(file_id: str) -> bool:
    """
    Delete a video file from Google Drive
    
    Args:
        file_id: The Google Drive file ID
    
    Returns:
        True if successful, False otherwise
    """
    try:
        service = get_drive_service()
        if not service:
            return False
        
        service.files().delete(fileId=file_id).execute()
        return True
        
    except Exception as e:
        print(f"Error deleting from Drive: {e}")
        return False


def get_video_info(file_id: str) -> dict:
    """
    Get information about a video file
    
    Args:
        file_id: The Google Drive file ID
    
    Returns:
        dict with file info, or None on failure
    """
    try:
        service = get_drive_service()
        if not service:
            return None
        
        file = service.files().get(
            fileId=file_id,
            fields='id, name, mimeType, size, webViewLink, webContentLink'
        ).execute()
        
        return file
        
    except Exception as e:
        print(f"Error getting video info: {e}")
        return None
