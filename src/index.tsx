import { useState, useEffect, useCallback } from "react";
import { definePlugin } from "@decky/api";
import { PanelSection, PanelSectionRow, ButtonItem } from "@decky/ui";
import { FaProjectDiagram } from "react-icons/fa";

import { StatusBadge } from "./components/StatusBadge";
import { DiscordPanel } from "./components/DiscordPanel";
import { ViewerPanel } from "./components/ViewerPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { usePrysmStatus } from "./hooks/usePrysmStatus";
import { getSettings, stopAll, type PrysmSettings } from "./lib/backend";

type Tab = "discord" | "viewer" | "settings";

function PrysmRoot() {
  const { status, loading, refresh } = usePrysmStatus();
  const [tab, setTab] = useState<Tab>("discord");
  const [settings, setSettings] = useState<PrysmSettings | null>(null);

  const refreshSettings = useCallback(async () => {
    try {
      const s = await getSettings();
      setSettings(s);
    } catch {
      // Backend not ready
    }
  }, []);

  useEffect(() => {
    refreshSettings();
  }, [refreshSettings]);

  if (loading || !settings) {
    return (
      <PanelSection title="Prysm">
        <PanelSectionRow>
          <div style={{ textAlign: "center", padding: "20px", opacity: 0.5 }}>
            Loading...
          </div>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  return (
    <>
      {/* Header with status */}
      <PanelSection title="PRYSM">
        <PanelSectionRow>
          <StatusBadge mode={status.mode} />
        </PanelSectionRow>

        {/* Tab switcher */}
        <PanelSectionRow>
          <div style={{ display: "flex", gap: "4px", width: "100%" }}>
            {(["discord", "viewer", "settings"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                style={{
                  flex: 1,
                  padding: "8px 4px",
                  fontSize: "12px",
                  fontWeight: tab === t ? 700 : 400,
                  background: tab === t ? "#ffffff15" : "transparent",
                  border: tab === t ? "1px solid #ffffff22" : "1px solid transparent",
                  borderRadius: "6px",
                  color: tab === t ? "#fff" : "#ffffff88",
                  cursor: "pointer",
                  textTransform: "capitalize",
                  transition: "all 0.15s ease",
                }}
              >
                {t === "discord" ? "Discord" : t === "viewer" ? "Viewer" : "Settings"}
              </button>
            ))}
          </div>
        </PanelSectionRow>
      </PanelSection>

      {/* Active tab content */}
      <PanelSection>
        {tab === "discord" && (
          <DiscordPanel status={status} onRefresh={refresh} />
        )}

        {tab === "viewer" && (
          <ViewerPanel
            status={status}
            settings={settings}
            onRefresh={refresh}
            onSettingsRefresh={refreshSettings}
          />
        )}

        {tab === "settings" && (
          <SettingsPanel settings={settings} onRefresh={refreshSettings} />
        )}
      </PanelSection>

      {/* Emergency stop */}
      {status.mode !== "idle" && (
        <PanelSection>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={async () => {
                await stopAll();
                refresh();
              }}
            >
              Stop Everything
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      )}
    </>
  );
}

export default definePlugin(() => ({
  name: "Prysm",
  content: <PrysmRoot />,
  icon: <FaProjectDiagram />,
  titleView: (
    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
      <FaProjectDiagram />
      <span>Prysm</span>
    </div>
  ),
  onDismount() {
    // Cleanup if needed — streams continue running in background
  },
}));
