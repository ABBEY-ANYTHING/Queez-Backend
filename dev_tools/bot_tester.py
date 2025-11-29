"""
Bot Tester for Live Multiplayer Mode
=====================================
Simulates bot users joining and playing in live multiplayer sessions.

Usage:
    python bot_tester.py <session_code> [--bots 5]

Example:
    python bot_tester.py ABC123 --bots 5
"""

import asyncio
import json
import random
import string
import argparse
import websockets
import ssl
from datetime import datetime
from typing import Any, Dict, List, Optional

# ============================================
# CONFIGURATION - EDIT THESE VALUES
# ============================================

# WebSocket URL (change to your backend URL)
WS_BASE_URL = "wss://queez-backend.onrender.com/api/ws"
# For local testing, use: "ws://localhost:8000/api/ws"

# Number of bots (can be overridden via command line)
DEFAULT_BOT_COUNT = 5

# Bot answer accuracy (0.0 to 1.0) - 0.75 means 75% correct answers
BOT_ACCURACY_MIN = 0.6
BOT_ACCURACY_MAX = 0.9

# Response time range in seconds (bots will answer randomly within this range)
RESPONSE_TIME_MIN = 1.0
RESPONSE_TIME_MAX = 8.0

# Bot name prefixes
BOT_NAMES = ["TestBot", "QuizMaster", "BrainBot", "SmartAI", "QuickBot", 
             "StudyBot", "LearnBot", "FastBot", "CleverBot", "WiseBot"]

# ============================================
# BOT IMPLEMENTATION
# ============================================

class QuizBot:
    def __init__(self, bot_id: int, session_code: str):
        self.bot_id = bot_id
        self.session_code = session_code
        self.user_id = f"bot_{bot_id}_{self._random_string(6)}"
        self.username = f"{random.choice(BOT_NAMES)}_{bot_id}"
        self.accuracy = random.uniform(BOT_ACCURACY_MIN, BOT_ACCURACY_MAX)
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.score = 0
        self.questions_answered = 0
        self.correct_answers = 0
        self.is_connected = False
        self.quiz_completed = False
        
    def _random_string(self, length: int) -> str:
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))
    
    def _log(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] 🤖 {self.username}: {message}")
    
    async def connect(self):
        """Connect to WebSocket server"""
        url = f"{WS_BASE_URL}/{self.session_code}?user_id={self.user_id}"
        self._log(f"Connecting to {url}")
        
        # SSL context for secure connections
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        try:
            self.websocket = await websockets.connect(
                url,
                ssl=ssl_context if url.startswith("wss://") else None,
                ping_interval=30,
                ping_timeout=10
            )
            self.is_connected = True
            self._log("✅ Connected!")
            return True
        except Exception as e:
            self._log(f"❌ Connection failed: {e}")
            return False
    
    async def join_session(self):
        """Send join message to session"""
        await self._send_message("join", {"username": self.username})
        self._log(f"Joining session as {self.username}")
    
    async def _send_message(self, msg_type: str, payload: Dict = None):
        """Send a message through WebSocket"""
        if self.websocket and self.is_connected:
            message = {"type": msg_type}
            if payload:
                message["payload"] = payload
            await self.websocket.send(json.dumps(message))
    
    def _get_answer_for_question(self, question: Dict) -> Any:
        """Determine answer based on question type and bot accuracy"""
        question_type = question.get("type") or question.get("questionType", "singleMcq")
        options = question.get("options", [])
        
        # Decide if bot answers correctly
        answer_correctly = random.random() < self.accuracy
        
        if question_type in ["singleMcq", "trueFalse"]:
            correct_index = question.get("correctAnswerIndex", 0)
            if answer_correctly:
                return correct_index
            else:
                # Pick a wrong answer
                wrong_options = [i for i in range(len(options)) if i != correct_index]
                return random.choice(wrong_options) if wrong_options else correct_index
                
        elif question_type == "multiMcq":
            correct_indices = question.get("correctAnswerIndices", [0])
            if answer_correctly:
                return correct_indices
            else:
                # Return partial or wrong answers
                all_indices = list(range(len(options)))
                num_to_select = random.randint(1, len(options))
                return random.sample(all_indices, min(num_to_select, len(all_indices)))
                
        elif question_type == "dragAndDrop":
            correct_matches = question.get("correctMatches", {})
            if answer_correctly:
                return correct_matches
            else:
                # Shuffle the matches
                keys = list(correct_matches.keys())
                values = list(correct_matches.values())
                random.shuffle(values)
                return dict(zip(keys, values))
        
        # Default fallback
        return 0
    
    async def handle_question(self, payload: Dict):
        """Handle incoming question"""
        question = payload.get("question", {})
        index = payload.get("index", 0)
        total = payload.get("total", 1)
        time_limit = payload.get("time_limit", 30)
        
        question_text = question.get("question", "Unknown question")[:50]
        question_type = question.get("type") or question.get("questionType", "unknown")
        
        self._log(f"📝 Question {index + 1}/{total}: {question_text}... (Type: {question_type})")
        
        # Simulate thinking time
        think_time = random.uniform(RESPONSE_TIME_MIN, min(RESPONSE_TIME_MAX, time_limit - 1))
        self._log(f"⏳ Thinking for {think_time:.1f}s...")
        await asyncio.sleep(think_time)
        
        # Get answer
        answer = self._get_answer_for_question(question)
        
        # Submit answer
        await self._send_message("submit_answer", {
            "answer": answer,
            "timestamp": think_time
        })
        
        self.questions_answered += 1
        self._log(f"📤 Submitted answer: {answer}")
    
    async def handle_answer_result(self, payload: Dict):
        """Handle answer result from server"""
        is_correct = payload.get("is_correct", False)
        points = payload.get("points", 0)
        new_score = payload.get("new_total_score", 0)
        
        self.score = new_score
        if is_correct:
            self.correct_answers += 1
            self._log(f"✅ Correct! +{points} points (Total: {new_score})")
        else:
            self._log(f"❌ Wrong! (Total: {new_score})")
        
        # Request next question (self-paced mode)
        await asyncio.sleep(0.5)
        await self._send_message("request_next_question", {})
    
    async def handle_quiz_completed(self, payload: Dict):
        """Handle quiz completion"""
        self.quiz_completed = True
        self._log(f"🏁 Quiz completed! Final score: {self.score}")
        self._log(f"📊 Stats: {self.correct_answers}/{self.questions_answered} correct ({self.accuracy*100:.0f}% target accuracy)")
    
    async def listen(self):
        """Listen for messages from server"""
        try:
            async for message in self.websocket:
                data = json.loads(message)
                msg_type = data.get("type")
                payload = data.get("payload", {})
                
                if msg_type == "session_state":
                    participant_count = payload.get("participant_count", 0)
                    participants = payload.get("participants", [])
                    self._log(f"📋 Received session state - {participant_count} participants: {[p.get('username') for p in participants]}")
                    
                elif msg_type == "quiz_started":
                    self._log("🚀 Quiz started!")
                    
                elif msg_type == "question":
                    await self.handle_question(payload)
                    
                elif msg_type == "answer_result":
                    await self.handle_answer_result(payload)
                    
                elif msg_type == "quiz_completed" or msg_type == "quiz_ended":
                    await self.handle_quiz_completed(payload)
                    break
                    
                elif msg_type == "session_update":
                    participant_count = payload.get("participant_count", 0)
                    participants = payload.get("participants", [])
                    self._log(f"📢 Session update - {participant_count} participants: {[p.get('username') for p in participants]}")
                    
                elif msg_type == "leaderboard_update":
                    # Silently handle leaderboard updates
                    pass
                    
                elif msg_type == "error":
                    self._log(f"⚠️ Error: {payload.get('message', 'Unknown error')}")
                    
        except websockets.exceptions.ConnectionClosed:
            self._log("🔌 Connection closed")
        except Exception as e:
            self._log(f"❌ Error: {e}")
        finally:
            self.is_connected = False
    
    async def disconnect(self):
        """Disconnect from server"""
        if self.websocket:
            await self.websocket.close()
            self.is_connected = False
            self._log("👋 Disconnected")


async def run_bots(session_code: str, num_bots: int):
    """Run multiple bots in a session"""
    print("\n" + "="*60)
    print(f"🤖 QUIZ BOT TESTER - Starting {num_bots} bots")
    print(f"📍 Session Code: {session_code}")
    print(f"🎯 Accuracy Range: {BOT_ACCURACY_MIN*100:.0f}% - {BOT_ACCURACY_MAX*100:.0f}%")
    print(f"⏱️ Response Time: {RESPONSE_TIME_MIN}s - {RESPONSE_TIME_MAX}s")
    print("="*60 + "\n")
    
    # Create bots
    bots = [QuizBot(i + 1, session_code) for i in range(num_bots)]
    
    # Connect all bots
    print("📡 Connecting bots...")
    connect_tasks = [bot.connect() for bot in bots]
    results = await asyncio.gather(*connect_tasks)
    
    connected_bots = [bot for bot, success in zip(bots, results) if success]
    print(f"✅ {len(connected_bots)}/{num_bots} bots connected\n")
    
    if not connected_bots:
        print("❌ No bots connected. Check your session code and server URL.")
        return
    
    # Join session
    print("🚪 Bots joining session...")
    join_tasks = [bot.join_session() for bot in connected_bots]
    await asyncio.gather(*join_tasks)
    
    print("\n⏳ Waiting for host to start the quiz...")
    print("   (Bots will automatically answer questions when quiz starts)\n")
    
    # Listen for messages
    listen_tasks = [bot.listen() for bot in connected_bots]
    await asyncio.gather(*listen_tasks)
    
    # Print final results
    print("\n" + "="*60)
    print("📊 FINAL RESULTS")
    print("="*60)
    
    for bot in sorted(connected_bots, key=lambda b: b.score, reverse=True):
        accuracy = (bot.correct_answers / bot.questions_answered * 100) if bot.questions_answered > 0 else 0
        print(f"  {bot.username}: {bot.score} pts ({bot.correct_answers}/{bot.questions_answered} correct, {accuracy:.0f}%)")
    
    print("="*60 + "\n")
    
    # Disconnect
    disconnect_tasks = [bot.disconnect() for bot in connected_bots]
    await asyncio.gather(*disconnect_tasks)


def main():
    global WS_BASE_URL
    
    parser = argparse.ArgumentParser(description="Quiz Bot Tester for Live Multiplayer")
    parser.add_argument("session_code", help="Session code to join")
    parser.add_argument("--bots", "-b", type=int, default=DEFAULT_BOT_COUNT, 
                        help=f"Number of bots (default: {DEFAULT_BOT_COUNT})")
    parser.add_argument("--url", "-u", type=str, default=WS_BASE_URL,
                        help=f"WebSocket base URL (default: {WS_BASE_URL})")
    
    args = parser.parse_args()
    
    # Update URL if provided
    WS_BASE_URL = args.url
    
    # Run bots
    asyncio.run(run_bots(args.session_code, args.bots))


if __name__ == "__main__":
    main()
