// Narrator audio capture — hands-free VAD over a WebSocket.
// Assets are vendored under /static/vad/ (no CDN). onnxruntime-web runs
// single-threaded so it needs no cross-origin isolation. Each detected utterance
// is sent as 16 kHz WAV; the server transcribes and pushes back the board.
// If VAD can't initialise, falls back to hold-to-talk (MediaRecorder).
(function () {
  const btn = document.getElementById("listen-btn");
  if (!btn) return;
  const statusEl = document.getElementById("listen-status");
  const transcriptEl = document.getElementById("transcript");
  const localChk = document.getElementById("local-asr");
  const caseId = btn.dataset.caseId;
  const ASSETS = "/static/vad/";

  const localMode = () => !!(localChk && localChk.checked && window.NarratorLocalASR && window.NarratorLocalASR.available);

  // Preload the on-device model when the user opts in (first utterance isn't slow).
  if (localChk) localChk.addEventListener("change", () => {
    if (localChk.checked && window.NarratorLocalASR) {
      setStatus("loading on-device model…");
      window.NarratorLocalASR.warmup().then((ok) => setStatus(ok ? (listening ? "● listening" : "ready (on-device)") : "on-device unavailable — using server"));
    }
  });

  // POST a transcript to the existing utterance endpoint and swap the board.
  async function postText(text) {
    const r = await fetch(`/case/${caseId}/utterance`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ text }),
    });
    const html = await r.text();
    const old = document.getElementById("board");
    if (old) {
      old.outerHTML = html;
      if (window.htmx) htmx.process(document.getElementById("board"));
      const s = document.getElementById("chart-scroll");
      if (s) s.scrollLeft = Math.max(0, (parseFloat(s.dataset.liveX || "0")) - s.clientWidth + 80);
    }
    if (transcriptEl) transcriptEl.textContent = text ? "“" + text + "”" : "";
  }

  // Route a finished utterance: on-device transcription if opted in, else server.
  async function handleUtterance(audio) {
    if (localMode()) {
      setStatus("transcribing on-device…");
      try {
        const text = await window.NarratorLocalASR.transcribe(audio);
        if (text) await postText(text);
        setStatus("● listening");
        return;
      } catch (e) {
        console.error("on-device ASR failed, falling back to server:", e);
      }
    }
    sendFloat32(audio);
    setStatus("transcribing…");
  }

  let ws = null, myvad = null, listening = false;
  let stream = null, rec = null, chunks = [], pttMode = false, recording = false;
  const setStatus = (s) => { if (statusEl) statusEl.textContent = s; };

  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${location.host}/ws/case/${caseId}`;
  }
  function ensureWs() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    ws = new WebSocket(wsUrl());
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (transcriptEl) transcriptEl.textContent = msg.transcript ? "“" + msg.transcript + "”" : "";
      if (msg.board) {
        const old = document.getElementById("board");
        if (old) {
          old.outerHTML = msg.board;
          if (window.htmx) htmx.process(document.getElementById("board"));
          const s = document.getElementById("chart-scroll");
          if (s) s.scrollLeft = Math.max(0, (parseFloat(s.dataset.liveX || "0")) - s.clientWidth + 80);
        }
      }
      if (listening) setStatus(pttMode ? "hold to talk" : "● listening");
    };
    ws.onclose = () => { ws = null; };
  }

  function sendFloat32(audio) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(float32ToWav(audio));
  }

  // 16 kHz mono Float32 → 16-bit PCM WAV
  function float32ToWav(float32) {
    const rate = 16000, n = float32.length;
    const dv = new DataView(new ArrayBuffer(44 + n * 2));
    const str = (o, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)); };
    str(0, "RIFF"); dv.setUint32(4, 36 + n * 2, true); str(8, "WAVE");
    str(12, "fmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true);
    dv.setUint16(22, 1, true); dv.setUint32(24, rate, true);
    dv.setUint32(28, rate * 2, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
    str(36, "data"); dv.setUint32(40, n * 2, true);
    let o = 44;
    for (let i = 0; i < n; i++) {
      const s = Math.max(-1, Math.min(1, float32[i]));
      dv.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true); o += 2;
    }
    return dv.buffer;
  }

  // --- Hands-free VAD --------------------------------------------------------
  async function startVad() {
    setStatus("loading…");
    if (!window.vad || !window.ort) throw new Error("vad assets missing");
    ort.env.wasm.wasmPaths = ASSETS;   // load wasm/mjs locally
    ort.env.wasm.numThreads = 1;       // single-threaded → no cross-origin isolation needed
    ensureWs();
    myvad = await vad.MicVAD.new({
      baseAssetPath: ASSETS,
      onnxWASMBasePath: ASSETS,
      // Browser DSP on the mic: cleaner input → better VAD + ASR (theatre noise).
      additionalAudioConstraints: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      },
      onSpeechStart: () => setStatus("listening — speech"),
      onSpeechEnd: (audio) => { handleUtterance(audio); },
    });
    myvad.start();
    listening = true;
    btn.textContent = "■ Stop";
    btn.classList.add("rec");
    setStatus("● listening");
  }
  function stopVad() {
    if (myvad) myvad.pause();
    if (ws) ws.close();
    listening = false;
    btn.textContent = "● Listen";
    btn.classList.remove("rec");
    setStatus("off");
  }

  // --- Push-to-talk fallback (only if VAD init fails) ------------------------
  async function pttStart() {
    if (recording) return;
    if (!stream) stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    ensureWs();
    chunks = [];
    rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.onstop = () => {
      const blob = new Blob(chunks, { type: (chunks[0] && chunks[0].type) || "audio/webm" });
      if (blob.size && ws && ws.readyState === WebSocket.OPEN) {
        blob.arrayBuffer().then((b) => ws.send(b));
        setStatus("transcribing…");
      }
    };
    rec.start(); recording = true; btn.classList.add("rec"); setStatus("● recording — release");
  }
  function pttStop() {
    if (!recording) return;
    recording = false; btn.classList.remove("rec");
    if (rec && rec.state !== "inactive") rec.stop();
  }
  function enablePttFallback(reason) {
    pttMode = true;
    console.warn("VAD unavailable, using push-to-talk:", reason);
    btn.textContent = "🎙 Hold to talk";
    setStatus("hold to talk (VAD unavailable)");
    btn.addEventListener("pointerdown", (e) => { e.preventDefault(); pttStart().catch(err => setStatus("mic error")); });
    btn.addEventListener("pointerup", pttStop);
    btn.addEventListener("pointerleave", pttStop);
    const typing = (t) => t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName);
    document.addEventListener("keydown", (e) => {
      if (e.code === "Space" && !e.repeat && !typing(e.target)) { e.preventDefault(); pttStart().catch(() => setStatus("mic error")); }
    });
    document.addEventListener("keyup", (e) => {
      if (e.code === "Space" && !typing(e.target)) { e.preventDefault(); pttStop(); }
    });
  }

  btn.addEventListener("click", () => {
    if (pttMode) return;                 // handled by pointer/space listeners
    if (listening) { stopVad(); return; }
    startVad().catch((err) => { stopVad(); enablePttFallback(err); });
  });
})();
