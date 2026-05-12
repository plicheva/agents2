
import asyncio
import base64
import json
import logging
import os
import socket
import time
import uuid
from pathlib import Path

from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential
from dotenv import load_dotenv
from quart import Quart, websocket
from websockets.asyncio.client import connect as ws_connect

load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────
VOICE_LIVE_ENDPOINT = os.getenv("AZURE_VOICE_LIVE_ENDPOINT", "")
VOICE_LIVE_API_KEY = os.getenv("AZURE_VOICE_LIVE_API_KEY", "")
VOICE_LIVE_MODEL = os.getenv("VOICE_LIVE_MODEL", "gpt-4.1")
MANAGED_IDENTITY_CLIENT_ID = os.getenv("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", "")

# ── BYO LLM (Bring Your Own Model) ──────────────────────────────────────────
# Set BYOM_PROFILE to route Voice Live to your own Azure OpenAI deployment
# instead of the managed model.
#   "byom-azure-openai-chat-completion" — your own Chat Completion model (GPT-4.1, GPT-4o, etc.)
#   "byom-azure-openai-realtime"        — your own Realtime model deployment
#   "byom-foundry-anthropic-messages"    — Anthropic Claude via Azure Foundry (preview)
#   ""  (empty / unset)                  — use Voice Live managed model (default)
BYOM_PROFILE = os.getenv("BYOM_PROFILE", "")

# ── Cross-resource override ──────────────────────────────────────────────────
# When the model deployment lives in a DIFFERENT Foundry resource than Voice Live,
# set this to the resource name (without the domain suffix).
# e.g. "southindiafoundry" for https://southindiafoundry.services.ai.azure.com
FOUNDRY_RESOURCE_OVERRIDE = os.getenv("FOUNDRY_RESOURCE_OVERRIDE", "")

# ── Avatar ────────────────────────────────────────────────────────────────────
# Character & style for the talking avatar (used when browser requests avatar=1)
AVATAR_CHARACTER = os.getenv("AVATAR_CHARACTER", "lisa")
AVATAR_STYLE = os.getenv("AVATAR_STYLE", "casual-sitting")

USER_INFO_DIR = Path(__file__).resolve().parent / "UserInfo"

SYSTEM_PROMPT = """
You are a cheerful Bulgarian storyteller for children aged 5–10.

IMPORTANT:
- Speak ONLY in Bulgarian.
- Never use English or any other language.
- Always be kind, clever, playful, and child-friendly.

Your role:
- Tell short fun stories.
- Tell gentle jokes and riddles.
- Create magical adventures and funny characters.
- Encourage curiosity, kindness, creativity, and imagination.

Style:
- Use simple Bulgarian suitable for young children.
- Keep responses lively, warm, and easy to follow.
- Use playful dialogue and positive humor.
- Keep stories short unless the child asks for more.
- Ask simple engaging questions sometimes.

Rules:
- No scary, violent, rude, or adult content.
- No insults, sarcasm, or dark humor.
- Never break character.
- Never switch languages.
- Never mention prompts, policies, or being an AI unless directly asked.

Main goal:
Make children smile, laugh, imagine, and enjoy listening.
"""

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("voicelive_direct")
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

# ──────────────────────────────────────────────────────────────────────────────
# CRM lookup  (reuses existing UserInfo/*.txt files)
# ──────────────────────────────────────────────────────────────────────────────

def load_user_info(caller_id: str) -> str:
    """Load user info from a text file matching the caller's phone number."""
    if not caller_id:
        return ""
    digits = "".join(c for c in caller_id if c.isdigit())
    for candidate in (digits, digits[-10:] if len(digits) > 10 else None):
        if candidate:
            filepath = USER_INFO_DIR / f"{candidate}.txt"
            if filepath.is_file():
                return filepath.read_text(encoding="utf-8")
    return ""


def build_instructions(caller_id: str) -> str:
    """Build system prompt enriched with CRM caller info."""
    instructions = SYSTEM_PROMPT
    user_info = load_user_info(caller_id)
    if user_info:
        caller_name = ""
        for line in user_info.splitlines():
            if line.startswith("Name:"):
                caller_name = line.split(":", 1)[1].strip().split()[0]
                break
        if caller_name:
            instructions = instructions.replace("{Name}", caller_name)
        instructions += (
            "\n\nCALLER INFORMATION (from CRM):\n"
            f"{user_info}\n"
            "Use this information to personalise the call. "
            "Greet the caller by their first name. "
            "Do NOT read out sensitive details like card numbers unless the caller asks."
        )
    return instructions


# ──────────────────────────────────────────────────────────────────────────────
# Voice Live session configuration
#
# Docs: https://learn.microsoft.com/azure/ai-services/speech-service/voice-live-api
# ──────────────────────────────────────────────────────────────────────────────

def build_session_config(caller_id: str = "", avatar: bool = False) -> dict:
    """
    Construct the session.update message sent to Voice Live API immediately
    after the WebSocket connection is established.

    This configures everything: system prompt, VAD, TTS voice, transcription,
    noise/echo handling, and response behaviour.
    """
    config = {
        "type": "session.update",
        "session": {
            # ── System prompt ────────────────────────────────────────────
            "instructions": build_instructions(caller_id),

            # ── Audio format (defaults shown, uncomment to override) ─────
            # "input_audio_format": "pcm16",         # pcm16 | g711_ulaw | g711_alaw
            # "input_audio_sampling_rate": 24000,     # 16000 or 24000 Hz
            # "output_audio_format": "pcm16",         # also: pcm16_8000hz, pcm16_16000hz

            # ── Turn detection (VAD) ─────────────────────────────────────
            "turn_detection": {
                "type": "azure_semantic_vad",
                # VAD options: server_vad | semantic_vad| azure_semantic_vad | azure_semantic_vad_multilingual

                "threshold": 0.5,           # speech sensitivity (0–1). Lower = more sensitive
                # To trigger VAD
                "speech_duration_ms": 120,   # min speech before VAD fires (filters coughs/clicks)
                "prefix_padding_ms": 400,   # human audio kept before speech start (prevents word clip)
                # To End VAD
                "silence_duration_ms": 400,  # silence before end-of-turn fires

                "remove_filler_words": True,  # drop "umm", "uh" etc.

                "languages": ["en", "hi"],   # languages for multilingual VAD

                "create_response": True,     # auto-generate response on speech end
                "interrupt_response": True,  # barge-in: user speech interrupts agent
                "auto_truncate": True,       # trim context to what user heard on interruption

                # Has user really stopped speaking, AI decides based on their query so far.
                # Semantic end-of-utterance — reduces premature end-of-turn 
                "end_of_utterance_detection": {
                    "model": "semantic_detection_v1_multilingual",
                    # Models: semantic_detection_v1 (EN only)
                    #         semantic_detection_v1_multilingual
                    "threshold_level": "low",  # low | medium | high | default, lower = wait longer before ending turn
                    "timeout_ms": 400,         # max wait for more speech after initial silence (filters long pauses)
                },
            },

            # ── Input audio transcription (async STT alongside audio) ────
            "input_audio_transcription": {
                "model": "azure-speech",
                # Models: azure-speech | whisper-1 | gpt-4o-transcribe
                #         | gpt-4o-mini-transcribe
                "language": "hi-IN,en-IN,kn-IN,mr-IN",  # BCP-47 locales (comma separated)
                "phrase_list": [
                    "Guptaji", "गुप्ताजी", "Kavya", "Sanjana", "Archana", "काव्या",
                    "credit card", "debit", "EMI", "CVV", "OTP",
                    "statement", "due date", "minimum due", "outstanding",
                    "late payment fee", "annual fee", "finance charge",
                    "dispute", "chargeback", "auto-pay",
                    "card block", "replacement card", "credit limit",
                ],
            },

            # ── Noise / echo handling ────────────────────────────────────
            "input_audio_noise_reduction": {
                "type": "azure_deep_noise_suppression",
            },
            "input_audio_echo_cancellation": {
                "type": "server_echo_cancellation",
            },

            # ── TTS voice ───────────────────────────────────────────────
            "voice": {
                "name": "en-IN-Meera2:DragonHDV2.3Neural",
                # See: https://learn.microsoft.com/azure/ai-services/speech-service/language-support
                "type": "azure-standard",
                # Types: openai | azure-standard | azure-custom | azure-personal
                "temperature": 0.8,   # voice expressiveness (0–1, HD voices only)
                "rate": "1.2",        # speaking speed (0.5–1.5)
            },

            # ── Model behaviour ──────────────────────────────────────────
            "temperature": 0.3,                    # GPT sampling temperature (0.6–1.2)
            "max_response_output_tokens": "750",   # caps verbosity per turn (1–4096 or "inf")
        },
    }

    # ── Avatar (optional) ────────────────────────────────────────────────
    if avatar:
        config["session"]["avatar"] = {
            "type": "video-avatar",
            # Types: video-avatar | photo-avatar
            "character": AVATAR_CHARACTER,
            "style": AVATAR_STYLE,
        }

    return config


# ──────────────────────────────────────────────────────────────────────────────
# Voice Live WebSocket handler
# ──────────────────────────────────────────────────────────────────────────────

class VoiceLiveSession:
    """
    Manages the full lifecycle of a single Voice Live session.

    Connection flow:
        1. Browser connects to /web/ws (raw PCM16 audio over WebSocket)
        2. Server opens a second WebSocket to Voice Live API
        3. Two async loops relay audio bidirectionally:
           - sender_loop:   browser audio  →  Voice Live  (input_audio_buffer.append)
           - receiver_loop: Voice Live     →  browser     (response.audio.delta + events)
    """

    def __init__(self, browser_ws, caller_id: str = "", avatar: bool = False):
        self.browser_ws = browser_ws       # The WebSocket connection TO the user's browser (for sending/receiving audio)
        self.caller_id = caller_id         # Phone number entered by the user (used for CRM lookup)
        self.avatar = avatar               # Whether to enable avatar video stream
        self.vl_ws = None                  # The WebSocket connection TO Azure Voice Live API (set later in start())
        self._send_queue: asyncio.Queue = asyncio.Queue()  # Outbox: audio chunks wait here before being sent to Voice Live
        self._user_speech_end_ts = None    # Timestamp when user stopped talking (used to measure response latency)
        self._first_audio_latency_logged = False  # Flag so we only log latency once per turn (first audio byte)
        self._pending_proactive = False    # Defer greeting until avatar WebRTC connects

    # ── 1. Connect to Voice Live ─────────────────────────────────────────

    async def start(self):
        """Open WebSocket to Voice Live, send session config, spawn loops."""

        # Build WSS URL
        endpoint = VOICE_LIVE_ENDPOINT.rstrip("/")
        model = VOICE_LIVE_MODEL.strip()
        url = (
            f"{endpoint}/voice-live/realtime"
            f"?api-version=2025-10-01&model={model}"
        )
        # Append BYOM profile if configured (Bring Your Own LLM)
        if BYOM_PROFILE:
            url += f"&profile={BYOM_PROFILE}"
            # Cross-resource: model is in a different Foundry resource
            if FOUNDRY_RESOURCE_OVERRIDE:
                url += f"&foundry-resource-override={FOUNDRY_RESOURCE_OVERRIDE}"
            logger.info(
                "BYO LLM mode: profile=%s, model=%s, resource_override=%s",
                BYOM_PROFILE, model, FOUNDRY_RESOURCE_OVERRIDE or "(same resource)",
            )
        url = url.replace("https://", "wss://")

        # Auth headers
        headers = {"x-ms-client-request-id": str(uuid.uuid4())}

        if MANAGED_IDENTITY_CLIENT_ID:
            # Managed identity (Azure Container Apps / VM)
            async with ManagedIdentityCredential(
                client_id=MANAGED_IDENTITY_CLIENT_ID
            ) as cred:
                token = await cred.get_token(
                    "https://cognitiveservices.azure.com/.default"
                )
                headers["Authorization"] = f"Bearer {token.token}"
        elif VOICE_LIVE_API_KEY:
            # API key (local dev)
            headers["api-key"] = VOICE_LIVE_API_KEY
        else:
            # DefaultAzureCredential fallback (az login, env vars, etc.)
            async with DefaultAzureCredential() as cred:
                token = await cred.get_token(
                    "https://cognitiveservices.azure.com/.default"
                )
                headers["Authorization"] = f"Bearer {token.token}"

        # Open WebSocket
        logger.info("Connecting to: %s", url)
        self.vl_ws = await ws_connect(
            url, additional_headers=headers, family=socket.AF_INET
        )
        logger.info("Voice Live connected (caller_id=%s)", self.caller_id)

        # Send session configuration
        await self._send_json(build_session_config(self.caller_id, avatar=self.avatar))

        # Trigger the opening greeting
        # For WebRTC avatar: defer greeting until avatar.connecting event
        if self.avatar:
            self._pending_proactive = True
        else:
            await self._send_json({"type": "response.create"})

        # Start bidirectional relay loops
        asyncio.create_task(self._receiver_loop())
        asyncio.create_task(self._sender_loop())

    # ── 2. Browser → Voice Live (sender) ─────────────────────────────────

    async def handle_browser_message(self, msg):
        """Handle messages from the browser: binary PCM audio or JSON signaling."""
        if isinstance(msg, bytes):
            # Raw PCM16 audio
            await self.handle_browser_audio(msg)
        elif isinstance(msg, str):
            # JSON signaling (WebRTC for avatar)
            try:
                data = json.loads(msg)
                kind = data.get("Kind")
                if kind == "AvatarSDP":
                    # Browser's SDP offer (base64-encoded JSON of RTCSessionDescription)
                    client_sdp = data.get("ClientSdp", "")
                    await self._send_json({
                        "type": "session.avatar.connect",
                        "client_sdp": client_sdp,
                    })
                    logger.info("Forwarded session.avatar.connect to Voice Live (sdp len=%d)", len(client_sdp))
                else:
                    logger.warning("Unknown browser JSON message kind: %s", kind)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from browser")

    async def handle_browser_audio(self, raw_pcm: bytes):
        """Queue raw PCM16 audio from the browser for Voice Live."""
        audio_b64 = base64.b64encode(raw_pcm).decode("ascii")
        await self._send_queue.put(
            json.dumps({
                "type": "input_audio_buffer.append",
                "audio": audio_b64,
            })
        )

    async def _sender_loop(self):
        """Drain queue and forward to Voice Live WebSocket."""
        try:
            while True:
                msg = await self._send_queue.get()
                if self.vl_ws:
                    await self.vl_ws.send(msg)
        except Exception:
            logger.exception("Sender loop error")

    # ── 3. Voice Live → Browser (receiver) ───────────────────────────────

    async def _receiver_loop(self):
        """
        Handle every event from Voice Live and relay audio/transcripts
        back to the browser.

        Key events:
            session.created / session.updated       – lifecycle, logged
            input_audio_buffer.speech_started        – user barge-in → stop playback
            input_audio_buffer.speech_stopped         – mark for latency measurement
            conversation.item.input_audio_transcription.completed – user STT
            response.audio.delta                     – TTS audio chunk (base64 PCM16)
            response.audio_transcript.done           – agent transcript text
            response.done                            – turn complete
            error                                    – logged
        """
        try:
            async for message in self.vl_ws:
                try:
                    event = json.loads(message)
                except (json.JSONDecodeError, ValueError):
                    logger.warning("Non-JSON message from VL (%d bytes)", len(message) if isinstance(message, (bytes, str)) else 0)
                    continue
                event_type = event.get("type")

                match event_type:
                    # ── Lifecycle ────────────────────────────────────────
                    case "session.created":
                        logger.info("Session created: %s", json.dumps(event, indent=2))

                    case "session.updated":
                        logger.info("Session updated: %s", json.dumps(event, indent=2))
                        # Forward avatar config (ICE servers) to browser for WebRTC setup
                        session_data = event.get("session", {})
                        avatar_cfg = session_data.get("avatar")
                        if avatar_cfg:
                            await self._send_to_browser(
                                json.dumps({"Kind": "AvatarConfig", "Avatar": avatar_cfg})
                            )

                    case "input_audio_buffer.cleared":
                        pass

                    # ── User speech events ───────────────────────────────
                    case "input_audio_buffer.speech_started":
                        # Barge-in: tell browser to stop playing TTS
                        await self._send_to_browser(
                            json.dumps({"Kind": "StopAudio"})
                        )

                    case "input_audio_buffer.speech_stopped":
                        self._user_speech_end_ts = time.monotonic()
                        self._first_audio_latency_logged = False

                    # ── User transcription ───────────────────────────────
                    case "conversation.item.input_audio_transcription.completed":
                        transcript = event.get("transcript", "")
                        stt_lang = event.get("language", "")
                        logger.info("USER [lang=%s]: %s", stt_lang, transcript)

                    case "conversation.item.input_audio_transcription.failed":
                        logger.error("Transcription failed: %s", event.get("error"))

                    # ── Agent response audio ─────────────────────────────
                    case "response.audio.delta":
                        # Measure time-to-first-audio-byte
                        if (
                            self._user_speech_end_ts
                            and not self._first_audio_latency_logged
                        ):
                            latency_ms = int(
                                (time.monotonic() - self._user_speech_end_ts) * 1000
                            )
                            logger.info(
                                "Time to first audio byte: [latency=%dms]", latency_ms
                            )
                            self._first_audio_latency_logged = True

                        # Decode and send raw PCM16 bytes to browser
                        delta = event.get("delta", "")
                        audio_bytes = base64.b64decode(delta)
                        await self._send_to_browser(audio_bytes)

                    # ── Agent transcript ─────────────────────────────────
                    case "response.audio_transcript.done":
                        transcript = event.get("transcript", "")
                        logger.info("AGENT: %s", transcript)
                        await self._send_to_browser(
                            json.dumps({"Kind": "Transcription", "Text": transcript})
                        )

                    # ── Avatar / WebRTC signaling ───────────────────────
                    case "session.avatar.connecting":
                        # Voice Live sends server SDP answer (base64-encoded)
                        server_sdp = event.get("server_sdp", "")
                        logger.info("Avatar SDP answer from VL (len=%d)", len(server_sdp))
                        await self._send_to_browser(
                            json.dumps({"Kind": "AvatarSDPAnswer", "ServerSdp": server_sdp})
                        )
                        # Avatar connected — send deferred proactive greeting
                        if self._pending_proactive:
                            self._pending_proactive = False
                            logger.info("Sending deferred response.create after avatar connect")
                            await self._send_json({"type": "response.create"})

                    # ── Intermediate events (ignored) ────────────────────
                    case (
                        "response.created"
                        | "response.output_item.added"
                        | "response.audio_transcript.delta"
                    ):
                        pass

                    # ── Response complete ─────────────────────────────────
                    case "response.done":
                        resp = event.get("response", {})
                        if resp.get("status") != "completed":
                            logger.error(
                                "Response error: %s",
                                json.dumps(resp.get("status_details", {})),
                            )

                    # ── Errors ────────────────────────────────────────────
                    case "error":
                        logger.error("Voice Live error: %s", json.dumps(event))

                    case _:
                        logger.info("Unhandled event: %s", event_type)

        except Exception:
            logger.exception("Receiver loop error")

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _send_json(self, obj: dict):
        if self.vl_ws:
            await self.vl_ws.send(json.dumps(obj))

    async def _send_to_browser(self, data):
        try:
            await self.browser_ws.send(data)
        except Exception:
            logger.exception("Failed to send to browser")

    async def close(self):
        if self.vl_ws:
            try:
                await self.vl_ws.close()
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
# Quart app  (reuses existing static/ frontend)
# ──────────────────────────────────────────────────────────────────────────────

app = Quart(__name__, static_folder="static")


@app.route("/")
async def index():
    return await app.send_static_file("index.html")


@app.websocket("/web/ws")
async def web_ws():
    """
    Browser WebSocket endpoint.

    Protocol (matches existing frontend):
        Browser → Server:  raw PCM16 bytes (ArrayBuffer)
        Server → Browser:  raw PCM16 bytes (TTS audio)
                           OR JSON string: {"Kind": "StopAudio"}
                           OR JSON string: {"Kind": "Transcription", "Text": "..."}
    """
    caller_id = websocket.args.get("callerId", "")
    avatar = websocket.args.get("avatar", "0") == "1"
    logger.info("Browser connected (callerId=%s, avatar=%s)", caller_id, avatar)

    session = VoiceLiveSession(websocket, caller_id=caller_id, avatar=avatar)
    asyncio.create_task(session.start())

    try:
        while True:
            msg = await websocket.receive()
            await session.handle_browser_message(msg)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Web WebSocket error")
    finally:
        await session.close()


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8000)