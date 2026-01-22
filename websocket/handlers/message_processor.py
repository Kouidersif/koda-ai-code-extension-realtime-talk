import asyncio
import base64
import json
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class MessageProcessor:
    """Processes incoming WebSocket messages and routes them to appropriate queues."""
    
    def __init__(self, audio_input_queue: asyncio.Queue, video_input_queue: asyncio.Queue, 
                 text_input_queue: asyncio.Queue, context_manager):
        self.audio_input_queue = audio_input_queue
        self.video_input_queue = video_input_queue
        self.text_input_queue = text_input_queue
        self.context_manager = context_manager
        self.current_turn_audio = []
    
    async def process_audio_chunk(self, audio_chunk: bytes):
        """Process incoming audio data."""
        # Add to Gemini queue with timeout to detect backpressure
        try:
            await asyncio.wait_for(self.audio_input_queue.put(audio_chunk), timeout=0.1)
        except asyncio.TimeoutError:
            logger.warning(f"Gemini audio queue full (size: {self.audio_input_queue.qsize()}), dropping chunk")
        except Exception as e:
            logger.error(f"Error adding audio to Gemini queue: {e}")
        
        # Accumulate audio for current turn
        self.current_turn_audio.append(audio_chunk)
    
    async def process_text_message(self, text: str):
        """Process incoming text messages."""
        try:
            payload = json.loads(text)
            
            # Handle editor context messages (legacy and new formats)
            if isinstance(payload, dict):
                payload_type = payload.get("type")
                payload_subtype = payload.get("subtype")
                
                # Handle context messages
                if (payload_type == "context" and payload_subtype in ["selection", "tree"]) or payload_type == "editor_context":
                    self.context_manager.update_context(payload)
                    return
                
                # Handle image messages
                if payload_type == "image":
                    image_data = base64.b64decode(payload["data"])
                    await self.video_input_queue.put(image_data)
                    return
                
                # Handle legacy context messages (from editorMonitor events)
                if payload_type == "context":
                    # Ignore these old-style messages
                    return
                    
        except json.JSONDecodeError:
            pass

        await self.text_input_queue.put(text)
    
    def clear_current_turn_audio(self):
        """Clear audio data for the current turn."""
        self.current_turn_audio.clear()