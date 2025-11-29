from typing import Dict, List, Any, Set
from fastapi import WebSocket
import json
import logging
import asyncio

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        # Map session_code -> {user_id -> WebSocket}
        self.session_connections: Dict[str, Dict[str, WebSocket]] = {}
        # Map user_id -> session_code (for reverse lookup)
        self.user_sessions: Dict[str, str] = {}
        # Map session_code -> {user_id -> isHost}
        self.connection_roles: Dict[str, Dict[str, bool]] = {}
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
        # Track dead connections to clean up
        self._dead_connections: Set[str] = set()

    async def connect(self, websocket: WebSocket, session_code: str, user_id: str, is_host: bool = False):
        """Register a new WebSocket connection"""
        async with self._lock:
            if session_code not in self.session_connections:
                self.session_connections[session_code] = {}
            
            if session_code not in self.connection_roles:
                self.connection_roles[session_code] = {}
            
            # Remove old connection if user reconnecting
            if user_id in self.user_sessions:
                old_session = self.user_sessions[user_id]
                if old_session in self.session_connections:
                    if user_id in self.session_connections[old_session]:
                        del self.session_connections[old_session][user_id]
            
            self.session_connections[session_code][user_id] = websocket
            self.user_sessions[user_id] = session_code
            self.connection_roles[session_code][user_id] = is_host
            
            # Remove from dead connections if present
            self._dead_connections.discard(user_id)
            
            logger.info(f"User {user_id} connected to session {session_code} (host={is_host}). Total: {len(self.session_connections[session_code])}")

    def disconnect(self, websocket: WebSocket, session_code: str, user_id: str):
        """Remove a WebSocket connection (sync for use in finally block)"""
        try:
            if session_code in self.session_connections:
                if user_id in self.session_connections[session_code]:
                    del self.session_connections[session_code][user_id]
                    logger.info(f"👋 User {user_id} disconnected from session {session_code}")
                
                # Clean up empty sessions
                if not self.session_connections[session_code]:
                    del self.session_connections[session_code]
            
            if session_code in self.connection_roles:
                if user_id in self.connection_roles[session_code]:
                    del self.connection_roles[session_code][user_id]
                
                if not self.connection_roles[session_code]:
                    del self.connection_roles[session_code]
            
            if user_id in self.user_sessions:
                del self.user_sessions[user_id]
            
            # Mark as dead for cleanup
            self._dead_connections.add(user_id)
        except Exception as e:
            logger.error(f"Error during disconnect cleanup for {user_id}: {e}")

    async def _safe_send(self, websocket: WebSocket, message: dict, user_id: str = None) -> bool:
        """Safely send a message, returning False if connection is dead"""
        try:
            await asyncio.wait_for(websocket.send_json(message), timeout=5.0)
            return True
        except asyncio.TimeoutError:
            logger.warning(f"Send timeout for user {user_id}")
            return False
        except Exception as e:
            logger.debug(f"Send failed for {user_id}: {type(e).__name__}")
            return False

    async def send_personal_message(self, message: dict, websocket: WebSocket = None, session_code: str = None, user_id: str = None):
        """Send message to a specific user. Can use either websocket directly or session_code + user_id"""
        target_ws = websocket
        target_user = user_id
        
        if target_ws is None:
            # Look up websocket by user_id
            if user_id and user_id in self.user_sessions:
                session = self.user_sessions[user_id]
                if session in self.session_connections:
                    target_ws = self.session_connections[session].get(user_id)
            
            if target_ws is None:
                logger.debug(f"Cannot send personal message: user {user_id} not connected")
                return
        
        success = await self._safe_send(target_ws, message, target_user)
        if not success and target_user:
            # Mark connection as potentially dead
            self._dead_connections.add(target_user)

    async def broadcast_to_session(self, message: dict, session_code: str):
        """Broadcast message to all participants in a session (with dead connection cleanup)"""
        if session_code not in self.session_connections:
            return
        
        # Get snapshot of connections to avoid modification during iteration
        connections = dict(self.session_connections.get(session_code, {}))
        
        if not connections:
            return
        
        dead_users = []
        
        # Send to all connections concurrently with gather
        async def send_to_user(user_id: str, ws: WebSocket):
            success = await self._safe_send(ws, message, user_id)
            if not success:
                dead_users.append(user_id)
        
        # Use gather for parallel sends (much faster for 50+ users)
        await asyncio.gather(
            *[send_to_user(uid, ws) for uid, ws in connections.items()],
            return_exceptions=True
        )
        
        # Clean up dead connections
        if dead_users:
            logger.debug(f"Cleaning up {len(dead_users)} dead connections from session {session_code}")
            for user_id in dead_users:
                if session_code in self.session_connections:
                    if user_id in self.session_connections[session_code]:
                        del self.session_connections[session_code][user_id]
                self._dead_connections.add(user_id)

    async def broadcast_except(self, message: dict, session_code: str, exclude_user_id: str):
        """Broadcast to all participants except one"""
        if session_code not in self.session_connections:
            return
        
        connections = dict(self.session_connections.get(session_code, {}))
        dead_users = []
        
        for user_id, ws in connections.items():
            if user_id == exclude_user_id:
                continue
            
            success = await self._safe_send(ws, message, user_id)
            if not success:
                dead_users.append(user_id)
        
        # Clean up dead connections
        for user_id in dead_users:
            if session_code in self.session_connections:
                if user_id in self.session_connections[session_code]:
                    del self.session_connections[session_code][user_id]
    
    async def broadcast_to_host(self, message: dict, session_code: str, host_id: str):
        """Send message specifically to the host"""
        if session_code not in self.session_connections:
            return
        
        ws = self.session_connections.get(session_code, {}).get(host_id)
        if ws:
            await self._safe_send(ws, message, host_id)
    
    async def broadcast_to_participants(self, message: dict, session_code: str):
        """Broadcast message to all participants (non-host users) in a session"""
        if session_code not in self.session_connections:
            return
        
        if session_code not in self.connection_roles:
            return
        
        connections = dict(self.session_connections.get(session_code, {}))
        roles = dict(self.connection_roles.get(session_code, {}))
        dead_users = []
        
        async def send_to_participant(user_id: str, ws: WebSocket):
            # Skip hosts
            if roles.get(user_id, False):
                return
            success = await self._safe_send(ws, message, user_id)
            if not success:
                dead_users.append(user_id)
        
        await asyncio.gather(
            *[send_to_participant(uid, ws) for uid, ws in connections.items()],
            return_exceptions=True
        )
        
        # Clean up dead connections
        for user_id in dead_users:
            if session_code in self.session_connections:
                if user_id in self.session_connections[session_code]:
                    del self.session_connections[session_code][user_id]

    def get_connection_count(self, session_code: str) -> int:
        """Get number of active connections in a session"""
        return len(self.session_connections.get(session_code, {}))
    
    def is_user_connected(self, session_code: str, user_id: str) -> bool:
        """Check if a user is connected to a session"""
        return (session_code in self.session_connections and 
                user_id in self.session_connections[session_code])


manager = ConnectionManager()

