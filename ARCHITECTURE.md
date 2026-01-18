# System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND (Browser)                       │
│                                                                  │
│  ┌────────────────┐     ┌──────────────────────────────────┐   │
│  │  Microphone    │────▶│   Media Handler (media-handler.js)│   │
│  │   Input        │     │   - Captures audio at 16kHz PCM16 │   │
│  └────────────────┘     │   - Downsamples & converts        │   │
│                         └──────────────┬───────────────────┘   │
│                                        │                         │
│  ┌────────────────────────────────────▼─────────────────────┐  │
│  │          WebSocket Client (gemini-client.js)             │  │
│  │          - Sends audio chunks to backend                 │  │
│  │          - Receives audio & JSON messages                │  │
│  └──────────────┬────────────────────────┬──────────────────┘  │
│                 │                        │                      │
│  ┌──────────────▼────────┐   ┌──────────▼──────────────────┐  │
│  │  Chat Display         │   │ Transcription Panel         │  │
│  │  - Gemini responses   │   │ - Original transcription    │  │
│  │  - User messages      │   │ - Arabic translation        │  │
│  │                       │   │ - Detected language         │  │
│  └───────────────────────┘   └─────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
                                    │
                                    │ WebSocket (/ws)
                                    ▼
┌──────────────────────────────────────────────────────────────┐
│                    BACKEND (FastAPI - Python)                 │
│                                                                │
│  ┌────────────────────────────────────────────────────────┐  │
│  │              WebSocket Handler (main.py)               │  │
│  │                                                        │  │
│  │  1. Receives audio bytes                              │  │
│  │  2. Puts into audio_input_queue ──────┐              │  │
│  │  3. Accumulates in audio_buffer        │              │  │
│  │  4. Every ~3 seconds ─────────┐       │              │  │
│  └────────────────────────────────┼───────┼──────────────┘  │
│                                   │       │                  │
│                                   │       │                  │
│  ┌────────────────────────────────▼───────▼──────────────┐  │
│  │         Parallel Processing                           │  │
│  │  ┌──────────────────┐    ┌─────────────────────────┐ │  │
│  │  │  Gemini Live API │    │  OpenAI Whisper API     │ │  │
│  │  │  (gemini_live.py)│    │                         │ │  │
│  │  │                  │    │  - Convert PCM → WAV    │ │  │
│  │  │  - Live chat     │    │  - Transcribe audio     │ │  │
│  │  │  - Voice response│    │  - Detect language      │ │  │
│  │  │  - Transcription │    │  - Translate (Whisper)  │ │  │
│  │  │                  │    │  - Runs async (5s min)  │ │  │
│  │  └────────┬─────────┘    └──────────┬──────────────┘ │  │
│  └───────────┼──────────────────────────┼─────────────────┘  │
│              │                          │                    │
│              │ Audio bytes              │ JSON response      │
│              │ JSON events              │                    │
│              │                          │                    │
│  ┌───────────▼──────────────────────────▼─────────────────┐  │
│  │           WebSocket Send to Frontend                   │  │
│  │  - Audio chunks (Gemini voice)                         │  │
│  │  - JSON: {type: "user", text: "..."}                   │  │
│  │  - JSON: {type: "gemini", text: "..."}                 │  │
│  │  - JSON: {type: "openai_transcription",                │  │
│  │           data: {transcription, translation, lang}}    │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
                    │                       │
                    │                       │
                    ▼                       ▼
        ┌────────────────────┐   ┌──────────────────┐
        │  Google Gemini API │   │   OpenAI API     │
        │  (Live Audio)      │   │  (Whisper)       │
        └────────────────────┘   └──────────────────┘
```

## Data Flow

### Audio Input Flow
1. User speaks → Microphone captures audio
2. Audio Worklet processes PCM audio at browser sample rate
3. Downsampled to 16kHz PCM16
4. Sent via WebSocket to backend
5. Backend receives and:
   - Forwards to Gemini Live (real-time)
   - Buffers for OpenAI (batched ~3s chunks)

### Gemini Response Flow
1. Gemini processes audio in real-time
2. Returns audio response + transcriptions
3. Audio sent to frontend as bytes
4. Transcriptions sent as JSON messages
5. Frontend displays in chat + plays audio

### OpenAI Transcription Flow
1. Backend accumulates audio chunks
2. Every 5+ seconds (with debouncing):
   - Converts PCM chunks to WAV
   - Sends to Whisper API for transcription
   - Requests language detection
   - Translates to English if needed (using Whisper translation)
   - Runs as background task (non-blocking)
3. Receives transcription response
4. Forwards to frontend via WebSocket
5. Frontend updates transcription panel

**Debouncing Mechanism:**
- Minimum 5 seconds between OpenAI calls
- Prevents infinite loops and API rate limiting
- Uses timestamp tracking and processing flag
- Runs in background to not block Gemini responses

### Editor Context Flow (VS Code Extension)
1. EditorMonitor watches for:
   - Active editor changes (file switches)
   - Selection changes
   - Cursor movement (debounced)
2. Builds structured context payload:
   - File name, language, line count
   - Cursor position
   - Selected text (if any)
   - Code snippet around cursor (~40 lines)
   - Optional: full file, git diff
3. Applies safety measures:
   - Size truncation (max 20KB default)
   - Secret redaction (API keys, passwords, etc.)
4. Sends to backend as JSON via WebSocket
5. Backend stores latest context per connection
6. On next audio turn, context is injected as text message to Gemini
7. Gemini uses context to ground its responses

**Context Injection Strategy:**
- Context is injected once per conversation turn (before first audio chunk)
- Formatted as structured text that Gemini can parse
- Reset after turn_complete or interrupted events
- Only latest context is kept (no history accumulation)

## Message Types

### Frontend → Backend
- `bytes`: Audio PCM16 data
- `text`: User text message or image data (JSON)
- `{type: "editor_context", version: 1, data: {...}}`: VS Code editor context

### Backend → Frontend
- `bytes`: Gemini audio response
- `{type: "user", text: "..."}`: User speech transcription (from Gemini)
- `{type: "gemini", text: "..."}`: Gemini response transcription
- `{type: "openai_transcription", data: {...}}`: OpenAI transcription result
- `{type: "interrupted"}`: Gemini interrupted
- `{type: "turn_complete"}`: Turn finished

### Editor Context Schema
```json
{
  "type": "editor_context",
  "version": 1,
  "timestamp": 1234567890,
  "revision": 42,
  "data": {
    "uri": "file:///path/to/file.ts",
    "fileName": "src/file.ts",
    "languageId": "typescript",
    "lineCount": 150,
    "cursor": { "line": 10, "character": 4 },
    "selection": {
      "start": { "line": 10, "character": 0 },
      "end": { "line": 15, "character": 20 },
      "text": "selected code..."
    },
    "snippet": {
      "startLine": 1,
      "endLine": 40,
      "text": "code around cursor..."
    },
    "fullText": null,
    "gitDiff": null
  }
}
```
