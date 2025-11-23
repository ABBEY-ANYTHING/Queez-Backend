from pydantic import BaseModel
from typing import Optional

class Note(BaseModel):
    id: Optional[str] = None
    title: str
    description: str
    category: str
    coverImagePath: Optional[str] = None
    creatorId: str
    content: str  # Quill Delta JSON as string
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

class NoteResponse(BaseModel):
    id: str
    message: str

class NoteLibraryItem(BaseModel):
    id: str
    title: str
    description: str
    category: str
    coverImagePath: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

class NoteLibraryResponse(BaseModel):
    success: bool
    data: list[NoteLibraryItem]
    count: int
