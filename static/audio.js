// Narrator audio capture — VAD-segmented utterances over a WebSocket.
// Browser-side voice-activity detection chunks speech into utterances; each is
// sent as 16 kHz WAV. The server transcribes, runs the pipeline, and returns
// {transcript, board, notice}; we swap the board and re-arm HTMX.
(function () {
  const btn = document.getElementById("listen-btn");
  if (!btn) return;
  const statusEl = document.getElementById("listen-status");
  const transcriptEl = document.getElementById("transcript");
  const caseId = btn.dataset.caseId;
  let myvad = null, ws = null, listening = false;

  const setStatus = (s) => { if (statusEl) statusEl.textContent = s; };

  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${location.host}/ws/case/${caseId}`;
  }

  function connect() {
    ws = new WebSocket(wsUrl());
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (transcriptEl && msg.transcript) transcriptEl.textContent = "“" + msg.transcript + "”";
      if (msg.board) {
        const old = document.getElementById("board");
        if (old) {
          old.outerHTML = msg.board;
          if (window.htmx) htmx.process(document.getElementById("board"));
          const s = document.getElementById("chart-scroll");
          if (s) s.scrollLeft = s.scrollWidth;
        }
      }
      setStatus(listening ? "listening…" : "off");
    };
  }

  // 16 kHz mono Float32 → 16-bit PCM WAV
  function float32ToWav(float32) {
    const rate = 16000, n = float32.length;
    const buf = new ArrayBuffer(44 + n * 2);
    const dv = new DataView(buf);
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
    return buf;
  }

  async function start() {
    setStatus("loading…");
    connect();
    myvad = await vad.MicVAD.new({
      onSpeechStart: () => setStatus("listening…"),
      onSpeechEnd: (audio) => {
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(float32ToWav(audio));
        setStatus("transcribing…");
      },
    });
    myvad.start();
    listening = true;
    btn.textContent = "■ Stop";
    setStatus("listening…");
  }

  function stop() {
    if (myvad) myvad.pause();
    if (ws) ws.close();
    listening = false;
    btn.textContent = "● Listen";
    setStatus("off");
  }

  btn.addEventListener("click", () => {
    if (listening) stop();
    else start().catch((err) => { console.error(err); setStatus("mic error"); });
  });
})();
