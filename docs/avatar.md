# Avatar Integration (WebRTC)

## Overview

Voice Live supports a **talking avatar** that renders a lifelike video character speaking the agent's TTS output. The avatar uses **WebRTC** for video/audio delivery, with signaling proxied through the existing Voice Live WebSocket connection.

This project implements the full signaling flow without any SDK — just `RTCPeerConnection` in the browser and JSON message forwarding in the backend.

## Supported Avatar Regions

Avatar is only available in these Azure regions:

- Southeast Asia
- North Europe
- West Europe
- Sweden Central
- South Central US
- East US 2
- West US 2

Your Voice Live endpoint must be in one of these regions for avatar to work.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AVATAR_CHARACTER` | `lisa` | Character name |
| `AVATAR_STYLE` | `casual-sitting` | Style variant |

### Session Config (sent in `session.update`)

```json
{
  "avatar": {
    "type": "video-avatar",
    "character": "lisa",
    "style": "casual-sitting"
  }
}
```

Avatar types:
- `video-avatar` — pre-rendered 2D HD avatar video
- `photo-avatar` — animated from a single photo (preview)

## Signaling Protocol

The WebRTC connection is established through Voice Live's WebSocket, not through a separate signaling server. Here is the full sequence:

### Step-by-Step Flow

```
1. Browser connects to /web/ws?callerId=XXX&avatar=1
2. Backend includes avatar config in session.update
3. Voice Live responds with session.updated containing ICE servers
4. Backend forwards ICE servers to browser as {"Kind": "AvatarConfig", ...}
5. Browser creates RTCPeerConnection with ICE servers
6. Browser creates SDP offer, waits for ICE gathering to complete
7. Browser base64-encodes the SDP and sends {"Kind": "AvatarSDP", "ClientSdp": "..."}
8. Backend forwards as {"type": "session.avatar.connect", "client_sdp": "..."}
9. Voice Live responds with {"type": "session.avatar.connecting", "server_sdp": "..."}
10. Backend forwards to browser as {"Kind": "AvatarSDPAnswer", "ServerSdp": "..."}
11. Browser decodes base64 SDP answer, sets remote description
12. WebRTC media flows — avatar video renders
13. Backend sends deferred response.create (greeting)
```

### Critical Implementation Details

These details were discovered through debugging and confirmed against the official samples:

1. **SDP Format**: The SDP offer/answer is a **base64-encoded JSON string** of the `RTCSessionDescription` object:
   ```javascript
   const sdp = btoa(JSON.stringify(pc.localDescription));
   ```

2. **Transceiver Direction**: Must be `sendrecv`, NOT `recvonly`:
   ```javascript
   pc.addTransceiver('video', { direction: 'sendrecv' });
   pc.addTransceiver('audio', { direction: 'sendrecv' });
   ```

3. **Data Channel**: A data channel named `eventChannel` must be created before the SDP offer:
   ```javascript
   pc.createDataChannel('eventChannel');
   ```

4. **ICE Gathering**: Wait for ICE gathering to complete before sending the SDP. The browser needs to collect all ICE candidates and embed them in the offer:
   ```javascript
   await new Promise((resolve) => {
     if (pc.iceGatheringState === 'complete') return resolve();
     pc.addEventListener('icegatheringstatechange', () => {
       if (pc.iceGatheringState === 'complete') resolve();
     });
     setTimeout(resolve, 10000); // 10s timeout
   });
   ```

5. **Deferred Greeting**: The `response.create` (proactive greeting) must NOT be sent until `session.avatar.connecting` is received. Sending it earlier causes the greeting audio to play before the avatar is ready.

6. **TURN Servers**: Voice Live provides TURN server credentials in the `session.updated` event. These are ACS (Azure Communication Services) relay servers:
   ```
   turn:relay.communication.microsoft.com:3478
   ```
   The ICE servers array includes both TURN (TCP/UDP) and STUN entries with time-limited credentials.

### Event Reference

| Event | Direction | Purpose |
|-------|-----------|---------|
| `session.update` (with `avatar` field) | Client → VL | Request avatar with character/style |
| `session.updated` (with `avatar.ice_servers`) | VL → Client | ICE servers for WebRTC |
| `session.avatar.connect` | Client → VL | Send base64 SDP offer |
| `session.avatar.connecting` | VL → Client | Base64 SDP answer |

## Frontend Implementation

### RTCPeerConnection Setup

The browser sets up the WebRTC connection in `setupAvatarWebRTC()`:

1. Create `RTCPeerConnection` with ICE servers from `AvatarConfig`
2. Add `sendrecv` transceivers for video and audio
3. Create `eventChannel` data channel
4. Set `ontrack` handler to render video/audio elements
5. Create and send SDP offer (after ICE gathering completes)

### Track Handling

When WebRTC media tracks arrive, the `ontrack` handler creates `<video>` or `<audio>` elements dynamically:

- Video tracks → `<video>` element in the avatar container (autoplay, muted, playsinline)
- Audio tracks → `<audio>` element (autoplay, hidden)

### TTS Audio with Avatar

When avatar is active, TTS audio arrives through the **WebRTC audio track** instead of the WebSocket binary frames. The `AudioWorklet` playback path is still available as a fallback if WebRTC audio isn't received.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| No `session.avatar.connecting` event | Wrong SDP format or transceiver direction | Ensure base64 encoding and `sendrecv` direction |
| Dark/black video box | `ontrack` not rendering video | Check that video element is created with `autoplay` and `playsinline` |
| Avatar renders but no audio | Audio track not handled | Ensure `<audio>` element is created for audio tracks |
| Greeting plays before avatar appears | `response.create` sent too early | Defer greeting until `session.avatar.connecting` event |
| ICE connection fails | Missing TURN servers | Verify ICE servers from `session.updated` are passed to `RTCPeerConnection` |
| "Avatar not supported in this region" | Endpoint in wrong Azure region | Use a supported region (see list above) |
