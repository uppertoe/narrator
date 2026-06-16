// Narrator audio capture — hands-free VAD, two-tier capture over HTTP.
//
// On each detected utterance we do two quick POSTs:
//   1. /utterance/provisional {captured_at}  → instantly drops a timestamped row
//      into the log ("transcribing…") and returns its event id. The timestamp —
//      the point of this app — is locked here, at capture, not at transcription.
//   2. /utterance/{id}/audio {audio}         → uploads the clip; the server (the
//      accuracy tier) transcribes it and fills the row in place.
// Recognition lives entirely server-side now, so there's no on-device model and
// no WebSocket. If VAD can't initialise, falls back to hold-to-talk.
(function () {
  const btn = document.getElementById("listen-btn");
  if (!btn) return;
  const statusEl = document.getElementById("listen-status");
  const transcriptEl = document.getElementById("transcript");
  const caseId = btn.dataset.caseId;
  const ASSETS = "/static/vad/";

  let myvad = null, listening = false, capturedAt = null;
  let stream = null, rec = null, chunks = [], pttMode = false, recording = false, pttEventId = null;
  const setStatus = (s) => { if (statusEl) statusEl.textContent = s; };

  const b64 = (buf) => {
    const bytes = new Uint8Array(buf); let s = "";
    for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s);
  };

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

  function postForm(url, data) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(data),
    });
  }

  // Surgically apply a capture payload: patch the chart and only the rows that
  // changed, INSERTING new rows in order — but never touch a row that's open in
  // in-line edit (so dictation can't wipe a correction in progress), nor the
  // +add form. Auto-scrolls to the newest row only if you're already following
  // the bottom of the log.
  function applyCapture(p) {
    const labels = document.querySelector(".chart-labels");
    const scroll = document.getElementById("chart-scroll");
    if (labels && p.chart_labels !== undefined) labels.innerHTML = p.chart_labels;
    if (scroll && p.chart_plot !== undefined) {
      scroll.innerHTML = p.chart_plot;
      scroll.dataset.liveX = p.chart_live_x;
      scroll.scrollLeft = Math.max(0, p.chart_live_x - scroll.clientWidth + 80);
    }
    const list = document.getElementById("timeline-rows");
    if (!list || !p.rows) return;

    const atBottom = (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 140);
    const ids = new Set(p.rows.map((r) => "event-" + r.id));
    list.querySelectorAll("article.ev").forEach((el) => { if (!ids.has(el.id)) el.remove(); });
    const empty = list.querySelector(".empty");
    if (empty && p.rows.length) empty.remove();

    let anchor = null;
    p.rows.forEach((r) => {
      const domId = "event-" + r.id;
      let el = document.getElementById(domId);
      if (el && el.querySelector("form.row-edit")) {
        // open in-line edit — leave it exactly as the clinician left it
      } else if (el) {
        el.outerHTML = r.html;
        el = document.getElementById(domId);
      } else {
        const tpl = document.createElement("template");
        tpl.innerHTML = r.html.trim();
        el = tpl.content.firstElementChild;
        if (anchor && anchor.nextSibling) list.insertBefore(el, anchor.nextSibling);
        else if (anchor) list.appendChild(el);
        else list.insertBefore(el, list.firstChild);
      }
      anchor = document.getElementById(domId) || anchor;
    });
    if (window.htmx) htmx.process(list);

    // convention-learning toast (kept across surgical updates)
    let flash = document.getElementById("capture-flash");
    if (p.notice) {
      if (!flash) {
        flash = document.createElement("p");
        flash.id = "capture-flash"; flash.className = "flash";
        list.parentNode.insertBefore(flash, list);
      }
      flash.textContent = "↻ " + p.notice;
    } else if (flash) { flash.remove(); }

    if (transcriptEl && p.transcript !== undefined)
      transcriptEl.textContent = p.transcript ? "“" + p.transcript + "”" : "";

    if (atBottom && p.focus_id) {
      const f = document.getElementById("event-" + p.focus_id);
      if (f) f.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  // Instant tier: timestamped placeholder. Returns the new event id.
  async function createProvisional(capturedAtIso) {
    const r = await postForm(`/case/${caseId}/utterance/provisional`,
      capturedAtIso ? { captured_at: capturedAtIso } : {});
    const p = await r.json();
    applyCapture(p);
    return p.focus_id;
  }

  // Accuracy tier: upload audio (base64 WAV/blob) to resolve a placeholder.
  async function resolveAudio(eventId, audioB64) {
    const r = await postForm(`/case/${caseId}/utterance/${eventId}/audio`, { audio: audioB64 });
    applyCapture(await r.json());
  }

  // Hands-free: one utterance → placeholder, then resolve.
  async function handleUtterance(audio, capturedAtIso) {
    try {
      const eventId = await createProvisional(capturedAtIso);
      setStatus("● listening (transcribing…)");
      if (!eventId) return;
      await resolveAudio(eventId, b64(float32ToWav(audio)));
      setStatus(listening ? "● listening" : "ready");
    } catch (e) {
      console.error("capture failed:", e);
      setStatus("capture error");
    }
  }

  // --- Hands-free VAD --------------------------------------------------------
  async function startVad() {
    setStatus("loading…");
    if (!window.vad || !window.ort) throw new Error("vad assets missing");
    ort.env.wasm.wasmPaths = ASSETS;   // load wasm/mjs locally
    ort.env.wasm.numThreads = 1;       // single-threaded → no cross-origin isolation needed
    myvad = await vad.MicVAD.new({
      baseAssetPath: ASSETS,
      onnxWASMBasePath: ASSETS,
      // Browser DSP on the mic: cleaner input → better VAD + ASR (theatre noise).
      additionalAudioConstraints: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      },
      // Anchor the event to when speech *started* — closest to when the drug was
      // spoken — not when transcription finishes seconds later.
      onSpeechStart: () => { capturedAt = new Date().toISOString(); setStatus("listening — speech"); },
      onSpeechEnd: (audio) => { handleUtterance(audio, capturedAt); },
    });
    myvad.start();
    listening = true;
    btn.textContent = "■ Stop";
    btn.classList.add("rec");
    setStatus("● listening");
  }
  function stopVad() {
    if (myvad) myvad.pause();
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
    // Placeholder + locked timestamp at the moment the key/button goes down.
    pttEventId = await createProvisional(new Date().toISOString());
    chunks = [];
    rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.onstop = () => {
      const blob = new Blob(chunks, { type: (chunks[0] && chunks[0].type) || "audio/webm" });
      if (blob.size && pttEventId) {
        blob.arrayBuffer().then((buf) => resolveAudio(pttEventId, b64(buf)));
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

  // Exposed for debugging / behavioural tests of the surgical update path.
  window.NarratorCapture = { applyCapture, createProvisional, resolveAudio };
})();
