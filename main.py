import asyncio
import base64
import json
import logging
import os
import hashlib

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from gemini_live import GeminiLive
from openai import AsyncOpenAI
from prompt_tools import get_prompt_tools, get_prompt_tool_mapping
from websocket.handlers import WebSocketHandler
from gemini_session import GeminiSessionManager

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize OpenAI client
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Initialize FastAPI
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
async def root():
    return FileResponse("frontend/index.html")


@app.get("/debug/context-status")
async def context_status():
    """Debug endpoint to check context injection status"""
    return {
        "message": "This endpoint would show real-time context state if we had a global state manager",
        "note": "Context state is per-websocket connection - check server logs for detailed debugging"
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for Gemini Live."""
    # Initialize the WebSocket handler
    ws_handler = WebSocketHandler(websocket)
    
    try:
        # Accept the WebSocket connection
        await ws_handler.accept_connection()
        
        # Start message reception
        await ws_handler.start_message_reception()
        
        # Set up callbacks for Gemini client
        async def audio_output_callback(data):
            await ws_handler.send_bytes(data)

        async def audio_interrupt_callback():
            # The event queue handles the JSON message, but we might want to do something else here
            pass
        
        async def event_callback(event):
            """Forward events to the WebSocket client."""
            await ws_handler.send_json(event)
        
        # Initialize Gemini client
        gemini_client = GeminiLive(
            input_sample_rate=16000,
            tools=get_prompt_tools(),
            tool_mapping=get_prompt_tool_mapping()
        )
        
        # Initialize session manager
        session_manager = GeminiSessionManager(
            gemini_client, 
            ws_handler.context_manager, 
            ws_handler.message_processor
        )
        
        # Start the Gemini session
        await session_manager.start_session(
            audio_input_queue=ws_handler.audio_input_queue,
            video_input_queue=ws_handler.video_input_queue,
            text_input_queue=ws_handler.text_input_queue,
            audio_output_callback=audio_output_callback,
            audio_interrupt_callback=audio_interrupt_callback,
            event_callback=event_callback
        )
        
    except Exception as e:
        logger.error(f"Error in WebSocket endpoint: {e}")
    finally:
        # Clean up resources
        ws_handler.cleanup()
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8001))
    uvicorn.run(app, host="localhost", port=port)
