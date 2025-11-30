from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

from app.services.session_manager import SessionManager
from app.core.database import collection
from bson import ObjectId

router = APIRouter(prefix="/api/multiplayer", tags=["live-multiplayer"])
logger = logging.getLogger(__name__)

session_manager = SessionManager()


class CreateLiveSessionRequest(BaseModel):
    quiz_id: str
    host_id: str
    mode: str = "live"
    per_question_time_limit: Optional[int] = 30  # Default 30 seconds per question


class CreateLiveSessionResponse(BaseModel):
    success: bool
    session_code: str
    message: str


@router.post("/create-session", response_model=CreateLiveSessionResponse)
async def create_live_session(request: CreateLiveSessionRequest):
    """
    Create a new live multiplayer session stored in Redis.
    This is different from the regular session endpoint which uses MongoDB.
    """
    try:
        # Verify quiz exists
        quiz = await collection.find_one({"_id": ObjectId(request.quiz_id)})
        if not quiz:
            raise HTTPException(status_code=404, detail="Quiz not found")
        
        # Create session in Redis
        session_code = await session_manager.create_session(
            quiz_id=request.quiz_id,
            host_id=request.host_id,
            mode=request.mode,
            per_question_time_limit=request.per_question_time_limit
        )
        
        logger.info(f"Created live session {session_code} for quiz {request.quiz_id}")
        
        return CreateLiveSessionResponse(
            success=True,
            session_code=session_code,
            message=f"Live session created successfully. Session code: {session_code}"
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating live session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error creating live session: {str(e)}")


@router.get("/session/{session_code}")
async def get_live_session(session_code: str):
    """Get live session information from Redis"""
    try:
        session = await session_manager.get_session(session_code)
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return {
            "success": True,
            "session": session
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting live session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error retrieving session: {str(e)}")



class ParticipantJoin(BaseModel):
    user_id: str
    username: str


@router.get("/session/{session_code}/participants")
async def get_session_participants(session_code: str):
    """Get participants for a live multiplayer session from Redis"""
    try:
        session = await session_manager.get_session(session_code)
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found or expired")
        
        participants = session.get("participants", {})
        participant_list = [
            {
                "user_id": p.get("user_id", ""),
                "username": p.get("username", "Anonymous"),
                "joined_at": p.get("joined_at", ""),
                "score": p.get("score", 0),
                "connected": p.get("connected", False)
            }
            for p in participants.values()
        ]
        
        return {
            "success": True,
            "session_code": session_code,
            "participant_count": len(participants),
            "participants": participant_list,
            "mode": session.get("mode", ""),
            "is_started": session.get("status") == "active"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting participants: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error retrieving participants: {str(e)}")


@router.post("/session/{session_code}/join")
async def join_session(session_code: str, participant: ParticipantJoin):
    """Join a live session"""
    try:
        session = await session_manager.get_session(session_code)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found or expired")
            
        if session.get("status") != "waiting":
             # Allow rejoining if already in participants
            participants = session.get("participants", {})
            if participant.user_id not in participants:
                raise HTTPException(status_code=400, detail="Quiz has already started")

        success = await session_manager.add_participant(
            session_code, 
            participant.user_id, 
            participant.username
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to join session")
            
        # Get updated count
        session = await session_manager.get_session(session_code)
        participants = session.get("participants", {})
        
        return {
            "success": True,
            "message": "Successfully joined the session",
            "session_code": session_code,
            "participant_count": len(participants),
            "quiz_id": session["quiz_id"]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error joining session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error joining session: {str(e)}")


# Defining SessionAction model for start/end
class SessionAction(BaseModel):
    host_id: str

@router.post("/session/{session_code}/start")
async def start_quiz_session(session_code: str, action: SessionAction):
    try:
        session = await session_manager.get_session(session_code)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
            
        if session["host_id"] != action.host_id:
            raise HTTPException(status_code=403, detail="Only host can start")
            
        success = await session_manager.start_session(session_code, action.host_id)
        if not success:
             raise HTTPException(status_code=400, detail="Failed to start session")
             
        return {
            "success": True,
            "message": "Quiz started successfully",
            "session_code": session_code
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error starting session: {str(e)}")


@router.post("/session/{session_code}/end")
async def end_quiz_session(session_code: str, action: SessionAction):
    try:
        session = await session_manager.get_session(session_code)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
            
        if session["host_id"] != action.host_id:
            raise HTTPException(status_code=403, detail="Only host can end")
            
        success = await session_manager.end_session(session_code)
        
        return {
            "success": True,
            "message": "Quiz session ended",
            "session_code": session_code
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error ending session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error ending session: {str(e)}")


@router.post("/session/{session_code}/validate")
async def validate_session(session_code: str):
    """Validate if a session code exists and is active"""
    try:
        session = await session_manager.get_session(session_code)
        
        if not session:
            return {
                "success": False,
                "valid": False,
                "message": "Session not found"
            }
        
        return {
            "success": True,
            "valid": True,
            "session_code": session_code,
            "status": session.get("status"),
            "quiz_title": session.get("quiz_title"),
            "participant_count": len(session.get("participants", {}))
        }
    
    except Exception as e:
        logger.error(f"Error validating session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error validating session: {str(e)}")


@router.get("/user/{user_id}/active-session")
async def get_user_active_session(user_id: str):
    """
    Check if a user has an active session they can rejoin.
    This is used when the app restarts to resume an in-progress quiz.
    
    Returns session info if user is either:
    - A participant in an active/waiting session
    - The host of an active/waiting session
    """
    from app.core.database import redis_client
    from app.services.game_controller import GameController
    
    try:
        game_controller = GameController()
        
        # Check Redis for user's active session
        # We store this when user joins a session
        active_session_key = f"user_active_session:{user_id}"
        session_code = await redis_client.get(active_session_key)
        
        if not session_code:
            return {
                "success": True,
                "has_active_session": False,
                "message": "No active session found"
            }
        
        # Get session details
        session = await session_manager.get_session(session_code)
        
        if not session:
            # Session expired, clean up
            await redis_client.delete(active_session_key)
            return {
                "success": True,
                "has_active_session": False,
                "message": "Previous session has expired"
            }
        
        # Check if session is still active or waiting
        status = session.get("status")
        if status == "completed":
            # Session ended, clean up
            await redis_client.delete(active_session_key)
            return {
                "success": True,
                "has_active_session": False,
                "message": "Previous session has ended",
                "session_ended": True,
                "session_code": session_code
            }
        
        # Determine user's role
        is_host = session.get("host_id") == user_id
        participants = session.get("participants", {})
        is_participant = user_id in participants
        
        if not is_host and not is_participant:
            # User is not in this session anymore
            await redis_client.delete(active_session_key)
            return {
                "success": True,
                "has_active_session": False,
                "message": "You are no longer in this session"
            }
        
        # Get user's progress if they're a participant
        user_progress = None
        if is_participant:
            participant = participants.get(user_id, {})
            question_index = await game_controller.get_participant_question_index(session_code, user_id)
            total_questions = await game_controller.get_total_questions(session_code)
            
            user_progress = {
                "username": participant.get("username", "Anonymous"),
                "score": participant.get("score", 0),
                "current_question_index": question_index,
                "total_questions": total_questions,
                "answers_count": len(participant.get("answers", [])),
                "completed": question_index >= total_questions if total_questions > 0 else False
            }
        
        return {
            "success": True,
            "has_active_session": True,
            "session_code": session_code,
            "status": status,
            "quiz_id": session.get("quiz_id"),
            "quiz_title": session.get("quiz_title"),
            "mode": session.get("mode", "live_multiplayer"),
            "host_id": session.get("host_id"),
            "is_host": is_host,
            "is_participant": is_participant,
            "user_progress": user_progress,
            "participant_count": len(participants)
        }
    
    except Exception as e:
        logger.error(f"Error checking active session for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error checking active session: {str(e)}")


@router.delete("/user/{user_id}/active-session")
async def clear_user_active_session(user_id: str):
    """
    Clear the user's active session tracking.
    Called when user manually leaves a quiz or session ends.
    """
    from app.core.database import redis_client
    
    try:
        active_session_key = f"user_active_session:{user_id}"
        await redis_client.delete(active_session_key)
        
        return {
            "success": True,
            "message": "Active session cleared"
        }
    
    except Exception as e:
        logger.error(f"Error clearing active session for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error clearing active session: {str(e)}")
