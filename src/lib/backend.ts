/**
 * Typed wrappers around Prysm's Python backend methods.
 * Each function maps to an `async def` on the Plugin class in main.py.
 */

import { callable } from "@decky/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type StreamMode = "discord" | "viewer" | "idle";

export interface PrysmStatus {
  mode: StreamMode;
  discord_running: boolean;
  discord_ipc: boolean;
  viewer_running: boolean;
  viewer_url: string;
  capture_running: boolean;
}

export interface PrysmSettings {
  preferred_client: "vesktop" | "discord";
  auto_join_channel: string;
  viewer_quality: string;
  viewer_bitrate: number;
  viewer_password: string;
  capture_method: "pipewire" | "kmsgrab";
  stream_method: "mpegts" | "webrtc";
  audio_enabled: boolean;
  last_voice_channel_id: string;
}

export interface ActionResult {
  success: boolean;
  error?: string;
  url?: string;
  data?: Record<string, unknown>;
}

export interface NetworkInfo {
  ip: string;
  viewer_port: number;
  viewer_url: string;
}

// ---------------------------------------------------------------------------
// Backend callables
// ---------------------------------------------------------------------------

// Status
export const getStatus = callable<[], PrysmStatus>("get_status");
export const getSettings = callable<[], PrysmSettings>("get_settings");
export const setSetting = callable<[string, unknown], void>("set_setting");

// Discord Go Live
export const discordLaunch = callable<[], ActionResult>("discord_launch");
export const discordGetVoice = callable<[], ActionResult>("discord_get_voice");
export const discordGoLive = callable<[], ActionResult>("discord_go_live");
export const discordStopLive = callable<[], ActionResult>("discord_stop_live");

// Prysm Viewer
export const viewerStart = callable<[], ActionResult>("viewer_start");
export const viewerStop = callable<[], ActionResult>("viewer_stop");
export const viewerGetUrl = callable<[], string>("viewer_get_url");

// Shared
export const stopAll = callable<[], ActionResult>("stop_all");
export const getNetworkInfo = callable<[], NetworkInfo>("get_network_info");
