import { useState } from "react";
import {
  ButtonItem,
  PanelSectionRow,
  DropdownItem,
  ToggleField,
} from "@decky/ui";
import { toaster, addEventListener } from "@decky/api";
import {
  viewerStart,
  viewerStop,
  setSetting,
  type PrysmStatus,
  type PrysmSettings,
} from "../lib/backend";

interface ViewerPanelProps {
  status: PrysmStatus;
  settings: PrysmSettings;
  onRefresh: () => void;
  onSettingsRefresh: () => void;
}

const QUALITY_OPTIONS = [
  { data: "480p30", label: "480p 30fps (Low)" },
  { data: "720p30", label: "720p 30fps (Balanced)" },
  { data: "720p60", label: "720p 60fps (Smooth)" },
  { data: "1080p30", label: "1080p 30fps (HD)" },
  { data: "1080p60", label: "1080p 60fps (Best)" },
];

const PRYSM_PURPLE = "#a855f7";

const STREAM_METHOD_OPTIONS = [
  { data: "mpegts", label: "MPEG-TS (Stable, ~500ms)" },
  { data: "webrtc", label: "WebRTC (Low latency, ~200ms)" },
];

export function ViewerPanel({ status, settings, onRefresh, onSettingsRefresh }: ViewerPanelProps) {
  const [busy, setBusy] = useState(false);
  const isLive = status.mode === "viewer";

  const handleStart = async () => {
    setBusy(true);
    try {
      const result = await viewerStart();
      if (result.success) {
        toaster.toast({
          title: "Prysm Viewer Live!",
          body: result.url ?? "Open the URL on any device",
        });
      } else {
        toaster.toast({ title: "Prysm", body: result.error ?? "Failed to start viewer" });
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
      toaster.toast({ title: "Prysm", body: "Viewer stopped" });
      onRefresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {/* Viewer URL shown in status field in index.tsx */}
      {false && (
        <PanelSectionRow></PanelSectionRow>
      )}

      {/* Start / Stop button */}
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={isLive ? handleStop : handleStart}
          disabled={busy}
        >
          {busy
            ? "Working..."
            : isLive
              ? "Stop Viewer"
              : "Start Prysm Viewer"}
        </ButtonItem>
      </PanelSectionRow>

      {/* Quality preset */}
      {!isLive && (
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
      )}

      {/* Stream method */}
      {!isLive && (
        <PanelSectionRow>
          <DropdownItem
            label="Stream Method"
            rgOptions={STREAM_METHOD_OPTIONS}
            selectedOption={settings.stream_method ?? "mpegts"}
            onChange={async (opt: { data: string; label: string }) => {
              await setSetting("stream_method", opt.data);
              onSettingsRefresh();
            }}
          />
        </PanelSectionRow>
      )}

      {/* Audio toggle */}
      {!isLive && (
        <PanelSectionRow>
          <ToggleField
            label="Include Audio"
            checked={settings.audio_enabled ?? true}
            onChange={async (val: boolean) => {
              await setSetting("audio_enabled", val);
              onSettingsRefresh();
            }}
          />
        </PanelSectionRow>
      )}
    </>
  );
}
