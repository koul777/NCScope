import { parse, VERSION } from "kordoc";
import { createInterface } from "node:readline";
import { format } from "node:util";

if (!/^\d+\.\d+\.\d+$/.test(String(VERSION || ""))) {
  throw new Error("invalid_kordoc_version");
}

// The Python service only needs the document IR. Avoid returning binary image
// payloads when a document contains embedded images.
const clean = (value, depth = 0) => {
  if (depth > 16 || value === undefined) return undefined;
  if (value instanceof Uint8Array || value instanceof ArrayBuffer) return undefined;
  if (typeof value === "bigint") return Number(value);
  if (Array.isArray(value)) return value.map((item) => clean(item, depth + 1));
  if (value && typeof value === "object") {
    const result = {};
    for (const [key, item] of Object.entries(value)) {
      if (["data", "bytes", "buffer", "imageData"].includes(key)) continue;
      const cleaned = clean(item, depth + 1);
      if (cleaned !== undefined) result[key] = cleaned;
    }
    return result;
  }
  return value;
};

const parseRequest = async (request) => {
  const bytes = Buffer.from(String(request.dataBase64 || ""), "base64");
  const options = {};
  if (request.ocr) options.ocr = true;

  const parsed = await parse(bytes, options);

  const response = {
    success: parsed?.success !== false,
    parser: "kordoc",
    parser_version: VERSION,
    parser_execution: {
      schema_version: "ncscope_parser_execution_v1",
      role: "selected",
      parser: "kordoc",
      mode: "local_node_subprocess",
      parser_version: VERSION,
      node_version: process.versions.node,
      build_identity: { kind: "local_source_bundle" },
    },
    markdown: parsed?.markdown || "",
    blocks: parsed?.blocks || [],
    metadata: parsed?.metadata || {},
    outline: parsed?.outline || [],
    warnings: parsed?.warnings || [],
    qualitySummary: parsed?.qualitySummary || null,
    pageQuality: parsed?.pageQuality || [],
  };
  // ``fileType`` is Kordoc's own format detection. It is not part of the JSON
  // returned to Python; the persistent worker uses it only to decide whether
  // the pdf.js font-id restoration below applies.
  return { fileType: parsed?.fileType, json: JSON.stringify(clean(response)) };
};

// Persistent mode: the Python service keeps one process alive so each document
// skips Node start-up and the Kordoc module import. Requests are one JSON line
// each and are handled strictly one at a time. Every protocol line starts with
// a marker so stray library output on stdout can never be mistaken for a
// response.
const serve = async () => {
  const marker = "\u001eNCSCOPE_KORDOC_WORKER ";
  const idleMs = Math.max(1000, Number(process.env.KORDOC_WORKER_IDLE_MS) || 600000);
  const toStderr = (...args) => process.stderr.write(format(...args) + "\n");
  console.log = toStderr;
  console.info = toStderr;
  console.debug = toStderr;
  let idleTimer;
  const armIdleTimer = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => process.exit(0), idleMs);
  };
  const emit = (id, status, payload) =>
    new Promise((resolve) => {
      process.stdout.write(`\n${marker}${id} ${status} ${payload}\n`, resolve);
    });
  // pdf.js numbers every loaded document per process (font ids "g_d<N>_f<M>").
  // A one-shot process always starts at d0, so shift the ids back by the first
  // document number this request used to keep the output byte-identical.
  // Only Kordoc's PDF parser emits pdf.js font ids. HWP/HWPX/DOCX results carry
  // the document's real font faces, which must pass through unchanged even if
  // a face happens to look like "g_d3_f1"; an unknown format is not touched.
  const fontIdPattern = /"fontName":"g_d(\d+)_f(\d+)"/g;
  const restoreFreshProcessFontIds = (json) => {
    let offset = Infinity;
    for (const match of json.matchAll(fontIdPattern)) {
      offset = Math.min(offset, Number(match[1]));
    }
    if (!Number.isFinite(offset) || offset === 0) return json;
    return json.replace(
      fontIdPattern,
      (_match, doc, font) => `"fontName":"g_d${Number(doc) - offset}_f${font}"`,
    );
  };
  await emit(0, "ready", JSON.stringify({ parser_version: VERSION }));
  armIdleTimer();
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of lines) {
    if (!line.trim()) continue;
    clearTimeout(idleTimer);
    let id = 0;
    try {
      const request = JSON.parse(line);
      id = Number(request.id) || 0;
      const { fileType, json } = await parseRequest(request);
      await emit(id, "ok", fileType === "pdf" ? restoreFreshProcessFontIds(json) : json);
    } catch (error) {
      const detail = String(error?.stack || error?.message || error).slice(-1200);
      await emit(id, "error", JSON.stringify({ detail, code: String(error?.code || "") }));
    }
    armIdleTimer();
  }
  process.exit(0);
};

if (process.argv.includes("--serve")) {
  await serve();
} else {
  const input = await new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.on("data", (chunk) => chunks.push(chunk));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", reject);
  });

  const request = JSON.parse(input || "{}");
  process.stdout.write((await parseRequest(request)).json);
}
