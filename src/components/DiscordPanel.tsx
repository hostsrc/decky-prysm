import { useState } from "react";
import {
  ButtonItem,
  PanelSectionRow,
  DropdownItem,
} from "@decky/ui";
import { toaster } from "@decky/api";
import {
  discordLaunch,
  discordGoLive,
  discordStopLive,
  type PrysmStatus,
} from "../lib/backend";

interface DiscordPanelProps {
  status: PrysmStatus;
  onRefresh: () => void;
}

const DISCORD_BLUE = "#5865F2";

export function DiscordPanel({ status, onRefresh }: DiscordPanelProps) {
  const [busy, setBusy] = useState(false);
  const isLive = status.mode === "discord";

  const handleLaunch = async () => {
    setBusy(true);
    try {
      const result = await discordLaunch();
      if (result.success) {
        toaster.toast({ title: "Prysm", body: "Discord connected!" });
      } else {
        toaster.toast({ title: "Prysm", body: result.error ?? "Failed to launch Discord" });
      }
      onRefresh();
    } finally {
      setBusy(false);
    }
  };

  const handleGoLive = async () => {
    setBusy(true);
    try {
      const result = await discordGoLive();
      if (result.success) {
        toaster.toast({ title: "Prysm", body: "Go Live started!" });
      } else {
        toaster.toast({ title: "Prysm", body: result.error ?? "Failed to start Go Live" });
      }
      onRefresh();
    } finally {
      setBusy(false);
    }
  };

  const handleStopLive = async () => {
    setBusy(true);
    try {
      await discordStopLive();
      toaster.toast({ title: "Prysm", body: "Stream stopped" });
      onRefresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {/* Connection status */}
      <PanelSectionRow>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px" }}>
          <div
            style={{
              width: "6px",
              height: "6px",
              borderRadius: "50%",
              background: status.discord_running ? "#22c55e" : "#ef4444",
            }}
          />
          <span style={{ opacity: 0.7 }}>
            {status.discord_running
              ? status.discord_ipc
                ? "Discord connected"
                : "Discord running (IPC pending)"
              : "Discord not running"}
          </span>
        </div>
      </PanelSectionRow>

      {/* Launch button */}
      {!status.discord_running && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={handleLaunch}
            disabled={busy}
          >
            {busy ? "Launching..." : "Launch Discord"}
          </ButtonItem>
        </PanelSectionRow>
      )}

      {/* Go Live / Stop */}
      {status.discord_running && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={isLive ? handleStopLive : handleGoLive}
            disabled={busy}
          >
            {busy
              ? "Working..."
              : isLive
                ? "Stop Go Live"
                : "Start Go Live"}
          </ButtonItem>
        </PanelSectionRow>
      )}

      {/* Quality dropdown - placeholder for future voice channel selector */}
      {status.discord_running && !isLive && (
        <PanelSectionRow>
          <DropdownItem
            label="Voice Channel"
            rgOptions={[
              { data: "auto", label: "Auto-detect current" },
            ]}
            selectedOption="auto"
            onChange={() => {}}
          />
        </PanelSectionRow>
      )}
    </>
  );
}
