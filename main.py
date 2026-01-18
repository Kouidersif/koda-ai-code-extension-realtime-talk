import asyncio
import base64
import json
import logging
import os
import io
import wave
import hashlib

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from gemini_live import GeminiLive
from openai import AsyncOpenAI

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


def format_editor_context_for_gemini(context: dict) -> str:
    """
    Format editor context payload into a text message for Gemini.
    Handles both legacy 'editor_context' and new 'context' (selection/tree) formats.
    """
    try:
        context_type = context.get("type")
        subtype = context.get("subtype")
        data = context.get("data", {})
        
        parts = []
        
        # NEW: Handle selection-only context
        if context_type == "context" and subtype == "selection":
            parts.append("[SELECTION CONTEXT - The user has selected this code and wants to discuss it]")
            
            file_name = data.get("fileName", "unknown")
            language = data.get("languageId", "unknown")
            parts.append(f"File: {file_name} ({language})")
            
            selection = data.get("selection", {})
            if selection:
                start = selection.get("start", {})
                end = selection.get("end", {})
                parts.append(f"Selection: lines {start.get('line', 0) + 1}-{end.get('line', 0) + 1}")
                
                sel_text = selection.get("text", "")
                if sel_text:
                    parts.append(f"\n--- SELECTED CODE ---\n{sel_text}\n--- END SELECTION ---")
            
            parts.append("[END SELECTION CONTEXT]")
            return "\n".join(parts)
        
        # NEW: Handle workspace tree context
        if context_type == "context" and subtype == "tree":
            parts.append("[WORKSPACE TREE - Directory structure of the user's project]")
            
            roots = data.get("roots", [])
            for root in roots:
                name = root.get("name", "workspace")
                tree = root.get("tree", "")
                parts.append(f"\n--- {name} ---\n{tree}")
            
            parts.append("[END WORKSPACE TREE]")
            return "\n".join(parts)
        
        # LEGACY: Handle editor_context format
        parts.append("[EDITOR CONTEXT - Use this to answer questions about the user's current code]")
        
        # File info
        file_name = data.get("fileName", "unknown")
        language = data.get("languageId", "unknown")
        parts.append(f"File: {file_name} ({language})")
        
        # Cursor position
        cursor = data.get("cursor")
        if cursor:
            parts.append(f"Cursor at line {cursor.get('line', 0) + 1}, column {cursor.get('character', 0) + 1}")
        
        # Selection (if any)
        selection = data.get("selection")
        if selection and selection.get("text"):
            sel_text = selection["text"]
            if len(sel_text) > 500:
                sel_text = sel_text[:500] + "... (truncated)"
            parts.append(f"\n--- SELECTED CODE ---\n{sel_text}\n--- END SELECTION ---")
        
        # Code snippet around cursor
        snippet = data.get("snippet")
        if snippet and snippet.get("text"):
            start_line = snippet.get("startLine", 1)
            end_line = snippet.get("endLine", 1)
            parts.append(f"\n--- CODE SNIPPET (lines {start_line}-{end_line}) ---\n{snippet['text']}\n--- END SNIPPET ---")
        
        # Git diff (if available)
        git_diff = data.get("gitDiff")
        if git_diff:
            if len(git_diff) > 1000:
                git_diff = git_diff[:1000] + "... (truncated)"
            parts.append(f"\n--- GIT DIFF ---\n{git_diff}\n--- END DIFF ---")
        
        parts.append("[END EDITOR CONTEXT]")
        
        return "\n".join(parts)
        
    except Exception as e:
        logger.error(f"Error formatting editor context: {e}")
        return ""


@app.get("/")
async def root():
    return FileResponse("frontend/index.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for Gemini Live."""
    await websocket.accept()

    logger.info("WebSocket connection accepted")

    audio_input_queue = asyncio.Queue(maxsize=100)  # Limit queue size to detect backpressure
    video_input_queue = asyncio.Queue(maxsize=10)
    text_input_queue = asyncio.Queue(maxsize=10)
    audio_buffer = []  # Buffer to accumulate audio chunks for current turn
    openai_processing = False  # Flag to prevent concurrent OpenAI calls
    current_turn_audio = []  # Audio for the current conversation turn
    
    # Editor context state (per connection)
    # Pending context (latest received, may not be injected yet)
    pending_context = None
    pending_context_hash = None
    # Last injected context hash (to avoid re-injecting identical context)
    last_injected_context_hash = None
    # Track if we've injected context for the CURRENT user speech turn
    # Only inject when user actually starts speaking (detected via transcription)
    user_speech_detected = False
    context_injected_this_speech = False
    
    # Debug counters
    context_received_count = 0
    context_injected_count = 0

    async def audio_output_callback(data):
        await websocket.send_bytes(data)

    async def audio_interrupt_callback():
        # The event queue handles the JSON message, but we might want to do something else here
        pass

    gemini_client = GeminiLive(
        input_sample_rate=16000 
    )

    # async def process_audio_with_openai(audio_data_copy, user_text_from_gemini=None, gemini_response=None):
    #     """Process accumulated audio with OpenAI Whisper for transcription/translation."""
    #     nonlocal openai_processing
        
    #     if not openai_client:
    #         return
        
    #     try:
    #         # Gemini already validated this is real speech by responding!
    #         # Just calculate energy for logging purposes
    #         import numpy as np
    #         audio_array = np.frombuffer(audio_data_copy, dtype=np.int16)
    #         audio_energy = np.sqrt(np.mean(audio_array.astype(np.float32)**2))
            
    #         # Create WAV file in memory
    #         wav_io = io.BytesIO()
    #         with wave.open(wav_io, 'wb') as wav_file:
    #             wav_file.setnchannels(1)  # Mono
    #             wav_file.setsampwidth(2)  # 16-bit
    #             wav_file.setframerate(16000)  # 16kHz
    #             wav_file.writeframes(audio_data_copy)
            
    #         wav_io.seek(0)
    #         wav_io.name = "audio.wav"
            
    #         # Use Whisper API for transcription
    #         transcription = await openai_client.audio.transcriptions.create(
    #             model="gpt-4o-transcribe",
    #             file=wav_io,
    #             # response_format="verbose_json"
    #         )
            
    #         no_speech_prob = getattr(transcription, 'no_speech_prob', 0)
    #         detected_lang = transcription.language if hasattr(transcription, 'language') else 'unknown'
    #         transcription_text = transcription.text if transcription.text else ""
            
    #         # Only skip if transcription is completely empty (Whisper failed)
    #         if not transcription_text or len(transcription_text.strip()) < 2:
    #             logger.warning(f"Whisper returned empty transcription despite Gemini validation")
    #             return
            
    #         # Log comparison with Gemini's transcription
    #         if user_text_from_gemini:
    #             logger.info(f"Gemini heard: '{user_text_from_gemini}'")
    #         logger.info(f"Whisper transcribed: '{transcription_text}' (energy: {audio_energy:.2f}, no_speech_prob: {no_speech_prob:.2f})")
            
    #         # If not English or Arabic, translate to Arabic using GPT
    #         translation_text = transcription_text
    #         if detected_lang not in ['en', 'ar', 'english', 'arabic'] and transcription_text:
    #             try:
    #                 # Use Chat Completions to translate to Arabic
    #                 chat_response = await openai_client.chat.completions.create(
    #                     model="gpt-4o-mini",
    #                     messages=[
    #                         {
    #                             "role": "system",
    #                             "content": "You are a translator. Translate the following text to Arabic. Return ONLY the Arabic translation, nothing else."
    #                         },
    #                         {
    #                             "role": "user",
    #                             "content": transcription_text
    #                         }
    #                     ],
    #                     temperature=0.3
    #                 )
    #                 translation_text = chat_response.choices[0].message.content.strip()
    #             except Exception as e:
    #                 logger.warning(f"Translation to Arabic failed: {e}")
    #                 # If translation fails, use transcription
    #                 translation_text = transcription_text
    #         elif detected_lang in ['en', 'english']:
    #             # If English, also translate to Arabic
    #             try:
    #                 chat_response = await openai_client.chat.completions.create(
    #                     model="gpt-4o-mini",
    #                     messages=[
    #                         {
    #                             "role": "system",
    #                             "content": "You are a translator. Translate the following English text to Arabic. Return ONLY the Arabic translation, nothing else."
    #                         },
    #                         {
    #                             "role": "user",
    #                             "content": transcription_text
    #                         }
    #                     ],
    #                     temperature=0.3
    #                 )
    #                 translation_text = chat_response.choices[0].message.content.strip()
    #             except Exception as e:
    #                 logger.warning(f"Translation to Arabic failed: {e}")
    #                 translation_text = transcription_text
            
    #         result = {
    #             "transcription": transcription_text,
    #             "translation": translation_text,
    #             "target_lang": detected_lang,
    #             "gemini_response": gemini_response  # Include Gemini's response
    #         }
            
    #         # Send to frontend
    #         await websocket.send_json({
    #             "type": "openai_transcription",
    #             "data": result
    #         })
            
    #         logger.info(f"OpenAI transcription: {result}")
            
    #     except Exception as e:
    #         logger.error(f"Error processing audio with OpenAI: {e}")
    #     finally:
    #         openai_processing = False
    
    async def receive_from_client():
        nonlocal openai_processing, current_turn_audio, pending_context, pending_context_hash
        nonlocal context_received_count
        
        try:
            while True:
                message = await websocket.receive()

                if message.get("bytes"):
                    audio_chunk = message["bytes"]
                    
                    # DO NOT inject context here on audio chunks!
                    # Context will be injected only when user speech is detected (in run_session)
                    
                    # Add to Gemini queue with timeout to detect backpressure
                    try:
                        await asyncio.wait_for(audio_input_queue.put(audio_chunk), timeout=0.1)
                    except asyncio.TimeoutError:
                        logger.warning(f"Gemini audio queue full (size: {audio_input_queue.qsize()}), dropping chunk")
                    except Exception as e:
                        logger.error(f"Error adding audio to Gemini queue: {e}")
                    
                    # Accumulate audio for current turn (for OpenAI processing after Gemini responds)
                    current_turn_audio.append(audio_chunk)
                        
                elif message.get("text"):
                    text = message["text"]
                    try:
                        payload = json.loads(text)
                        
                        # Handle editor context messages (legacy and new formats)
                        if isinstance(payload, dict):
                            payload_type = payload.get("type")
                            payload_subtype = payload.get("subtype")
                            
                            # NEW: Handle selection context
                            if payload_type == "context" and payload_subtype == "selection":
                                context_received_count += 1
                                
                                data = payload.get("data", {})
                                selection_text = data.get("selection", {}).get("text", "")[:500]
                                hash_content = json.dumps({
                                    "type": "selection",
                                    "fileName": data.get("fileName"),
                                    "selectionText": selection_text,
                                }, sort_keys=True)
                                new_hash = hashlib.md5(hash_content.encode()).hexdigest()[:16]
                                
                                if new_hash != pending_context_hash:
                                    pending_context = payload
                                    pending_context_hash = new_hash
                                    logger.info(f"Selection context updated: file={data.get('fileName', 'unknown')}, "
                                              f"chars={len(data.get('selection', {}).get('text', ''))}, hash={new_hash}")
                                continue
                            
                            # NEW: Handle workspace tree context
                            if payload_type == "context" and payload_subtype == "tree":
                                context_received_count += 1
                                
                                data = payload.get("data", {})
                                tree_content = json.dumps(data, sort_keys=True)[:2000]
                                new_hash = hashlib.md5(tree_content.encode()).hexdigest()[:16]
                                
                                if new_hash != pending_context_hash:
                                    pending_context = payload
                                    pending_context_hash = new_hash
                                    logger.info(f"Workspace tree context updated: roots={len(data.get('roots', []))}, hash={new_hash}")
                                continue
                            
                            # LEGACY: Handle editor_context format
                            if payload_type == "editor_context":
                                context_received_count += 1
                                
                                data = payload.get("data", {})
                                hash_content = json.dumps({
                                    "fileName": data.get("fileName"),
                                    "cursor": data.get("cursor"),
                                    "selection": data.get("selection", {}).get("text", "")[:500] if data.get("selection") else None,
                                    "snippet": data.get("snippet", {}).get("text", "")[:2000] if data.get("snippet") else None,
                                }, sort_keys=True)
                                new_hash = hashlib.md5(hash_content.encode()).hexdigest()[:16]
                                
                                if new_hash != pending_context_hash:
                                    pending_context = payload
                                    pending_context_hash = new_hash
                                    logger.info(f"Editor context updated: file={data.get('fileName', 'unknown')}, "
                                              f"hash={new_hash}, received_count={context_received_count}")
                                else:
                                    logger.debug(f"Context unchanged (hash={new_hash}), skipping update")
                                continue
                        
                        # Handle image messages
                        if isinstance(payload, dict) and payload.get("type") == "image":
                            image_data = base64.b64decode(payload["data"])
                            await video_input_queue.put(image_data)
                            continue
                            
                        # Handle legacy context messages (from editorMonitor events)
                        if isinstance(payload, dict) and payload.get("type") == "context":
                            # Ignore these old-style messages
                            continue
                            
                    except json.JSONDecodeError:
                        pass

                    await text_input_queue.put(text)
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected")
        except Exception as e:
            logger.error(f"Error receiving from client: {e}")

    receive_task = asyncio.create_task(receive_from_client())

    # Track user and Gemini text for the current turn
    current_user_text = None
    current_gemini_text = None
    
    async def run_session():
        nonlocal current_turn_audio, current_user_text, current_gemini_text, openai_processing
        nonlocal pending_context, pending_context_hash, last_injected_context_hash
        nonlocal user_speech_detected, context_injected_this_speech, context_injected_count
        
        async def inject_context_if_needed():
            """Inject context only if: pending exists, hash differs from last injected, and not already injected this speech"""
            nonlocal last_injected_context_hash, context_injected_this_speech, context_injected_count
            
            if not pending_context:
                return
            if context_injected_this_speech:
                return
            if pending_context_hash == last_injected_context_hash:
                logger.debug(f"Context unchanged (hash={pending_context_hash}), skipping injection")
                return
            
            context_text = format_editor_context_for_gemini(pending_context)
            if context_text:
                try:
                    await text_input_queue.put(context_text)
                    last_injected_context_hash = pending_context_hash
                    context_injected_this_speech = True
                    context_injected_count += 1
                    logger.info(f"Injected editor context (hash={pending_context_hash}, total_injections={context_injected_count})")
                except Exception as e:
                    logger.error(f"Error injecting context: {e}")
        
        try:
            logger.info("Starting Gemini session...")
            async for event in gemini_client.start_session(
                audio_input_queue=audio_input_queue,
                video_input_queue=video_input_queue,
                text_input_queue=text_input_queue,
                audio_output_callback=audio_output_callback,
                audio_interrupt_callback=audio_interrupt_callback,
            ):
                if event:
                    event_type = event.get("type")
                    
                    # USER SPEECH DETECTED - this is when we inject context!
                    if event_type == "user":
                        user_text = event.get("text", "")
                        current_user_text = user_text
                        
                        # Only inject context once at the START of user speech
                        if not user_speech_detected:
                            user_speech_detected = True
                            logger.info(f"User speech started: '{user_text[:50]}...'")
                            # Inject context now - before Gemini processes the full utterance
                            await inject_context_if_needed()
                    
                    elif event_type == "gemini":
                        if current_gemini_text:
                            current_gemini_text += event.get("text", "")
                        else:
                            current_gemini_text = event.get("text", "")
                    
                    elif event_type == "error":
                        logger.error(f"Gemini error: {event.get('error')}")
                        await websocket.send_json({
                            "type": "system_error",
                            "message": f"Gemini connection error: {event.get('error')}"
                        })
                    
                    elif event_type == "turn_complete":
                        logger.info(f"Gemini event: turn_complete")
                        # Reset for next user turn - DO NOT reset last_injected_context_hash!
                        user_speech_detected = False
                        context_injected_this_speech = False
                        current_turn_audio.clear()
                        current_user_text = None
                        current_gemini_text = None
                            
                    elif event_type == "interrupted":
                        logger.debug(f"Gemini event: interrupted")
                        # Don't reset context state on interruption - just clear audio
                        current_turn_audio.clear()
                    
                    # Forward events to client (check if still connected)
                    if websocket.application_state.value == 1:  # WebSocketState.CONNECTED
                        try:
                            await websocket.send_json(event)
                        except Exception as e:
                            logger.warning(f"Failed to send event to client: {e}")
                            break
                    else:
                        logger.debug("Client disconnected, stopping event loop")
                        break
            
            logger.warning("Gemini session ended normally (no more events)")
        except Exception as e:
            logger.error(f"Error in Gemini session loop: {e}", exc_info=True)
            # Notify client
            try:
                await websocket.send_json({
                    "type": "system_error",
                    "message": "Gemini connection lost"
                })
            except:
                pass

    try:
        await run_session()
    except Exception as e:
        logger.error(f"Error in Gemini session: {e}")
    finally:
        receive_task.cancel()
        # Ensure websocket is closed if not already
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8001))
    uvicorn.run(app, host="localhost", port=port)
