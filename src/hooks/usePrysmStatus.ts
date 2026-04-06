import { useState, useEffect, useCallback, useRef } from "react";
import { addEventListener } from "@decky/api";
import { getStatus, type PrysmStatus, type StreamMode } from "../lib/backend";

const POLL_INTERVAL = 3000;

export function usePrysmStatus() {
  const [status, setStatus] = useState<PrysmStatus>({
    mode: "idle",
    discord_running: false,
    discord_ipc: false,
    viewer_running: false,
    viewer_url: "",
    capture_running: false,
  });
  const [loading, setLoading] = useState(true);
  const intervalRef = useRef<ReturnType<typeof setInterval>>(undefined);

  const refresh = useCallback(async () => {
    try {
      const s = await getStatus();
      setStatus(s);
    } catch {
      // Backend not ready yet
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    intervalRef.current = setInterval(refresh, POLL_INTERVAL);

    // addEventListener returns a function that can unsubscribe
    const unsubscribe = addEventListener<[string]>("mode_changed", (_mode: string) => {
      refresh();
    });

    return () => {
      clearInterval(intervalRef.current);
      if (typeof unsubscribe === "function") {
        unsubscribe("");
      }
    };
  }, [refresh]);

  return { status, loading, refresh };
}
