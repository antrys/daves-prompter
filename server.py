"""
FastAPI server for the teleprompter application.
Serves the web frontend and provides WebSocket for real-time speech sync.
"""

import asyncio
import json
import signal
from pathlib import Path
from typing import List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from speech_engine import SpeechEngine
from word_matcher import WordMatcher

# Configuration
HOST = "0.0.0.0"
PORT = 8765
MODEL_PATHS = [
    "models/vosk-model-small-en-us-0.15",
    "models/vosk-model-en-us-0.22",
    "models/vosk-model-en-us-0.42-gigaspeech",
]

app = FastAPI(title="Dave's Prompter")

# Global state
speech_engine: Optional[SpeechEngine] = None
word_matcher = WordMatcher()
connected_clients: Set[WebSocket] = set()
is_running = False
current_script = ""
main_loop: Optional[asyncio.AbstractEventLoop] = None


class ScriptRequest(BaseModel):
    """Request to load a script."""
    text: str


class ConfigRequest(BaseModel):
    """Configuration update request."""
    device_index: Optional[int] = None


async def broadcast(message: dict):
    """Broadcast a message to all connected WebSocket clients."""
    if not connected_clients:
        return
    
    message_json = json.dumps(message)
    disconnected = set()
    
    for client in connected_clients:
        try:
            await client.send_text(message_json)
        except Exception:
            disconnected.add(client)
    
    # Clean up disconnected clients
    for client in disconnected:
        connected_clients.discard(client)


def on_partial_result(text: str):
    """Handle partial speech recognition result - THIS IS THE MAIN DRIVER."""
    # Match partials aggressively - this is what makes the prompter responsive
    words = text.split()
    if len(words) >= 3:  # Need at least a few words
        result = word_matcher.match_words(words)
        
        if result.confidence > 0 and main_loop:
            asyncio.run_coroutine_threadsafe(broadcast({
                "type": "match",
                "spoken_text": text,
                "word_index": result.word_index,
                "confidence": result.confidence,
                "matched_words": result.matched_words,
                "context": word_matcher.get_context()
            }), main_loop)


def on_final_result(text: str):
    """Handle final speech recognition result."""
    words = text.split()
    if words:
        result = word_matcher.match_words(words)
        
        if main_loop:
            asyncio.run_coroutine_threadsafe(broadcast({
                "type": "match",
                "spoken_text": text,
                "word_index": result.word_index,
                "confidence": result.confidence,
                "matched_words": result.matched_words,
                "context": word_matcher.get_context()
            }), main_loop)


def on_words_result(words: list):
    """Handle word-level recognition results."""
    # Extract just the words
    word_texts = [w['word'] for w in words]
    if word_texts:
        result = word_matcher.match_words(word_texts)
        
        if main_loop:
            asyncio.run_coroutine_threadsafe(broadcast({
                "type": "words",
                "words": words,
                "word_index": result.word_index,
                "confidence": result.confidence,
                "matched_words": result.matched_words,
                "context": word_matcher.get_context()
            }), main_loop)


def find_model() -> Optional[str]:
    """Find an available Vosk model."""
    for path in MODEL_PATHS:
        if Path(path).exists():
            return path
    return None


def init_speech_engine() -> bool:
    """Initialize the speech engine with an available model."""
    global speech_engine
    
    model_path = find_model()
    if not model_path:
        print("No Vosk model found. Please download one to the models/ directory.")
        return False
    
    print(f"Using model: {model_path}")
    speech_engine = SpeechEngine(model_path)
    
    # Set up callbacks
    speech_engine.on_partial(on_partial_result)
    speech_engine.on_result(on_final_result)
    speech_engine.on_words(on_words_result)
    
    return True


# API Routes

@app.get("/")
async def index():
    """Serve the main page."""
    return FileResponse("static/index.html")


@app.get("/api/status")
async def get_status():
    """Get the current status of the application."""
    return {
        "running": is_running,
        "script_loaded": len(current_script) > 0,
        "word_count": word_matcher.get_word_count(),
        "current_position": word_matcher.current_position,
        "model_loaded": speech_engine is not None and speech_engine.model is not None
    }


@app.get("/api/devices")
async def list_devices():
    """List available audio input devices."""
    if speech_engine is None:
        if not init_speech_engine():
            return JSONResponse(
                status_code=503,
                content={"error": "Speech engine not initialized"}
            )
    
    devices = speech_engine.list_devices()
    return {"devices": devices}


@app.post("/api/script")
async def load_script(request: ScriptRequest):
    """Load a script for the teleprompter."""
    global current_script
    
    current_script = request.text
    word_matcher.set_script(request.text)
    
    # Broadcast script update to all clients
    await broadcast({
        "type": "script",
        "text": request.text,
        "word_count": word_matcher.get_word_count()
    })
    
    return {
        "success": True,
        "word_count": word_matcher.get_word_count()
    }


@app.get("/api/script")
async def get_script():
    """Get the current script."""
    return {
        "text": current_script,
        "word_count": word_matcher.get_word_count(),
        "current_position": word_matcher.current_position
    }


@app.post("/api/config")
async def update_config(request: ConfigRequest):
    """Update configuration."""
    if speech_engine and request.device_index is not None:
        speech_engine.set_device(request.device_index)
    
    return {"success": True}


@app.post("/api/start")
async def start_recognition():
    """Start speech recognition."""
    global is_running
    
    if speech_engine is None:
        if not init_speech_engine():
            return JSONResponse(
                status_code=503,
                content={"error": "Failed to initialize speech engine"}
            )
    
    if not speech_engine.model:
        if not speech_engine.load_model():
            return JSONResponse(
                status_code=503,
                content={"error": "Failed to load speech model"}
            )
    
    if speech_engine.start():
        is_running = True
        await broadcast({"type": "status", "running": True})
        return {"success": True, "running": True}
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to start speech recognition"}
        )


@app.post("/api/stop")
async def stop_recognition():
    """Stop speech recognition."""
    global is_running
    
    if speech_engine:
        speech_engine.stop()
    
    is_running = False
    await broadcast({"type": "status", "running": False})
    return {"success": True, "running": False}


@app.post("/api/reset")
async def reset_position():
    """Reset the script position to the beginning."""
    word_matcher.reset()
    
    if speech_engine:
        speech_engine.reset()
    
    await broadcast({
        "type": "reset",
        "position": 0,
        "context": word_matcher.get_context()
    })
    
    return {"success": True, "position": 0}


@app.post("/api/shutdown")
async def shutdown_server():
    """Safely shutdown the server."""
    global is_running, speech_engine
    
    # Stop recognition if running
    if speech_engine and is_running:
        speech_engine.stop()
        is_running = False
    
    # Notify clients
    await broadcast({"type": "shutdown", "message": "Server shutting down"})
    
    # Clean up speech engine
    if speech_engine:
        speech_engine.cleanup()
        speech_engine = None
    
    # Schedule shutdown after response is sent
    import threading
    def delayed_exit():
        import time
        time.sleep(0.5)
        import os
        os._exit(0)
    
    threading.Thread(target=delayed_exit, daemon=True).start()
    
    return {"success": True, "message": "Server shutting down"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await websocket.accept()
    connected_clients.add(websocket)
    
    # Send current state
    await websocket.send_json({
        "type": "init",
        "running": is_running,
        "script": current_script,
        "word_count": word_matcher.get_word_count(),
        "position": word_matcher.current_position,
        "context": word_matcher.get_context()
    })
    
    try:
        while True:
            # Handle incoming messages from client
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            
            elif message.get("type") == "goto":
                # Manual position update
                position = message.get("position", 0)
                word_matcher.current_position = max(0, min(position, word_matcher.get_word_count() - 1))
                await broadcast({
                    "type": "position",
                    "position": word_matcher.current_position,
                    "context": word_matcher.get_context()
                })
    
    except WebSocketDisconnect:
        connected_clients.discard(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        connected_clients.discard(websocket)


# Mount static files (must be after API routes)
app.mount("/static", StaticFiles(directory="static"), name="static")


# Startup/shutdown events
@app.on_event("startup")
async def startup():
    """Initialize on startup."""
    global main_loop
    main_loop = asyncio.get_event_loop()
    
    print(f"Starting Speech Prompter on http://{HOST}:{PORT}")
    
    # Try to initialize speech engine
    if init_speech_engine():
        print("Speech engine initialized successfully")
    else:
        print("Warning: Could not initialize speech engine. Download a Vosk model first.")


@app.on_event("shutdown")
async def shutdown():
    """Clean up on shutdown."""
    global speech_engine
    
    if speech_engine:
        speech_engine.cleanup()
        speech_engine = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    print("\nShutting down...")
    if speech_engine:
        speech_engine.cleanup()
    raise SystemExit(0)


if __name__ == "__main__":
    import uvicorn
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    uvicorn.run(app, host=HOST, port=PORT)



