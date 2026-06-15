// On-device transcription (beta) — Whisper in the browser via transformers.js.
// The library loads from a pinned CDN (public code); the MODEL is served from our
// own /static (vendored in the image) so audio + model stay on-prem. Exposes
// window.NarratorLocalASR for the classic-script audio client to call.
import {
  pipeline, env,
} from "https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.2.0/dist/transformers.min.js";

// Load the model only from our server, never from a third party.
env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = "/static/asr/models/";

// tiny.en: ~2x faster than base.en on-device; Claude extraction backstops the
// small accuracy difference. Vendored from our /static (see Dockerfile).
const MODEL = "onnx-community/whisper-tiny.en";
let pipePromise = null;

function getPipe() {
  if (!pipePromise) {
    const device = (typeof navigator !== "undefined" && navigator.gpu) ? "webgpu" : "wasm";
    pipePromise = pipeline("automatic-speech-recognition", MODEL, { dtype: "q8", device })
      .catch((e) => { pipePromise = null; throw e; });
  }
  return pipePromise;
}

window.NarratorLocalASR = {
  available: true,
  // Preload the model so the first utterance isn't slow.
  warmup() { return getPipe().then(() => true).catch(() => false); },
  async transcribe(float32) {
    const asr = await getPipe();
    // English-only model: do NOT pass language/task (transformers.js rejects them).
    const out = await asr(float32);
    return ((out && out.text) || "").trim();
  },
};
