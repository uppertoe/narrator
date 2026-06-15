// Narrator audio capture — push-to-talk over a WebSocket.
// Hold the button (or the Spacebar) to record one utterance; release to send.
// MediaRecorder produces a compressed blob (webm/ogg/mp4 by browser); the
// server decodes it with faster-whisper. No external assets — robust in theatre.
(function () {
  const btn = document.getElementById("listen-btn");
  if (!btn) return;
  const statusEl = document.getElementById("listen-status");
  const transcriptEl = document.getElementById("transcript");
  const caseId = btn.dataset.caseId;

  let ws = null, stream = null, rec = null, chunks = [], recording = false, busy = false;
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
      if (transcriptEl) transcriptEl.textContent = msg.transcript ? "“" + msg.transcript + "”" : "(no speech)";
      if (msg.board) {
        const old = document.getElementById("board");
        if (old) {
          old.outerHTML = msg.board;
          if (window.htmx) htmx.process(document.getElementById("board"));
          const s = document.getElementById("chart-scroll");
          if (s) s.scrollLeft = s.scrollWidth;
        }
      }
      setStatus("hold to talk");
    };
    ws.onclose = () => { ws = null; };
  }

  async function ensureMic() {
    if (!stream) stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    return stream;
  }

  async function startRec() {
    if (recording) return;
    await ensureMic();
    ensureWs();
    chunks = [];
    rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.onstop = () => {
      const blob = new Blob(chunks, { type: (chunks[0] && chunks[0].type) || "audio/webm" });
      if (blob.size && ws && ws.readyState === WebSocket.OPEN) {
        blob.arrayBuffer().then((b) => ws.send(b));
        setStatus("transcribing…");
      } else {
        setStatus("hold to talk");
      }
    };
    rec.start();
    recording = true;
    btn.classList.add("rec");
    setStatus("● recording — release to send");
  }

  function stopRec() {
    if (!recording) return;
    recording = false;
    btn.classList.remove("rec");
    if (rec && rec.state !== "inactive") rec.stop();
  }

  const press = (e) => {
    e.preventDefault();
    startRec().catch((err) => { console.error(err); setStatus("mic error"); });
  };
  const release = () => stopRec();

  btn.addEventListener("pointerdown", press);
  btn.addEventListener("pointerup", release);
  btn.addEventListener("pointerleave", release);
  btn.addEventListener("pointercancel", release);

  // Spacebar push-to-talk (ignored while typing in a field).
  const typing = (t) => t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !e.repeat && !typing(e.target)) {
      e.preventDefault();
      startRec().catch((err) => { console.error(err); setStatus("mic error"); });
    }
  });
  document.addEventListener("keyup", (e) => {
    if (e.code === "Space" && !typing(e.target)) { e.preventDefault(); stopRec(); }
  });
})();
