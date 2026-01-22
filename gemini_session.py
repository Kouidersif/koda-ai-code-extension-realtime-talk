import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from gemini_live import GeminiLive
from websocket.handlers import ContextManager, MessageProcessor

logger = logging.getLogger(__name__)


class GeminiSessionManager:
    """Manages the Gemini Live session and event processing."""
    
    def __init__(self, gemini_client: GeminiLive, context_manager: ContextManager, 
                 message_processor: MessageProcessor):
        self.gemini_client = gemini_client
        self.context_manager = context_manager
        self.message_processor = message_processor
        
        # Track user and Gemini text for the current turn
        self.current_user_text: Optional[str] = None
        self.current_gemini_text: Optional[str] = None
    
    async def start_session(self, audio_input_queue: asyncio.Queue, video_input_queue: asyncio.Queue,
                           text_input_queue: asyncio.Queue, audio_output_callback: Callable,
                           audio_interrupt_callback: Callable, event_callback: Callable):
        """
        Start the Gemini Live session and process events.
        
        Args:
            audio_input_queue: Queue for audio input
            video_input_queue: Queue for video input  
            text_input_queue: Queue for text input
            audio_output_callback: Callback for audio output
            audio_interrupt_callback: Callback for audio interruption
            event_callback: Callback for forwarding events to client
        """
        try:
            logger.info("Starting Gemini session...")
            async for event in self.gemini_client.start_session(
                audio_input_queue=audio_input_queue,
                video_input_queue=video_input_queue,
                text_input_queue=text_input_queue,
                audio_output_callback=audio_output_callback,
                audio_interrupt_callback=audio_interrupt_callback,
            ):
                if event:
                    await self._process_event(event, text_input_queue, event_callback)
            
            logger.warning("Gemini session ended normally (no more events)")
        except Exception as e:
            logger.error(f"Error in Gemini session loop: {e}", exc_info=True)
            # Notify client via callback
            try:
                await event_callback({
                    "type": "system_error",
                    "message": "Gemini connection lost"
                })
            except Exception:
                pass
            raise
    
    async def _process_event(self, event: Dict[str, Any], text_input_queue: asyncio.Queue,
                            event_callback: Callable):
        """Process individual Gemini events."""
        event_type = event.get("type")
        
        # USER SPEECH DETECTED - this is when we inject context!
        if event_type == "user":
            user_text = event.get("text", "")
            self.current_user_text = user_text
            
            logger.info(f"🗣️  User said: {user_text}")
            
            # Only inject context once at the START of user speech
            if not self.context_manager.user_speech_detected:
                self.context_manager.on_user_speech_started()
                # Inject context now - before Gemini processes the full utterance
                await self.context_manager.inject_context_if_needed(text_input_queue)
        
        elif event_type == "gemini":
            if self.current_gemini_text:
                self.current_gemini_text += event.get("text", "")
            else:
                self.current_gemini_text = event.get("text", "")
        
        elif event_type == "error":
            logger.error(f"Gemini error: {event.get('error')}")
            await event_callback({
                "type": "system_error",
                "message": f"Gemini connection error: {event.get('error')}"
            })
        
        elif event_type == "turn_complete":
            logger.info("Gemini event: turn_complete")
            
            # Debug summary for this turn
            logger.info(f"📊 TURN SUMMARY: user_speech={bool(self.current_user_text)}, "
                      f"context_injected={self.context_manager.context_injected_this_speech}, "
                      f"pending_context={bool(self.context_manager.pending_context)}, "
                      f"total_injections={self.context_manager.context_injected_count}")
            
            # Reset for next user turn
            self.context_manager.reset_for_new_turn()
            self.message_processor.clear_current_turn_audio()
            self.current_user_text = None
            self.current_gemini_text = None
                
        elif event_type == "interrupted":
            logger.debug("Gemini event: interrupted")
            # Don't reset context state on interruption - just clear audio
            self.message_processor.clear_current_turn_audio()
        
        # Forward events to client
        await event_callback(event)