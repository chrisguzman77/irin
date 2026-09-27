// The watcher page's pairing session and its crypto helpers, copied from
// frontend/clinician/session.js (justin.md B2: "the same pairing code as the
// inbox"). tweetnacl (nacl.min.js, a classic <script>) defines window.nacl:
// crypto_box = X25519 + XSalsa20-Poly1305, the same box the Pi uses. Keys,
// nonces and ciphertext travel as standard padded base64.
//
// localStorage "irin.watch.v0" holds ONE pairing at a time, with the relay's wire
// names (the relay calls every browser peer a "doctor" in its pairing fields):
//   {relay, token, device_pk, device_id, is_demo, name, doctor_pk, doctor_sk,
//    state: "joining" | "paired", doctor_id, bearer, paired_at}
// doctor_id is this buddy's peer_id: its inbox and its volunteer id on the hub.
// The private key never leaves this browser. The bearer is handed out by the
// relay exactly once, so it is saved before anything else happens.

const KEY = "irin.watch.v0";

export const nacl = () => {
  if (!window.nacl) throw new Error("nacl.min.js did not load");
  return window.nacl;
};

export function b64(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}
export function unb64(text) {
  const s = atob(String(text).trim());
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}
const utf8 = (s) => new TextEncoder().encode(s);

// SHA-256 in plain JS: crypto.subtle exists only on https, and a laptop demo runs
// on http. Used for code4 only (not for secrets).
const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);
export function sha256(bytes) {
  const len = bytes.length, total = ((len + 9 + 63) >> 6) << 6;
  const m = new Uint8Array(total);
  m.set(bytes);
  m[len] = 0x80;
  const bits = len * 8;
  m[total - 4] = bits >>> 24; m[total - 3] = bits >>> 16; m[total - 2] = bits >>> 8; m[total - 1] = bits;
  m[total - 8] = Math.floor(bits / 2 ** 32);
  const H = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const W = new Uint32Array(64);
  const rotr = (x, n) => (x >>> n) | (x << (32 - n));
  for (let o = 0; o < total; o += 64) {
    for (let i = 0; i < 16; i++) W[i] = (m[o + 4 * i] << 24) | (m[o + 4 * i + 1] << 16) | (m[o + 4 * i + 2] << 8) | m[o + 4 * i + 3];
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(W[i - 15], 7) ^ rotr(W[i - 15], 18) ^ (W[i - 15] >>> 3);
      const s1 = rotr(W[i - 2], 17) ^ rotr(W[i - 2], 19) ^ (W[i - 2] >>> 10);
      W[i] = W[i - 16] + s0 + W[i - 7] + s1;
    }
    let [a, b, c, d, e, f, g, h] = H;
    for (let i = 0; i < 64; i++) {
      const t1 = (h + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + W[i]) >>> 0;
      const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
      h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    H[0] += a; H[1] += b; H[2] += c; H[3] += d; H[4] += e; H[5] += f; H[6] += g; H[7] += h;
  }
  const out = new Uint8Array(32);
  H.forEach((v, i) => { out[4 * i] = v >>> 24; out[4 * i + 1] = v >>> 16; out[4 * i + 2] = v >>> 8; out[4 * i + 3] = v; });
  return out;
}

/** The anti-swap code both sides show (crypto.code4): first 4 bytes of
 * sha256(device_pk_b64 + doctor_pk_b64 + token_hex), big-endian, mod 10000. */
export function code4(devicePk, doctorPk, token) {
  const d = sha256(utf8(devicePk + doctorPk + token));
  const n = ((d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3]) >>> 0;
  return String(n % 10000).padStart(4, "0");
}

// --- the session ---

export function load() {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || "null");
    return s && typeof s === "object" ? s : null;
  } catch {
    return null;
  }
}
export function save(s) {
  localStorage.setItem(KEY, JSON.stringify(s)); // throws if storage is blocked: the caller must not go on
}
export function clear() {
  try {
    localStorage.removeItem(KEY);
  } catch { /* nothing stored */ }
}

// --- the relay ---

export async function relay(s, path, init = {}) {
  const headers = new Headers(init.headers);
  if (s.bearer) headers.set("Authorization", `Bearer ${s.bearer}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  return fetch(s.relay.replace(/\/$/, "") + path, { ...init, headers, cache: "no-store" });
}

/** A sealed box from the device, opened with our key; null if it does not open
 * (tampered, or sealed by any key but the paired device's). */
export function openFromDevice(s, nonce, ciphertext) {
  try {
    const plain = nacl().box.open(unb64(ciphertext), unb64(nonce), unb64(s.device_pk), unb64(s.doctor_sk));
    return plain ? new TextDecoder().decode(plain) : null;
  } catch {
    return null;
  }
}
