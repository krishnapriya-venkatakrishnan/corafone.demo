/**
 * Corafone Voice UI -- vanilla JS + Web Audio API.
 * Captures mic audio, streams it to the backend WebSocket, plays back the
 * agent's synthesized speech gaplessly, and handles barge-in (the backend
 * tells us the instant Deepgram confirms the customer is talking over Cora).
 */

// --- Config ---
const WS_URL = "ws://127.0.0.1:8000/ws/stream"; // FastAPI backend's /ws/stream
const TARGET_SAMPLE_RATE = 24000;      // Voice Agent session rate, in and out
const UPLOAD_INTERVAL_MS = 100;        // how often we flush mic audio upstream
const PLAYBACK_SAMPLE_RATE = 24000;    // backend sends PCM16 @ 24kHz mono
const PLAYBACK_STARTUP_BUFFER_SECONDS = 0.1; // cushion for the first scheduled buffer

// --- DOM references ---
const micButton = document.getElementById("micButton");
const micIcon = document.getElementById("micIcon");
const statusText = document.getElementById("statusText");
const statusDot = document.getElementById("statusDot");
const timerEl = document.getElementById("timer");
const ring1 = document.getElementById("ring1");
const ring2 = document.getElementById("ring2");
const helperText = document.getElementById("helperText");
const errorBanner = document.getElementById("errorBanner");

// --- App state ---
let ws = null;
let micStream = null;
let audioContext = null;        // capture-side context (native sample rate)
let sourceNode = null;
let downsampleNode = null;      // AudioWorkletNode or ScriptProcessorNode fallback
let usingWorklet = false;

let playbackContext = null;     // playback-side context, fixed at PLAYBACK_SAMPLE_RATE
let playbackCursor = 0;         // AudioContext.currentTime cursor for gapless scheduling
const activePlaybackSources = []; // scheduled sources, so we can stop them on demand
let masterGainNode = null;      // shared gain node all sources route through

let uploadBuffer = [];          // queued Int16Array chunks waiting to be flushed upstream
let uploadTimerId = null;

let callState = "disconnected"; // "disconnected" | "connecting" | "listening" | "speaking"
let timerIntervalId = null;
let callStartedAt = null;

// --- UI helpers ---
function setStatus(state) {
  callState = state;

  const config = {
    disconnected: { text: "Disconnected",    dot: "bg-neutral-600", pill: "text-neutral-400" },
    connecting:   { text: "Connecting…",      dot: "bg-amber-400",   pill: "text-amber-300" },
    listening:    { text: "Listening…",       dot: "bg-emerald-400", pill: "text-emerald-300" },
    speaking:     { text: "Cora Speaking…",   dot: "bg-sky-400",     pill: "text-sky-300" },
  }[state];

  statusText.textContent = config.text;
  statusDot.className = `w-2 h-2 rounded-full transition-colors duration-300 ${config.dot}`;
  statusText.parentElement.className =
    `flex items-center gap-2 px-4 py-1.5 rounded-full bg-neutral-900 border border-neutral-800 text-sm transition-colors duration-300 ${config.pill}`;

  const isLive = state !== "disconnected";
  ring1.classList.toggle("hidden", !isLive);
  ring2.classList.toggle("hidden", !isLive);
  micButton.classList.toggle("breathe", state === "listening");

  if (state === "disconnected") {
    micIcon.innerHTML = MIC_ICON_PATH;
    micButton.classList.remove("bg-red-950", "border-red-800", "text-red-300");
    micButton.classList.add("bg-neutral-800", "border-neutral-700", "text-neutral-300");
    helperText.textContent = "Tap the microphone to start a live call with Cora.";
  } else {
    micIcon.innerHTML = HANGUP_ICON_PATH;
    micButton.classList.remove("bg-neutral-800", "border-neutral-700", "text-neutral-300");
    micButton.classList.add("bg-red-950", "border-red-800", "text-red-300");
    helperText.textContent = state === "connecting"
      ? "Establishing connection…"
      : "Tap again to end the call.";
  }
}

const MIC_ICON_PATH = `
  <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
  <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
  <line x1="12" y1="19" x2="12" y2="23"></line>
  <line x1="8" y1="23" x2="16" y2="23"></line>
`;
const HANGUP_ICON_PATH = `
  <path d="M22 8.5s-1.5-1-3-1.5"></path>
  <path d="M2 8.5s5-4 10-4 10 4 10 4"></path>
  <path d="M6 12.5c1.5-1 4-2 6-2s4.5 1 6 2"></path>
  <line x1="4" y1="4" x2="20" y2="20"></line>
`;

function showError(message) {
  errorBanner.textContent = message;
  errorBanner.classList.remove("hidden");
}
function clearError() {
  errorBanner.classList.add("hidden");
  errorBanner.textContent = "";
}

function startTimer() {
  callStartedAt = Date.now();
  timerEl.textContent = "00:00";
  timerIntervalId = setInterval(() => {
    const elapsedSec = Math.floor((Date.now() - callStartedAt) / 1000);
    const mm = String(Math.floor(elapsedSec / 60)).padStart(2, "0");
    const ss = String(elapsedSec % 60).padStart(2, "0");
    timerEl.textContent = `${mm}:${ss}`;
  }, 250);
}
function stopTimer() {
  clearInterval(timerIntervalId);
  timerIntervalId = null;
  timerEl.textContent = "00:00";
}

// --- AudioWorklet processor ---
// Runs on the audio render thread; forwards raw Float32 frames to the main
// thread, which does the resampling/Int16 conversion (keeps the realtime
// thread free of allocations). Loaded via a Blob URL so no separate file
// is needed on the audio-render side.
const WORKLET_SOURCE = `
class PCMForwarderProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0] && input[0].length > 0) {
      this.port.postMessage(input[0].slice());
    }
    return true;
  }
}
registerProcessor("pcm-forwarder", PCMForwarderProcessor);
`;

// --- Downsampling: Float32 at inputRate -> Int16 at TARGET_SAMPLE_RATE ---
// Linear interpolation; sufficient quality for narrowband voice telephony.
function downsampleFloat32ToTargetInt16(float32Input, inputSampleRate) {
  if (inputSampleRate === TARGET_SAMPLE_RATE) {
    return floatTo16BitPCM(float32Input);
  }

  const ratio = inputSampleRate / TARGET_SAMPLE_RATE;
  const outputLength = Math.floor(float32Input.length / ratio);
  const output = new Float32Array(outputLength);

  for (let i = 0; i < outputLength; i++) {
    const srcIndex = i * ratio;
    const srcIndexFloor = Math.floor(srcIndex);
    const srcIndexCeil = Math.min(srcIndexFloor + 1, float32Input.length - 1);
    const frac = srcIndex - srcIndexFloor;
    output[i] = float32Input[srcIndexFloor] * (1 - frac) + float32Input[srcIndexCeil] * frac;
  }

  return floatTo16BitPCM(output);
}

function floatTo16BitPCM(float32Array) {
  const int16Array = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const clamped = Math.max(-1, Math.min(1, float32Array[i]));
    int16Array[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
  }
  return int16Array;
}

// --- Mic capture pipeline ---
async function startMicCapture() {
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  // Must be created after a user gesture. Try the target rate first (some
  // browsers reject a sampleRate hint); downsample adapts either way.
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  try {
    audioContext = new AudioContextCtor({ sampleRate: TARGET_SAMPLE_RATE });
  } catch (err) {
    console.warn(`${TARGET_SAMPLE_RATE}Hz AudioContext hint rejected, falling back to device default:`, err);
    audioContext = new AudioContextCtor();
  }
  const nativeSampleRate = audioContext.sampleRate; // typically 44100 or 48000

  sourceNode = audioContext.createMediaStreamSource(micStream);

  try {
    // Preferred: AudioWorklet (off main thread, no deprecation warnings).
    const blob = new Blob([WORKLET_SOURCE], { type: "application/javascript" });
    const workletUrl = URL.createObjectURL(blob);
    await audioContext.audioWorklet.addModule(workletUrl);
    URL.revokeObjectURL(workletUrl);

    downsampleNode = new AudioWorkletNode(audioContext, "pcm-forwarder");
    downsampleNode.port.onmessage = (event) => {
      const float32Chunk = event.data;
      const int16Chunk = downsampleFloat32ToTargetInt16(float32Chunk, nativeSampleRate);
      queueForUpload(int16Chunk);
    };
    usingWorklet = true;
  } catch (err) {
    // Fallback for browsers without AudioWorklet support.
    console.warn("AudioWorklet unavailable, falling back to ScriptProcessorNode:", err);
    const bufferSize = 2048;
    downsampleNode = audioContext.createScriptProcessor(bufferSize, 1, 1);
    downsampleNode.onaudioprocess = (event) => {
      const float32Chunk = event.inputBuffer.getChannelData(0);
      const int16Chunk = downsampleFloat32ToTargetInt16(float32Chunk, nativeSampleRate);
      queueForUpload(int16Chunk);
    };
    usingWorklet = false;
  }

  // mic -> downsample node -> muted destination (must connect to destination
  // or some browsers stop pulling the node; gain-zero avoids echoing the mic).
  const silentGain = audioContext.createGain();
  silentGain.gain.value = 0;
  sourceNode.connect(downsampleNode);
  downsampleNode.connect(silentGain);
  silentGain.connect(audioContext.destination);

  // Batch uploads on a fixed interval instead of per-callback.
  uploadTimerId = setInterval(flushUploadBuffer, UPLOAD_INTERVAL_MS);

  console.log(
    `Streaming audio at ${TARGET_SAMPLE_RATE}Hz Int16 (mic native rate: ${nativeSampleRate}Hz, worklet: ${usingWorklet})`
  );
}

function queueForUpload(int16Chunk) {
  uploadBuffer.push(int16Chunk);
}

function flushUploadBuffer() {
  if (uploadBuffer.length === 0) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;

  const totalLength = uploadBuffer.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Int16Array(totalLength);
  let offset = 0;
  for (const chunk of uploadBuffer) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }
  uploadBuffer = [];

  ws.send(merged.buffer);
}

function stopMicCapture() {
  clearInterval(uploadTimerId);
  uploadTimerId = null;
  uploadBuffer = [];

  if (downsampleNode) {
    downsampleNode.disconnect();
    if (!usingWorklet) downsampleNode.onaudioprocess = null;
    else downsampleNode.port.onmessage = null;
    downsampleNode = null;
  }
  if (sourceNode) {
    sourceNode.disconnect();
    sourceNode = null;
  }
  if (micStream) {
    micStream.getTracks().forEach((track) => track.stop());
    micStream = null;
  }
  if (audioContext) {
    audioContext.close().catch(() => {});
    audioContext = null;
  }
}

// --- Playback pipeline ---
// Incoming PCM16 chunks are scheduled back-to-back on an ever-advancing
// cursor for gapless playback (avoids clicks from scheduling at `currentTime`
// with jitter).
function ensurePlaybackContext() {
  if (playbackContext) return;
  playbackContext = new (window.AudioContext || window.webkitAudioContext)({
    sampleRate: PLAYBACK_SAMPLE_RATE,
  });

  // A fresh AudioContext's first scheduled buffer can get its opening
  // frames clipped (hardware pipeline isn't running yet at t=0), so start
  // slightly ahead instead of at `currentTime`. Call this early (see
  // startCall()) so the context is already warm before real audio arrives.
  playbackCursor = playbackContext.currentTime + PLAYBACK_STARTUP_BUFFER_SECONDS;

  masterGainNode = playbackContext.createGain();
  masterGainNode.gain.value = 1.0;
  masterGainNode.connect(playbackContext.destination);
}

function playIncomingPCMChunk(arrayBuffer) {
  ensurePlaybackContext();

  const int16Data = new Int16Array(arrayBuffer);
  if (int16Data.length === 0) return;

  const float32Data = new Float32Array(int16Data.length);
  for (let i = 0; i < int16Data.length; i++) {
    float32Data[i] = int16Data[i] / (int16Data[i] < 0 ? 0x8000 : 0x7fff);
  }

  const audioBuffer = playbackContext.createBuffer(1, float32Data.length, playbackContext.sampleRate);
  audioBuffer.getChannelData(0).set(float32Data);

  const source = playbackContext.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(masterGainNode);

  // If we've fallen behind real time, snap forward instead of catching up.
  const now = playbackContext.currentTime;
  if (playbackCursor < now) {
    playbackCursor = now;
  }

  source.start(playbackCursor);
  playbackCursor += audioBuffer.duration;

  activePlaybackSources.push(source);
  source.onended = () => {
    const idx = activePlaybackSources.indexOf(source);
    if (idx !== -1) activePlaybackSources.splice(idx, 1);
    if (activePlaybackSources.length === 0 && callState === "speaking") {
      setStatus("listening");
    }
  };

  if (callState === "listening") {
    setStatus("speaking");
  }
}

// Barge-in: backend confirms (via Deepgram) the customer is talking over
// Cora -- stop all scheduled audio immediately.
function clearAudioBuffer() {
  console.log("[Interruption Signal] Flushing hardware audio buffer nodes instantly!");

  activePlaybackSources.forEach((source) => {
    try {
      source.onended = null;
      source.stop();
    } catch (_) {}
  });
  activePlaybackSources.length = 0;

  if (playbackContext) {
    playbackCursor = playbackContext.currentTime;
  }

  if (callState === "speaking") {
    setStatus("listening");
  }
}

function stopPlayback() {
  activePlaybackSources.forEach((source) => {
    try { source.onended = null; source.stop(); } catch (_) {}
  });
  activePlaybackSources.length = 0;

  if (playbackContext) {
    playbackContext.close().catch(() => {});
    playbackContext = null;
  }
  masterGainNode = null;
  playbackCursor = 0;
}

// --- WebSocket lifecycle ---
function connectWebSocket() {
  return new Promise((resolve, reject) => {
    ws = new WebSocket(WS_URL);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => resolve();

    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        playIncomingPCMChunk(event.data);
        return;
      }

      // JSON control packets -- currently just clear_audio_buffer (barge-in).
      try {
        const packet = JSON.parse(event.data);
        if (packet.type === "clear_audio_buffer") {
          clearAudioBuffer();
        }
      } catch (err) {
        console.warn("Received non-JSON text frame from server:", event.data);
      }
    };

    ws.onerror = (event) => {
      console.error("WebSocket error:", event);
      reject(new Error("WebSocket connection failed"));
    };

    ws.onclose = () => {
      if (callState !== "disconnected") {
        endCall();
      }
    };
  });
}

// --- Connect / disconnect orchestration ---
async function startCall() {
  clearError();
  micButton.disabled = true;
  setStatus("connecting");

  try {
    // Warm up the playback context now (still inside the click gesture, so
    // autoplay policy allows it), before the greeting could possibly arrive.
    ensurePlaybackContext();
    await connectWebSocket();
    await startMicCapture();
    setStatus("listening");
    startTimer();
  } catch (err) {
    console.error(err);
    showError(
      err.name === "NotAllowedError"
        ? "Microphone access was denied. Please allow mic permissions and try again."
        : "Couldn't connect to Cora. Is the backend running?"
    );
    endCall();
  } finally {
    micButton.disabled = false;
  }
}

function endCall() {
  setStatus("disconnected");
  stopTimer();
  stopMicCapture();
  stopPlayback();

  if (ws) {
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
    ws.onopen = ws.onmessage = ws.onerror = ws.onclose = null;
    ws = null;
  }
}

micButton.addEventListener("click", () => {
  if (callState === "disconnected") {
    startCall();
  } else {
    endCall();
  }
});

window.addEventListener("beforeunload", () => {
  if (callState !== "disconnected") endCall();
});

setStatus("disconnected");
