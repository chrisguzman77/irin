import { deviceFetch } from "./api";
import type { components } from "../types/pi";

// Family Story (docs/FAMILY_STORY.md). Recipients change ONLY through their
// own PIN-gated routes (the Pi refuses family_recipients in POST
// /api/settings): add, edit, pause, resume, revoke. A new recipient's first
// story, and the next story after a level or email change, waits for the
// patient's approval. Stories are approved or skipped by the patient.
type S = components["schemas"];
export type Recipient = S["FamilyRecipient"];
export type RecipientRequest = S["RecipientRequest"];
export type RecipientPatch = S["RecipientPatch"];
export type FamilyStory = S["FamilyStory"];

type Result<T> = { ok: true; value: T } | { ok: false; reason: string };

async function call<T>(base: string, path: string, body?: unknown): Promise<Result<T>> {
  const res = await deviceFetch(base, path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.ok) return { ok: true, value: (await res.json()) as T };
  let reason = `Your Irin refused it (${res.status}).`;
  try {
    const b = await res.json();
    if (b && typeof b.detail === "string") reason = `Your Irin refused it: ${b.detail}`;
  } catch {
    /* no body */
  }
  return { ok: false, reason };
}

const rec = (id: string) => `/api/family/recipients/${encodeURIComponent(id)}`;

export const addRecipient = (base: string, r: RecipientRequest) => call<Recipient>(base, "/api/family/recipients", r);
export const editRecipient = (base: string, id: string, p: RecipientPatch) => call<Recipient>(base, rec(id), p);
export const pauseRecipient = (base: string, id: string) => call<Recipient>(base, `${rec(id)}/pause`);
export const resumeRecipient = (base: string, id: string) => call<Recipient>(base, `${rec(id)}/resume`);
export const revokeRecipient = (base: string, id: string) => call<Recipient>(base, `${rec(id)}/revoke`);

const story = (id: string) => `/api/family/stories/${encodeURIComponent(id)}`;
export const approveStory = (base: string, id: string) => call<FamilyStory>(base, `${story(id)}/approve`);
export const skipStory = (base: string, id: string) => call<FamilyStory>(base, `${story(id)}/skip`);

export async function listStories(base: string, nightDate?: string): Promise<FamilyStory[]> {
  const q = nightDate ? `?night_date=${encodeURIComponent(nightDate)}` : "";
  const res = await deviceFetch(base, `/api/family/stories${q}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status}`);
  return (await res.json()) as FamilyStory[];
}

export const storyAudioPath = (s: FamilyStory) =>
  s.audio_url ? `${story(s.story_id)}/audio.mp3` : null;
