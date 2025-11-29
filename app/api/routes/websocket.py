from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.services.connection_manager import manager
from app.services.session_manager import SessionManager
from app.services.game_controller import GameController
from app.services.leaderboard_manager import LeaderboardManager
from app.core.database import get_redis
from app.core.config import SESSION_EXPIRY_HOURS
import json
import logging
import asyncio

router = APIRouter()
logger = logging.getLogger(__name__)

session_manager = SessionManager()
game_controller = GameController()
leaderboard_manager = LeaderboardManager()

# Store active timers for auto-advance
active_timers = {}

# Semaphore to limit concurrent answer processing per session
answer_semaphores: dict[str, asyncio.Semaphore] = {}

def get_answer_semaphore(session_code: str) -> asyncio.Semaphore:
    """Get or create a semaphore for answer processing in a session"""
    if session_code not in answer_semaphores:
        # Allow up to 10 concurrent answer submissions per session
        answer_semaphores[session_code] = asyncio.Semaphore(10)
    return answer_semaphores[session_code]

@router.websocket("/api/ws/{session_code}")
async def websocket_endpoint(websocket: WebSocket, session_code: str, user_id: str = Query(...)):
    """
    WebSocket endpoint for real-time quiz sessions
    """
    # SECURITY: Validate session code format (6 alphanumeric characters)
    import re
    if not session_code or not re.match(r'^[A-Z0-9]{6}$', session_code.upper()):
        logger.warning(f"Invalid session code format: {session_code}")
        await websocket.close(code=4001, reason="Invalid session code format")
        return
    
    # SECURITY: Validate user_id format (prevent injection)
    if not user_id or len(user_id) > 128 or not re.match(r'^[a-zA-Z0-9_-]+$', user_id):
        logger.warning(f"Invalid user_id format: {user_id}")
        await websocket.close(code=4002, reason="Invalid user ID format")
        return
    
    session_code = session_code.upper()  # Normalize to uppercase
    
    # === CRITICAL: Accept connection FIRST ===
    try:
        await websocket.accept()
        logger.info(f"WebSocket accepted for session={session_code}, user={user_id}")
    except Exception as e:
        logger.error(f"Failed to accept WebSocket: {e}")
        return

    # Check if user is host
    is_host = await session_manager.is_host(session_code, user_id)
    
    # Register connection
    await manager.connect(websocket, session_code, user_id, is_host)
    logger.info(f"Connection registered for user={user_id}, is_host={is_host}")

    try:
        # Listen for messages
        async for message_data in websocket.iter_text():
            try:
                # SECURITY: Limit message size to prevent DoS
                MAX_MESSAGE_SIZE = 10000  # 10KB max
                if len(message_data) > MAX_MESSAGE_SIZE:
                    logger.warning(f"Message too large from {user_id}: {len(message_data)} bytes")
                    await manager.send_personal_message({
                        "type": "error",
                        "payload": {"message": "Message too large"}
                    }, websocket)
                    continue
                
                message = json.loads(message_data)
                message_type = message.get("type")
                payload = message.get("payload", {})
                
                logger.debug(f"📨 Received message type={message_type} from user={user_id}")

                if message_type == "join":
                    await handle_join(websocket, session_code, user_id, payload)
                
                elif message_type == "start_quiz":
                    await handle_start_quiz(websocket, session_code, user_id, payload)
                
                elif message_type == "submit_answer":
                    logger.debug(f"Processing submit_answer from {user_id}")
                    # Use semaphore to prevent overwhelming Redis
                    sem = get_answer_semaphore(session_code)
                    async with sem:
                        await handle_submit_answer(websocket, session_code, user_id, payload)
                
                elif message_type == "next_question":
                    await handle_next_question(websocket, session_code, user_id)
                
                elif message_type == "request_next_question":
                    await handle_request_next_question(websocket, session_code, user_id)
                
                elif message_type == "end_quiz":
                    await handle_end_quiz(websocket, session_code, user_id)
                
                elif message_type == "request_leaderboard":
                    await handle_request_leaderboard(websocket, session_code, user_id)
                
                elif message_type == "ping":
                    # Handle ping/pong for keepalive
                    await manager.send_personal_message({"type": "pong"}, websocket)
                
                else:
                    logger.warning(f"Unknown message type: {message_type}")

            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON: {e}")
            except Exception as e:
                logger.error(f"Error processing message: {e}", exc_info=True)

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for user={user_id}")
    except Exception as e:
        logger.error(f"WebSocket error for user={user_id}: {e}")
    finally:
        manager.disconnect(websocket, session_code, user_id)
        
        # Handle host disconnect - notify participants
        if is_host:
            session = await session_manager.get_session(session_code)
            if session and session.get("status") == "active":
                # Host left during active quiz - notify participants
                await manager.broadcast_to_session({
                    "type": "host_disconnected",
                    "payload": {"message": "Host has disconnected. Quiz may be interrupted."}
                }, session_code)
                logger.warning(f"Host {user_id} disconnected during active session {session_code}")
        
        logger.info(f"User {user_id} disconnected from session {session_code}")


async def handle_join(websocket: WebSocket, session_code: str, user_id: str, payload: dict):
    username = payload.get("username", "Anonymous")
    
    # Validate session
    session = await session_manager.get_session(session_code)
    if not session:
        logger.warning(f"Session {session_code} not found")
        await manager.send_personal_message({"type": "error", "payload": {"message": "Session not found"}}, websocket)
        return
    
    # Check if session has expired
    expires_at = session.get("expires_at")
    if expires_at:
        try:
            expiry_time = datetime.fromisoformat(expires_at)
            if datetime.utcnow() > expiry_time:
                logger.warning(f"Session {session_code} has expired")
                await manager.send_personal_message({"type": "error", "payload": {"message": "Session has expired"}}, websocket)
                return
        except (ValueError, TypeError):
            pass  # Invalid date format, skip check
    
    # ✅ CHECK IF USER IS HOST FIRST
    is_host = await session_manager.is_host(session_code, user_id)
    
    if is_host:
        # Host is joining - send session state without adding to participants
        logger.info(f"Host {user_id} joined session {session_code}")
        
        # Prepare session payload with participants as list
        session_payload = {**session}
        participants_list = list(session.get("participants", {}).values())
        session_payload["participants"] = participants_list
        session_payload["participant_count"] = len(participants_list)
        
        # Track host's active session for reconnection
        redis = await get_redis()
        from app.core.config import SESSION_EXPIRY_HOURS
        active_session_key = f"user_active_session:{user_id}"
        await redis.set(active_session_key, session_code, ex=SESSION_EXPIRY_HOURS * 3600)
        
        # Check if host is reconnecting during active quiz
        if session.get("status") == "active":
            # Notify participants that host is back
            await manager.broadcast_to_session({
                "type": "host_reconnected",
                "payload": {"message": "Host has reconnected"}
            }, session_code)
            logger.info(f"Host {user_id} reconnected to active session {session_code}")
        
        # Send session state to host
        await manager.send_personal_message({
            "type": "session_state",
            "payload": session_payload
        }, websocket)
        return  # Done - host doesn't get added to participants
    
    # ✅ REGULAR PARTICIPANT LOGIC BELOW
    participants = session.get("participants", {})
    is_reconnecting = user_id in participants
    
    # Check if session is still accepting new participants
    if session["status"] != "waiting" and not is_reconnecting:
        await manager.send_personal_message({"type": "error", "payload": {"message": "Session is already active"}}, websocket)
        return
    
    # Add or reconnect participant
    success = await session_manager.add_participant(session_code, user_id, username)
    
    if success:
        logger.info(f"{'Reconnected' if is_reconnecting else 'Added'} {username} to session {session_code}")
        
        # Broadcast update to all
        session = await session_manager.get_session(session_code)
        participants_list = list(session["participants"].values())
        
        await manager.broadcast_to_session({
            "type": "session_update",
            "payload": {
                "status": session["status"],
                "participant_count": len(participants_list),
                "participants": participants_list
            }
        }, session_code)
        
        # Send current state to this participant
        session_payload = {**session}
        session_payload["participants"] = participants_list
        session_payload["participant_count"] = len(participants_list)
        
        await manager.send_personal_message({
            "type": "session_state",
            "payload": session_payload
        }, websocket)
        
        # Handle different session states for reconnecting/joining users
        if session["status"] == "completed":
            # Quiz already finished - send final results
            final_results = await leaderboard_manager.get_final_results(session_code)
            await manager.send_personal_message({
                "type": "quiz_completed",
                "payload": {
                    "message": "This quiz has already ended.",
                    "results": final_results
                }
            }, websocket)
        elif session["status"] == "active":
            # Quiz in progress - send THIS user's current question (not the broadcast one)
            user_question_index = await game_controller.get_participant_question_index(session_code, user_id)
            total_questions = await game_controller.get_total_questions(session_code)
            
            # EDGE CASE: If user rejoins and their index was never set, initialize to 0
            redis = await get_redis()
            index_key = f"participant:{session_code}:{user_id}:question_index"
            stored_index = await redis.get(index_key)
            if stored_index is None and not is_reconnecting:
                # New joiner during active quiz - they start from question 0
                await game_controller.set_participant_question_index(session_code, user_id, 0)
                user_question_index = 0
                logger.info(f"Initialized question index for late joiner {user_id}")
            
            if user_question_index >= total_questions:
                # User already completed - send their results
                final_results = await leaderboard_manager.get_final_results(session_code)
                await manager.send_personal_message({
                    "type": "quiz_completed",
                    "payload": {
                        "message": "You've completed all questions!",
                        "results": final_results
                    }
                }, websocket)
            else:
                # Send the question at THEIR current index
                question_data = await game_controller.get_question_by_index(session_code, user_question_index)
                if question_data:
                    await manager.send_personal_message({
                        "type": "question",
                        "payload": question_data
                    }, websocket)
    else:
        logger.error(f"Failed to add {username} to session {session_code}")


async def auto_advance_question(session_code: str, time_limit: int, question_index: int):
    """Auto-advance to next question after timeout"""
    try:
        await asyncio.sleep(time_limit + 2)  # Wait for time limit + 2 second buffer
        
        # Check if session is still active
        session = await session_manager.get_session(session_code)
        if not session or session.get("status") != "active":
            return
        
        # Check if we're still on the same question
        from app.core.database import redis_client
        current_index = await redis_client.hget(f"session:{session_code}", "current_question_index")
        if current_index and int(current_index) != question_index:
            return
        
        logger.debug(f"Auto-advancing session {session_code} past Q{question_index}")
        
        # Get next question
        question_data = await game_controller.next_question(session_code)
        
        if question_data:
            # Send next question to all
            await manager.broadcast_to_session({
                "type": "question",
                "payload": question_data
            }, session_code)
            
            # Start timer for next question
            next_time_limit = question_data.get("time_limit", time_limit)
            timer_key = f"{session_code}:{question_index + 1}"
            active_timers[timer_key] = asyncio.create_task(
                auto_advance_question(session_code, next_time_limit, question_index + 1)
            )
        else:
            # No more questions - end quiz
            logger.info(f"Quiz ended for session {session_code}")
            await session_manager.end_session(session_code)
            
            # Get final results
            final_results = await leaderboard_manager.get_final_results(session_code)
            
            # Broadcast quiz end
            await manager.broadcast_to_session({
                "type": "quiz_ended",
                "payload": {
                    "message": "Quiz completed!",
                    "results": final_results
                }
            }, session_code)
    except Exception as e:
        logger.error(f"Auto-advance error: {e}")


async def handle_start_quiz(websocket: WebSocket, session_code: str, user_id: str, payload: dict = None):
    """Host starts the quiz"""
    is_host = await session_manager.is_host(session_code, user_id)
    
    if not is_host:
        await manager.send_personal_message({
            "type": "error",
            "payload": {"message": "Only host can start the quiz"}
        }, websocket)
        return
    
    # Check if quiz has questions
    total_questions = await game_controller.get_total_questions(session_code)
    if total_questions == 0:
        await manager.send_personal_message({
            "type": "error",
            "payload": {"message": "Cannot start quiz: No questions found"}
        }, websocket)
        return
    
    # Check if there are any participants
    session = await session_manager.get_session(session_code)
    if not session:
        await manager.send_personal_message({
            "type": "error",
            "payload": {"message": "Session not found"}
        }, websocket)
        return
    
    participants = session.get("participants", {})
    if len(participants) == 0:
        await manager.send_personal_message({
            "type": "error",
            "payload": {"message": "Cannot start quiz: No participants have joined"}
        }, websocket)
        return
    
    # Extract time settings from payload
    if session.get("status") == "active":
        await manager.send_personal_message({
            "type": "error",
            "payload": {"message": "Quiz has already started"}
        }, websocket)
        return
    
    if session.get("status") == "completed":
        await manager.send_personal_message({
            "type": "error",
            "payload": {"message": "Quiz has already ended"}
        }, websocket)
        return
    
    # Extract time settings from payload
    if payload:
        per_question_time_limit = payload.get('per_question_time_limit', 30)
        
        # Update session with time settings
        from app.core.database import redis_client
        await redis_client.hset(f"session:{session_code}", "per_question_time_limit", per_question_time_limit)
    
    # Start the quiz - update session status
    success = await session_manager.start_session(session_code, user_id)
    if not success:
        await manager.send_personal_message({
            "type": "error",
            "payload": {"message": "Failed to start session"}
        }, websocket)
        return
    
    # Initialize all participants to question 0
    session = await session_manager.get_session(session_code)
    participants = session.get("participants", {})
    
    for participant_id in participants.keys():
        await game_controller.set_participant_question_index(session_code, participant_id, 0)
    
    # Start the question timer
    await game_controller.start_question_timer(session_code)
    
    # Get first question (index 0)
    question_data = await game_controller.get_question_by_index(session_code, 0)
    
    if not question_data:
        await manager.send_personal_message({
            "type": "error",
            "payload": {"message": "No questions available"}
        }, websocket)
        return
    
    # Get time settings for the quiz_started payload
    per_question_time_limit = int(session.get("per_question_time_limit", 30))
    
    # Broadcast quiz started to all participants with time settings
    await manager.broadcast_to_session({
        "type": "quiz_started",
        "payload": {
            "message": "Quiz is starting!",
            "per_question_time_limit": per_question_time_limit
        }
    }, session_code)
    
    # Send first question to all participants
    await manager.broadcast_to_session({
        "type": "question",
        "payload": question_data
    }, session_code)
    
    # Start auto-advance timer for first question
    timer_key = f"{session_code}:0"
    active_timers[timer_key] = asyncio.create_task(
        auto_advance_question(session_code, per_question_time_limit, 0)
    )



async def handle_submit_answer(websocket: WebSocket, session_code: str, user_id: str, payload: dict):
    """Participant submits an answer"""
    try:
        # Check if user is host - hosts cannot participate
        is_host = await session_manager.is_host(session_code, user_id)
        if is_host:
            logger.debug(f"Host {user_id} tried to submit answer - ignoring")
            await manager.send_personal_message({
                "type": "error",
                "payload": {"message": "Host cannot participate in quiz"}
            }, websocket)
            return
        
        # RATE LIMIT: Prevent answer spam (max 1 answer per 0.5 seconds per user)
        redis = await get_redis()
        rate_key = f"rate:answer:{session_code}:{user_id}"
        last_submit = await redis.get(rate_key)
        if last_submit:
            await manager.send_personal_message({
                "type": "error",
                "payload": {"message": "Please wait before submitting again"}
            }, websocket)
            return
        await redis.set(rate_key, "1", ex=1)  # 1 second cooldown
        
        answer = payload.get("answer")
        timestamp = payload.get("timestamp", datetime.utcnow().timestamp())
        is_timeout = payload.get("timeout", False)
        
        # Allow null answers for timeouts
        if answer is None and not is_timeout:
            await manager.send_personal_message({
                "type": "error",
                "payload": {"message": "Invalid answer submission"}
            }, websocket)
            return
        
        # Process answer
        result = await game_controller.submit_answer(
            session_code, user_id, answer, timestamp
        )
        
        # Check for errors
        if "error" in result:
            await manager.send_personal_message({
                "type": "answer_result",
                "payload": result
            }, websocket)
            return
        
        # Send result to participant
        await manager.send_personal_message({
            "type": "answer_result",
            "payload": result
        }, websocket)
        
        # ✅ ALWAYS broadcast leaderboard after each answer
        # This ensures the host sees real-time progress
        leaderboard = await leaderboard_manager.get_leaderboard(session_code)
        
        await manager.broadcast_to_session({
            "type": "leaderboard_update",
            "payload": {"leaderboard": leaderboard}
        }, session_code)
    
    except Exception as e:
        logger.error(f"Error processing answer for {user_id}: {e}", exc_info=True)
        try:
            await manager.send_personal_message({
                "type": "error",
                "payload": {"message": "Error processing answer"}
            }, websocket)
        except:
            pass


async def handle_next_question(websocket: WebSocket, session_code: str, user_id: str):
    """Host moves to next question (broadcast to all)"""
    is_host = await session_manager.is_host(session_code, user_id)
    
    if not is_host:
        await manager.send_personal_message({
            "type": "error",
            "payload": {"message": "Only host can control questions"}
        }, websocket)
        return
    
    # Cancel existing timer for current question
    from app.core.database import redis_client
    current_index = await redis_client.hget(f"session:{session_code}", "current_question_index")
    if current_index:
        timer_key = f"{session_code}:{current_index}"
        if timer_key in active_timers:
            active_timers[timer_key].cancel()
            del active_timers[timer_key]
    
    # Get next question
    question_data = await game_controller.next_question(session_code)
    
    if question_data:
        # Send next question
        await manager.broadcast_to_session({
            "type": "question",
            "payload": question_data
        }, session_code)
        
        # Start auto-advance timer for new question
        next_index = question_data.get("index", 0)
        time_limit = question_data.get("time_limit", 30)
        timer_key = f"{session_code}:{next_index}"
        active_timers[timer_key] = asyncio.create_task(
            auto_advance_question(session_code, time_limit, next_index)
        )
    else:
        # No more questions - quiz complete
        await handle_end_quiz(websocket, session_code, user_id)


async def handle_request_next_question(websocket: WebSocket, session_code: str, user_id: str):
    """Participant requests their next question (self-paced)
    
    NOTE: The participant's question index is already advanced after they answer.
    So we just need to fetch the question at their current index.
    """
    try:
        # Check if user is host - hosts don't participate
        is_host = await session_manager.is_host(session_code, user_id)
        if is_host:
            return  # Silently ignore host requests for questions
        
        # Check if session is still active
        session = await session_manager.get_session(session_code)
        if not session:
            await manager.send_personal_message({
                "type": "error",
                "payload": {"message": "Session not found"}
            }, websocket)
            return
        
        if session.get("status") == "completed":
            # Quiz already ended - send final results
            final_results = await leaderboard_manager.get_final_results(session_code)
            await manager.send_personal_message({
                "type": "quiz_completed",
                "payload": {
                    "message": "This quiz has already ended.",
                    "results": final_results
                }
            }, websocket)
            return
        
        # Get participant's current index (already advanced after answering)
        current_index = await game_controller.get_participant_question_index(session_code, user_id)
        total_questions = await game_controller.get_total_questions(session_code)
        
        if total_questions == 0:
            await manager.send_personal_message({
                "type": "error",
                "payload": {"message": "Quiz not found"}
            }, websocket)
            return
        
        # Check if participant has already completed all questions
        if current_index >= total_questions:
            # Check if we already marked this user as completed (to avoid duplicate logs)
            redis = await get_redis()
            completed_key = f"completed:{session_code}:{user_id}"
            already_completed = await redis.get(completed_key)
            
            if not already_completed:
                # First time completing - log and mark
                await redis.set(completed_key, "1", ex=3600)  # 1 hour expiry
                logger.info(f"Participant {user_id} completed all questions in session {session_code}")
                
                # Check if ALL participants have completed
                await check_all_participants_completed(session_code, total_questions)
            
            # Always send completion message to participant (they might have refreshed)
            final_results = await leaderboard_manager.get_final_results(session_code)
            await manager.send_personal_message({
                "type": "quiz_completed",
                "payload": {
                    "message": "You've completed all questions!",
                    "results": final_results
                }
            }, websocket)
            return
        
        # Get the question at current index
        question_data = await game_controller.get_question_by_index(session_code, current_index)
        
        if question_data:
            # Send question to this participant only
            await manager.send_personal_message({
                "type": "question",
                "payload": question_data
            }, websocket)
        else:
            # Edge case: question_data is None but index < total (shouldn't happen)
            logger.warning(f"No question data for index {current_index} in session {session_code}")
    
    except Exception as e:
        logger.error(f"Error getting next question for {user_id}: {e}", exc_info=True)
        try:
            await manager.send_personal_message({
                "type": "error",
                "payload": {"message": "Error getting next question"}
            }, websocket)
        except:
            pass


async def check_all_participants_completed(session_code: str, total_questions: int):
    """Check if all participants have completed the quiz and broadcast quiz_ended if so"""
    try:
        # Use Redis lock to prevent multiple concurrent completion checks
        redis = await get_redis()
        completion_lock_key = f"completion_check:{session_code}"
        
        # Try to acquire the completion check lock (only one check at a time)
        lock_acquired = await redis.set(completion_lock_key, "1", ex=30, nx=True)
        if not lock_acquired:
            return  # Another check is in progress
        
        try:
            session = await session_manager.get_session(session_code)
            if not session:
                return
            
            # Skip if already completed
            if session.get("status") == "completed":
                return
            
            participants = session.get("participants", {})
            if not participants:
                return
            
            # Check each participant's progress
            all_completed = True
            completed_count = 0
            
            for participant_id in participants.keys():
                participant_index = await game_controller.get_participant_question_index(session_code, participant_id)
                if participant_index >= total_questions:
                    completed_count += 1
                else:
                    all_completed = False
            
            if all_completed:
                logger.info(f"All {completed_count} participants completed session {session_code}")
                
                # Mark session as completed
                await session_manager.end_session(session_code)
                
                # Get final results
                final_results = await leaderboard_manager.get_final_results(session_code)
                
                # Broadcast quiz_ended to everyone (this triggers the podium on host)
                await manager.broadcast_to_session({
                    "type": "quiz_ended",
                    "payload": {
                        "message": "All participants have completed the quiz!",
                        "results": final_results
                    }
                }, session_code)
        finally:
            # Always release the lock
            await redis.delete(completion_lock_key)
    
    except Exception as e:
        logger.error(f"Completion check error: {e}", exc_info=True)


async def handle_end_quiz(websocket: WebSocket, session_code: str, user_id: str):
    """Host ends the quiz or quiz completes naturally"""
    is_host = await session_manager.is_host(session_code, user_id)
    
    if not is_host:
        await manager.send_personal_message({
            "type": "error",
            "payload": {"message": "Only host can end the quiz"}
        }, websocket)
        return
    
    # Mark session as completed
    await session_manager.end_session(session_code)
    
    # Get final results
    final_results = await leaderboard_manager.get_final_results(session_code)
    
    # Broadcast quiz end
    await manager.broadcast_to_session({
        "type": "quiz_ended",
        "payload": {
            "message": "Quiz completed!",
            "results": final_results
        }
    }, session_code)


async def handle_request_leaderboard(websocket: WebSocket, session_code: str, user_id: str):
    """Participant requests real-time leaderboard with question progress"""
    try:
        # Get session data with participants
        session = await session_manager.get_session(session_code)
        if not session:
            await manager.send_personal_message({
                "type": "error",
                "payload": {"message": "Session not found"}
            }, websocket)
            return
        
        participants = session.get("participants", {})
        total_questions = await game_controller.get_total_questions(session_code)
        
        # Build leaderboard with question progress
        leaderboard_entries = []
        for participant_id, participant_data in participants.items():
            # Get participant's current question index (already advanced after answering)
            question_index = await game_controller.get_participant_question_index(session_code, participant_id)
            
            # Count answered questions from their answers array
            answers = participant_data.get("answers", [])
            answered_count = len(answers)
            
            leaderboard_entries.append({
                "user_id": participant_id,
                "username": participant_data.get("username", "Unknown"),
                "score": participant_data.get("score", 0),
                "question_index": question_index,  # This is the NEXT question they'll see
                "answered_count": answered_count,  # How many they've answered
                "total_questions": total_questions,
                "connected": participant_data.get("connected", False)
            })
        
        # Sort by score (descending)
        leaderboard_entries.sort(key=lambda x: x["score"], reverse=True)
        
        # Send leaderboard to requesting user
        await manager.send_personal_message({
            "type": "leaderboard_response",
            "payload": {
                "leaderboard": leaderboard_entries,
                "total_questions": total_questions
            }
        }, websocket)
    
    except Exception as e:
        logger.error(f"Leaderboard request error: {e}", exc_info=True)
