import { useState, useEffect, useCallback } from "react";
import { definePlugin } from "@decky/api";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  Field,
} from "@decky/ui";
import { FaProjectDiagram } from "react-icons/fa";

import { ViewerPanel } from "./components/ViewerPanel";
import { usePrysmStatus } from "./hooks/usePrysmStatus";
import { getSettings, stopAll, type PrysmSettings } from "./lib/backend";

function PrysmRoot() {
  const { status, loading, refresh } = usePrysmStatus();
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

  const isLive = status.mode === "viewer";

  return (
    <>
      <PanelSection title="PRYSM">
        <ViewerPanel
          status={status}
          settings={settings}
          onRefresh={refresh}
          onSettingsRefresh={refreshSettings}
        />

        {/* Stop button when streaming */}
        {isLive && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={async () => {
                await stopAll();
                refresh();
              }}
            >
              Stop Streaming
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>
    </>
  );
}

export default definePlugin(() => ({
  name: "Prysm",
  content: <PrysmRoot />,
  icon: <FaProjectDiagram />,
  onDismount() {},
}));
