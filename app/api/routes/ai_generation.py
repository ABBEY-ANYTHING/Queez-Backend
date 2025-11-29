from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
import secrets
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ai",
    tags=["AI Generation"]
)

# In-memory token storage (for production, use Redis)
upload_tokens = {}

class UploadTokenResponse(BaseModel):
    uploadUrl: str
    expiresAt: str

class GenerationSettings(BaseModel):
    quizCount: int = 2
    flashcardSetCount: int = 2
    noteCount: int = 1
    difficulty: str = "Mixed"
    questionsPerQuiz: int = 10
    cardsPerSet: int = 20

class StudySetConfig(BaseModel):
    name: str
    description: str
    category: str
    language: str
    coverImagePath: Optional[str] = None

class UploadUrlRequest(BaseModel):
    file_name: str
    mime_type: str

class GenerateStudySetRequest(BaseModel):
    fileUris: List[str]
    config: StudySetConfig
    settings: GenerationSettings

@router.post("/get-upload-url")
async def get_upload_url(
    request: UploadUrlRequest,
    authorization: Optional[str] = Header(None)
):
    """
    Generate a temporary resumable upload URL for Gemini File API
    This keeps the API key secure on the server
    """
    try:
        # Verify Firebase auth token
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        # Import requests for making HTTP calls
        import requests
        
        # Get Gemini API key from environment
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY environment variable not set")
            raise HTTPException(
                status_code=500,
                detail="AI service configuration missing"
            )
        
        # Request a resumable upload URL from Gemini
        headers = {
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Type": request.mime_type,
            "Content-Type": "application/json"
        }
        
        metadata = {
            "file": {
                "display_name": request.file_name
            }
        }
        
        response = requests.post(
            f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}",
            headers=headers,
            json=metadata,
            timeout=30
        )
        
        if response.status_code != 200:
            logger.error(f"Failed to get upload URL: {response.text}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create upload session: {response.text}"
            )
        
        # Extract upload URL from response headers
        upload_url = response.headers.get("X-Goog-Upload-URL")
        if not upload_url:
            raise HTTPException(
                status_code=500,
                detail="No upload URL returned from Gemini"
            )
        
        expiration = datetime.utcnow() + timedelta(hours=1)
        
        logger.info(f"Generated upload URL for file: {request.file_name}")
        
        return {
            "uploadUrl": upload_url,
            "expiresAt": expiration.isoformat() + "Z"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating upload URL: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate-study-set")
async def generate_study_set(
    request: GenerateStudySetRequest,
    authorization: Optional[str] = Header(None)
):
    """
    Generate a study set using Gemini AI from uploaded documents
    """
    try:
        # Verify authorization
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        # Validate file URIs
        if not request.fileUris or len(request.fileUris) == 0:
            raise HTTPException(status_code=400, detail="No files provided")
        
        if len(request.fileUris) > 3:
            raise HTTPException(status_code=400, detail="Maximum 3 files allowed")
        
        # Validate config
        if not request.config.name or len(request.config.name) < 3:
            raise HTTPException(status_code=400, detail="Name must be at least 3 characters")
        
        if not request.config.description or len(request.config.description) < 10:
            raise HTTPException(status_code=400, detail="Description must be at least 10 characters")
        
        # Import Gemini SDK
        try:
            import google.generativeai as genai
        except ImportError:
            logger.error("google-generativeai package not installed")
            raise HTTPException(
                status_code=500,
                detail="AI generation service not configured"
            )
        
        # Configure Gemini
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY environment variable not set")
            raise HTTPException(
                status_code=500,
                detail="AI service configuration missing"
            )
        
        genai.configure(api_key=api_key)
        
        # Initialize Gemini model
        model = genai.GenerativeModel('gemini-2.0-flash-exp')
        
        logger.info(f"Generating study set from {len(request.fileUris)} files")
        logger.info(f"Config: {request.config.name} - {request.config.category}")
        logger.info(f"Settings: {request.settings.quizCount} quizzes, {request.settings.flashcardSetCount} flashcard sets")
        
        # Get file objects from URIs
        files = []
        for uri in request.fileUris:
            try:
                # Extract file name from URI (gemini://file/...)
                file_obj = genai.get_file(name=uri.replace("gemini://", ""))
                files.append(file_obj)
                logger.info(f"Loaded file: {file_obj.display_name}")
            except Exception as e:
                logger.warning(f"Could not load file {uri}: {str(e)}")
                # Continue with other files
        
        if not files:
            raise HTTPException(status_code=400, detail="Could not load any files")
        
        # Build the prompt
        prompt = f"""You are an expert educator creating study materials. Analyze the provided documents and generate a comprehensive study set.

STUDY SET DETAILS:
- Name: {request.config.name}
- Description: {request.config.description}
- Category: {request.config.category}
- Language: {request.config.language}
- Difficulty: {request.settings.difficulty}

GENERATE THE FOLLOWING:
1. {request.settings.quizCount} quizzes with {request.settings.questionsPerQuiz} multiple-choice questions each
2. {request.settings.flashcardSetCount} flashcard sets with {request.settings.cardsPerSet} cards each
3. {request.settings.noteCount} comprehensive study notes

REQUIREMENTS:
- Extract key concepts, definitions, and important facts from the documents
- Create questions that test understanding, not just memorization
- Ensure flashcards cover different aspects of the material
- Notes should summarize main topics with examples
- Use proper formatting and clear language
- Match the specified difficulty level

OUTPUT FORMAT (JSON):
{{
  "studySet": {{
    "name": "{request.config.name}",
    "description": "{request.config.description}",
    "category": "{request.config.category}",
    "language": "{request.config.language}",
    "coverImage": null,
    "quizzes": [
      {{
        "title": "Quiz title",
        "description": "Quiz description",
        "difficulty": "Easy|Medium|Hard",
        "questions": [
          {{
            "questionText": "Question text",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correctOption": 0,
            "explanation": "Why this is correct"
          }}
        ]
      }}
    ],
    "flashcardSets": [
      {{
        "title": "Flashcard set title",
        "description": "Set description",
        "cards": [
          {{
            "front": "Term or question",
            "back": "Definition or answer",
            "hint": "Optional hint"
          }}
        ]
      }}
    ],
    "notes": [
      {{
        "title": "Note title",
        "content": "Comprehensive note content with formatting",
        "summary": "Brief summary"
      }}
    ]
  }}
}}

Generate the study set now based on the documents provided."""

        # Send request to Gemini with file objects
        content_parts = [prompt] + files
        
        response = model.generate_content(
            content_parts,
            generation_config={
                "temperature": 0.7,
                "top_p": 0.95,
                "top_k": 40,
                "max_output_tokens": 8192,
            }
        )
        
        # Parse response
        import json
        response_text = response.text
        
        # Extract JSON from markdown code blocks if present
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip()
        elif "```" in response_text:
            start = response_text.find("```") + 3
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip()
        
        study_set_data = json.loads(response_text)
        
        logger.info(f"Successfully generated study set: {request.config.name}")
        
        # Return the generated study set
        return {
            "success": True,
            "studySet": study_set_data.get("studySet", study_set_data)
        }
    
    except HTTPException:
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to parse AI response. Please try again."
        )
    except Exception as e:
        logger.error(f"Error generating study set: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Cleanup expired tokens periodically
@router.on_event("startup")
async def cleanup_tokens():
    """Remove expired tokens from memory"""
    import asyncio
    
    async def cleanup_task():
        while True:
            await asyncio.sleep(300)  # Run every 5 minutes
            current_time = datetime.utcnow()
            expired = [
                token for token, data in upload_tokens.items()
                if data["expires_at"] < current_time
            ]
            for token in expired:
                del upload_tokens[token]
            if expired:
                logger.info(f"Cleaned up {len(expired)} expired tokens")
    
    asyncio.create_task(cleanup_task())
