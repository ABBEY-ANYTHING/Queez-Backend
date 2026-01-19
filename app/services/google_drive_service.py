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
    print("🔑 [GoogleDrive] Loading credentials...")
    
    # First try environment variable (JSON string)
    creds_json = os.getenv('GOOGLE_DRIVE_CREDENTIALS')
    if creds_json:
        print("🔑 [GoogleDrive] Found GOOGLE_DRIVE_CREDENTIALS env var")
        print(f"🔑 [GoogleDrive] Env var length: {len(creds_json)} chars")
        try:
            parsed = json.loads(creds_json)
            print(f"🔑 [GoogleDrive] ✅ Successfully parsed credentials JSON")
            print(f"🔑 [GoogleDrive] Project ID: {parsed.get('project_id', 'N/A')}")
            print(f"🔑 [GoogleDrive] Client Email: {parsed.get('client_email', 'N/A')}")
            return parsed
        except json.JSONDecodeError as e:
            print(f"🔑 [GoogleDrive] ❌ Failed to parse credentials JSON: {e}")
            print(f"🔑 [GoogleDrive] First 100 chars: {creds_json[:100]}...")
            return None
    else:
        print("🔑 [GoogleDrive] GOOGLE_DRIVE_CREDENTIALS env var not found")
    
    # Fallback to file
    creds_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 
                              'credentials', 'google_drive_service_account.json')
    print(f"🔑 [GoogleDrive] Checking credentials file: {creds_path}")
    
    if os.path.exists(creds_path):
        print("🔑 [GoogleDrive] ✅ Credentials file found, loading...")
        with open(creds_path, 'r') as f:
            parsed = json.load(f)
            print(f"🔑 [GoogleDrive] Project ID: {parsed.get('project_id', 'N/A')}")
            return parsed
    else:
        print("🔑 [GoogleDrive] ❌ Credentials file not found")
    
    print("🔑 [GoogleDrive] ❌ No credentials available!")
    return None


def get_drive_service():
    """Get authenticated Google Drive service using service account"""
    try:
        print("🚀 [GoogleDrive] Initializing Drive service...")
        creds_info = _get_credentials_info()
        if not creds_info:
            print("🚀 [GoogleDrive] ❌ No credentials - cannot initialize service")
            return None
        
        print("🚀 [GoogleDrive] Creating service account credentials...")
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=SCOPES
        )
        print(f"🚀 [GoogleDrive] Credentials created with scopes: {SCOPES}")
        
        print("🚀 [GoogleDrive] Building Drive API v3 service...")
        service = build('drive', 'v3', credentials=credentials)
        print("🚀 [GoogleDrive] ✅ Drive service initialized successfully!")
        return service
    except Exception as e:
        print(f"🚀 [GoogleDrive] ❌ Error creating Drive service: {e}")
        import traceback
        traceback.print_exc()
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
        print(f"📤 [GoogleDrive] Starting upload for: {filename}")
        print(f"📤 [GoogleDrive] File size: {len(file_content)} bytes ({len(file_content) / (1024*1024):.2f} MB)")
        print(f"📤 [GoogleDrive] Title: {title}")
        print(f"📤 [GoogleDrive] Target folder ID: {QUEEZ_FOLDER_ID}")
        
        service = get_drive_service()
        if not service:
            print("📤 [GoogleDrive] ❌ Failed to get Drive service - aborting upload")
            return None
        
        # Determine mime type
        mime_type, _ = mimetypes.guess_type(filename)
        if not mime_type:
            mime_type = 'video/mp4'  # Default to mp4
        print(f"📤 [GoogleDrive] MIME type: {mime_type}")
        
        # Use title or filename
        display_name = title if title else filename
        print(f"📤 [GoogleDrive] Display name: {display_name}")
        
        # File metadata
        file_metadata = {
            'name': display_name,
            'parents': [QUEEZ_FOLDER_ID]
        }
        print(f"📤 [GoogleDrive] File metadata: {file_metadata}")
        
        # Upload file
        print("📤 [GoogleDrive] Creating MediaIoBaseUpload...")
        media = MediaIoBaseUpload(
            BytesIO(file_content),
            mimetype=mime_type,
            resumable=True
        )
        
        print("📤 [GoogleDrive] Calling files().create()...")
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, name, webViewLink, webContentLink'
        ).execute()
        
        file_id = file.get('id')
        print(f"📤 [GoogleDrive] ✅ File created with ID: {file_id}")
        
        # Set file to be publicly viewable (anyone with link)
        print("📤 [GoogleDrive] Setting file permissions (public reader)...")
        service.permissions().create(
            fileId=file_id,
            body={
                'type': 'anyone',
                'role': 'reader'
            }
        ).execute()
        print("📤 [GoogleDrive] ✅ Permissions set successfully")
        
        # Get the shareable link
        shareable_link = f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
        
        result = {
            'fileId': file_id,
            'shareableLink': shareable_link,
            'name': display_name,
            'webViewLink': file.get('webViewLink'),
            'webContentLink': file.get('webContentLink')
        }
        print(f"📤 [GoogleDrive] ✅ Upload complete! Result: {result}")
        return result
        
    except Exception as e:
        print(f"📤 [GoogleDrive] ❌ Error uploading to Drive: {e}")
        import traceback
        traceback.print_exc()
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
