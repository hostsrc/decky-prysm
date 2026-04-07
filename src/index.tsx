import { useState, useEffect, useCallback } from "react";
import { definePlugin } from "@decky/api";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  DropdownItem,
  Field,
} from "@decky/ui";
import { FaProjectDiagram } from "react-icons/fa";

import { DiscordPanel } from "./components/DiscordPanel";
import { ViewerPanel } from "./components/ViewerPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { usePrysmStatus } from "./hooks/usePrysmStatus";
import { getSettings, stopAll, type PrysmSettings } from "./lib/backend";

type Tab = "viewer" | "discord" | "settings";

const TAB_OPTIONS = [
  { data: "viewer" as Tab, label: "Viewer" },
  { data: "discord" as Tab, label: "Discord" },
  { data: "settings" as Tab, label: "Settings" },
];

const MODE_LABELS: Record<string, string> = {
  idle: "Ready",
  discord: "Discord Go Live",
  viewer: "Streaming",
};

function PrysmRoot() {
  const { status, loading, refresh } = usePrysmStatus();
  const [tab, setTab] = useState<Tab>("viewer");
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
          <Field label="Status">Loading...</Field>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  return (
    <>
      <PanelSection title="PRYSM">
        {/* Status */}
        <PanelSectionRow>
          <Field
            label="Status"
            description={status.viewer_url || undefined}
          >
            {MODE_LABELS[status.mode] ?? status.mode}
          </Field>
        </PanelSectionRow>

        {/* Tab selector */}
        <PanelSectionRow>
          <DropdownItem
            label="Mode"
            rgOptions={TAB_OPTIONS}
            selectedOption={tab}
            onChange={(opt: { data: Tab; label: string }) => setTab(opt.data)}
          />
        </PanelSectionRow>
      </PanelSection>

      {/* Active tab content */}
      <PanelSection>
        {tab === "viewer" && (
          <ViewerPanel
            status={status}
            settings={settings}
            onRefresh={refresh}
            onSettingsRefresh={refreshSettings}
          />
        )}

        {tab === "discord" && (
          <DiscordPanel status={status} onRefresh={refresh} />
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
  onDismount() {},
}));
