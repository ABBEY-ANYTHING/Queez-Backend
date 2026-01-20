from fastapi import APIRouter, HTTPException
from typing import List, Union
from app.core.database import db
from pydantic import BaseModel

router = APIRouter(prefix="/library", tags=["library"])

# Get collections
quiz_collection = db["quizzes"]
flashcard_collection = db["flashcard_sets"]
note_collection = db["notes"]
course_pack_collection = db["course_pack"]

class LibraryItem(BaseModel):
    id: str
    type: str  # "quiz", "flashcard", "note", or "course_pack"
    title: str
    description: str
    coverImagePath: str
    createdAt: str
    itemCount: int  # questionCount for quizzes, cardCount for flashcards, 0 for notes
    category: str
    language: str = ""  # Only for quizzes and course packs
    originalOwner: str | None = None
    originalOwnerUsername: str | None = None
    originalCoursePackId: str | None = None
    ownerId: str | None = None
    sharedMode: str | None = None  # Only for quizzes
    # Course pack specific fields
    isPublic: bool = False
    rating: float = 0.0
    enrolledCount: int = 0
    estimatedHours: float = 0.0
    videoCount: int = 0

class UnifiedLibraryResponse(BaseModel):
    success: bool
    data: List[LibraryItem]
    count: int

@router.get("/{user_id}", response_model=UnifiedLibraryResponse, summary="Get all quizzes, flashcards, notes, and course packs for a user")
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
        
        # Fetch course packs from MongoDB
        course_packs = []
        try:
            course_pack_cursor = course_pack_collection.find({"ownerId": user_id})
            course_packs = await course_pack_cursor.to_list(length=None)
        except Exception as e:
            print(f"Error fetching course packs: {e}")
        
        # Convert course packs to LibraryItem
        for course_pack in course_packs:
            total_items = (
                len(course_pack.get('quizzes', [])) +
                len(course_pack.get('flashcardSets', [])) +
                len(course_pack.get('notes', [])) +
                len(course_pack.get('videoLectures', []))
            )
            library_items.append(LibraryItem(
                id=str(course_pack["_id"]),
                type="course_pack",
                title=course_pack.get("name", "Untitled Course"),
                description=course_pack.get("description", ""),
                coverImagePath=course_pack.get("coverImagePath") or fallback_image,
                createdAt=course_pack.get("createdAt", ""),
                itemCount=total_items,
                category=course_pack.get("category", ""),
                language=course_pack.get("language", ""),
                originalOwner=course_pack.get("originalOwner"),
                originalOwnerUsername=None,
                originalCoursePackId=course_pack.get("originalCoursePackId"),
                ownerId=course_pack.get("ownerId"),
                isPublic=course_pack.get("isPublic", False),
                rating=course_pack.get("rating", 0.0),
                enrolledCount=course_pack.get("enrolledCount", 0),
                estimatedHours=course_pack.get("estimatedHours", 0.0),
                videoCount=len(course_pack.get('videoLectures', []))
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
