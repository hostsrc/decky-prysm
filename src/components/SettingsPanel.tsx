import {
  PanelSectionRow,
  DropdownItem,
} from "@decky/ui";
import { setSetting, type PrysmSettings } from "../lib/backend";

interface SettingsPanelProps {
  settings: PrysmSettings;
  onRefresh: () => void;
}

export function SettingsPanel({ settings, onRefresh }: SettingsPanelProps) {
  return (
    <>
      {/* Preferred Discord client */}
      <PanelSectionRow>
        <DropdownItem
          label="Discord Client"
          rgOptions={[
            { data: "vesktop", label: "Vesktop (recommended)" },
            { data: "discord", label: "Discord (official)" },
          ]}
          selectedOption={settings.preferred_client ?? "vesktop"}
          onChange={async (opt: { data: string; label: string }) => {
            await setSetting("preferred_client", opt.data);
            onRefresh();
          }}
        />
      </PanelSectionRow>

      {/* Capture method */}
      <PanelSectionRow>
        <DropdownItem
          label="Capture Method"
          rgOptions={[
            { data: "pipewire", label: "PipeWire (default)" },
            { data: "kmsgrab", label: "KMS/DRM (fallback)" },
          ]}
          selectedOption={settings.capture_method ?? "pipewire"}
          onChange={async (opt: { data: string; label: string }) => {
            await setSetting("capture_method", opt.data);
            onRefresh();
          }}
        />
      </PanelSectionRow>

      {/* Info */}
      <PanelSectionRow>
        <div style={{ fontSize: "11px", opacity: 0.5, padding: "4px 0" }}>
          Prysm v0.1.0 — Split your screen everywhere
        </div>
      </PanelSectionRow>
    </>
  );
}
