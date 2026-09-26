import { useCallback, useEffect, useRef, useState } from "react";
import type { AlarmState, Forecast, Reading, Settings, StateSnapshot, WSMessage } from "./contracts";

// The ONE socket to the Pi (justin.md hard client rule 1). Screens render
// from the state_snapshot the Pi sends on every connect plus the updates
// after it; nothing assumes it saw messages while disconnected. Coming back
// to the page (phone unlock, tab switch) reopens the socket so a fresh
// snapshot repaints everything.

export const DISCONNECTED_BANNER_MS = 15_000;
const HIDDEN_RECONNECT_MS = 5_000;

export interface DeviceSocket {
  snapshot: StateSnapshot | null;
  connected: boolean;
  /** true once the socket has been down longer than 15 s: show the banner */
  disconnectedLong: boolean;
}

export function applyMessage(snap: StateSnapshot | null, msg: WSMessage): StateSnapshot | null {
  const p = msg.payload as Record<string, unknown>;
  if (msg.type === "state_snapshot") return p as unknown as StateSnapshot;
  if (!snap) return snap; // updates before the first snapshot are meaningless
  switch (msg.type) {
    case "reading_update":
      return { ...snap, latest_reading: p as unknown as Reading };
    case "forecast_update":
      // status ok | suspended | unavailable; only ok carries a forecast to draw
      return { ...snap, forecast: p.status === "ok" ? ((p.forecast ?? null) as Forecast | null) : null };
    case "alarm_state_change":
      return { ...snap, alarm: ((p.alarm ?? p) as unknown) as AlarmState };
    case "settings_change":
      return { ...snap, settings: ((p.settings ?? p) as unknown) as Settings };
    case "mode_change":
      return { ...snap, mode: p.mode as StateSnapshot["mode"] };
    case "presence_change":
      return { ...snap, presence: ((p.presence ?? p) as unknown) as StateSnapshot["presence"] };
    default:
      return snap;
  }
}

export function useDeviceSocket(baseUrl: string | null): DeviceSocket {
  const [snapshot, setSnapshot] = useState<StateSnapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const downSince = useRef<number>(Date.now());
  const isOpen = useRef(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retry = useRef(500);
  const timer = useRef<number | undefined>(undefined);
  const hiddenAt = useRef<number | null>(null);

  const open = useCallback(() => {
    if (!baseUrl) return;
    window.clearTimeout(timer.current);
    wsRef.current?.close();
    const ws = new WebSocket(baseUrl.replace(/^http/, "ws") + "/ws");
    wsRef.current = ws;
    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      retry.current = 500;
      isOpen.current = true;
      setConnected(true);
    };
    ws.onmessage = (ev) => {
      if (wsRef.current !== ws) return;
      let msg: WSMessage;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (!msg || typeof msg.type !== "string") return; // the hub's echo replies carry no type
      setSnapshot((s) => applyMessage(s, msg));
    };
    ws.onclose = () => {
      if (wsRef.current !== ws) return; // replaced on purpose
      if (isOpen.current) downSince.current = Date.now();
      isOpen.current = false;
      setConnected(false);
      timer.current = window.setTimeout(open, retry.current);
      retry.current = Math.min(retry.current * 2, 5000);
    };
    ws.onerror = () => ws.close();
  }, [baseUrl]);

  useEffect(() => {
    if (!baseUrl) return;
    downSince.current = Date.now();
    open();
    const onVisible = () => {
      if (document.visibilityState === "hidden") {
        hiddenAt.current = Date.now();
        return;
      }
      const away = hiddenAt.current ? Date.now() - hiddenAt.current : 0;
      hiddenAt.current = null;
      const ws = wsRef.current;
      // After a lock/unlock the old socket may be dead without knowing it: reopen for a fresh snapshot.
      if (!ws || ws.readyState !== WebSocket.OPEN || away > HIDDEN_RECONNECT_MS) open();
    };
    const onOnline = () => open();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onOnline);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onOnline);
      window.clearTimeout(timer.current);
      const ws = wsRef.current;
      wsRef.current = null;
      ws?.close();
    };
  }, [baseUrl, open]);

  // tick so the 15 s banner appears without a message arriving
  useEffect(() => {
    if (connected) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [connected]);

  return {
    snapshot,
    connected,
    disconnectedLong: !!baseUrl && !connected && now - downSince.current > DISCONNECTED_BANNER_MS,
  };
}
