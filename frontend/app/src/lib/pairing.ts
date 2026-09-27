import { deviceFetch } from "./api";
import { postFresh, type FreshResult } from "./freshPin";
import type { Pairing } from "./cards";

// Share with my doctor (justin.md R1 + R3; chris.md R5). POST /api/pair/start
// (PIN) answers with the QR URL; only the screen that started a pairing can
// draw its QR, because pairing_state never carries the token. The Pi learns
// that the doctor's browser joined only when asked (GET /api/pair/status,
// PIN), so the starting screen polls while its QR is up; the answer is the
// same dict as pairing_state. Confirm is a fresh-PIN verb (postFresh);
// nothing is shared until it succeeds. Revoke is instant on both sides.

export interface PairingState {
  status: "idle" | "awaiting_scan" | "awaiting_confirm";
  peer_kind: string | null;
  expires_at: string | null;
  /** the PENDING peer's name (never the paired doctor's) */
  doctor_display_name: string | null;
  code4: string | null;
  pairings: Pairing[];
}

export interface PairStart {
  token: string;
  qr_url: string;
  expires_at: string;
  expires_in_s: number;
  peer_kind: string;
  is_demo: boolean;
}

/** pairing_state is a plain dict in the snapshot: read it defensively. */
export function readPairingState(x: unknown): PairingState {
  const p = (x ?? {}) as Record<string, unknown>;
  const status = p.status === "awaiting_scan" || p.status === "awaiting_confirm" ? p.status : "idle";
  const str = (v: unknown) => (typeof v === "string" ? v : null);
  return {
    status,
    peer_kind: str(p.peer_kind),
    expires_at: str(p.expires_at),
    doctor_display_name: str(p.doctor_display_name),
    code4: str(p.code4),
    pairings: Array.isArray(p.pairings)
      ? (p.pairings as Pairing[]).filter((q) => q && typeof q.doctor_id === "string")
      : [],
  };
}

async function reason(res: Response): Promise<string> {
  try {
    const b = await res.json();
    if (b && typeof b.detail === "string") return `Your Irin refused it: ${b.detail}`;
  } catch {
    /* no body */
  }
  return `Your Irin refused it (${res.status}).`;
}

export async function startPairing(base: string): Promise<{ ok: true; value: PairStart } | { ok: false; reason: string }> {
  const res = await deviceFetch(base, "/api/pair/start", { method: "POST", body: JSON.stringify({ peer_kind: "doctor" }) });
  if (!res.ok) return { ok: false, reason: await reason(res) };
  return { ok: true, value: (await res.json()) as PairStart };
}

export async function pairStatus(base: string): Promise<PairingState | null> {
  const res = await deviceFetch(base, "/api/pair/status", { pinned: true, cache: "no-store" });
  return res.ok ? readPairingState(await res.json()) : null;
}

export function confirmPairing(base: string): Promise<FreshResult> {
  return postFresh(base, "/api/pair/confirm", undefined, "Enter your PIN to start sharing");
}

export async function revokePairing(base: string, doctorId: string): Promise<{ ok: true } | { ok: false; reason: string }> {
  const res = await deviceFetch(base, `/api/pair/${encodeURIComponent(doctorId)}/revoke`, { method: "POST" });
  return res.ok ? { ok: true } : { ok: false, reason: await reason(res) };
}

/** The QR as one SVG path (qrcode-generator, loaded by index.html as window.qrcode). */
export function qrPath(text: string): { size: number; d: string } {
  const q = window.qrcode(0, "M");
  q.addData(text);
  q.make();
  const n = q.getModuleCount(), m = 4;
  let d = "";
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (q.isDark(r, c)) d += `M${c + m} ${r + m}h1v1h-1z`;
  return { size: n + 2 * m, d };
}

declare global {
  interface Window {
    qrcode: (typeNumber: number, level: "L" | "M" | "Q" | "H") => {
      addData(text: string): void;
      make(): void;
      getModuleCount(): number;
      isDark(row: number, col: number): boolean;
    };
  }
}
