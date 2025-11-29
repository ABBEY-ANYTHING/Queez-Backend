import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List
import json

from app.core.database import redis_client, collection as quiz_collection
from app.core.config import QUESTION_TIME_SECONDS
from bson import ObjectId

logger = logging.getLogger(__name__)

class GameController:
    def __init__(self):
        self.redis = redis_client

    async def get_current_question(self, session_code: str) -> Optional[Dict[str, Any]]:
        """Get the current question for the session"""
        session_key = f"session:{session_code}"
        
        logger.info(f"📚 Getting current question for session {session_code}")
        
        # Get current index and quiz ID
        session_data = await self.redis.hmget(session_key, ["current_question_index", "quiz_id", "question_start_time"])
        logger.info(f"📊 Session data from Redis: index={session_data[0]}, quiz_id={session_data[1]}")
        
        if not all(session_data[:2]): # Check if index and quiz_id exist
            logger.error(f"❌ Missing session data! index={session_data[0]}, quiz_id={session_data[1]}")
            return None
            
        current_index = int(session_data[0])
        quiz_id = session_data[1]
        start_time = session_data[2]
        
        # Fetch quiz from MongoDB (could be cached in Redis for performance)
        logger.info(f"🔍 Fetching quiz from MongoDB with ID: {quiz_id}")
        quiz = await quiz_collection.find_one({"_id": ObjectId(quiz_id)})
        
        if not quiz:
            logger.error(f"❌ Quiz not found in MongoDB with ID: {quiz_id}")
            return None
            
        if "questions" not in quiz:
            logger.error(f"❌ Quiz {quiz_id} has no questions field!")
            return None
            
        logger.info(f"✅ Quiz loaded successfully. Total questions: {len(quiz['questions'])}")
            
        questions = quiz["questions"]
        if current_index >= len(questions):
            logger.warning(f"⚠️ Question index {current_index} out of range (total: {len(questions)})")
            return None
            
        question = questions[current_index]
        
        # Ensure question has required fields
        question_text = question.get('questionText', question.get('question', ''))
        question_type = question.get('type', 'single')
        
        # Validate question text is not empty
        if not question_text or not question_text.strip():
            logger.error(f"❌ Question {current_index} has empty question text!")
            return None
        
        logger.info(f"✅ Retrieved question {current_index + 1}/{len(questions)}: {question_text[:50]}...")
        
        # Get per-question time limit (default to global config if not set)
        question_time_limit = question.get('timeLimit', QUESTION_TIME_SECONDS)
        
        # Calculate time remaining based on question's time limit
        time_remaining = question_time_limit
        if start_time:
            elapsed = (datetime.utcnow() - datetime.fromisoformat(start_time)).total_seconds()
            time_remaining = max(0, question_time_limit - int(elapsed))
        
        # Build question payload with normalized field names
        question_payload = {
            "question": question_text,
            "questionType": question_type,
            "type": question_type,  # Keep for backward compatibility
            "options": question.get('options', []),
            "id": question.get('id', str(current_index)),
            "timeLimit": question_time_limit,  # Include time limit in payload
        }
        
        # Include optional fields if present
        if 'correctAnswerIndex' in question:
            question_payload['correctAnswerIndex'] = question['correctAnswerIndex']
        if 'correctAnswerIndices' in question:
            question_payload['correctAnswerIndices'] = question['correctAnswerIndices']
        if 'dragItems' in question:
            question_payload['dragItems'] = question['dragItems']
        if 'dropTargets' in question:
            question_payload['dropTargets'] = question['dropTargets']
        if 'correctMatches' in question:
            question_payload['correctMatches'] = question['correctMatches']
        if 'imageUrl' in question:
            question_payload['imageUrl'] = question['imageUrl']
            
        return {
            "question": question_payload,
            "index": current_index,
            "total": len(questions),
            "time_remaining": time_remaining,
            "time_limit": question_time_limit  # Include total time limit for scoring calculations
        }

    async def submit_answer(self, session_code: str, user_id: str, answer: Any, timestamp: float) -> Dict[str, Any]:
        """Process a participant's answer with distributed locking to prevent race conditions"""
        session_key = f"session:{session_code}"
        answer_lock_key = f"lock:answer:{session_code}:{user_id}"
        
        # Acquire distributed lock for this specific user's answer submission
        max_retries = 20
        lock_timeout = 5
        
        for attempt in range(max_retries):
            try:
                # Try to acquire lock
                lock_acquired = await self.redis.set(
                    answer_lock_key,
                    "1",
                    nx=True,
                    ex=lock_timeout
                )
                
                if not lock_acquired:
                    # Lock held by another process, wait and retry
                    await asyncio.sleep(0.05 * (attempt + 1))
                    continue
                
                try:
                    return await self._process_answer_internal(session_code, user_id, answer, timestamp)
                finally:
                    # Always release the lock
                    await self.redis.delete(answer_lock_key)
                    
            except Exception as e:
                logger.error(f"❌ Answer submission error for {user_id} (attempt {attempt + 1}): {e}")
                try:
                    await self.redis.delete(answer_lock_key)
                except:
                    pass
                if attempt == max_retries - 1:
                    return {"error": "Failed to process answer, please try again"}
                await asyncio.sleep(0.05)
        
        return {"error": "Failed to process answer after multiple attempts"}

    async def _process_answer_internal(self, session_code: str, user_id: str, answer: Any, timestamp: float) -> Dict[str, Any]:
        """Internal answer processing (called with per-user lock held)
        
        CRITICAL: We need a session-wide lock for participants updates to prevent
        race conditions where concurrent answers overwrite each other.
        """
        session_key = f"session:{session_code}"
        cache_key = f"quiz_cache:{session_code}"
        participants_lock_key = f"lock:participants:{session_code}"
        
        # Get participant's current question index
        current_index = await self.get_participant_question_index(session_code, user_id)
        
        logger.info(f"📝 Processing answer for {user_id} on Q{current_index + 1}")

        # Try to get quiz from cache first
        cached_quiz = await self.redis.get(cache_key)
        
        if cached_quiz:
            quiz_data = json.loads(cached_quiz)
            questions = quiz_data.get("questions", [])
        else:
            # Fallback to MongoDB and cache
            quiz_id = await self.redis.hget(session_key, "quiz_id")
            if not quiz_id:
                return {"error": "Quiz ID not found"}
            
            quiz = await quiz_collection.find_one({"_id": ObjectId(quiz_id)})
            
            if not quiz or "questions" not in quiz:
                return {"error": "Quiz not found"}
            
            questions = quiz["questions"]
            
            # Cache for next time
            quiz_to_cache = {"questions": questions, "quiz_id": quiz_id}
            await self.redis.setex(cache_key, 3600, json.dumps(quiz_to_cache))
        
        if current_index >= len(questions):
            return {"error": "Invalid question index"}
        
        question = questions[current_index]
        question_type = question.get("type", "singleMcq")
        
        # Handle different question types
        is_correct = False
        
        # Handle timeout (null answer)
        if answer is None:
            logger.debug(f"⏰ Timeout - user {user_id} did not answer in time")
            is_correct = False
        elif question_type in ["singleMcq", "trueFalse"]:
            # Single answer questions
            correct_answer = question.get("correctAnswerIndex")
            if correct_answer is None:
                logger.error(f"❌ No correct answer found for question {current_index}")
                return {"error": "Invalid question configuration"}
            
            is_correct = int(answer) == int(correct_answer)
            
        elif question_type == "multiMcq":
            # Multiple answer questions with partial credit
            correct_answers = question.get("correctAnswerIndices", [])
            if not correct_answers:
                logger.error(f"❌ No correct answers found for multi-choice question {current_index}")
                return {"error": "Invalid question configuration"}
            
            # Answer should be a list of indices
            user_answers = answer if isinstance(answer, list) else [answer]
            user_answers_set = set(int(a) for a in user_answers)
            correct_answers_set = set(int(a) for a in correct_answers)
            
            # Calculate partial credit
            correct_selections = user_answers_set & correct_answers_set
            wrong_selections = user_answers_set - correct_answers_set
            
            total_correct = len(correct_answers_set)
            num_correct = len(correct_selections)
            num_wrong = len(wrong_selections)
            
            if total_correct > 0:
                partial_credit = (num_correct - num_wrong) / total_correct
                partial_credit = max(0.0, min(1.0, partial_credit))
            else:
                partial_credit = 0.0
            
            is_correct = user_answers_set == correct_answers_set
            
        elif question_type == "dragAndDrop":
            # Drag and drop questions
            correct_matches = question.get("correctMatches", {})
            if not correct_matches:
                logger.error(f"❌ No correct matches found for drag-drop question {current_index}")
                return {"error": "Invalid question configuration"}
            
            user_matches = answer if isinstance(answer, dict) else {}
            is_correct = user_matches == correct_matches
        
        else:
            logger.error(f"❌ Unknown question type: {question_type}")
            return {"error": "Unknown question type"}
        
        # Calculate points (time-based scoring with multiplier)
        # Formula: 1000 base points + speed multiplier (up to 1000 bonus points)
        # Faster answers get higher multipliers: 2x for instant, 1x at time limit
        points = 0
        time_bonus = 0
        multiplier = 1.0
        partial_credit_percentage = 0.0
        
        # Get per-question time limit
        question_time_limit = question.get('timeLimit', QUESTION_TIME_SECONDS)
        
        # For multi-choice, use partial credit
        if question_type == "multiMcq" and 'partial_credit' in locals():
            base_points = 1000
            
            # Apply partial credit to base points
            partial_points = int(base_points * partial_credit)
            partial_credit_percentage = partial_credit * 100
            
            # Calculate time-based multiplier only if they got some points
            if partial_credit > 0 and timestamp is not None and timestamp >= 0:
                elapsed = min(timestamp, question_time_limit)
                multiplier = max(1.0, 2.0 - (elapsed / question_time_limit))
                time_bonus = int(partial_points * (multiplier - 1))
            
            points = partial_points + time_bonus
            
        elif is_correct:
            # Full credit for other question types
            base_points = 1000
            partial_credit_percentage = 100.0
            
            # Calculate time-based multiplier
            if timestamp is not None and timestamp >= 0:
                elapsed = min(timestamp, question_time_limit)
                multiplier = max(1.0, 2.0 - (elapsed / question_time_limit))
                time_bonus = int(base_points * (multiplier - 1))
            
            points = base_points + time_bonus
        
        # ============================================================
        # CRITICAL SECTION: Update participant data with session-wide lock
        # This prevents race conditions where concurrent answers overwrite each other
        # ============================================================
        participants_lock_key = f"lock:participants:{session_code}"
        max_lock_retries = 50
        lock_timeout = 3
        
        for lock_attempt in range(max_lock_retries):
            # Try to acquire session-wide participants lock
            lock_acquired = await self.redis.set(
                participants_lock_key,
                user_id,
                nx=True,
                ex=lock_timeout
            )
            
            if not lock_acquired:
                await asyncio.sleep(0.02 * (lock_attempt + 1))
                continue
            
            try:
                # === LOCKED: Read-modify-write participants ===
                participants_json = await self.redis.hget(session_key, "participants")
                if not participants_json:
                    return {"error": "Session not found"}
                
                participants = json.loads(participants_json)
                if user_id not in participants:
                    logger.error(f"❌ Participant {user_id} not found in session")
                    return {"error": "Participant not found"}
                
                participant = participants[user_id]
                
                # Check if already answered
                for ans in participant["answers"]:
                    if ans["question_index"] == current_index:
                        logger.warning(f"⚠️ User {user_id} already answered question {current_index}")
                        return {"error": "Already answered"}
                
                # Record answer
                participant["answers"].append({
                    "question_index": current_index,
                    "answer": answer,
                    "timestamp": timestamp,
                    "is_correct": is_correct,
                    "points_earned": points
                })
                participant["score"] += points
                
                # Log BEFORE saving
                answers_count = len(participant["answers"])
                logger.info(f"💾 SAVING - {user_id}: Q{current_index + 1}, answers={answers_count}, score={participant['score']}")
                
                # Save back to Redis
                await self.redis.hset(session_key, "participants", json.dumps(participants))
                
                # === END LOCKED SECTION ===
                break
                
            finally:
                # Always release the lock
                await self.redis.delete(participants_lock_key)
        else:
            # Failed to acquire lock after all retries
            logger.error(f"❌ Failed to acquire participants lock for {user_id}")
            return {"error": "Server busy, please try again"}
        
        # Verify save by reading back (outside lock for performance)
        verify_json = await self.redis.hget(session_key, "participants")
        verify_participants = json.loads(verify_json)
        if user_id in verify_participants:
            verify_count = len(verify_participants[user_id].get("answers", []))
            if verify_count != answers_count:
                logger.error(f"❌ MISMATCH - {user_id}: expected {answers_count}, got {verify_count} in Redis!")
            else:
                logger.info(f"✅ VERIFIED - {user_id}: {verify_count} answers in Redis")
        
        # Return correct answer based on question type
        correct_answer_response = None
        if question_type in ["singleMcq", "trueFalse"]:
            correct_answer_response = str(question.get("correctAnswerIndex"))
        elif question_type == "multiMcq":
            correct_answer_response = question.get("correctAnswerIndices", [])
        elif question_type == "dragAndDrop":
            correct_answer_response = question.get("correctMatches", {})
        
        # Store the answer for returning
        stored_answer = answer
        
        # Get the updated score from what we saved
        final_score = participant["score"]
        
        response = {
            "is_correct": is_correct,
            "points": points,
            "time_bonus": time_bonus,
            "multiplier": round(multiplier, 2),
            "correct_answer": correct_answer_response,
            "user_answer": stored_answer,
            "new_total_score": final_score,
            "question_type": question_type,
            "question_index": current_index
        }
        
        # Add partial credit info for multi-choice
        if question_type == "multiMcq":
            response["partial_credit"] = round(partial_credit_percentage, 1)
            response["is_partial"] = partial_credit_percentage > 0 and partial_credit_percentage < 100
        
        # ✅ ADVANCE participant's question index immediately after answering
        # This way request_next_question just needs to get the next question
        await self.set_participant_question_index(session_code, user_id, current_index + 1)
        
        return response

    async def advance_question(self, session_code: str) -> bool:
        """Move to the next question"""
        session_key = f"session:{session_code}"
        
        # Increment index
        current_index = await self.redis.hincrby(session_key, "current_question_index", 1)
        
        # Reset start time
        await self.redis.hset(session_key, "question_start_time", datetime.utcnow().isoformat())
        
        return True

    async def next_question(self, session_code: str) -> Optional[Dict[str, Any]]:
        """Advance to and return the next question"""
        session_key = f"session:{session_code}"
        
        # Increment index
        await self.redis.hincrby(session_key, "current_question_index", 1)
        
        # Reset start time for the new question
        await self.redis.hset(session_key, "question_start_time", datetime.utcnow().isoformat())
        
        # Return the new current question
        return await self.get_current_question(session_code)
        
    async def start_question_timer(self, session_code: str):
        """Start the timer for the current question"""
        session_key = f"session:{session_code}"
        await self.redis.hset(session_key, "question_start_time", datetime.utcnow().isoformat())

    async def check_all_answered(self, session_code: str) -> bool:
        """Check if all connected participants have answered the current question"""
        session_key = f"session:{session_code}"
        session_data = await self.redis.hmget(session_key, ["current_question_index", "participants"])
        current_index = int(session_data[0])
        participants = json.loads(session_data[1])
        
        for p in participants.values():
            if p.get("connected", False):
                has_answered = any(a["question_index"] == current_index for a in p["answers"])
                if not has_answered:
                    return False
        return True

    async def get_answer_distribution(self, session_code: str) -> Dict[str, int]:
        """Calculate answer distribution statistics for current question"""
        session_key = f"session:{session_code}"
        session_data = await self.redis.hmget(session_key, ["current_question_index", "participants"])
        current_index = int(session_data[0])
        participants = json.loads(session_data[1])
        
        distribution = {}
        for p in participants.values():
            for ans in p["answers"]:
                if ans["question_index"] == current_index:
                    answer_key = str(ans["answer"])
                    distribution[answer_key] = distribution.get(answer_key, 0) + 1
        
        return distribution

    async def calculate_accuracy(self, session_code: str, user_id: str) -> float:
        """Calculate accuracy percentage for a participant"""
        session_key = f"session:{session_code}"
        participants_json = await self.redis.hget(session_key, "participants")
        participants = json.loads(participants_json)
        
        if user_id not in participants:
            return 0.0
        
        participant = participants[user_id]
        answers = participant.get("answers", [])
        
        if not answers:
            return 0.0
        
        correct_count = sum(1 for ans in answers if ans.get("is_correct", False))
        return (correct_count / len(answers)) * 100

    async def get_participant_question_index(self, session_code: str, user_id: str) -> int:
        """Get the current question index for a specific participant (Redis is the source of truth)"""
        participant_key = f"participant:{session_code}:{user_id}:question_index"
        
        # Get from Redis - this is the single source of truth
        index = await self.redis.get(participant_key)
        
        if index is not None:
            return int(index)
        
        # Not initialized yet - default to 0
        # This should only happen if they joined before quiz started
        return 0

    async def set_participant_question_index(self, session_code: str, user_id: str, index: int):
        """Set the current question index for a specific participant"""
        participant_key = f"participant:{session_code}:{user_id}:question_index"
        await self.redis.set(participant_key, index)
        logger.debug(f"✅ PROGRESS - Set {user_id} question index to {index}")

    async def get_total_questions(self, session_code: str) -> int:
        """Get total number of questions in the quiz (uses cached data)"""
        # Try to get from cache first
        cache_key = f"quiz_cache:{session_code}"
        cached_quiz = await self.redis.get(cache_key)
        
        if cached_quiz:
            quiz_data = json.loads(cached_quiz)
            return len(quiz_data.get("questions", []))
        
        # Fallback to MongoDB and cache it
        session_key = f"session:{session_code}"
        quiz_id = await self.redis.hget(session_key, "quiz_id")
        
        if not quiz_id:
            return 0
        
        quiz = await quiz_collection.find_one({"_id": ObjectId(quiz_id)})
        if not quiz or "questions" not in quiz:
            return 0
        
        # Cache the quiz for 1 hour
        quiz_to_cache = {
            "questions": quiz["questions"],
            "quiz_id": quiz_id
        }
        await self.redis.setex(cache_key, 3600, json.dumps(quiz_to_cache))
        
        return len(quiz["questions"])

    async def get_question_by_index(self, session_code: str, index: int) -> Optional[Dict[str, Any]]:
        """Get a specific question by index (uses cached data for speed)"""
        session_key = f"session:{session_code}"
        cache_key = f"quiz_cache:{session_code}"
        
        logger.debug(f"📚 Getting question {index} for session {session_code}")
        
        # Get session time settings
        session_per_question_limit_raw = await self.redis.hget(session_key, "per_question_time_limit")
        session_per_question_limit = int(session_per_question_limit_raw) if session_per_question_limit_raw else QUESTION_TIME_SECONDS
        
        # Try to get quiz from cache first
        cached_quiz = await self.redis.get(cache_key)
        
        if cached_quiz:
            quiz_data = json.loads(cached_quiz)
            questions = quiz_data.get("questions", [])
        else:
            # Fetch from MongoDB and cache
            quiz_id = await self.redis.hget(session_key, "quiz_id")
            
            if not quiz_id:
                logger.error(f"❌ Quiz ID not found for session {session_code}")
                return None
            
            quiz = await quiz_collection.find_one({"_id": ObjectId(quiz_id)})
            
            if not quiz or "questions" not in quiz:
                logger.error(f"❌ Quiz or questions not found")
                return None
            
            questions = quiz["questions"]
            
            # Cache the quiz for 1 hour
            quiz_to_cache = {
                "questions": questions,
                "quiz_id": quiz_id
            }
            await self.redis.setex(cache_key, 3600, json.dumps(quiz_to_cache))
        
        if index >= len(questions):
            logger.debug(f"⚠️ Question index {index} out of range (total: {len(questions)})")
            return None
        
        question = questions[index]
        
        # Ensure question has required fields
        question_text = question.get('questionText', question.get('question', ''))
        question_type = question.get('type', 'single')
        
        if not question_text or not question_text.strip():
            logger.error(f"❌ Question {index} has empty question text!")
            return None
        
        logger.debug(f"✅ Retrieved question {index + 1}/{len(questions)}: {question_text[:50]}...")
        
        # Use session's per-question time limit (set by host)
        question_time_limit = session_per_question_limit
        
        # Build question payload
        question_payload = {
            "question": question_text,
            "questionType": question_type,
            "type": question_type,
            "options": question.get('options', []),
            "id": question.get('id', str(index)),
            "timeLimit": question_time_limit,
        }
        
        # Include optional fields
        if 'correctAnswerIndex' in question:
            question_payload['correctAnswerIndex'] = question['correctAnswerIndex']
        if 'correctAnswerIndices' in question:
            question_payload['correctAnswerIndices'] = question['correctAnswerIndices']
        if 'dragItems' in question:
            question_payload['dragItems'] = question['dragItems']
        if 'dropTargets' in question:
            question_payload['dropTargets'] = question['dropTargets']
        if 'correctMatches' in question:
            question_payload['correctMatches'] = question['correctMatches']
        if 'imageUrl' in question:
            question_payload['imageUrl'] = question['imageUrl']
        
        return {
            "question": question_payload,
            "index": index,
            "total": len(questions),
            "time_remaining": question_time_limit,
            "time_limit": question_time_limit
        }