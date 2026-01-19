from fastapi import APIRouter, HTTPException, status
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime, timedelta
from bson import ObjectId
import random
import string

from app.core.database import db

router = APIRouter(prefix="/study-sets", tags=["Study Sets"])

# Get study sets collection
study_sets_collection = db["study_sets"]
study_set_sessions_collection = db["study_set_sessions"]


# Helper function to generate share code
def generate_share_code(length=6):
    """Generate a random alphanumeric share code"""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))


# Pydantic Models
class Quiz(BaseModel):
    id: str
    title: str
    description: str
    category: str
    language: str
    coverImagePath: Optional[str] = None
    ownerId: str
    questions: List[dict]
    createdAt: str
    updatedAt: str


class Flashcard(BaseModel):
    id: Optional[str] = None
    front: str
    back: str


class FlashcardSet(BaseModel):
    id: Optional[str] = None
    title: str
    description: str
    category: str
    coverImagePath: Optional[str] = None
    creatorId: str
    cards: List[Flashcard]
    createdAt: Optional[str] = None


class Note(BaseModel):
    id: Optional[str] = None
    title: str
    description: str
    category: str
    coverImagePath: Optional[str] = None
    creatorId: str
    content: str
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class StudySet(BaseModel):
    id: str
    name: str
    description: str
    category: str
    language: str
    coverImagePath: Optional[str] = None
    ownerId: str
    quizzes: List[Quiz] = []
    flashcardSets: List[FlashcardSet] = []
    notes: List[Note] = []
    createdAt: str
    updatedAt: str


class StudySetCreate(BaseModel):
    id: str
    name: str
    description: str
    category: str
    language: str
    coverImagePath: Optional[str] = None
    ownerId: str
    quizzes: List[dict] = []
    flashcardSets: List[dict] = []
    notes: List[dict] = []


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_study_set(study_set: StudySetCreate):
    """Create a new study set"""
    try:
        study_set_data = study_set.dict()
        
        # Format createdAt as "Month, Year"
        now = datetime.utcnow()
        study_set_data['createdAt'] = now.strftime("%B, %Y")
        study_set_data['updatedAt'] = datetime.utcnow().isoformat()
        
        # Save to MongoDB
        result = await study_sets_collection.insert_one(study_set_data)
        
        return {
            "id": str(result.inserted_id),
            "success": True,
            "message": "Study set created successfully"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create study set: {str(e)}"
        )


@router.get("/{study_set_id}")
async def get_study_set(study_set_id: str):
    """Get a study set by ID"""
    try:
        doc = await study_sets_collection.find_one({"_id": ObjectId(study_set_id)})
        
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Study set not found"
            )
        
        doc['id'] = str(doc['_id'])
        del doc['_id']
        
        return {
            "success": True,
            "studySet": doc
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch study set: {str(e)}"
        )


@router.get("/user/{user_id}")
async def get_user_study_sets(user_id: str):
    """Get all study sets for a user"""
    try:
        cursor = study_sets_collection.find({"ownerId": user_id}).sort("updatedAt", -1)
        docs = await cursor.to_list(length=None)
        
        study_sets = []
        for doc in docs:
            doc['id'] = str(doc['_id'])
            del doc['_id']
            study_sets.append(doc)
        
        return {
            "success": True,
            "studySets": study_sets,
            "count": len(study_sets)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch study sets: {str(e)}"
        )


@router.put("/{study_set_id}")
async def update_study_set(study_set_id: str, study_set: StudySetCreate):
    """Update a study set"""
    try:
        existing = await study_sets_collection.find_one({"_id": ObjectId(study_set_id)})
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Study set not found"
            )
        
        study_set_data = study_set.dict()
        study_set_data['updatedAt'] = datetime.utcnow().isoformat()
        
        await study_sets_collection.update_one(
            {"_id": ObjectId(study_set_id)},
            {"$set": study_set_data}
        )
        
        return {
            "success": True,
            "message": "Study set updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update study set: {str(e)}"
        )


@router.delete("/{study_set_id}")
async def delete_study_set(study_set_id: str):
    """Delete a study set"""
    try:
        existing = await study_sets_collection.find_one({"_id": ObjectId(study_set_id)})
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Study set not found"
            )
        
        await study_sets_collection.delete_one({"_id": ObjectId(study_set_id)})
        
        return {
            "success": True,
            "message": "Study set deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete study set: {str(e)}"
        )


@router.get("/{study_set_id}/stats")
async def get_study_set_stats(study_set_id: str):
    """Get statistics for a study set"""
    try:
        doc = await study_sets_collection.find_one({"_id": ObjectId(study_set_id)})
        
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Study set not found"
            )
        
        stats = {
            "totalQuizzes": len(doc.get('quizzes', [])),
            "totalFlashcardSets": len(doc.get('flashcardSets', [])),
            "totalNotes": len(doc.get('notes', [])),
            "totalItems": (
                len(doc.get('quizzes', [])) +
                len(doc.get('flashcardSets', [])) +
                len(doc.get('notes', []))
            ),
            "totalQuestions": sum(
                len(quiz.get('questions', [])) 
                for quiz in doc.get('quizzes', [])
            ),
            "totalFlashcards": sum(
                len(fs.get('cards', [])) 
                for fs in doc.get('flashcardSets', [])
            )
        }
        
        return {
            "success": True,
            "stats": stats
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch study set stats: {str(e)}"
        )


@router.post("/{study_set_id}/create-share-code")
async def create_study_set_share_code(study_set_id: str):
    """Create a share code for a study set (valid for 10 minutes)"""
    try:
        # Verify study set exists - try both _id (ObjectId) and custom id field
        study_set = None
        
        # First try as MongoDB ObjectId
        try:
            study_set = await study_sets_collection.find_one({"_id": ObjectId(study_set_id)})
        except Exception:
            pass  # Invalid ObjectId format, try custom id
        
        # If not found, try custom id field
        if not study_set:
            study_set = await study_sets_collection.find_one({"id": study_set_id})
        
        if not study_set:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Study set not found"
            )
        
        # Generate unique share code
        share_code = generate_share_code()
        
        # Ensure uniqueness
        while await study_set_sessions_collection.find_one({"share_code": share_code}):
            share_code = generate_share_code()
        
        # Calculate expiration (10 minutes from now)
        created_at = datetime.utcnow()
        expires_at = created_at + timedelta(minutes=10)
        expires_in = 600  # 10 minutes in seconds
        
        # Create share session document
        session = {
            "share_code": share_code,
            "study_set_id": study_set_id,
            "owner_id": study_set.get("ownerId"),
            "is_active": True,
            "created_at": created_at,
            "expires_at": expires_at,
            "study_set_name": study_set.get("name", "Untitled Study Set")
        }
        
        await study_set_sessions_collection.insert_one(session)
        
        return {
            "success": True,
            "share_code": share_code,
            "expires_in": expires_in,
            "expires_at": expires_at.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating share code: {str(e)}"
        )


@router.post("/add-to-library")
async def add_study_set_to_library(data: dict):
    """Add a study set to user's library using a share code"""
    try:
        share_code = data.get("share_code")
        user_id = data.get("user_id")
        
        if not share_code or not user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing share_code or user_id"
            )
        
        # Find the share session
        session = await study_set_sessions_collection.find_one({"share_code": share_code})
        
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invalid or expired share code"
            )
        
        # Check if expired
        if session["expires_at"] < datetime.utcnow():
            await study_set_sessions_collection.delete_one({"share_code": share_code})
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Share code has expired"
            )
        
        # Get the original study set
        original_study_set = await study_sets_collection.find_one(
            {"_id": ObjectId(session["study_set_id"])}
        )
        
        if not original_study_set:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Study set not found"
            )
        
        original_owner_id = original_study_set.get("ownerId")
        
        # Check if user already has this study set
        if original_owner_id == user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You are the owner of this study set"
            )
        
        # Check if user already has a copy
        existing = await study_sets_collection.find_one({
            "ownerId": user_id,
            "originalOwner": original_owner_id,
            "name": original_study_set.get("name")
        })
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You already have this study set in your library"
            )
        
        # Create a copy of the study set for the user
        new_study_set = {
            "name": original_study_set.get("name"),
            "description": original_study_set.get("description"),
            "language": original_study_set.get("language"),
            "category": original_study_set.get("category"),
            "coverImagePath": original_study_set.get("coverImagePath"),
            "ownerId": user_id,
            "originalOwner": original_owner_id,
            "quizzes": original_study_set.get("quizzes", []),
            "flashcardSets": original_study_set.get("flashcardSets", []),
            "notes": original_study_set.get("notes", []),
            "createdAt": datetime.utcnow().strftime("%B, %Y"),
            "updatedAt": datetime.utcnow().isoformat()
        }
        
        result = await study_sets_collection.insert_one(new_study_set)
        new_study_set_id = str(result.inserted_id)
        
        return {
            "success": True,
            "study_set_id": new_study_set_id,
            "study_set_name": new_study_set.get("name", "Untitled Study Set"),
            "message": "Study set added to your library successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error adding study set: {str(e)}"
        )

