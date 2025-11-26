from fastapi import APIRouter, HTTPException, status
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from bson import ObjectId

from app.core.database import db

router = APIRouter(prefix="/study-sets", tags=["Study Sets"])

# Get study sets collection
study_sets_collection = db["study_sets"]


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
        study_set_data['createdAt'] = datetime.utcnow().isoformat()
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
