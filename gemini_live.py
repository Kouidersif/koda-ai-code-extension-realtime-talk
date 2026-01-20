import asyncio
import inspect
import os
from google import genai
from google.genai import types
from google.oauth2 import service_account
import json


# Configuration
GOOGLE_VERTEX_PROJECT = os.getenv("PROJECT_ID", "social-media-moderation-434816")
GOOGLE_VERTEX_LOCATION = os.getenv("LOCATION", "us-central1")
GEMINI_MODEL = os.getenv("MODEL", "gemini-live-2.5-flash-preview-native-audio-09-2025")
# GEMINI_MODEL: str = "gemini-live-2.5-flash-native-audio"


SCOPES=['https://www.googleapis.com/auth/cloud-platform']

def get_google_creds():
    creds_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    # optional local fallback: path to JSON file
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")  # e.g. /path/to/key.json
    if path:
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)

    raise RuntimeError("Missing creds: set GOOGLE_APPLICATION_CREDENTIALS_JSON (preferred) or GOOGLE_APPLICATION_CREDENTIALS (file path).")


class GeminiLive:
    """
    Handles the interaction with the Gemini Live API.
    """
    def __init__(self, input_sample_rate, tools=None, tool_mapping=None):
        """
        Initializes the GeminiLive client.

        Args:
            input_sample_rate (int): The sample rate for audio input.
            tools (list, optional): List of tools to enable. Defaults to None.
            tool_mapping (dict, optional): Mapping of tool names to functions. Defaults to None.
        """
        self.project_id = GOOGLE_VERTEX_PROJECT
        self.location = GOOGLE_VERTEX_LOCATION
        self.model = GEMINI_MODEL
        creds = get_google_creds()
        
        self.client = genai.Client(
            vertexai=True,
            project=self.project_id,
            location=self.location,
            credentials=creds
        )
        self.input_sample_rate = input_sample_rate
        self.tools = tools or []
        self.tool_mapping = tool_mapping or {}

    async def start_session(self, audio_input_queue, video_input_queue, text_input_queue, audio_output_callback, audio_interrupt_callback=None):
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Puck"
                    )
                )
            ),
            system_instruction=types.Content(parts=[types.Part(text="""
You are a helpful AI coding assistant integrated into VS Code. Respond to EVERY user message with a concise, friendly, conversational verbal audio response.

CONTEXT YOU MAY RECEIVE:
- [SELECTION CONTEXT] (primary): focus on the selected code; be specific about what you see.
- [WORKSPACE TREE]: use for project layout and file location hints.
- [EDITOR CONTEXT]: current file/snippet/cursor; refer to it when user says “this function”.

RESPONSE RULES:
- If no code context is provided and the user asks about code, say: “I don’t see any code selected. Could you select the code you want to discuss?”
- Be specific (line numbers/symbols) when helpful.
- Pair-program: explain, debug, improve.

COPILOT PROMPT GENERATION (generate_prompt):
- DO NOT generate a Copilot prompt by default.
- Only call generate_prompt when:
  (a) the user explicitly asks you to “write a prompt for Copilot / Copilot Chat”, OR
  (b) you ask “Want me to generate a Copilot prompt?” and the user says yes.
- If the user just wants help/explanation, answer directly instead of generating a prompt.
- If you do call generate_prompt, the prompt MUST follow RISEN (Role, Instructions, Steps, End goal, Narrowing).
- After generating it, briefly say you created a Copilot prompt.


""")]),
            
            
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            # Disable proactive audio - it can interfere with turn detection
            # proactivity=types.ProactivityConfig(proactive_audio=True),
            tools=self.tools,
        )
        
        async with self.client.aio.live.connect(model=self.model, config=config) as session:
            
            async def send_audio():
                try:
                    import logging
                    logger = logging.getLogger(__name__)
                    while True:
                        chunk = await audio_input_queue.get()
                        try:
                            await session.send_realtime_input(
                                audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={self.input_sample_rate}")
                            )
                        except Exception as e:
                            logger.error(f"Error sending audio to Gemini: {e}")
                            raise  # Re-raise to stop the task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Fatal error in send_audio: {e}", exc_info=True)

            async def send_video():
                try:
                    while True:
                        chunk = await video_input_queue.get()
                        await session.send_realtime_input(
                            video=types.Blob(data=chunk, mime_type="image/jpeg")
                        )
                except asyncio.CancelledError:
                    pass

            async def send_text():
                try:
                    while True:
                        text = await text_input_queue.get()
                        await session.send(input=text, end_of_turn=True)
                except asyncio.CancelledError:
                    pass

            event_queue = asyncio.Queue()

            async def receive_loop():
                try:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.info("Gemini receive loop started")
                    response_count = 0
                    while True:
                        async for response in session.receive():
                            response_count += 1
                            server_content = response.server_content
                            tool_call = response.tool_call
                            
                            # Debug logging to see what we're getting
                            if server_content:
                                logger.debug(f"Response #{response_count}: model_turn={bool(server_content.model_turn)}, "
                                           f"input_trans={bool(server_content.input_transcription)}, "
                                           f"output_trans={bool(server_content.output_transcription)}, "
                                           f"turn_complete={bool(server_content.turn_complete)}, "
                                           f"interrupted={bool(server_content.interrupted)}")
                                
                                if server_content.model_turn:
                                    has_audio = False
                                    if not server_content.model_turn.parts:
                                        logger.warning(f"model_turn present but no parts: {server_content.model_turn}")
                                        continue
                                    for part in server_content.model_turn.parts:
                                        if part.inline_data:
                                            has_audio = True
                                            if inspect.iscoroutinefunction(audio_output_callback):
                                                await audio_output_callback(part.inline_data.data)
                                            else:
                                                audio_output_callback(part.inline_data.data)
                                    
                                    if not has_audio:
                                        logger.warning(f"model_turn present but no audio data in parts: {server_content.model_turn.parts}")
                                
                                if server_content.input_transcription and server_content.input_transcription.text:
                                    logger.info(f"User said: {server_content.input_transcription.text}")
                                    await event_queue.put({"type": "user", "text": server_content.input_transcription.text})
                                
                                if server_content.output_transcription and server_content.output_transcription.text:
                                    logger.info(f"Gemini said: {server_content.output_transcription.text}")
                                    await event_queue.put({"type": "gemini", "text": server_content.output_transcription.text})
                                
                                if server_content.turn_complete:
                                    await event_queue.put({"type": "turn_complete"})
                                
                                if server_content.interrupted:
                                    if audio_interrupt_callback:
                                        if inspect.iscoroutinefunction(audio_interrupt_callback):
                                            await audio_interrupt_callback()
                                        else:
                                            audio_interrupt_callback()
                                    await event_queue.put({"type": "interrupted"})

                            if tool_call:
                                function_responses = []
                                if not tool_call.function_calls:
                                    logger.warning(f"tool_call present but no function calls: {tool_call}")
                                    continue
                                for fc in tool_call.function_calls:
                                    func_name = fc.name
                                    args = fc.args or {}
                                    
                                    if func_name in self.tool_mapping:
                                        try:
                                            tool_func = self.tool_mapping[func_name]
                                            if inspect.iscoroutinefunction(tool_func):
                                                result = await tool_func(**args)
                                            else:
                                                loop = asyncio.get_running_loop()
                                                result = await loop.run_in_executor(None, lambda: tool_func(**args))
                                        except Exception as e:
                                            result = f"Error: {e}"
                                        
                                        function_responses.append(types.FunctionResponse(
                                            name=func_name,
                                            id=fc.id,
                                            response={"result": result}
                                        ))
                                        
                                        # If this is generate_prompt, emit prompt_ready event
                                        if func_name == 'generate_prompt' and isinstance(result, dict) and result.get('success'):
                                            prompt_text = result.get('prompt', '')
                                            logger.info(f"Prompt generated ({len(prompt_text)} chars), emitting prompt_ready")
                                            await event_queue.put({"type": "prompt_ready", "prompt": prompt_text})
                                        else:
                                            # Generic tool call event
                                            await event_queue.put({"type": "tool_call", "name": func_name, "args": args, "result": result})
                                
                                await session.send_tool_response(function_responses=function_responses)

                except Exception as e:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"Error in Gemini receive_loop: {e}", exc_info=True)
                    await event_queue.put({"type": "error", "error": str(e)})
                finally:
                    import logging
                    logging.getLogger(__name__).warning("Gemini receive loop ended")
                    await event_queue.put(None)

            send_audio_task = asyncio.create_task(send_audio())
            send_video_task = asyncio.create_task(send_video())
            send_text_task = asyncio.create_task(send_text())
            receive_task = asyncio.create_task(receive_loop())

            try:
                while True:
                    event = await event_queue.get()
                    if event is None:
                        import logging
                        logging.getLogger(__name__).warning("Gemini session ended - event queue received None")
                        break
                    if isinstance(event, dict) and event.get("type") == "error":
                        # Just yield the error event, don't raise to keep the stream alive if possible or let caller handle
                        yield event
                        break 
                    yield event
            finally:
                import logging
                logging.getLogger(__name__).info("Cancelling Gemini tasks...")
                send_audio_task.cancel()
                send_video_task.cancel()
                send_text_task.cancel()
                receive_task.cancel()
