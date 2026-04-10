import { useState, useEffect, useRef } from "react";
import {
  ButtonItem,
  PanelSectionRow,
  DropdownItem,
  ToggleField,
  Field,
} from "@decky/ui";
import { toaster } from "@decky/api";
import {
  viewerStart,
  viewerStop,
  setSetting,
  getStreamStats,
  type PrysmStatus,
  type PrysmSettings,
  type StreamStats,
} from "../lib/backend";

interface ViewerPanelProps {
  status: PrysmStatus;
  settings: PrysmSettings;
  onRefresh: () => void;
  onSettingsRefresh: () => void;
}

const QUALITY_OPTIONS = [
  { data: "480p30", label: "480p 30fps" },
  { data: "720p30", label: "720p 30fps" },
  { data: "720p60", label: "720p 60fps" },
  { data: "1080p30", label: "1080p 30fps" },
  { data: "1080p60", label: "1080p 60fps" },
];

const METHOD_OPTIONS = [
  { data: "mpegts", label: "MPEG-TS (~500ms)" },
  { data: "webrtc", label: "WebRTC (~200ms)" },
];

function formatBytes(bytes: number): string {
  if (bytes > 1e9) return (bytes / 1e9).toFixed(1) + " GB";
  if (bytes > 1e6) return (bytes / 1e6).toFixed(1) + " MB";
  if (bytes > 1e3) return (bytes / 1e3).toFixed(0) + " KB";
  return bytes + " B";
}

export function ViewerPanel({ status, settings, onRefresh, onSettingsRefresh }: ViewerPanelProps) {
  const [busy, setBusy] = useState(false);
  const [stats, setStats] = useState<StreamStats | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const isLive = status.mode === "viewer";

  // Poll stats when streaming
  useEffect(() => {
    if (isLive) {
      const poll = async () => {
        try {
          const s = await getStreamStats();
          setStats(s);
        } catch {
          // ignore
        }
      };
      poll();
      intervalRef.current = setInterval(poll, 3000);
      return () => clearInterval(intervalRef.current);
    } else {
      setStats(null);
    }
  }, [isLive]);

  const handleStart = async () => {
    setBusy(true);
    try {
      const result = await viewerStart();
      if (result.success) {
        toaster.toast({ title: "Prysm", body: result.url ?? "Streaming started" });
      } else {
        toaster.toast({ title: "Prysm", body: result.error ?? "Failed to start" });
      }
      onRefresh();
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    setBusy(true);
    try {
      await viewerStop();
      toaster.toast({ title: "Prysm", body: "Stopped" });
      onRefresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {/* Start / Stop - always first */}
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={isLive ? handleStop : handleStart}
          disabled={busy}
        >
          {busy ? "Working..." : isLive ? "Stop Streaming" : "Start Streaming"}
        </ButtonItem>
      </PanelSectionRow>

      {/* Status */}
      <PanelSectionRow>
        <Field label="Status">
          {isLive ? "Streaming" : "Ready"}
        </Field>
      </PanelSectionRow>

      {/* Live stats when streaming */}
      {isLive && status.viewer_url && (
        <PanelSectionRow>
          <Field label="URL">
            {status.viewer_url}
          </Field>
        </PanelSectionRow>
      )}

      {isLive && stats && (
        <>
          <PanelSectionRow>
            <Field label="Viewers">
              {stats.clients}
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Sent">
              {formatBytes(stats.total_bytes)}
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Encoder">
              {stats.ffmpeg_alive ? "Active" : "Down"} · {stats.quality} · {stats.method?.toUpperCase()}
            </Field>
          </PanelSectionRow>
        </>
      )}

      {/* Settings - only when not streaming */}
      {!isLive && (
        <>
          <PanelSectionRow>
            <DropdownItem
              label="Method"
              rgOptions={METHOD_OPTIONS}
              selectedOption={settings.stream_method ?? "mpegts"}
              onChange={async (opt: { data: string; label: string }) => {
                await setSetting("stream_method", opt.data);
                onSettingsRefresh();
              }}
            />
          </PanelSectionRow>

          <PanelSectionRow>
            <DropdownItem
              label="Quality"
              rgOptions={QUALITY_OPTIONS}
              selectedOption={settings.viewer_quality ?? "720p30"}
              onChange={async (opt: { data: string; label: string }) => {
                await setSetting("viewer_quality", opt.data);
                onSettingsRefresh();
              }}
            />
          </PanelSectionRow>

          <PanelSectionRow>
            <ToggleField
              label="Audio"
              checked={settings.audio_enabled ?? true}
              onChange={async (val: boolean) => {
                await setSetting("audio_enabled", val);
                onSettingsRefresh();
              }}
            />
          </PanelSectionRow>
        </>
      )}
    </>
  );
}
