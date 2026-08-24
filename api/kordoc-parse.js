import crypto from "node:crypto";

// Kordoc must never make an external OCR/model request from this private
// bridge. Set the flag before loading the package through a dynamic import.
process.env.KORDOC_OFFLINE = "1";

const MAX_UPLOAD_BYTES = 4 * 1024 * 1024;
// Vercel rejects function responses above 4.5 MB. Keep a safety margin so the
// platform never replaces our controlled error with a generic 500 response.
const MAX_RESPONSE_BYTES = 4 * 1024 * 1024;
const KORDOC_VERSION = "4.9.1";
const SIGNATURE_TTL_SECONDS = 120;
const ED25519_PUBLIC_KEY_RAW = "tbBF-dGBMdncj9AkHph9pD4pUaeFevCUOGjyNDCgWIc";
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");
const ED25519_PUBLIC_KEY = crypto.createPublicKey({
  key: Buffer.concat([ED25519_SPKI_PREFIX, Buffer.from(ED25519_PUBLIC_KEY_RAW, "base64url")]),
  format: "der",
  type: "spki",
});
let parsePromise;

const sendJson = (res, status, payload) => {
  res.statusCode = status;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.setHeader("cache-control", "no-store");
  res.end(JSON.stringify(payload));
};

const normalizeSecret = (value) => String(value || "").trim().replace(/^\uFEFF+/, "").trim();

const sameSecret = (provided, expected) => {
  const providedText = normalizeSecret(provided);
  const expectedText = normalizeSecret(expected);
  if (!providedText) return false;
  const left = crypto.createHash("sha256").update(providedText, "utf8").digest();
  const right = crypto.createHash("sha256").update(expectedText, "utf8").digest();
  return crypto.timingSafeEqual(left, right);
};

const headerText = (req, name) => {
  const value = req.headers[name];
  return Array.isArray(value) ? String(value[0] || "") : String(value || "");
};

const hasValidSignature = (req, bytes) => {
  try {
    const timestampText = headerText(req, "x-ncscope-kordoc-timestamp");
    const signatureText = headerText(req, "x-ncscope-kordoc-signature");
    const encodedFilename = headerText(req, "x-ncscope-filename-b64");
    const ocrFlag = headerText(req, "x-ncscope-ocr") === "1" ? "1" : "0";
    if (!/^\d{10}$/.test(timestampText)) return false;
    if (!/^[A-Za-z0-9_-]{86}$/.test(signatureText)) return false;
    if (!/^[A-Za-z0-9_-]{0,1024}$/.test(encodedFilename)) return false;
    const timestamp = Number(timestampText);
    const now = Math.floor(Date.now() / 1000);
    if (!Number.isSafeInteger(timestamp) || Math.abs(now - timestamp) > SIGNATURE_TTL_SECONDS) {
      return false;
    }
    const bodySha256 = crypto.createHash("sha256").update(bytes).digest("hex");
    const message = [timestampText, bodySha256, encodedFilename, ocrFlag].join("\n");
    const signature = Buffer.from(signatureText, "base64url");
    return signature.length === 64 && crypto.verify(
      null,
      Buffer.from(message, "ascii"),
      ED25519_PUBLIC_KEY,
      signature,
    );
  } catch {
    return false;
  }
};

const readBody = async (req) => {
  const declaredLength = Number(req.headers["content-length"] || 0);
  if (Number.isFinite(declaredLength) && declaredLength > MAX_UPLOAD_BYTES) {
    const error = new Error("upload_too_large");
    error.code = "UPLOAD_TOO_LARGE";
    throw error;
  }

  if (Buffer.isBuffer(req.body)) return req.body;
  if (req.body instanceof Uint8Array) return Buffer.from(req.body);
  if (req.body instanceof ArrayBuffer) return Buffer.from(req.body);
  if (typeof req.body === "string") return Buffer.from(req.body, "latin1");

  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += bytes.length;
    if (size > MAX_UPLOAD_BYTES) {
      const error = new Error("upload_too_large");
      error.code = "UPLOAD_TOO_LARGE";
      throw error;
    }
    chunks.push(bytes);
  }
  return Buffer.concat(chunks, size);
};

const clean = (value, depth = 0, seen = new WeakSet()) => {
  if (depth > 16 || value === undefined) return undefined;
  if (value instanceof Uint8Array || value instanceof ArrayBuffer) return undefined;
  if (typeof value === "bigint") return Number(value);
  if (Array.isArray(value)) {
    return value.map((item) => clean(item, depth + 1, seen)).filter((item) => item !== undefined);
  }
  if (value && typeof value === "object") {
    if (seen.has(value)) return undefined;
    seen.add(value);
    const result = {};
    for (const [key, item] of Object.entries(value)) {
      if (["data", "bytes", "buffer", "imageData"].includes(key)) continue;
      const cleaned = clean(item, depth + 1, seen);
      if (cleaned !== undefined) result[key] = cleaned;
    }
    return result;
  }
  return value;
};

const getKordocParse = async () => {
  if (!parsePromise) {
    parsePromise = import("kordoc").then((module) => module.parse);
  }
  return parsePromise;
};

export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("allow", "POST");
    return sendJson(res, 405, { success: false, code: "method_not_allowed" });
  }

  const expectedSecret = normalizeSecret(process.env.KORDOC_BRIDGE_SECRET);
  const hasValidSharedSecret = Boolean(expectedSecret) && sameSecret(
    req.headers["x-ncscope-kordoc-secret"],
    expectedSecret,
  );

  const contentType = String(req.headers["content-type"] || "").toLowerCase();
  if (!contentType.startsWith("application/octet-stream")) {
    return sendJson(res, 415, { success: false, code: "unsupported_media_type" });
  }

  try {
    const bytes = await readBody(req);
    if (!bytes.length) {
      return sendJson(res, 400, { success: false, code: "empty_document" });
    }
    if (bytes.length > MAX_UPLOAD_BYTES) {
      return sendJson(res, 413, { success: false, code: "upload_too_large" });
    }
    if (!hasValidSharedSecret && !hasValidSignature(req, bytes)) {
      return sendJson(res, 401, { success: false, code: "unauthorized" });
    }

    const parse = await getKordocParse();
    if (typeof parse !== "function") {
      return sendJson(res, 503, { success: false, code: "parser_unavailable" });
    }
    const options = req.headers["x-ncscope-ocr"] === "1" ? { ocr: true } : {};
    const parsed = await parse(bytes, options);
    const response = clean({
      success: parsed?.success !== false,
      parser: "kordoc",
      parser_version: KORDOC_VERSION,
      markdown: parsed?.markdown || "",
      blocks: parsed?.blocks || [],
      metadata: parsed?.metadata || {},
      outline: parsed?.outline || [],
      warnings: parsed?.warnings || [],
      qualitySummary: parsed?.qualitySummary || null,
      pageQuality: parsed?.pageQuality || [],
    });
    const serialized = JSON.stringify(response);
    if (Buffer.byteLength(serialized, "utf8") > MAX_RESPONSE_BYTES) {
      return sendJson(res, 413, { success: false, code: "parse_result_too_large" });
    }
    res.statusCode = 200;
    res.setHeader("content-type", "application/json; charset=utf-8");
    res.setHeader("cache-control", "no-store");
    return res.end(serialized);
  } catch (error) {
    if (error?.code === "UPLOAD_TOO_LARGE") {
      return sendJson(res, 413, { success: false, code: "upload_too_large" });
    }
    // Never return the document, shared secret, stack, or parser exception.
    return sendJson(res, 422, { success: false, code: "parse_failed" });
  }
}
