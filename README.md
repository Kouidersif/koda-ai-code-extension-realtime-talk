# Gemini Live API - Python SDK & Vanilla JS

A demonstration of the Gemini Live API using the [Google Gen AI Python SDK](https://github.com/googleapis/python-genai) for the backend and vanilla JavaScript for the frontend. This example shows how to build a real-time multimodal application with a robust Python backend handling the API connection.

**New Feature**: Integrated OpenAI Whisper API for real-time audio transcription and translation!

## Quick Start

### 1. Backend Setup

Install Python dependencies and start the FastAPI server:

```bash
# Install dependencies
pip install -r requirements.txt

# Set up your environment variables
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY and configure other settings

# Authenticate with Google Cloud
gcloud auth application-default login

# Start the server
python main.py
```

### 2. Frontend

Open your browser and navigate to:

[http://localhost:8000](http://localhost:8000)

## Features

- **Google Gen AI SDK**: Uses the official Python SDK (`google-genai`) for simplified API inte
- **🎙️ OpenAI Whisper Integration**: Real-time audio transcription and translation
  - Automatically transcribes user audio input
  - Detects the spoken language
  - Translates to English (when source language is not English or Arabic)
  - Displays results in a beautiful UI panel alongside the Gemini chat
  - Debounced to process every 5 seconds to avoid blocking Gemini responsesraction.
- **FastAPI Backend**: Robust, async-ready web server handling WebSocket connections.
- **Real-time Streaming**: Bi-directional audio and video streaming.
- **Tool Use**: Demonstrates how to register and handle server-side tools.
- **Vanilla JS Frontend**: Lightweight frontend with no build steps or framework dependencies.

## Project Structure

```
/
├── main.py             # FastAPI server & WebSocket endpoint
├── gemini_live.py      # Gemini Live API wrapper using Gen AI SDK
├── requirements.txt    # Python dependencies
└── frontend/
    ├── index.html      # User Interface
    ├── main.js         # Application logic
    ├── gemini-client.js # WebSocket client for backend communication
    ├── media-handler.js # Audio/Video capture and playback
    └── pcm-processor.js # AudioWorklet for PCM processingin a `.env` file or by directly editing the defaults in `main.py`.

**Required Configuration:**

1. **Google Cloud Project**: Update the `PROJECT_ID` to match your Google Cloud project.
2. **OpenAI API Key**: Add your OpenAI API key for GPT-audio-mini transcription/translation.

Create a `.env` file based on `.env.example`:

```bash
cp .env.example .env
```

Then edit `.env` with your credentials:

```env
PROJECT_ID=your-gcp-project-id
LOCATION=us-central1
MODEL=gemini-live-2.5-flash-native-audio
OPENAI_API_KEY=sk-your-openai-api-key-here
PORT=8001
```

Alternatively, you can set environment variables directly
# Configuration
PROJECT_ID = os.getenv("PROJECT_ID", "your-project-id-here")
```

Alternatively, you can set the `PROJECT_ID` environment variable before running the server.

## Core Components

### Backend (`gemini_live.py`)

The `GeminiLive` class wraps the `genai.Client` to manage the session:

```python
# Connects using the SDK
async with self.client.aio.live.connect(model=self.model, config=config) as session:
    # Manages input/output queues
    await asyncio.gather(
        send_audio(),
        send_video(),
        receive_responses()
    )
```

### Frontend (`gemini-client.js`)

The frontend communicates with the FastAPI backend via WebSockets, sending base64-encoded media chunks and receiving audio responses.
# koda-ai-code-extension-realtime-talk
# koda-ai-code-extension-realtime-talk
