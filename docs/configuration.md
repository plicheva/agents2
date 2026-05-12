# Configuration Reference

All configuration is done through environment variables in the `.env` file and constants in `backend.py`.

## Environment Variables (`.env`)

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `AZURE_VOICE_LIVE_ENDPOINT` | **Yes** | Full HTTPS endpoint of your Azure AI Foundry / Speech resource | `https://myresource.services.ai.azure.com` |
| `AZURE_VOICE_LIVE_API_KEY` | For local dev | API key for the resource above. Leave blank when using Managed Identity | `abc123...` |
| `VOICE_LIVE_MODEL` | No (default: `gpt-4.1`) | Model deployment name used by Voice Live | `gpt-4.1-mini-southIndia` |
| `BYOM_PROFILE` | No | Bring Your Own Model profile (see below) | `byom-azure-openai-chat-completion` |
| `FOUNDRY_RESOURCE_OVERRIDE` | No | Cross-resource override when the model lives in a different Foundry resource | `SouthIndiaFoundry` |
| `AVATAR_CHARACTER` | No (default: `lisa`) | Avatar character name | `lisa` |
| `AVATAR_STYLE` | No (default: `casual-sitting`) | Avatar style variant | `casual-sitting` |
| `AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID` | No | Client ID of user-assigned managed identity (Azure-hosted only) | `00000000-0000-...` |

## Authentication Priority

The backend tries authentication methods in this order:

1. **Managed Identity** — if `AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID` is set
2. **API Key** — if `AZURE_VOICE_LIVE_API_KEY` is set
3. **DefaultAzureCredential** — fallback (uses `az login`, environment variables, etc.)

Token scope: `https://cognitiveservices.azure.com/.default`

## BYOM Profiles

| Profile | Use Case |
|---------|----------|
| `byom-azure-openai-chat-completion` | Your own Chat Completion deployment (GPT-4.1, GPT-4o, etc.) |
| `byom-azure-openai-realtime` | Your own Realtime model deployment |
| `byom-foundry-anthropic-messages` | Anthropic Claude via Azure Foundry (preview) |
| _(empty / unset)_ | Voice Live managed model (default) |

When `BYOM_PROFILE` is set, the model name from `VOICE_LIVE_MODEL` is appended to the Voice Live WebSocket URL. If the model deployment lives in a **different** Foundry resource, set `FOUNDRY_RESOURCE_OVERRIDE` to the resource name (without the `.services.ai.azure.com` suffix).

## VAD (Voice Activity Detection)

Configured in `build_session_config()` → `turn_detection`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `type` | `azure_semantic_vad` | VAD engine. Options: `server_vad`, `semantic_vad`, `azure_semantic_vad`, `azure_semantic_vad_multilingual` |
| `threshold` | `0.5` | Speech sensitivity (0–1). Lower = more sensitive |
| `speech_duration_ms` | `120` | Minimum speech duration before VAD fires (filters coughs/clicks) |
| `prefix_padding_ms` | `400` | Audio kept before speech start (prevents clipping first word) |
| `silence_duration_ms` | `400` | Silence duration before end-of-turn fires |
| `remove_filler_words` | `true` | Drop "umm", "uh", etc. |
| `languages` | `["en", "hi"]` | Languages for multilingual VAD |
| `create_response` | `true` | Auto-generate response when speech ends |
| `interrupt_response` | `true` | Barge-in: user speech interrupts agent playback |
| `auto_truncate` | `true` | Trim context to what user actually heard on interruption |

### End-of-Utterance Detection

Nested under `turn_detection.end_of_utterance_detection`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `model` | `semantic_detection_v1_multilingual` | Also available: `semantic_detection_v1` (English only) |
| `threshold_level` | `low` | Options: `low`, `medium`, `high`, `default`. Lower = wait longer before ending turn |
| `timeout_ms` | `400` | Max wait for more speech after initial silence |

## TTS Voice

Configured in `build_session_config()` → `voice`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `name` | `en-IN-Meera2:DragonHDV2.3Neural` | Voice name ([full list](https://learn.microsoft.com/azure/ai-services/speech-service/language-support)) |
| `type` | `azure-standard` | Options: `openai`, `azure-standard`, `azure-custom`, `azure-personal` |
| `temperature` | `0.8` | Voice expressiveness (0–1, HD voices only) |
| `rate` | `1.2` | Speaking speed (0.5–1.5) |

## Input Audio Transcription

Configured in `build_session_config()` → `input_audio_transcription`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `model` | `azure-speech` | Options: `azure-speech`, `whisper-1`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe` |
| `language` | `hi-IN,en-IN,kn-IN,mr-IN` | BCP-47 locales (comma-separated) |
| `phrase_list` | _(see code)_ | Custom phrases for better recognition (names, bank terms) |

## Noise & Echo

| Setting | Value | Description |
|---------|-------|-------------|
| `input_audio_noise_reduction.type` | `azure_deep_noise_suppression` | Deep noise suppression for noisy environments |
| `input_audio_echo_cancellation.type` | `server_echo_cancellation` | Server-side echo cancellation (avoids feedback loops) |

## Model Behaviour

| Parameter | Default | Description |
|-----------|---------|-------------|
| `temperature` | `0.3` | GPT sampling temperature (lower = more deterministic) |
| `max_response_output_tokens` | `750` | Maximum tokens per agent response (1–4096 or `"inf"`) |

## Audio Format (Defaults)

The current config uses Voice Live defaults. Available options (uncomment in code to override):

| Parameter | Default | Options |
|-----------|---------|---------|
| `input_audio_format` | `pcm16` | `pcm16`, `g711_ulaw`, `g711_alaw` |
| `input_audio_sampling_rate` | `24000` | `16000`, `24000` |
| `output_audio_format` | `pcm16` | `pcm16`, `pcm16_8000hz`, `pcm16_16000hz` |
