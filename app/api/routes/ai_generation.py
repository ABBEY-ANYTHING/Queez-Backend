from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
import secrets
import os
import logging
import json
import uuid

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
    config: Optional[StudySetConfig] = None
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
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        logger.info(f"Generating AI study set from {len(request.fileUris)} files")
        logger.info(f"Settings: {request.settings.quizCount} quizzes, {request.settings.flashcardSetCount} flashcard sets, {request.settings.noteCount} notes")
        logger.info(f"Difficulty: {request.settings.difficulty}")
        
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
        prompt = f"""You are an expert educator creating study materials. Analyze the provided documents and generate a comprehensive study set with ALL metadata.

GENERATE THE FOLLOWING:
1. Study Set Metadata (name, description, category, language)
2. {request.settings.quizCount} quizzes with {request.settings.questionsPerQuiz} multiple-choice questions each (each quiz has its own title, description, difficulty, category, language)
3. {request.settings.flashcardSetCount} flashcard sets with {request.settings.cardsPerSet} cards each (each set has its own title, description, category)
4. {request.settings.noteCount} comprehensive study notes (each note has its own title, description, category)

REQUIREMENTS:
- Analyze the document(s) and create an appropriate study set name and description
- Determine the appropriate category (Science, Math, History, Language, Technology, Business, Arts, Health, Engineering, Social Studies, Philosophy, Psychology, Geography, Literature, Music, Sports, Law, Economics, Politics, Other)
- Detect the language of the content
- Extract key concepts, definitions, and important facts
- Create questions that test understanding, not just memorization
- Ensure flashcards cover different aspects of the material
- Notes should summarize main topics with examples
- Use proper formatting and clear language
- Match difficulty level: {request.settings.difficulty}
- Each quiz, flashcard set, and note should have its own unique title, description, category, and language

OUTPUT FORMAT (JSON):
{{
  "studySet": {{
    "name": "Generated study set name based on document content",
    "description": "Comprehensive description of what this study set covers",
    "category": "Most appropriate category from the list above",
    "language": "Detected language (English, Spanish, French, etc.)"
  }},
  "quizzes": [
    {{
      "title": "Unique quiz title (e.g., 'Chapter 1: Introduction to Biology')",
      "description": "What this specific quiz covers",
      "difficulty": "Easy|Medium|Hard",
      "category": "Category for this quiz",
      "language": "Language for this quiz",
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
      "title": "Unique flashcard set title (e.g., 'Key Terms: Cell Biology')",
      "description": "What this flashcard set focuses on",
      "category": "Category for this set",
      "cards": [
        {{
          "front": "Term or question",
          "back": "Definition or answer"
        }}
      ]
    }}
  ],
  "notes": [
    {{
      "title": "Unique note title (e.g., 'Summary: Cellular Processes')",
      "description": "Brief summary of note content",
      "category": "Category for this note",
      "content": "{{\\"ops\\":[{{\\"insert\\":\\"Note content in Quill Delta format\\\\n\\"}}]}}"
    }}
  ]
}}

IMPORTANT: Return ONLY valid JSON without any markdown formatting or code blocks. The JSON should be parseable directly."""

        # Send request to Gemini with file objects
        content_parts = [prompt] + files
        
        response = model.generate_content(
            content_parts,
            generation_config={
                "temperature": 0.7,
                "top_p": 0.95,
                "top_k": 40,
                "max_output_tokens": 65536,
            }
        )
        
        # Parse response
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
        
        ai_response = json.loads(response_text)
        
        # Extract study set metadata from AI response
        study_set_metadata = ai_response.get("studySet", {})
        
        # Get Firebase user ID from token
        import firebase_admin
        from firebase_admin import auth as firebase_auth
        
        # Verify the Firebase token
        try:
            token = authorization.replace("Bearer ", "")
            decoded_token = firebase_auth.verify_id_token(token)
            user_id = decoded_token['uid']
        except Exception as e:
            logger.error(f"Token verification failed: {str(e)}")
            raise HTTPException(status_code=401, detail="Invalid authentication token")
        
        # Build the complete study set structure
        current_time = datetime.utcnow().isoformat() + "Z"
        study_set_id = str(uuid.uuid4())
        
        # Process quizzes
        quizzes = []
        for quiz_data in ai_response.get("quizzes", []):
            quiz_id = str(uuid.uuid4())
            questions = []
            for q_data in quiz_data.get("questions", []):
                question = {
                    "id": str(uuid.uuid4()),
                    "questionText": q_data.get("questionText", ""),
                    "options": q_data.get("options", []),
                    "correctOption": q_data.get("correctOption", 0),
                    "explanation": q_data.get("explanation", "")
                }
                questions.append(question)
            
            quiz = {
                "id": quiz_id,
                "title": quiz_data.get("title", f"Quiz {len(quizzes) + 1}"),
                "description": quiz_data.get("description", ""),
                "category": quiz_data.get("category", study_set_metadata.get("category", "Other")),
                "language": quiz_data.get("language", study_set_metadata.get("language", "English")),
                "difficulty": quiz_data.get("difficulty", request.settings.difficulty),
                "creatorId": user_id,
                "questions": questions,
                "createdAt": current_time
            }
            quizzes.append(quiz)
        
        # Process flashcard sets
        flashcard_sets = []
        for set_data in ai_response.get("flashcardSets", []):
            set_id = str(uuid.uuid4())
            cards = []
            for card_data in set_data.get("cards", []):
                card = {
                    "id": str(uuid.uuid4()),
                    "front": card_data.get("front", ""),
                    "back": card_data.get("back", "")
                }
                cards.append(card)
            
            flashcard_set = {
                "id": set_id,
                "title": set_data.get("title", f"Flashcard Set {len(flashcard_sets) + 1}"),
                "description": set_data.get("description", ""),
                "category": set_data.get("category", study_set_metadata.get("category", "Other")),
                "creatorId": user_id,
                "cards": cards,
                "createdAt": current_time
            }
            flashcard_sets.append(flashcard_set)
        
        # Process notes
        notes = []
        for note_data in ai_response.get("notes", []):
            note_id = str(uuid.uuid4())
            
            # Convert content to Quill Delta format if it's plain text
            content = note_data.get("content", "")
            if isinstance(content, str):
                # Simple conversion to Quill Delta
                content = {"ops": [{"insert": content + "\\n"}]}
            
            note = {
                "id": note_id,
                "title": note_data.get("title", f"Note {len(notes) + 1}"),
                "description": note_data.get("description", note_data.get("summary", "")),
                "category": note_data.get("category", study_set_metadata.get("category", "Other")),
                "content": json.dumps(content) if isinstance(content, dict) else content,
                "creatorId": user_id,
                "createdAt": current_time,
                "updatedAt": current_time
            }
            notes.append(note)
        
        # Build final study set with AI-generated metadata
        study_set = {
            "id": study_set_id,
            "name": study_set_metadata.get("name", "AI Generated Study Set"),
            "description": study_set_metadata.get("description", "Study materials generated from uploaded documents"),
            "category": study_set_metadata.get("category", "Other"),
            "language": study_set_metadata.get("language", "English"),
            "coverImagePath": None,
            "ownerId": user_id,
            "quizzes": quizzes,
            "flashcardSets": flashcard_sets,
            "notes": notes,
            "createdAt": current_time,
            "updatedAt": current_time
        }
        
        logger.info(f"Successfully generated study set: {study_set['name']}")
        logger.info(f"Quizzes: {len(quizzes)}, Flashcard Sets: {len(flashcard_sets)}, Notes: {len(notes)}")
        
        # Return the generated study set
        return {
            "success": True,
            "studySet": study_set
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
