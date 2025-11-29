from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.services.connection_manager import manager
from app.services.session_manager import SessionManager
from app.services.game_controller import GameController
from app.services.leaderboard_manager import LeaderboardManager
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
        logger.info(f"User {user_id} disconnected from session {session_code}")


async def handle_join(websocket: WebSocket, session_code: str, user_id: str, payload: dict):
    username = payload.get("username", "Anonymous")
    
    # Validate session
    session = await session_manager.get_session(session_code)
    if not session:
        logger.warning(f"Session {session_code} not found")
        await manager.send_personal_message({"type": "error", "payload": {"message": "Session not found"}}, websocket)
        return
    
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
        
        # If reconnecting during active quiz, send current question
        if is_reconnecting and session["status"] == "active":
            question_data = await game_controller.get_current_question(session_code)
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
        # Get participant's current index (already advanced after answering)
        current_index = await game_controller.get_participant_question_index(session_code, user_id)
        total_questions = await game_controller.get_total_questions(session_code)
        
        if total_questions == 0:
            await manager.send_personal_message({
                "type": "error",
                "payload": {"message": "Quiz not found"}
            }, websocket)
            return
        
        # Check if participant has completed all questions
        if current_index >= total_questions:
            logger.info(f"Participant {user_id} completed all questions")
            
            # Get final results
            final_results = await leaderboard_manager.get_final_results(session_code)
            
            # Send completion message to participant
            await manager.send_personal_message({
                "type": "quiz_completed",
                "payload": {
                    "message": "You've completed all questions!",
                    "results": final_results
                }
            }, websocket)
            
            # Check if ALL participants have completed
            await check_all_participants_completed(session_code, total_questions)
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
            # No more questions - participant finished
            logger.info(f"Participant {user_id} completed all questions")
            
            # Get final results
            final_results = await leaderboard_manager.get_final_results(session_code)
            
            # Send completion message to participant
            await manager.send_personal_message({
                "type": "quiz_completed",
                "payload": {
                    "message": "You've completed all questions!",
                    "results": final_results
                }
            }, websocket)
            
            # Check if ALL participants have completed
            await check_all_participants_completed(session_code, total_questions)
    
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
