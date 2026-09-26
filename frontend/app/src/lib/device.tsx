import { createContext, useContext, type ReactNode } from "react";
import { useDeviceSocket, type DeviceSocket } from "./useDeviceSocket";
import { useDeviceTarget, type DeviceTarget } from "./useDeviceTarget";

interface Device {
  target: DeviceTarget;
  socket: DeviceSocket;
}

const DeviceContext = createContext<Device | null>(null);

/** One device connection for the whole app, so every tab can show the DEMO badge. */
export function DeviceProvider({ children }: { children: ReactNode }) {
  const target = useDeviceTarget();
  const socket = useDeviceSocket(target.status === "ready" ? target.url : null);
  return <DeviceContext.Provider value={{ target, socket }}>{children}</DeviceContext.Provider>;
}

export function useDevice(): Device {
  const d = useContext(DeviceContext);
  if (!d) throw new Error("useDevice outside DeviceProvider");
  return d;
}
