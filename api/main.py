"""
Main FastAPI application for NiFi Chat UI.

This module sets up the FastAPI application with WebSocket support,
static file serving, and API endpoints.
"""

import json
import asyncio
import sys
import os
from typing import Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

# Add parent directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Setup logging
try:
    from config.logging_setup import setup_logging
    setup_logging(context='client')
    logger.info("FastAPI Chat UI logging initialized")
except ImportError as e:
    logger.warning(f"Could not import logging setup: {e}")

from .websocket_manager import manager
from .chat_api import router as chat_router
from .settings_api import router as settings_router
from .diagram_api import router as diagram_router
from .storage import storage
from .event_bridge import event_bridge

# Create FastAPI app
app = FastAPI(
    title="NiFi Chat UI",
    description="Real-time chat interface for NiFi workflow management",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include API routers
app.include_router(chat_router)
app.include_router(settings_router)
app.include_router(diagram_router)


@app.get("/", response_class=HTMLResponse)
async def get_index():
    """Serve the main HTML page."""
    try:
        with open("static/index.html", "r") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(
            content="""
            <html>
                <head><title>NiFi Chat UI</title></head>
                <body>
                    <h1>NiFi Chat UI</h1>
                    <p>Static files not found. Please ensure the frontend is built.</p>
                </body>
            </html>
            """,
            status_code=404
        )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time communication."""
    await manager.connect(websocket)
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            
            try:
                message = json.loads(data)
                await handle_websocket_message(message, websocket)
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON received: {data}")
                await manager.send_personal_message(
                    json.dumps({"type": "error", "message": "Invalid JSON format"}),
                    websocket
                )
            except Exception as e:
                logger.error(f"Error handling WebSocket message: {e}")
                await manager.send_personal_message(
                    json.dumps({"type": "error", "message": str(e)}),
                    websocket
                )
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


async def handle_websocket_message(message: Dict[str, Any], websocket: WebSocket):
    """Handle incoming WebSocket messages."""
    message_type = message.get("type")
    
    if message_type == "ping":
        # Respond to ping with pong
        await manager.send_personal_message(
            json.dumps({"type": "pong"}),
            websocket
        )
    
    elif message_type == "stop_workflow":
        # Handle workflow stop request
        request_id = message.get("request_id")
        if request_id:
            # TODO: Implement workflow stopping logic
            logger.info(f"Stop workflow request for {request_id}")
            await manager.send_personal_message(
                json.dumps({
                    "type": "workflow_stopped",
                    "request_id": request_id
                }),
                websocket
            )
    
    elif message_type == "get_status":
        # Handle status request
        await manager.send_personal_message(
            json.dumps({
                "type": "status",
                "connections": manager.get_connection_count(),
                "timestamp": str(asyncio.get_event_loop().time())
            }),
            websocket
        )
    
    else:
        logger.warning(f"Unknown message type: {message_type}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "connections": manager.get_connection_count(),
        "database": "connected"  # TODO: Add actual database health check
    }


@app.on_event("startup")
async def startup_event():
    """Application startup event."""
    logger.info("NiFi Chat UI FastAPI server starting up...")
    
    # Initialize database
    try:
        storage.init_database()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
    
    # Start event bridge
    try:
        await event_bridge.start()
        logger.info("Event bridge started successfully")
    except Exception as e:
        logger.error(f"Failed to start event bridge: {e}")
    
    logger.info("NiFi Chat UI FastAPI server started successfully")


@app.on_event("shutdown")
async def shutdown_event():
    """Application shutdown event."""
    logger.info("NiFi Chat UI FastAPI server shutting down...")
    
    # Close all WebSocket connections
    for connection in manager.active_connections[:]:  # Copy list to avoid modification during iteration
        try:
            await connection.close()
        except Exception as e:
            logger.error(f"Error closing WebSocket connection: {e}")
    
    logger.info("NiFi Chat UI FastAPI server shutdown complete")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
