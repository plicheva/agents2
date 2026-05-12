# Architecture & Data Flow

## Overview

This project is a **direct WebSocket integration** with the Azure Voice Live API — no SDK is used. The Python backend (Quart) acts as a relay between the browser and Voice Live, forwarding audio and signaling in both directions.

## Components

### 1. Frontend (`static/index.html`)

A single-page vanilla HTML/JS application that handles:

- **Microphone capture**: Uses `getUserMedia` + `ScriptProcessor` to capture PCM16 audio at 24 kHz
- **TTS playback**: Receives raw PCM16 bytes from the backend and plays them via an `AudioWorkletNode` ring-buffer
- **Avatar rendering**: Sets up a WebRTC `RTCPeerConnection` when avatar is enabled; video/audio tracks are rendered into the avatar container
- **WebSocket messaging**: Sends binary PCM16 audio and receives JSON control messages + binary TTS audio

### 2. Audio Processor (`static/audio-processor.js`)

An `AudioWorkletProcessor` that implements a ring-buffer for smooth TTS playback. It receives Float32 PCM samples via `port.postMessage` and outputs them to the audio destination. Supports a `clear` command for barge-in (stopping playback when the user starts speaking).

### 3. Backend (`backend.py`)

A Quart async web server that:

- Serves the static frontend on `GET /`
- Exposes a WebSocket endpoint at `/web/ws`
- For each browser connection, opens a second WebSocket to Voice Live API
- Runs two async loops:
  - **Sender loop**: Drains a queue of base64-encoded audio and forwards it to Voice Live
  - **Receiver loop**: Processes all Voice Live events and relays audio/transcripts/signaling to the browser
- Performs CRM lookup from `UserInfo/` text files based on caller phone number
- Proxies WebRTC signaling (SDP offer/answer) for avatar

### 4. CRM Data (`UserInfo/`)

Plain text files named by phone number (e.g. `8867771295.txt`). The backend reads these to enrich the system prompt with caller-specific information (name, cards, balances, transactions).

## Data Flow

### Audio (non-avatar)

```
Browser mic → PCM16 bytes → WebSocket (binary) → Backend
    → base64 encode → input_audio_buffer.append → Voice Live

Voice Live → response.audio.delta (base64 PCM16) → Backend
    → base64 decode → WebSocket (binary) → Browser AudioWorklet → Speakers
```

### Control Messages (Browser ↔ Backend)

| Direction | Message | Purpose |
|-----------|---------|---------|
| Backend → Browser | `{"Kind": "StopAudio"}` | Barge-in: stop TTS playback |
| Backend → Browser | `{"Kind": "Transcription", "Text": "..."}` | Agent transcript |
| Backend → Browser | `{"Kind": "AvatarConfig", "Avatar": {...}}` | ICE servers + avatar config |
| Backend → Browser | `{"Kind": "AvatarSDPAnswer", "ServerSdp": "..."}` | WebRTC SDP answer (base64) |
| Browser → Backend | `{"Kind": "AvatarSDP", "ClientSdp": "..."}` | WebRTC SDP offer (base64) |

### Voice Live Events Handled

| Event | Action |
|-------|--------|
| `session.created` | Logged |
| `session.updated` | Logged; avatar config forwarded to browser |
| `input_audio_buffer.speech_started` | Send `StopAudio` to browser (barge-in) |
| `input_audio_buffer.speech_stopped` | Record timestamp for latency measurement |
| `conversation.item.input_audio_transcription.completed` | Log user speech |
| `response.audio.delta` | Decode + send PCM16 to browser |
| `response.audio_transcript.done` | Log + send transcript to browser |
| `session.avatar.connecting` | Forward SDP answer; send deferred greeting |
| `response.done` | Check for errors |
| `error` | Log error details |

## Session Lifecycle

```
1. Browser opens WebSocket to /web/ws?callerId=XXX&avatar=1
2. Backend creates VoiceLiveSession
3. Backend opens WSS to Voice Live API with auth headers
4. Backend sends session.update (system prompt, VAD, voice, avatar config)
5. If avatar: wait for session.avatar.connecting before sending response.create
   If no avatar: send response.create immediately (proactive greeting)
6. Two async loops run until the browser disconnects:
   - Sender: browser audio → Voice Live
   - Receiver: Voice Live events → browser
7. On browser disconnect, Voice Live WebSocket is closed
```

## Latency Measurement

The backend tracks **time-to-first-audio-byte**: the time between `input_audio_buffer.speech_stopped` and the first `response.audio.delta`. This is logged as `Time to first audio byte: [latency=XXXms]`.
