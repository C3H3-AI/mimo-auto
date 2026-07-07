/* ===== Session ===== */

export interface Session {
  id: string;
  slug?: string;
  title: string;
  time?: {
    created?: number;
    updated?: number;
  };
}

/* ===== Message ===== */

export interface MessageInfo {
  id: string;
  role: "user" | "assistant" | "system";
  parentID?: string;
  /** Hono response field — when role=assistant, parts contain text */
  finish?: "stop" | "error";
  modelID?: string;
  providerID?: string;
  /** Message timestamps */
  time?: {
    created?: number;
    completed?: number;
  };
  [key: string]: unknown;
}

export interface MessagePart {
  type: string;
  text?: string;
  language?: string;
  fileName?: string;
  mimeType?: string;
  data?: string;
  /** Token usage from step-finish */
  tokens?: {
    total?: number;
    input?: number;
    output?: number;
    reasoning?: number;
    cache?: {
      write?: number;
      read?: number;
    };
  };
  /** Hono response may include step-start, reasoning, step-finish etc */
  [key: string]: unknown;
}

export interface Message {
  info: MessageInfo;
  parts: MessagePart[];
}

export interface MessageResponse {
  info: MessageInfo;
  parts: MessagePart[];
}

/* ===== Config ===== */

export interface AppConfig {
  model?: string;
  [key: string]: unknown;
}

/* ===== Provider ===== */

export interface Provider {
  id: string;
  name: string;
  models?: Record<string, unknown>;
  status?: string;
  auth_url?: string;
  [key: string]: unknown;
}

export interface ProviderList {
  all: Provider[];
  connected: string[];
}

/* ===== Agent ===== */

export interface Agent {
  name: string;
  slug?: string;
  description?: string;
}

/* ===== Command ===== */

export interface Command {
  name: string;
  command?: string;
  description?: string;
}

/* ===== Skill ===== */

export interface Skill {
  name: string;
  description?: string;
}

/* ===== File Browser ===== */

export interface FileEntry {
  name: string;
  path: string;
  type: "file" | "directory";
  size?: number;
  modified?: number;
}

/* ===== UI State Types ===== */

export type ThemeMode = "light" | "dark" | "system";
