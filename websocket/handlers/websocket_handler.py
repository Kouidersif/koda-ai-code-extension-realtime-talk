import asyncio
import logging
from typing import Optional, Dict, Any
from fastapi import WebSocket
from websocket.handlers.context_manager import ContextManager
from websocket.handlers.message_processor import MessageProcessor

logger = logging.getLogger(__name__)


class WebSocketHandler:
    """Main WebSocket connection handler."""
    
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.audio_input_queue = asyncio.Queue(maxsize=100)
        self.video_input_queue = asyncio.Queue(maxsize=10)
        self.text_input_queue = asyncio.Queue(maxsize=10)
        
        self.context_manager = ContextManager()
        self.message_processor = MessageProcessor(
            self.audio_input_queue, self.video_input_queue, 
            self.text_input_queue, self.context_manager
        )
        
        self.receive_task: Optional[asyncio.Task] = None
    
    async def accept_connection(self):
        """Accept the WebSocket connection."""
        await self.websocket.accept()
        logger.info("WebSocket connection accepted")
    
    async def start_message_reception(self):
        """Start the message reception loop."""
        self.receive_task = asyncio.create_task(self._receive_from_client())
    
    async def _receive_from_client(self):
        """Handle incoming WebSocket messages."""
        try:
            while True:
                message = await self.websocket.receive()

                if message.get("bytes"):
                    audio_chunk = message["bytes"]
                    await self.message_processor.process_audio_chunk(audio_chunk)
                        
                elif message.get("text"):
                    text = message["text"]
                    await self.message_processor.process_text_message(text)
                    
        except Exception as e:
            logger.error(f"Error receiving from client: {e}")
    
    async def send_json(self, data: Dict[str, Any]):
        """Send JSON data to the client."""
        if self.websocket.application_state.value == 1:  # WebSocketState.CONNECTED
            try:
                await self.websocket.send_json(data)
            except Exception as e:
                logger.warning(f"Failed to send JSON to client: {e}")
                raise
        else:
            logger.debug("Client disconnected, cannot send JSON")
            raise ConnectionError("WebSocket disconnected")
    
    async def send_bytes(self, data: bytes):
        """Send binary data to the client."""
        if self.websocket.application_state.value == 1:  # WebSocketState.CONNECTED
            try:
                await self.websocket.send_bytes(data)
            except Exception as e:
                logger.warning(f"Failed to send bytes to client: {e}")
                raise
        else:
            logger.debug("Client disconnected, cannot send bytes")
            raise ConnectionError("WebSocket disconnected")
    
    def cleanup(self):
        """Clean up resources."""
        if self.receive_task:
            self.receive_task.cancel()
        
        # Close websocket if not already closed
        try:
            # Note: websocket.close() should be called by the caller
            pass
        except Exception:
            pass