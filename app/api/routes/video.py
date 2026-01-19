"""
Video Upload API Routes
Handles video lecture uploads to Google Drive
"""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from typing import Optional
from pydantic import BaseModel
from app.services.google_drive_service import (
    upload_video_to_drive,
    delete_video_from_drive,
    get_video_info
)

router = APIRouter(prefix="/video", tags=["Video"])


class VideoUploadResponse(BaseModel):
    success: bool
    fileId: Optional[str] = None
    shareableLink: Optional[str] = None
    name: Optional[str] = None
    message: Optional[str] = None


class VideoDeleteResponse(BaseModel):
    success: bool
    message: str


@router.post("/upload", response_model=VideoUploadResponse, summary="Upload video to Google Drive")
async def upload_video(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None)
):
    """
    Upload a video file to the central Queez Google Drive folder.
    
    - **file**: The video file to upload (max 100MB recommended)
    - **title**: Optional display title for the video
    
    Returns the file ID and shareable link.
    """
    try:
        print(f"📹 [VIDEO_UPLOAD] Starting upload...")
        print(f"📹 [VIDEO_UPLOAD] Filename: {file.filename}")
        print(f"📹 [VIDEO_UPLOAD] Content-Type: {file.content_type}")
        print(f"📹 [VIDEO_UPLOAD] Title: {title}")
        
        # Check file size (limit to 100MB)
        content = await file.read()
        file_size = len(content)
        print(f"📹 [VIDEO_UPLOAD] File size: {file_size} bytes ({file_size / (1024*1024):.2f} MB)")
        
        if file_size > 100 * 1024 * 1024:  # 100MB
            raise HTTPException(
                status_code=413,
                detail="File too large. Maximum size is 100MB."
            )
        
        # Check if it's a video file
        content_type = file.content_type or ""
        if not content_type.startswith("video/"):
            # Try to determine from filename
            filename = file.filename or "video.mp4"
            video_extensions = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.wmv', '.flv']
            if not any(filename.lower().endswith(ext) for ext in video_extensions):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid file type. Please upload a video file."
                )
        
        print(f"📹 [VIDEO_UPLOAD] Calling upload_video_to_drive...")
        
        # Upload to Google Drive
        result = upload_video_to_drive(
            file_content=content,
            filename=file.filename or "video.mp4",
            title=title
        )
        
        print(f"📹 [VIDEO_UPLOAD] Upload result: {result}")
        
        if result:
            print(f"📹 [VIDEO_UPLOAD] ✅ Success! File ID: {result['fileId']}")
            return VideoUploadResponse(
                success=True,
                fileId=result['fileId'],
                shareableLink=result['shareableLink'],
                name=result['name']
            )
        else:
            print(f"📹 [VIDEO_UPLOAD] ❌ Failed - result is None")
            raise HTTPException(
                status_code=500,
                detail="Failed to upload video to Google Drive"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"📹 [VIDEO_UPLOAD] ❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error uploading video: {str(e)}"
        )


@router.delete("/{file_id}", response_model=VideoDeleteResponse, summary="Delete video from Google Drive")
async def delete_video(file_id: str):
    """
    Delete a video file from Google Drive.
    
    - **file_id**: The Google Drive file ID to delete
    """
    try:
        success = delete_video_from_drive(file_id)
        
        if success:
            return VideoDeleteResponse(
                success=True,
                message="Video deleted successfully"
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to delete video from Google Drive"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error deleting video: {str(e)}"
        )


@router.get("/{file_id}", summary="Get video info")
async def get_video(file_id: str):
    """
    Get information about a video file.
    
    - **file_id**: The Google Drive file ID
    """
    try:
        info = get_video_info(file_id)
        
        if info:
            return {
                "success": True,
                "video": info
            }
        else:
            raise HTTPException(
                status_code=404,
                detail="Video not found"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error getting video info: {str(e)}"
        )
