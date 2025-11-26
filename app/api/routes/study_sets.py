from fastapi import APIRouter, HTTPException, status
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

from app.services.firestore_service import firestore_db

router = APIRouter(prefix="/study-sets", tags=["Study Sets"])


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


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_study_set(study_set: StudySetCreate):
    """Create a new study set"""
    try:
        study_set_data = study_set.dict()
        study_set_data['createdAt'] = datetime.utcnow().isoformat()
        study_set_data['updatedAt'] = datetime.utcnow().isoformat()
        
        # Save to Firestore
        firestore_db.collection('study_sets').document(study_set.id).set(study_set_data)
        
        return {
            "success": True,
            "message": "Study set created successfully",
            "studySetId": study_set.id
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
        doc = firestore_db.collection('study_sets').document(study_set_id).get()
        
        if not doc.exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Study set not found"
            )
        
        study_set_data = doc.to_dict()
        study_set_data['id'] = doc.id
        
        return {
            "success": True,
            "studySet": study_set_data
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
        study_sets_ref = firestore_db.collection('study_sets')
        query = study_sets_ref.where('ownerId', '==', user_id).order_by('updatedAt', direction='DESCENDING')
        docs = query.stream()
        
        study_sets = []
        for doc in docs:
            study_set_data = doc.to_dict()
            study_set_data['id'] = doc.id
            study_sets.append(study_set_data)
        
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
        doc_ref = firestore_db.collection('study_sets').document(study_set_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Study set not found"
            )
        
        study_set_data = study_set.dict()
        study_set_data['updatedAt'] = datetime.utcnow().isoformat()
        
        doc_ref.update(study_set_data)
        
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
        doc_ref = firestore_db.collection('study_sets').document(study_set_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Study set not found"
            )
        
        doc_ref.delete()
        
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
        doc = firestore_db.collection('study_sets').document(study_set_id).get()
        
        if not doc.exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Study set not found"
            )
        
        study_set_data = doc.to_dict()
        
        stats = {
            "totalQuizzes": len(study_set_data.get('quizzes', [])),
            "totalFlashcardSets": len(study_set_data.get('flashcardSets', [])),
            "totalNotes": len(study_set_data.get('notes', [])),
            "totalItems": (
                len(study_set_data.get('quizzes', [])) +
                len(study_set_data.get('flashcardSets', [])) +
                len(study_set_data.get('notes', []))
            ),
            "totalQuestions": sum(
                len(quiz.get('questions', [])) 
                for quiz in study_set_data.get('quizzes', [])
            ),
            "totalFlashcards": sum(
                len(fs.get('cards', [])) 
                for fs in study_set_data.get('flashcardSets', [])
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
