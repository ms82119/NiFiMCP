"""
Database storage for chat history persistence.

This module handles storing and retrieving chat messages using SQLite.
"""

import json
import sqlite3
from typing import List, Dict, Any, Optional
from datetime import datetime
from loguru import logger


class ChatStorage:
    """Handles chat message storage and retrieval."""
    
    def __init__(self, db_path: str = "chat_history.db"):
        self.db_path = db_path
        self.logger = logger.bind(component="ChatStorage")
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        provider TEXT,
                        model_name TEXT,
                        nifi_server_id TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        metadata TEXT
                    )
                """)
                
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS workflows (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT UNIQUE NOT NULL,
                        workflow_name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        start_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                        end_time DATETIME,
                        result TEXT
                    )
                """)
                
                # Create indexes for better performance
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_request_id ON messages(request_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_request_id ON workflows(request_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status)")
                
                self.logger.info("Database initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize database: {e}")
            raise
    
    def save_message(
        self, 
        request_id: str, 
        role: str, 
        content: str,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
        nifi_server_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Save a chat message to the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # For assistant messages, check if we already have a UI conversation format message for this request
                if role == "assistant":
                    cursor = conn.execute(
                        """SELECT id FROM messages 
                           WHERE request_id = ? AND role = ? 
                           AND json_extract(metadata, '$.format') = 'ui_conversation'
                           ORDER BY timestamp DESC LIMIT 1""",
                        (request_id, role)
                    )
                    existing = cursor.fetchone()
                    
                    if existing:
                        self.logger.debug(f"UI conversation format assistant message already exists for request {request_id}")
                        return
                
                metadata_json = json.dumps(metadata) if metadata else None
                conn.execute(
                    """INSERT INTO messages 
                       (request_id, role, content, provider, model_name, nifi_server_id, metadata) 
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (request_id, role, content, provider, model_name, nifi_server_id, metadata_json)
                )
                self.logger.debug(f"Saved message for request {request_id}")
        except Exception as e:
            self.logger.error(f"Failed to save message: {e}")
            raise
    
    def save_workflow(
        self,
        request_id: str,
        workflow_name: str,
        status: str = "started",
        result: Optional[Dict[str, Any]] = None
    ):
        """Save or update workflow information."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                if status == "started":
                    conn.execute(
                        "INSERT OR REPLACE INTO workflows (request_id, workflow_name, status) VALUES (?, ?, ?)",
                        (request_id, workflow_name, status)
                    )
                else:
                    result_json = json.dumps(result) if result else None
                    conn.execute(
                        """UPDATE workflows 
                           SET status = ?, end_time = CURRENT_TIMESTAMP, result = ? 
                           WHERE request_id = ?""",
                        (status, result_json, request_id)
                    )
                self.logger.debug(f"Saved workflow {workflow_name} for request {request_id}")
        except Exception as e:
            self.logger.error(f"Failed to save workflow: {e}")
            raise
    
    def get_chat_history(
        self, 
        limit: Optional[int] = None, 
        request_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get chat history from the database for UI display."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row  # Enable dict-like access
                
                # Query to load user messages, system messages, and UI conversation format messages
                # Exclude LLM conversation format messages (used for LLM history, not UI display)
                query = """
                    SELECT * FROM messages 
                    WHERE role = 'user' 
                       OR (role = 'assistant' AND json_extract(metadata, '$.format') = 'ui_conversation')
                       OR role = 'system'
                """
                params = []
                
                if request_id:
                    query += " AND request_id = ?"
                    params.append(request_id)
                
                query += " ORDER BY timestamp ASC"
                
                if limit:
                    query += f" LIMIT {limit}"
                
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()
                
                # Convert to list of dictionaries and deduplicate
                messages = []
                seen_content = set()  # Track content to avoid duplicates
                
                for row in rows:
                    message = dict(row)
                    # Parse metadata if present
                    if message.get('metadata'):
                        try:
                            message['metadata'] = json.loads(message['metadata'])
                        except json.JSONDecodeError:
                            message['metadata'] = None
                    
                    # Create a unique key for deduplication (request_id + role + content length + first 100 chars)
                    content = message.get('content', '')
                    content_preview = content[:100] if content else ''
                    unique_key = f"{message.get('request_id', '')}_{message.get('role', '')}_{len(content)}_{content_preview}"
                    
                    if unique_key not in seen_content:
                        seen_content.add(unique_key)
                        messages.append(message)
                    else:
                        self.logger.debug(f"Dropping duplicate message: {unique_key}")
                
                self.logger.debug(f"Retrieved {len(messages)} UI messages from database (after deduplication)")
                return messages
        except Exception as e:
            self.logger.error(f"Failed to get chat history: {e}")
            return []
    
    def get_llm_conversation_history(
        self, 
        limit: Optional[int] = None, 
        request_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get LLM conversation history from the database for LLM context."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row  # Enable dict-like access
                
                # Query to load LLM conversation format messages (user, assistant, tool)
                query = """
                    SELECT * FROM messages 
                    WHERE json_extract(metadata, '$.format') = 'llm_conversation'
                       OR role = 'user'
                """
                params = []
                
                if request_id:
                    query += " AND request_id = ?"
                    params.append(request_id)
                
                query += " ORDER BY timestamp ASC"
                
                if limit:
                    query += f" LIMIT {limit}"
                
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()
                
                # Convert to list of dictionaries
                messages = []
                for row in rows:
                    message = dict(row)
                    # Parse metadata if present
                    if message.get('metadata'):
                        try:
                            message['metadata'] = json.loads(message['metadata'])
                        except json.JSONDecodeError:
                            message['metadata'] = None
                    messages.append(message)
                
                self.logger.debug(f"Retrieved {len(messages)} LLM conversation messages from database")
                return messages
        except Exception as e:
            self.logger.error(f"Failed to get LLM conversation history: {e}")
            return []
    
    def get_workflow_status(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get workflow status for a specific request."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM workflows WHERE request_id = ?",
                    (request_id,)
                )
                row = cursor.fetchone()
                
                if row:
                    workflow = dict(row)
                    # Parse result if present
                    if workflow.get('result'):
                        try:
                            workflow['result'] = json.loads(workflow['result'])
                        except json.JSONDecodeError:
                            workflow['result'] = None
                    return workflow
                return None
        except Exception as e:
            self.logger.error(f"Failed to get workflow status: {e}")
            return None
    
    def get_recent_workflows(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent workflows."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM workflows ORDER BY start_time DESC LIMIT ?",
                    (limit,)
                )
                rows = cursor.fetchall()
                
                workflows = []
                for row in rows:
                    workflow = dict(row)
                    # Parse result if present
                    if workflow.get('result'):
                        try:
                            workflow['result'] = json.loads(workflow['result'])
                        except json.JSONDecodeError:
                            workflow['result'] = None
                    workflows.append(workflow)
                
                return workflows
        except Exception as e:
            self.logger.error(f"Failed to get recent workflows: {e}")
            return []
    
    def cleanup_old_messages(self, days: int = 30):
        """Clean up messages older than specified days."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM messages WHERE timestamp < datetime('now', '-{} days')".format(days)
                )
                deleted_count = conn.total_changes
                self.logger.info(f"Cleaned up {deleted_count} old messages")
        except Exception as e:
            self.logger.error(f"Failed to cleanup old messages: {e}")
    
    def clear_chat_history(self):
        """Clear all chat history from the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Delete all messages
                conn.execute("DELETE FROM messages")
                deleted_count = conn.total_changes
                self.logger.info(f"Cleared {deleted_count} messages from chat history")
                
                # Also clear workflows table
                conn.execute("DELETE FROM workflows")
                deleted_workflows = conn.total_changes
                self.logger.info(f"Cleared {deleted_workflows} workflows from history")
                
        except Exception as e:
            self.logger.error(f"Failed to clear chat history: {e}")
            raise


# Global storage instance
storage = ChatStorage()

