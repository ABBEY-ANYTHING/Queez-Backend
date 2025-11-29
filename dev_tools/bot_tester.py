"""
Bot Tester for Live Multiplayer Mode
=====================================
All bots answer each question together, synchronized.
Waits 2-3 seconds between questions.

Usage:
    python bot_tester.py <session_code> [--bots 5]
"""

import asyncio
import json
import random
import string
import argparse
import websockets
import ssl
from datetime import datetime
from typing import Any, Dict, Optional

# ============================================
# CONFIGURATION
# ============================================
WS_BASE_URL = "wss://queez-backend.onrender.com/api/ws"
DEFAULT_BOT_COUNT = 5
BOT_ACCURACY_MIN = 0.6
BOT_ACCURACY_MAX = 0.9
RESPONSE_TIME_MIN = 1.0
RESPONSE_TIME_MAX = 4.0
QUESTION_DELAY = 2.5  # Delay between questions (seconds)

BOT_NAMES = ["TestBot", "QuizMaster", "BrainBot", "SmartAI", "QuickBot", 
             "StudyBot", "LearnBot", "FastBot", "CleverBot", "WiseBot"]


class QuizBot:
    def __init__(self, bot_id: int, session_code: str):
        self.bot_id = bot_id
        self.session_code = session_code
        self.user_id = f"bot_{bot_id}_{''.join(random.choices(string.ascii_lowercase + string.digits, k=6))}"
        self.username = f"{random.choice(BOT_NAMES)}_{bot_id}"
        self.accuracy = random.uniform(BOT_ACCURACY_MIN, BOT_ACCURACY_MAX)
        self.websocket = None
        self.score = 0
        self.questions_answered = 0
        self.correct_answers = 0
        self.is_connected = False
        self.quiz_completed = False
        self.current_question = None
        self.waiting_for_result = False
        self.total_questions = 0
        self._receive_lock = asyncio.Lock()
        
    def _log(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] 🤖 {self.username}: {message}")
    
    async def connect(self):
        url = f"{WS_BASE_URL}/{self.session_code}?user_id={self.user_id}"
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        try:
            self.websocket = await asyncio.wait_for(
                websockets.connect(
                    url,
                    ssl=ssl_context if url.startswith("wss://") else None,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5
                ),
                timeout=15.0
            )
            self.is_connected = True
            self._log("✅ Connected")
            return True
        except asyncio.TimeoutError:
            self._log("❌ Connection timeout")
            return False
        except Exception as e:
            self._log(f"❌ Connection failed: {e}")
            return False
    
    async def join_session(self):
        await self._send_message("join", {"username": self.username})
        self._log(f"Joined as {self.username}")
    
    async def _send_message(self, msg_type: str, payload: dict = None):
        if self.websocket and self.is_connected:
            try:
                message = {"type": msg_type}
                if payload:
                    message["payload"] = payload
                await asyncio.wait_for(
                    self.websocket.send(json.dumps(message)),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                self._log("⚠️ Send timeout")
                self.is_connected = False
            except Exception as e:
                self.is_connected = False
                self._log(f"⚠️ Send failed: {e}")
    
    def _get_answer(self, question: dict) -> Any:
        question_type = question.get("type") or question.get("questionType", "singleMcq")
        options = question.get("options", [])
        answer_correctly = random.random() < self.accuracy
        
        if question_type in ["singleMcq", "trueFalse"]:
            correct_index = question.get("correctAnswerIndex", 0)
            if answer_correctly:
                return correct_index
            wrong = [i for i in range(len(options)) if i != correct_index]
            return random.choice(wrong) if wrong else correct_index
        elif question_type == "multiMcq":
            correct = question.get("correctAnswerIndices", [0])
            if answer_correctly:
                return correct
            return random.sample(range(len(options)), random.randint(1, len(options)))
        elif question_type == "dragAndDrop":
            correct = question.get("correctMatches", {})
            if answer_correctly:
                return correct
            keys, values = list(correct.keys()), list(correct.values())
            random.shuffle(values)
            return dict(zip(keys, values))
        return 0
    
    async def submit_answer(self):
        """Submit answer for current question"""
        if not self.current_question or not self.is_connected or self.quiz_completed:
            return
            
        question = self.current_question.get("question", {})
        index = self.current_question.get("index", 0)
        total = self.current_question.get("total", 1)
        self.total_questions = total
        
        # Think time (shorter for stress testing)
        think_time = random.uniform(RESPONSE_TIME_MIN, RESPONSE_TIME_MAX)
        await asyncio.sleep(think_time)
        
        # Submit
        answer = self._get_answer(question)
        await self._send_message("submit_answer", {"answer": answer, "timestamp": think_time})
        self.questions_answered += 1
        self.waiting_for_result = True
        self._log(f"📤 Answered Q{index + 1}/{total}")
    
    async def request_next(self):
        """Request next question"""
        if self.is_connected and not self.quiz_completed:
            await self._send_message("request_next_question", {})
    
    async def listen_loop(self):
        """Listen for messages"""
        try:
            async for message in self.websocket:
                async with self._receive_lock:
                    try:
                        data = json.loads(message)
                        msg_type = data.get("type")
                        payload = data.get("payload", {})
                        
                        if msg_type == "question":
                            self.current_question = payload
                            self.total_questions = payload.get("total", self.total_questions)
                            q = payload.get("question", {}).get("question", "")[:40]
                            idx = payload.get("index", 0) + 1
                            total = payload.get("total", 1)
                            self._log(f"📝 Q{idx}/{total}: {q}...")
                            
                        elif msg_type == "answer_result":
                            self.waiting_for_result = False
                            is_correct = payload.get("is_correct", False)
                            points = payload.get("points", 0)
                            self.score = payload.get("new_total_score", self.score)
                            if is_correct:
                                self.correct_answers += 1
                                self._log(f"✅ +{points} pts (Total: {self.score})")
                            else:
                                self._log(f"❌ Wrong (Total: {self.score})")
                            
                        elif msg_type in ["quiz_completed", "quiz_ended"]:
                            self.quiz_completed = True
                            self._log(f"🏁 Done! Final Score: {self.score}")
                            
                        elif msg_type == "session_state":
                            count = payload.get("participant_count", len(payload.get("participants", [])))
                            self._log(f"📋 {count} participants")
                            
                        elif msg_type == "quiz_started":
                            self._log("🚀 Quiz started!")
                            
                        elif msg_type == "leaderboard_update":
                            # Silently process leaderboard updates
                            pass
                            
                        elif msg_type == "error":
                            error_msg = payload.get("message", "Error")
                            if "Already answered" not in error_msg:
                                self._log(f"⚠️ {error_msg}")
                            
                        elif msg_type == "pong":
                            pass  # Keepalive response
                            
                    except json.JSONDecodeError:
                        pass
                    except Exception as e:
                        self._log(f"❌ Message error: {e}")
                    
        except websockets.exceptions.ConnectionClosed as e:
            self._log(f"🔌 Connection closed: {e.code}")
        except Exception as e:
            self._log(f"❌ Listen error: {e}")
        finally:
            self.is_connected = False
    
    async def disconnect(self):
        if self.websocket:
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=2.0)
            except:
                pass
            self._log("👋 Disconnected")


async def run_bots(session_code: str, num_bots: int, batch_size: int = 10, batch_delay: float = 1.0):
    print("\n" + "="*60)
    print(f"🤖 QUIZ BOT TESTER - {num_bots} bots")
    print(f"📍 Session: {session_code}")
    print(f"🎯 Accuracy: {BOT_ACCURACY_MIN*100:.0f}%-{BOT_ACCURACY_MAX*100:.0f}%")
    print(f"⏱️ Response: {RESPONSE_TIME_MIN}-{RESPONSE_TIME_MAX}s")
    print("="*60 + "\n")
    
    bots = [QuizBot(i + 1, session_code) for i in range(num_bots)]
    connected_bots = []
    
    # Connect in batches
    print("📡 Connecting bots...")
    for i in range(0, num_bots, batch_size):
        batch = bots[i:i + batch_size]
        results = await asyncio.gather(*[b.connect() for b in batch], return_exceptions=True)
        for b, result in zip(batch, results):
            if result is True:
                connected_bots.append(b)
        print(f"   Connected {len(connected_bots)}/{num_bots}...")
        if i + batch_size < num_bots:
            await asyncio.sleep(batch_delay)
    
    print(f"✅ {len(connected_bots)}/{num_bots} connected\n")
    if not connected_bots:
        print("❌ No bots connected")
        return
    
    # Join in batches
    print("🚪 Joining session...")
    for i in range(0, len(connected_bots), batch_size):
        batch = connected_bots[i:i + batch_size]
        for bot in batch:
            await bot.join_session()
            await asyncio.sleep(0.1)  # Smaller delay for joining
        if i + batch_size < len(connected_bots):
            await asyncio.sleep(batch_delay * 0.5)
    
    print(f"\n✅ All bots joined!")
    print("⏳ Waiting for quiz to start...\n")
    
    # Start listeners
    listeners = [asyncio.create_task(bot.listen_loop()) for bot in connected_bots]
    
    # Wait for quiz to start (first bot gets a question)
    start_wait = 0
    while not any(b.current_question for b in connected_bots) and start_wait < 300:
        await asyncio.sleep(0.5)
        start_wait += 0.5
        # Check if any bots disconnected
        connected_bots = [b for b in connected_bots if b.is_connected]
        if not connected_bots:
            print("❌ All bots disconnected while waiting")
            return
    
    if not any(b.current_question for b in connected_bots):
        print("⚠️ Timeout waiting for quiz to start")
        return
    
    print("\n🎮 QUIZ IN PROGRESS - All bots answering together\n")
    
    # Main quiz loop - all bots answer together
    max_rounds = 100  # Safety limit
    round_count = 0
    no_progress_count = 0
    last_question_count = 0
    
    while round_count < max_rounds:
        round_count += 1
        
        # Check if all bots completed or disconnected
        active_connected = [b for b in connected_bots if b.is_connected and not b.quiz_completed]
        completed_bots = [b for b in connected_bots if b.quiz_completed]
        
        total_questions_answered = sum(b.questions_answered for b in connected_bots)
        
        if round_count % 5 == 0:  # Log every 5 rounds
            print(f"📊 Round {round_count}: {len(completed_bots)} completed, {len(active_connected)} active, {total_questions_answered} total answers")
        
        if not active_connected:
            print("✅ All bots completed or disconnected")
            break
        
        # Get bots that have a question and are ready to answer
        ready_bots = [b for b in active_connected if b.current_question and not b.waiting_for_result]
        
        if not ready_bots:
            no_progress_count += 1
            if no_progress_count > 30:  # 15 seconds with no progress
                print("⚠️ No progress for 15 seconds...")
                
                # Check if bots answered all questions
                for bot in active_connected:
                    if bot.total_questions > 0 and bot.questions_answered >= bot.total_questions:
                        bot.quiz_completed = True
                        print(f"   ✅ {bot.username} marked complete ({bot.questions_answered}/{bot.total_questions})")
                
                no_progress_count = 0
            await asyncio.sleep(0.5)
            continue
        
        no_progress_count = 0
        
        # All ready bots submit answers together (in batches to prevent overwhelming)
        for i in range(0, len(ready_bots), batch_size):
            batch = ready_bots[i:i + batch_size]
            await asyncio.gather(*[b.submit_answer() for b in batch], return_exceptions=True)
            if i + batch_size < len(ready_bots):
                await asyncio.sleep(0.2)
        
        # Wait for results (with timeout)
        timeout = 20
        while timeout > 0:
            waiting = [b for b in ready_bots if b.waiting_for_result and b.is_connected]
            if not waiting:
                break
            await asyncio.sleep(0.3)
            timeout -= 0.3
        
        if timeout <= 0:
            print(f"⚠️ Timeout waiting for {len([b for b in ready_bots if b.waiting_for_result])} results, continuing...")
            for bot in ready_bots:
                bot.waiting_for_result = False
        
        # Delay between questions
        await asyncio.sleep(QUESTION_DELAY)
        
        # All non-completed bots request next question together (in batches)
        non_completed = [b for b in connected_bots if b.is_connected and not b.quiz_completed]
        if non_completed:
            for i in range(0, len(non_completed), batch_size):
                batch = non_completed[i:i + batch_size]
                await asyncio.gather(*[b.request_next() for b in batch], return_exceptions=True)
                if i + batch_size < len(non_completed):
                    await asyncio.sleep(0.1)
        
        # Wait for questions to arrive
        await asyncio.sleep(1.5)
    
    # All done - wait a bit for final messages
    print("\n" + "="*60)
    print("🏁 QUIZ SESSION COMPLETE!")
    print("="*60)
    print("⏳ Waiting 10 seconds for final results...\n")
    await asyncio.sleep(10)
    
    # Print results
    print("="*60)
    print("📊 FINAL RESULTS")
    print("="*60)
    
    sorted_bots = sorted(connected_bots, key=lambda b: b.score, reverse=True)
    for i, bot in enumerate(sorted_bots[:20]):  # Show top 20
        acc = (bot.correct_answers / bot.questions_answered * 100) if bot.questions_answered > 0 else 0
        status = "✅" if bot.quiz_completed else ("🔌" if not bot.is_connected else "❓")
        print(f"  {i+1:2d}. {status} {bot.username}: {bot.score} pts ({bot.correct_answers}/{bot.questions_answered} correct, {acc:.0f}%)")
    
    if len(sorted_bots) > 20:
        print(f"  ... and {len(sorted_bots) - 20} more bots")
    
    print("="*60)
    
    # Summary stats
    total_answered = sum(b.questions_answered for b in connected_bots)
    total_correct = sum(b.correct_answers for b in connected_bots)
    completed_count = sum(1 for b in connected_bots if b.quiz_completed)
    disconnected_count = sum(1 for b in connected_bots if not b.is_connected)
    
    print(f"\n📈 SUMMARY:")
    print(f"   Total bots: {len(connected_bots)}")
    print(f"   Completed: {completed_count}")
    print(f"   Disconnected: {disconnected_count}")
    print(f"   Total answers: {total_answered}")
    print(f"   Total correct: {total_correct}")
    if total_answered > 0:
        print(f"   Overall accuracy: {total_correct/total_answered*100:.1f}%")
    print()
    
    # Cancel listeners and disconnect
    for task in listeners:
        task.cancel()
    
    print("👋 Disconnecting...")
    await asyncio.gather(*[b.disconnect() for b in connected_bots], return_exceptions=True)
    print("✅ Done!")


def main():
    global WS_BASE_URL
    
    parser = argparse.ArgumentParser(description="Quiz Bot Tester")
    parser.add_argument("session_code", help="Session code")
    parser.add_argument("--bots", "-b", type=int, default=DEFAULT_BOT_COUNT)
    parser.add_argument("--batch", type=int, default=10)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--url", "-u", type=str, default=WS_BASE_URL)
    
    args = parser.parse_args()
    WS_BASE_URL = args.url
    
    asyncio.run(run_bots(args.session_code, args.bots, args.batch, args.delay))


if __name__ == "__main__":
    main()
