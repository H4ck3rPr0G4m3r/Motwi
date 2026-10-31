// Shapes returned by the core's local API (core/src/motwi/api/server.py).

export type Kind = "none" | "delete" | "warn" | "timeout" | "ban_request";
export type Ceiling = "delete" | "warn" | "timeout" | "ban" | "always_timeout" | "always_ban";
export type ActionStatus =
  | "suggested"
  | "pending"
  | "done"
  | "failed"
  | "undone"
  | "denied"
  | "ignored"
  | "expired";
export type Source = "blocked_word" | "spam" | "ai";
export type EngineState =
  | "stopped"
  | "connecting"
  | "watching"
  | "suggesting"
  | "paused"
  | "reconnect";
export type TwitchState = "signed_out" | "idle" | "connecting" | "connected" | "reconnect" | "error";
export type AIState = "ok" | "degraded" | "down" | "off";
export type Role = "streamer" | "bot";
export type Slot = "primary" | "backup";

export interface Account {
  login: string;
  user_id: string;
}

export interface ProviderView {
  provider: "groq" | "gemini" | "openrouter" | "custom";
  model: string;
  base_url: string | null;
  has_key: boolean;
  key_from_env: boolean;
}

export interface Status {
  version: string;
  setup_complete: boolean;
  accounts: { streamer: Account | null; bot: Account | null };
  moderator: string | null;
  twitch: { state: TwitchState; detail: string };
  stream: { live: boolean; title: string | null; game: string | null; stream_id: string | null };
  engine: EngineState;
  suggest_only: boolean;
  paused: boolean;
  ai: { state: AIState; detail: string; primary: ProviderView | null; backup: ProviderView | null };
  month_cost: number;
  open_requests: number;
  update: { available: boolean; version: string | null; url: string | null };
}

export interface Action {
  id: number;
  created_at: number;
  updated_at: number;
  stream_id: string | null;
  user_id: string;
  user_login: string;
  message_id: string;
  message_text: string;
  context: string[];
  source: Source;
  rule_id: string | null;
  score: number | null;
  reason: string;
  kind: Kind;
  delete: boolean;
  duration_s: number | null;
  ladder_index: number | null;
  status: ActionStatus;
  actor: string | null;
  hold_applied: boolean;
  error: string | null;
  level: number | null;
}

export interface Rule {
  id: string;
  text: string;
  ceiling: Ceiling;
  scope: "community" | "stream";
}

export interface BlockedWord {
  id: string;
  phrase: string;
  base_index: number;
  ceiling: Ceiling;
  hidden: boolean;
}

export interface LadderStep {
  kind: "warn" | "timeout" | "ban_request";
  duration_s: number | null;
}

export interface Settings {
  strictness: "relaxed" | "standard" | "strict" | "custom";
  custom_thresholds: { warn: number; timeout: number; ban: number } | null;
  ladder: LadderStep[];
  vip_leeway: number;
  suggest_only: boolean;
  hold_timeout_enabled: boolean;
  hold_timeout_s: number;
  warn_style: "twitch" | "chat";
  delete_on_warn: boolean;
  retention_days: number;
  setup_complete: boolean;
  paused: boolean;
  check_updates: boolean;
  [key: string]: unknown;
}

export interface KindTotal {
  count: number;
  undone: number;
  denied: number;
  failed: number;
  suggested: number;
}

export type Totals = Record<"delete" | "warn" | "timeout" | "ban_request", KindTotal>;

export interface StreamInfo {
  id: string;
  started_at: number;
  ended_at: number | null;
  title: string | null;
  game: string | null;
}

export interface Warning {
  kind: string;
  level: "info" | "warn" | "error";
  text: string;
  url?: string;
}

export interface Recap {
  stream_id: string;
  title: string | null;
  game: string | null;
  started_at: number;
  ended_at: number;
  seen_ending: boolean;
  checked: number;
  unchecked: number;
  actions: Totals;
  triggers: { label: string; count: number }[];
  escalated: { login: string; actions: number; kind: Kind; duration_s: number | null }[];
  cost: number;
  ai_requests: number;
  outages: { state: "down" | "degraded"; detail: string; started_at: number; ended_at: number }[];
}

export interface UpdateView {
  state: "idle" | "off" | "current" | "available" | "unpublished" | "failed";
  latest: string | null;
  url: string | null;
  notes: string;
  detail: string;
  checked_at: number | null;
}

export interface AppInfo {
  version: string;
  autostart: { available: boolean; enabled: boolean };
  update: UpdateView;
}

export interface Overview {
  today: { checked: number; actions: Totals; cost: number; unchecked: number };
  open_requests: Action[];
  recent: Action[];
  warnings: Warning[];
  last_stream: StreamInfo | null;
  last_recap: Recap | null;
}

export interface DayRow {
  day: string;
  delete: number;
  warn: number;
  timeout: number;
  ban_request: number;
}

export interface Stats {
  range: string;
  empty: boolean;
  stream: StreamInfo | null;
  checked?: number;
  streams?: number;
  totals?: Totals;
  per_day?: DayRow[];
  triggers?: { label: string; count: number }[];
  cost?: number;
  ai_requests?: number;
}

export interface People {
  trusted: string[];
  vip: string[];
  vip_leeway: number;
  recent: { user_id: string; login: string; actions: number; last_at: number; last_kind: Kind }[];
}

export interface Banned {
  user_id: string;
  login: string;
  expires_at: string | null;
  reason: string;
  moderator: string | null;
  created_at: string | null;
}

export interface AIConfig {
  primary: ProviderView | null;
  backup: ProviderView | null;
  providers: Record<string, { base_url: string | null; default_model: string | null }>;
}

export interface TryResult {
  source?: Source;
  score?: number | null;
  rule?: { id: string; text: string } | null;
  reason?: string;
  action?: { kind: Kind; duration_s: number | null };
  error?: string;
}

export interface ExtensionClient {
  id: string;
  label: string;
  created_at: number;
  last_seen: number | null;
  connected: boolean;
}

export interface ModClient extends ExtensionClient {
  user_id: string;
  login: string;
}

export interface ModInvite {
  id: string;
  /** Null until the tunnel has an address. */
  url: string | null;
  expires_at: number;
}

export interface ModsView {
  can_invite: boolean;
  tunnel: { state: "off" | "starting" | "up" | "failed"; detail: string };
  link: string | null;
  needs_sharing: boolean;
  invites: ModInvite[];
  mods: ModClient[];
  check_problem: string;
}

export interface ExtensionInfo {
  clients: ExtensionClient[];
  folder: string | null;
}

export interface DeviceCode {
  user_code: string;
  verification_uri: string;
  expires_in: number;
}
