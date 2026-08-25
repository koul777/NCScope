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
const ED25519_PUBLIC_KEY_RAW = "VBlGEy_kzpdThiEEmtrGj7hU6bfUkNtw0SjIrXwK8vA";
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");
const ED25519_PUBLIC_KEY = crypto.createPublicKey({
  key: Buffer.concat([ED25519_SPKI_PREFIX, Buffer.from(ED25519_PUBLIC_KEY_RAW, "base64url")]),
  format: "der",
  type: "spki",
});
let parserModulePromise;

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

const validateSignatureHeaders = (req) => {
  try {
    const timestampText = headerText(req, "x-ncscope-kordoc-timestamp");
    const signatureText = headerText(req, "x-ncscope-kordoc-signature");
    const bodySha256 = headerText(req, "x-ncscope-kordoc-body-sha256");
    const encodedFilename = headerText(req, "x-ncscope-filename-b64");
    if (!/^\d{10}$/.test(timestampText)) return { valid: false, reason: "timestamp_format" };
    if (!/^[A-Za-z0-9_-]{86}$/.test(signatureText)) {
      return { valid: false, reason: "signature_format" };
    }
    if (!/^[A-Za-z0-9_-]{0,1024}$/.test(encodedFilename)) {
      return { valid: false, reason: "filename_format" };
    }
    if (!/^[a-f0-9]{64}$/.test(bodySha256)) {
      return { valid: false, reason: "body_hash_format" };
    }
    const timestamp = Number(timestampText);
    const now = Math.floor(Date.now() / 1000);
    if (!Number.isSafeInteger(timestamp) || Math.abs(now - timestamp) > SIGNATURE_TTL_SECONDS) {
      return { valid: false, reason: "timestamp_expired" };
    }
    const ocrFlag = headerText(req, "x-ncscope-ocr") === "1" ? "1" : "0";
    const message = [timestampText, bodySha256, encodedFilename, ocrFlag].join("\n");
    const signature = Buffer.from(signatureText, "base64url");
    const valid = signature.length === 64 && crypto.verify(
      null,
      Buffer.from(message, "ascii"),
      ED25519_PUBLIC_KEY,
      signature,
    );
    return { valid, reason: valid ? "header_signature_passed" : "signature_mismatch" };
  } catch {
    return { valid: false, reason: "verification_error" };
  }
};

const validateSignature = (req, bytes) => {
  try {
    const headerReview = validateSignatureHeaders(req);
    if (!headerReview.valid) return headerReview;
    const declaredHash = Buffer.from(
      headerText(req, "x-ncscope-kordoc-body-sha256"),
      "hex",
    );
    const actualHash = crypto.createHash("sha256").update(bytes).digest();
    const valid = declaredHash.length === actualHash.length
      && crypto.timingSafeEqual(declaredHash, actualHash);
    return { valid, reason: valid ? "passed" : "body_hash_mismatch" };
  } catch {
    return { valid: false, reason: "verification_error" };
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

const getKordocModule = async () => {
  if (!parserModulePromise) {
    parserModulePromise = import("kordoc");
  }
  return parserModulePromise;
};

const buildIdentity = () => {
  const identity = { kind: "vercel_deployment" };
  const deploymentUrl = String(process.env.VERCEL_URL || "").trim().toLowerCase();
  const deploymentId = String(process.env.VERCEL_DEPLOYMENT_ID || "").trim();
  const gitCommitSha = String(process.env.VERCEL_GIT_COMMIT_SHA || "").trim().toLowerCase();
  if (/^[a-z0-9.-]+\.vercel\.app$/.test(deploymentUrl)) identity.deployment_url = deploymentUrl;
  if (/^[A-Za-z0-9_-]{1,128}$/.test(deploymentId)) identity.deployment_id = deploymentId;
  if (/^[a-f0-9]{40}$/.test(gitCommitSha)) identity.git_commit_sha = gitCommitSha;
  return identity;
};

export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("allow", "POST");
    return sendJson(res, 405, { success: false, code: "method_not_allowed" });
  }

  const expectedSecret = normalizeSecret(process.env.KORDOC_BRIDGE_SECRET);
  const hasStrongSharedSecret = /^[\x20-\x7E]{32,}$/.test(expectedSecret);
  const hasValidSharedSecret = hasStrongSharedSecret && sameSecret(
    req.headers["x-ncscope-kordoc-secret"],
    expectedSecret,
  );

  const contentType = String(req.headers["content-type"] || "").toLowerCase();
  if (!contentType.startsWith("application/octet-stream")) {
    return sendJson(res, 415, { success: false, code: "unsupported_media_type" });
  }

  if (!hasValidSharedSecret) {
    const signatureHeaderReview = validateSignatureHeaders(req);
    if (!signatureHeaderReview.valid) {
      res.setHeader("x-ncscope-bridge-rejection", signatureHeaderReview.reason);
      return sendJson(res, 401, {
        success: false,
        code: "unauthorized",
        reason_code: signatureHeaderReview.reason,
      });
    }
  }

  try {
    const bytes = await readBody(req);
    if (!bytes.length) {
      return sendJson(res, 400, { success: false, code: "empty_document" });
    }
    if (bytes.length > MAX_UPLOAD_BYTES) {
      return sendJson(res, 413, { success: false, code: "upload_too_large" });
    }
    const signatureReview = validateSignature(req, bytes);
    if (!hasValidSharedSecret && !signatureReview.valid) {
      res.setHeader("x-ncscope-bridge-rejection", signatureReview.reason);
      if (headerText(req, "x-ncscope-kordoc-signature")) {
        console.warn("kordoc_bridge_signed_request_rejected", { reason: signatureReview.reason });
      }
      return sendJson(res, 401, {
        success: false,
        code: "unauthorized",
        reason_code: signatureReview.reason,
      });
    }
    const executionBuildIdentity = buildIdentity();
    if (!executionBuildIdentity.deployment_url) {
      return sendJson(res, 503, {
        success: false,
        code: "deployment_identity_unavailable",
      });
    }

    const parserModule = await getKordocModule();
    const parse = parserModule?.parse;
    const actualVersion = String(parserModule?.VERSION || "").trim();
    if (typeof parse !== "function" || actualVersion !== KORDOC_VERSION) {
      return sendJson(res, 503, { success: false, code: "parser_unavailable" });
    }
    const options = req.headers["x-ncscope-ocr"] === "1" ? { ocr: true } : {};
    const parsed = await parse(bytes, options);
    const response = clean({
      success: parsed?.success !== false,
      parser: "kordoc",
      parser_version: actualVersion,
      parser_execution: {
        schema_version: "ncscope_parser_execution_v1",
        role: "selected",
        parser: "kordoc",
        mode: "authenticated_serverless_bridge",
        parser_version: actualVersion,
        node_version: process.versions.node,
        build_identity: executionBuildIdentity,
      },
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
