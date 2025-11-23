from fastapi import APIRouter, HTTPException, Query
from typing import List
from datetime import datetime
from bson import ObjectId

from app.models.note import Note, NoteResponse, NoteLibraryItem, NoteLibraryResponse
from app.core.database import db

router = APIRouter(prefix="/notes", tags=["notes"])

# Get the notes collection
note_collection = db["notes"]

@router.post("", response_model=NoteResponse, summary="Create a new note")
async def create_note(note: Note):
    try:
        # Validate required fields
        if not note.title or not note.title.strip():
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        
        if not note.description or not note.description.strip():
            raise HTTPException(status_code=400, detail="Description cannot be empty")
        
        if not note.category or not note.category.strip():
            raise HTTPException(status_code=400, detail="Category cannot be empty")
        
        if not note.creatorId or not note.creatorId.strip():
            raise HTTPException(status_code=400, detail="creatorId is required")
        
        if not note.content or not note.content.strip():
            raise HTTPException(status_code=400, detail="Content cannot be empty")
        
        note_dict = note.dict()
        note_dict.pop("id", None)

        # Format timestamps as "Month, Year"
        now = datetime.utcnow()
        note_dict["createdAt"] = now.strftime("%B, %Y")
        note_dict["updatedAt"] = now.strftime("%B, %Y")

        # Set default cover image based on category if not provided
        if not note_dict.get("coverImagePath"):
            category = note_dict.get("category", "others").lower()
            if category == "language learning":
                note_dict["coverImagePath"] = "https://img.freepik.com/free-vector/notes-concept-illustration_114360-839.jpg?ga=GA1.1.377073698.1750732876&semt=ais_items_boosted&w=740"
            elif category == "science and technology":
                note_dict["coverImagePath"] = "https://img.freepik.com/free-vector/coding-concept-illustration_114360-1155.jpg?ga=GA1.1.377073698.1750732876&semt=ais_items_boosted&w=740"
            elif category == "law":
                note_dict["coverImagePath"] = "http://img.freepik.com/free-vector/law-firm-concept-illustration_114360-8626.jpg?ga=GA1.1.377073698.1750732876&semt=ais_items_boosted&w=740"
            else:
                note_dict["coverImagePath"] = "https://img.freepik.com/free-vector/student-asking-teacher-concept-illustration_114360-19831.jpg?ga=GA1.1.377073698.1750732876&semt=ais_items_boosted&w=740"

        result = await note_collection.insert_one(note_dict)
        return NoteResponse(
            id=str(result.inserted_id),
            message="Note created successfully"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/library", response_model=NoteLibraryResponse, summary="Get all notes for a user")
async def get_user_notes(user_id: str = Query(...)):
    try:
        notes = await note_collection.find({"creatorId": user_id}).to_list(1000)
        
        note_items = []
        for note in notes:
            note["id"] = str(note.pop("_id"))
            note_items.append(NoteLibraryItem(**note))
        
        return NoteLibraryResponse(
            success=True,
            data=note_items,
            count=len(note_items)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{note_id}", response_model=Note, summary="Get a specific note")
async def get_note(note_id: str, user_id: str = Query(...)):
    try:
        if not ObjectId.is_valid(note_id):
            raise HTTPException(status_code=400, detail="Invalid note ID")

        note = await note_collection.find_one(
            {"_id": ObjectId(note_id), "creatorId": user_id}
        )

        if not note:
            raise HTTPException(status_code=404, detail="Note not found")

        note["id"] = str(note.pop("_id"))
        return Note(**note)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{note_id}", response_model=NoteResponse, summary="Update a note")
async def update_note(note_id: str, note: Note):
    try:
        if not ObjectId.is_valid(note_id):
            raise HTTPException(status_code=400, detail="Invalid note ID")

        # Validate required fields
        if not note.title or not note.title.strip():
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        
        if not note.content or not note.content.strip():
            raise HTTPException(status_code=400, detail="Content cannot be empty")

        note_dict = note.dict()
        note_dict.pop("id", None)
        note_dict.pop("createdAt", None)  # Don't update created date
        
        # Update the updatedAt timestamp
        now = datetime.utcnow()
        note_dict["updatedAt"] = now.strftime("%B, %Y")

        result = await note_collection.update_one(
            {"_id": ObjectId(note_id), "creatorId": note.creatorId},
            {"$set": note_dict}
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Note not found")

        return NoteResponse(
            id=note_id,
            message="Note updated successfully"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{note_id}", summary="Delete a note")
async def delete_note(note_id: str, user_id: str = Query(...)):
    try:
        if not ObjectId.is_valid(note_id):
            raise HTTPException(status_code=400, detail="Invalid note ID")

        result = await note_collection.delete_one(
            {"_id": ObjectId(note_id), "creatorId": user_id}
        )

        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Note not found")

        return {"success": True, "message": "Note deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
