from fastapi import APIRouter, HTTPException
from typing import List, Union
from app.core.database import db
from app.services.firestore_service import firestore_db
from pydantic import BaseModel

router = APIRouter(prefix="/library", tags=["library"])

# Get collections
quiz_collection = db["quizzes"]
flashcard_collection = db["flashcard_sets"]
note_collection = db["notes"]

class LibraryItem(BaseModel):
    id: str
    type: str  # "quiz", "flashcard", or "note"
    title: str
    description: str
    coverImagePath: str
    createdAt: str
    itemCount: int  # questionCount for quizzes, cardCount for flashcards, 0 for notes
    category: str
    language: str = ""  # Only for quizzes
    originalOwner: str | None = None
    originalOwnerUsername: str | None = None
    sharedMode: str | None = None  # Only for quizzes

class UnifiedLibraryResponse(BaseModel):
    success: bool
    data: List[LibraryItem]
    count: int

@router.get("/{user_id}", response_model=UnifiedLibraryResponse, summary="Get all quizzes, flashcards, and notes for a user")
async def get_unified_library(user_id: str):
    try:
        fallback_image = "https://img.freepik.com/free-vector/student-asking-teacher-concept-illustration_114360-19831.jpg?ga=GA1.1.377073698.1750732876&semt=ais_items_boosted&w=740"
        
        # Fetch quizzes
        quiz_cursor = quiz_collection.find(
            {"creatorId": user_id},
            {
                "title": 1,
                "description": 1,
                "coverImagePath": 1,
                "createdAt": 1,
                "questions": 1,
                "language": 1,
                "category": 1,
                "_id": 1,
                "originalOwner": 1,
                "sharedMode": 1
            }
        )
        quizzes = await quiz_cursor.to_list(length=None)
        
        # Fetch flashcards
        flashcard_cursor = flashcard_collection.find(
            {"creatorId": user_id},
            {
                "title": 1,
                "description": 1,
                "coverImagePath": 1,
                "createdAt": 1,
                "cards": 1,
                "category": 1,
                "_id": 1,
                "originalOwner": 1
            }
        )
        flashcards = await flashcard_cursor.to_list(length=None)
        
        # Fetch notes
        note_cursor = note_collection.find(
            {"creatorId": user_id},
            {
                "title": 1,
                "description": 1,
                "coverImagePath": 1,
                "createdAt": 1,
                "category": 1,
                "_id": 1
            }
        )
        notes = await note_cursor.to_list(length=None)
        
        # Fetch study sets from Firestore
        study_sets = []
        try:
            study_sets_ref = firestore_db.collection('study_sets')
            query = study_sets_ref.where('ownerId', '==', user_id)
            docs = query.stream()
            
            for doc in docs:
                study_set_data = doc.to_dict()
                study_set_data['_id'] = doc.id
                study_sets.append(study_set_data)
        except Exception as e:
            print(f"Error fetching study sets: {e}")
            # Continue even if study sets fail
        
        # Convert quizzes to LibraryItem
        library_items = []
        for quiz in quizzes:
            library_items.append(LibraryItem(
                id=str(quiz["_id"]),
                type="quiz",
                title=quiz.get("title", "Untitled Quiz"),
                description=quiz.get("description", ""),
                coverImagePath=quiz.get("coverImagePath") or fallback_image,
                createdAt=quiz.get("createdAt", ""),
                itemCount=len(quiz.get("questions", [])),
                language=quiz.get("language", ""),
                category=quiz.get("category", ""),
                originalOwner=quiz.get("originalOwner"),
                originalOwnerUsername=None,
                sharedMode=quiz.get("sharedMode")
            ))
        
        # Convert flashcards to LibraryItem
        for flashcard_set in flashcards:
            library_items.append(LibraryItem(
                id=str(flashcard_set["_id"]),
                type="flashcard",
                title=flashcard_set.get("title", "Untitled Flashcard Set"),
                description=flashcard_set.get("description", ""),
                coverImagePath=flashcard_set.get("coverImagePath") or fallback_image,
                createdAt=flashcard_set.get("createdAt", ""),
                itemCount=len(flashcard_set.get("cards", [])),
                category=flashcard_set.get("category", ""),
                originalOwner=flashcard_set.get("originalOwner"),
                originalOwnerUsername=None
            ))
        
        # Convert notes to LibraryItem
        for note in notes:
            library_items.append(LibraryItem(
                id=str(note["_id"]),
                type="note",
                title=note.get("title", "Untitled Note"),
                description=note.get("description", ""),
                coverImagePath=note.get("coverImagePath") or fallback_image,
                createdAt=note.get("createdAt", ""),
                itemCount=0,  # Notes don't have a count
                category=note.get("category", ""),
                originalOwner=None,
                originalOwnerUsername=None
            ))
        
        # Convert study sets to LibraryItem
        for study_set in study_sets:
            total_items = (
                len(study_set.get('quizzes', [])) +
                len(study_set.get('flashcardSets', [])) +
                len(study_set.get('notes', []))
            )
            library_items.append(LibraryItem(
                id=str(study_set["_id"]),
                type="study_set",
                title=study_set.get("name", "Untitled Study Set"),
                description=study_set.get("description", ""),
                coverImagePath=study_set.get("coverImagePath") or fallback_image,
                createdAt=study_set.get("createdAt", ""),
                itemCount=total_items,
                category=study_set.get("category", ""),
                language=study_set.get("language", ""),
                originalOwner=None,
                originalOwnerUsername=None
            ))
        
        # Sort by createdAt (most recent first)
        # Note: This is a simple string sort. For better sorting, parse dates
        library_items.sort(key=lambda x: x.createdAt, reverse=True)
        
        return UnifiedLibraryResponse(
            success=True,
            data=library_items,
            count=len(library_items)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
