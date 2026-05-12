# Voice Live Direct — Real-time Voice Agent with Avatar

A real-time voice agent built on **Azure Voice Live API** (no SDK). The Python backend connects directly to the Voice Live WebSocket, relaying audio between the browser and Azure. An optional **talking avatar** renders via WebRTC peer-to-peer video.

## Architecture

```
┌─────────────────────────┐         ┌─────────────────────────┐         ┌──────────────────┐
│    Browser (Frontend)   │◄──WS───►│  Python Server (Quart)  │◄──WS───►│ Azure Voice Live │
│                         │         │                         │         │     Service      │
│  • Mic capture (PCM16)  │         │  • Session management   │         └──────────────────┘
│  • TTS playback         │         │  • Audio relay          │                  │
│  • Avatar video         │◄──WebRTC (peer-to-peer video)────────────────────────┘
│  • AudioWorklet         │         │  • CRM lookup           │
│                         │         │  • Avatar SDP relay     │
└─────────────────────────┘         └─────────────────────────┘
```

## Project Structure

```
Voice live direct/
├── backend.py              # Quart server, Voice Live WebSocket relay, avatar signaling
├── requirements.txt        # Python dependencies
├── .env                    # Credentials & config (not committed)
├── .gitignore
├── static/
│   ├── index.html          # Frontend UI (audio + avatar + controls)
│   └── audio-processor.js  # AudioWorklet ring-buffer for TTS playback
├── UserInfo/
│   └── 8867771295.txt      # Sample CRM data (Kavya Sharma)
└── docs/
    ├── architecture.md     # Detailed architecture & data flow
    ├── configuration.md    # All environment variables & tuning
    └── avatar.md           # Avatar WebRTC setup & protocol
```

## Prerequisites

- **Python 3.10+**
- An **Azure AI Services** (Foundry) resource with Voice Live enabled
- A model deployment (GPT-4.1, GPT-4o, etc.) — either managed or BYO
- *(Optional)* Avatar requires a region that supports it: Southeast Asia, North Europe, West Europe, Sweden Central, South Central US, East US 2, or West US 2

## Quick Start

### 1. Clone & install

```bash
git clone <repo-url>
cd "Voice live direct"
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### 2. Configure `.env`

Create a `.env` file in the project root:

```env
# ── Required ──────────────────────────────────────────────
AZURE_VOICE_LIVE_ENDPOINT=https://<your-resource>.services.ai.azure.com/
AZURE_VOICE_LIVE_API_KEY=<your-api-key>
VOICE_LIVE_MODEL=gpt-4.1

# ── BYO Model (optional) ─────────────────────────────────
# BYOM_PROFILE=byom-azure-openai-chat-completion
# FOUNDRY_RESOURCE_OVERRIDE=<resource-name-if-different>

# ── Avatar (optional) ────────────────────────────────────
# AVATAR_CHARACTER=lisa
# AVATAR_STYLE=casual-sitting

# ── Managed Identity (Azure-hosted only) ─────────────────
# AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID=<client-id>
```

### 3. Run

```bash
python backend.py
```

Open **http://localhost:8000** in your browser.

### 4. Use

1. Enter a phone number (e.g. `8867771295` to load sample CRM data)
2. *(Optional)* Check **Enable Avatar** for a talking avatar
3. Click **Start Talking to Agent**
4. Speak — the agent responds in real time

## Authentication

| Method | When | Config |
|--------|------|--------|
| API Key | Local development | `AZURE_VOICE_LIVE_API_KEY` |
| Managed Identity | Azure-hosted (Container Apps, VM) | `AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID` |
| DefaultAzureCredential | Fallback (az login, env vars) | No extra config needed |

## Further Documentation

- [Architecture & Data Flow](docs/architecture.md)
- [Configuration Reference](docs/configuration.md)
- [Avatar (WebRTC) Setup](docs/avatar.md)

## License

Internal use — Microsoft demo project.
