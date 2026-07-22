import type {
  Session,
  Message,
  MessageResponse,
  AppConfig,
  ProviderList,
  Agent,
  Command,
  Skill,
} from "../types";

// Build base URL: detect HA ingress proxy path from window.location
// When accessed via HA Supervisor ingress, the URL is like:
//   /api/hassio_ingress/XXXX/
// So API calls must be:
//   /api/hassio_ingress/XXXX/api/session
// The HA Supervisor proxy forwards /api/hassio_ingress/XXXX/ -> http://127.0.0.1:8099/
// Then server.py proxies /api/* -> http://127.0.0.1:14095/*
let BASE_URL = "/api";
if (typeof window !== "undefined") {
  const path = window.location.pathname.replace(/\/+$/, "");
  // Check if we're behind an ingress proxy (path has extra segments before /api)
  if (path !== "" && !path.endsWith("/api")) {
    BASE_URL = path + "/api";
  }
}
console.log("[MiMo SPA] BASE_URL:", BASE_URL);

/** Re-export BASE_URL so channel/wechat components can use it too. */
export const API_BASE_URL = BASE_URL;

class MimoApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "MimoApiError";
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...options.headers,
    },
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new MimoApiError(res.status, text.length > 200 ? text.slice(0, 200) : text);
  }

  const text = await res.text();
  if (!text) return null as T;
  return JSON.parse(text) as T;
}

export const MimoClient = {
  /* ===== Session ===== */

  async listSessions(): Promise<Session[]> {
    return request<Session[]>("/session");
  },

  async createSession(): Promise<Session> {
    return request<Session>("/session", { method: "POST", body: "{}" });
  },

  async getSessionMessages(sessionId: string): Promise<Message[]> {
    return request<Message[]>(`/session/${sessionId}/message`);
  },

  /**
   * Send a message and receive streaming JSON response.
   * mimo serve returns a single JSON object with chunked transfer encoding.
   * We progressively parse it to show typing effect.
   */
  async sendMessageStream(
    sessionId: string,
    message: string,
    signal?: AbortSignal,
    onText?: (text: string) => void,
    onReasoning?: (text: string) => void
  ): Promise<MessageResponse> {
    const url = `${BASE_URL}/session/${sessionId}/message`;
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message,
        parts: [{ type: "text", text: message }],
      }),
      signal,
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "Unknown error");
      throw new MimoApiError(res.status, text);
    }

    const reader = res.body?.getReader();
    if (!reader) {
      throw new MimoApiError(0, "Response body is not readable");
    }

    const decoder = new TextDecoder();
    let fullText = "";
    let lastTextLength = 0;
    let lastReasoningLength = 0;

    // Try to parse accumulated JSON progressively for typing effect
    const tryExtractProgress = (buf: string) => {
      try {
        const obj = JSON.parse(buf);
        if (obj && obj.parts) {
          // Extract text content
          const allText = obj.parts
            .filter((p: { type: string; text?: string }) => p.type === "text")
            .map((p: { text?: string }) => p.text || "")
            .join("\n");
          if (allText.length > lastTextLength) {
            const newChars = allText.slice(lastTextLength);
            lastTextLength = allText.length;
            if (onText) onText(newChars);
          }

          // Extract reasoning content
          const allReasoning = obj.parts
            .filter((p: { type: string; text?: string }) => p.type === "reasoning")
            .map((p: { text?: string }) => p.text || "")
            .join("\n");
          if (allReasoning.length > lastReasoningLength && onReasoning) {
            const newChars = allReasoning.slice(lastReasoningLength);
            lastReasoningLength = allReasoning.length;
            onReasoning(newChars);
          }
        }
      } catch {
        // Not valid JSON yet — keep accumulating
      }
    };

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        // Skip heartbeat whitespace
        if (chunk.trim() === "") continue;
        fullText += chunk;

        // Try progressive parse for typing effect
        tryExtractProgress(fullText);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        throw err;
      }
      throw err;
    } finally {
      reader.releaseLock();
    }

    // Final parse
    return JSON.parse(fullText) as MessageResponse;
  },

  /* ===== Config ===== */

  async getConfig(): Promise<AppConfig> {
    return request<AppConfig>("/config");
  },

  async updateConfig(config: Partial<AppConfig>): Promise<AppConfig> {
    return request<AppConfig>("/config", {
      method: "PATCH",
      body: JSON.stringify(config),
    });
  },

  /* ===== Provider ===== */

  async getProviders(): Promise<ProviderList> {
    return request<ProviderList>("/provider");
  },

  /* ===== Agent ===== */

  async listAgents(): Promise<Agent[]> {
    return request<Agent[]>("/agent");
  },

  /* ===== Command ===== */

  async listCommands(): Promise<Command[]> {
    return request<Command[]>("/command");
  },

  /* ===== Session Management ===== */

  async deleteSession(sessionId: string): Promise<boolean> {
    return request<boolean>(`/session/${sessionId}`, { method: "DELETE" });
  },

  async updateSessionTitle(sessionId: string, title: string): Promise<Session> {
    return request<Session>(`/session/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
  },

  /* ===== Skill ===== */

  async listSkills(): Promise<Skill[]> {
    return request<Skill[]>("/skill");
  },

  /* ===== Files ===== */

  async listFiles(path: string = ""): Promise<any[]> {
    return request(`/file?path=${encodeURIComponent(path)}`);
  },

  async readFile(path: string): Promise<string> {
    const res = await fetch(`${BASE_URL}/file?path=${encodeURIComponent(path)}`);
    return res.text();
  },

  async writeFile(path: string, content: string): Promise<boolean> {
    const res = await fetch(`${BASE_URL}/file?path=${encodeURIComponent(path)}`, {
      method: "PUT",
      headers: { "Content-Type": "text/plain; charset=utf-8" },
      body: content,
    });
    return res.ok;
  },

  /* ===== Filesystem (direct, bypasses mimo workspace restriction) ===== */

  async fsList(path: string = "/"): Promise<any[]> {
    const res = await fetch(`${BASE_URL}/fs/list?path=${encodeURIComponent(path || "/")}`);
    const data = await res.json();
    return (data && data.entries) || [];
  },

  async fsRead(path: string): Promise<string> {
    const res = await fetch(`${BASE_URL}/fs/read?path=${encodeURIComponent(path)}`);
    return res.text();
  },

  async fsWrite(path: string, content: string): Promise<boolean> {
    const res = await fetch(`${BASE_URL}/fs/write?path=${encodeURIComponent(path)}`, {
      method: "PUT",
      body: content,
    });
    return res.ok;
  },

  /* ===== Message Management ===== */

  async deleteMessage(sessionId: string, messageId: string): Promise<boolean> {
    return request<boolean>(`/session/${sessionId}/message/${messageId}`, { method: "DELETE" });
  },

  /* ===== Session Control ===== */

  async abortSession(sessionId: string): Promise<boolean> {
    return request<boolean>(`/session/${sessionId}/abort`, { method: "POST" });
  },

  async executeCommand(sessionId: string, command: string): Promise<MessageResponse> {
    return request<MessageResponse>(`/session/${sessionId}/command`, {
      method: "POST",
      body: JSON.stringify({ command }),
    });
  },
};
