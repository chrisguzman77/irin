import { useEffect, useState } from "react";
import { deviceFetch } from "../lib/api";

/** An <audio src> cannot carry the owner bearer, so fetch the clip through
 * deviceFetch and play the object URL. Renders nothing until it loads. */
export default function PiAudio({ base, path, className }: { base: string; path: string; className?: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    let url: string | null = null;
    deviceFetch(base, path)
      .then((r) => (r.ok ? r.blob() : null))
      .then((b) => {
        if (!b || !alive) return;
        url = URL.createObjectURL(b);
        setSrc(url);
      })
      .catch(() => {});
    return () => {
      alive = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [base, path]);
  return src ? <audio controls src={src} className={className} /> : null;
}
