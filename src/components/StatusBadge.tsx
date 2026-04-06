import type { StreamMode } from "../lib/backend";

interface StatusBadgeProps {
  mode: StreamMode;
}

const MODE_LABELS: Record<StreamMode, string> = {
  idle: "Ready",
  discord: "Discord Go Live",
  viewer: "Prysm Viewer",
};

const MODE_COLORS: Record<StreamMode, string> = {
  idle: "#6b7280",
  discord: "#5865F2",
  viewer: "#a855f7",
};

export function StatusBadge({ mode }: StatusBadgeProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        padding: "8px 12px",
        borderRadius: "8px",
        background: `${MODE_COLORS[mode]}22`,
        border: `1px solid ${MODE_COLORS[mode]}44`,
      }}
    >
      <div
        style={{
          width: "8px",
          height: "8px",
          borderRadius: "50%",
          background: MODE_COLORS[mode],
          boxShadow: mode !== "idle" ? `0 0 8px ${MODE_COLORS[mode]}` : "none",
          animation: mode !== "idle" ? "pulse 2s infinite" : "none",
        }}
      />
      <span style={{ fontSize: "13px", fontWeight: 600, color: MODE_COLORS[mode] }}>
        {MODE_LABELS[mode]}
      </span>
    </div>
  );
}
