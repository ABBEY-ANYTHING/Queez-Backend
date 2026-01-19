from fastapi import APIRouter, HTTPException, status
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from bson import ObjectId

from app.core.database import db

router = APIRouter(prefix="/course-pack", tags=["Course Pack"])

# Get course_pack collection
course_pack_collection = db["course_pack"]


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


class VideoLecture(BaseModel):
    id: Optional[str] = None
    title: str
    driveFileId: str
    shareableLink: str
    duration: Optional[float] = 0.0  # Duration in minutes
    uploadedAt: Optional[str] = None


class CoursePack(BaseModel):
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
    videoLectures: List[VideoLecture] = []
    # Marketplace fields
    isPublic: bool = False
    rating: float = 0.0
    ratingCount: int = 0
    enrolledCount: int = 0
    estimatedHours: float = 0.0
    createdAt: str
    updatedAt: str


class CoursePackCreate(BaseModel):
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
    videoLectures: List[dict] = []
    isPublic: bool = False
    estimatedHours: float = 0.0


class CoursePackPublish(BaseModel):
    isPublic: bool = True


class VideoLectureAdd(BaseModel):
    title: str
    driveFileId: str
    shareableLink: str
    duration: Optional[float] = 0.0


def calculate_estimated_hours(course_data: dict) -> float:
    """Calculate estimated study hours based on content"""
    hours = 0.0
    
    # Quizzes: ~5 min per 10 questions
    for quiz in course_data.get('quizzes', []):
        question_count = len(quiz.get('questions', []))
        hours += (question_count / 10) * (5 / 60)  # Convert to hours
    
    # Flashcards: ~2 min per 10 cards
    for fs in course_data.get('flashcardSets', []):
        card_count = len(fs.get('cards', []))
        hours += (card_count / 10) * (2 / 60)
    
    # Notes: ~10 min per note
    hours += len(course_data.get('notes', [])) * (10 / 60)
    
    # Video lectures: actual duration
    for video in course_data.get('videoLectures', []):
        hours += video.get('duration', 0) / 60  # Convert minutes to hours
    
    return round(hours, 1)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_course_pack(course_pack: CoursePackCreate):
    """Create a new course pack"""
    try:
        course_pack_data = course_pack.dict()
        
        # Calculate estimated hours
        course_pack_data['estimatedHours'] = calculate_estimated_hours(course_pack_data)
        
        # Initialize marketplace fields
        course_pack_data['rating'] = 0.0
        course_pack_data['ratingCount'] = 0
        course_pack_data['enrolledCount'] = 0
        
        # Format timestamps
        now = datetime.utcnow()
        course_pack_data['createdAt'] = now.strftime("%B, %Y")
        course_pack_data['updatedAt'] = datetime.utcnow().isoformat()
        
        # Save to MongoDB
        result = await course_pack_collection.insert_one(course_pack_data)
        
        return {
            "id": str(result.inserted_id),
            "success": True,
            "message": "Course pack created successfully"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create course pack: {str(e)}"
        )


@router.get("/public")
async def get_public_course_packs(
    category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """Get all public course packs for marketplace"""
    try:
        query = {"isPublic": True}
        if category and category != "All":
            query["category"] = category
        
        cursor = course_pack_collection.find(query).sort("enrolledCount", -1).skip(offset).limit(limit)
        docs = await cursor.to_list(length=limit)
        
        course_packs = []
        for doc in docs:
            doc['id'] = str(doc['_id'])
            del doc['_id']
            course_packs.append(doc)
        
        # Get total count
        total = await course_pack_collection.count_documents(query)
        
        return {
            "success": True,
            "coursePacks": course_packs,
            "count": len(course_packs),
            "total": total
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch public course packs: {str(e)}"
        )


@router.get("/featured")
async def get_featured_course_packs(limit: int = 5):
    """Get featured course packs (highest rated public courses)"""
    try:
        cursor = course_pack_collection.find(
            {"isPublic": True}
        ).sort([("rating", -1), ("enrolledCount", -1)]).limit(limit)
        
        docs = await cursor.to_list(length=limit)
        
        course_packs = []
        for doc in docs:
            doc['id'] = str(doc['_id'])
            del doc['_id']
            course_packs.append(doc)
        
        return {
            "success": True,
            "coursePacks": course_packs,
            "count": len(course_packs)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch featured course packs: {str(e)}"
        )


@router.get("/{course_pack_id}")
async def get_course_pack(course_pack_id: str):
    """Get a course pack by ID"""
    try:
        doc = await course_pack_collection.find_one({"_id": ObjectId(course_pack_id)})
        
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Course pack not found"
            )
        
        doc['id'] = str(doc['_id'])
        del doc['_id']
        
        return {
            "success": True,
            "coursePack": doc
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch course pack: {str(e)}"
        )


@router.get("/user/{user_id}")
async def get_user_course_packs(user_id: str):
    """Get all course packs for a user"""
    try:
        cursor = course_pack_collection.find({"ownerId": user_id}).sort("updatedAt", -1)
        docs = await cursor.to_list(length=None)
        
        course_packs = []
        for doc in docs:
            doc['id'] = str(doc['_id'])
            del doc['_id']
            course_packs.append(doc)
        
        return {
            "success": True,
            "coursePacks": course_packs,
            "count": len(course_packs)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch course packs: {str(e)}"
        )


@router.put("/{course_pack_id}")
async def update_course_pack(course_pack_id: str, course_pack: CoursePackCreate):
    """Update a course pack"""
    try:
        existing = await course_pack_collection.find_one({"_id": ObjectId(course_pack_id)})
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Course pack not found"
            )
        
        course_pack_data = course_pack.dict()
        course_pack_data['estimatedHours'] = calculate_estimated_hours(course_pack_data)
        course_pack_data['updatedAt'] = datetime.utcnow().isoformat()
        
        await course_pack_collection.update_one(
            {"_id": ObjectId(course_pack_id)},
            {"$set": course_pack_data}
        )
        
        return {
            "success": True,
            "message": "Course pack updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update course pack: {str(e)}"
        )


@router.post("/{course_pack_id}/publish")
async def publish_course_pack(course_pack_id: str, publish_data: CoursePackPublish):
    """Publish or unpublish a course pack to marketplace"""
    try:
        existing = await course_pack_collection.find_one({"_id": ObjectId(course_pack_id)})
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Course pack not found"
            )
        
        await course_pack_collection.update_one(
            {"_id": ObjectId(course_pack_id)},
            {"$set": {"isPublic": publish_data.isPublic, "updatedAt": datetime.utcnow().isoformat()}}
        )
        
        action = "published to" if publish_data.isPublic else "removed from"
        return {
            "success": True,
            "message": f"Course pack {action} marketplace"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update course pack: {str(e)}"
        )


@router.post("/{course_pack_id}/enroll")
async def enroll_in_course_pack(course_pack_id: str, user_id: str):
    """Enroll in a course pack (increment enrollment counter)"""
    try:
        existing = await course_pack_collection.find_one({"_id": ObjectId(course_pack_id)})
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Course pack not found"
            )
        
        await course_pack_collection.update_one(
            {"_id": ObjectId(course_pack_id)},
            {"$inc": {"enrolledCount": 1}}
        )
        
        return {
            "success": True,
            "message": "Successfully enrolled in course pack"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to enroll: {str(e)}"
        )


@router.post("/{course_pack_id}/rate")
async def rate_course_pack(course_pack_id: str, rating: float):
    """Rate a course pack (updates average rating)"""
    try:
        if rating < 1 or rating > 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Rating must be between 1 and 5"
            )
        
        existing = await course_pack_collection.find_one({"_id": ObjectId(course_pack_id)})
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Course pack not found"
            )
        
        # Calculate new average rating
        current_rating = existing.get('rating', 0)
        rating_count = existing.get('ratingCount', 0)
        
        new_count = rating_count + 1
        new_rating = ((current_rating * rating_count) + rating) / new_count
        
        await course_pack_collection.update_one(
            {"_id": ObjectId(course_pack_id)},
            {"$set": {"rating": round(new_rating, 1), "ratingCount": new_count}}
        )
        
        return {
            "success": True,
            "message": "Rating submitted successfully",
            "newRating": round(new_rating, 1)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to rate course pack: {str(e)}"
        )


@router.post("/{course_pack_id}/video")
async def add_video_lecture(course_pack_id: str, video: VideoLectureAdd):
    """Add a video lecture to a course pack"""
    try:
        existing = await course_pack_collection.find_one({"_id": ObjectId(course_pack_id)})
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Course pack not found"
            )
        
        video_data = video.dict()
        video_data['id'] = str(ObjectId())
        video_data['uploadedAt'] = datetime.utcnow().isoformat()
        
        # Add video and recalculate estimated hours
        await course_pack_collection.update_one(
            {"_id": ObjectId(course_pack_id)},
            {
                "$push": {"videoLectures": video_data},
                "$set": {"updatedAt": datetime.utcnow().isoformat()}
            }
        )
        
        # Recalculate estimated hours
        updated = await course_pack_collection.find_one({"_id": ObjectId(course_pack_id)})
        new_hours = calculate_estimated_hours(updated)
        await course_pack_collection.update_one(
            {"_id": ObjectId(course_pack_id)},
            {"$set": {"estimatedHours": new_hours}}
        )
        
        return {
            "success": True,
            "message": "Video lecture added successfully",
            "videoId": video_data['id']
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add video lecture: {str(e)}"
        )


@router.delete("/{course_pack_id}/video/{video_id}")
async def remove_video_lecture(course_pack_id: str, video_id: str):
    """Remove a video lecture from a course pack"""
    try:
        existing = await course_pack_collection.find_one({"_id": ObjectId(course_pack_id)})
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Course pack not found"
            )
        
        await course_pack_collection.update_one(
            {"_id": ObjectId(course_pack_id)},
            {
                "$pull": {"videoLectures": {"id": video_id}},
                "$set": {"updatedAt": datetime.utcnow().isoformat()}
            }
        )
        
        # Recalculate estimated hours
        updated = await course_pack_collection.find_one({"_id": ObjectId(course_pack_id)})
        new_hours = calculate_estimated_hours(updated)
        await course_pack_collection.update_one(
            {"_id": ObjectId(course_pack_id)},
            {"$set": {"estimatedHours": new_hours}}
        )
        
        return {
            "success": True,
            "message": "Video lecture removed successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to remove video lecture: {str(e)}"
        )


@router.delete("/{course_pack_id}")
async def delete_course_pack(course_pack_id: str):
    """Delete a course pack"""
    try:
        existing = await course_pack_collection.find_one({"_id": ObjectId(course_pack_id)})
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Course pack not found"
            )
        
        await course_pack_collection.delete_one({"_id": ObjectId(course_pack_id)})
        
        return {
            "success": True,
            "message": "Course pack deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete course pack: {str(e)}"
        )


@router.get("/{course_pack_id}/stats")
async def get_course_pack_stats(course_pack_id: str):
    """Get statistics for a course pack"""
    try:
        doc = await course_pack_collection.find_one({"_id": ObjectId(course_pack_id)})
        
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Course pack not found"
            )
        
        stats = {
            "totalQuizzes": len(doc.get('quizzes', [])),
            "totalFlashcardSets": len(doc.get('flashcardSets', [])),
            "totalNotes": len(doc.get('notes', [])),
            "totalVideoLectures": len(doc.get('videoLectures', [])),
            "totalItems": (
                len(doc.get('quizzes', [])) +
                len(doc.get('flashcardSets', [])) +
                len(doc.get('notes', [])) +
                len(doc.get('videoLectures', []))
            ),
            "totalQuestions": sum(
                len(quiz.get('questions', [])) 
                for quiz in doc.get('quizzes', [])
            ),
            "totalFlashcards": sum(
                len(fs.get('cards', [])) 
                for fs in doc.get('flashcardSets', [])
            ),
            "rating": doc.get('rating', 0),
            "ratingCount": doc.get('ratingCount', 0),
            "enrolledCount": doc.get('enrolledCount', 0),
            "estimatedHours": doc.get('estimatedHours', 0),
            "isPublic": doc.get('isPublic', False)
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
            detail=f"Failed to fetch course pack stats: {str(e)}"
        )
