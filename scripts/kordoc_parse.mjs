import { parse, VERSION } from "kordoc";

if (!/^\d+\.\d+\.\d+$/.test(String(VERSION || ""))) {
  throw new Error("invalid_kordoc_version");
}

const input = await new Promise((resolve, reject) => {
  const chunks = [];
  process.stdin.on("data", (chunk) => chunks.push(chunk));
  process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
  process.stdin.on("error", reject);
});

const request = JSON.parse(input || "{}");
const bytes = Buffer.from(String(request.dataBase64 || ""), "base64");
const options = {};
if (request.ocr) options.ocr = true;

const parsed = await parse(bytes, options);

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

process.stdout.write(JSON.stringify(clean(response)));
