import { create } from "zustand";
import type { Session, Message, MessageResponse } from "../types";
import { MimoClient } from "../api/mimoClient";

interface StreamState {
  loading: boolean;
  abortController: AbortController | null;
  currentText: string;
  phase?: "sending" | "reasoning" | "text";
}

interface SessionState {
  sessions: Session[];
  sessionsLoading: boolean;
  activeSessionId: string | null;

  messages: Record<string, Message[]>;
  messagesLoading: boolean;

  stream: StreamState;
  toasts: { id: number; message: string; severity: "error" | "success" | "info" }[];

  loadSessions: () => Promise<void>;
  createSession: () => Promise<Session | null>;
  ensureSession: () => Promise<string | null>;
  setActiveSession: (sessionId: string) => Promise<void>;
  loadMessages: (sessionId: string) => Promise<void>;
  sendMessage: (text: string, retryCount?: number) => Promise<void>;
  cancelStream: () => void;
  deleteSession: (sessionId: string) => Promise<void>;
  deleteMessage: (sessionId: string, messageId: string) => Promise<void>;
  deleteAllSessions: () => Promise<void>;
  abortSession: (sessionId: string) => Promise<void>;
  addMessageToSession: (sessionId: string, message: Message) => void;
  renameSession: (sessionId: string, title: string) => Promise<void>;
  notify: (message: string, severity?: "error" | "success" | "info") => void;
  dismissToast: (id: number) => void;
}

let toastId = 0;

function sortSessions(sessions: Session[]): Session[] {
  return [...sessions].sort((a, b) => {
    const ta = a.time?.updated || a.time?.created || 0;
    const tb = b.time?.updated || b.time?.created || 0;
    return tb - ta;
  });
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: [],
  sessionsLoading: false,
  activeSessionId: null,
  messages: {},
  messagesLoading: false,
  stream: { loading: false, abortController: null, currentText: "" },
  toasts: [],

  loadSessions: async () => {
    set({ sessionsLoading: true });
    try {
      const sessions = await MimoClient.listSessions();
      set({ sessions: sortSessions(Array.isArray(sessions) ? sessions : []), sessionsLoading: false });
    } catch { set({ sessions: [], sessionsLoading: false }); }
  },

  createSession: async () => {
    try {
      const session = await MimoClient.createSession();
      if (!session) return null;
      set((state) => ({
        sessions: sortSessions([session, ...state.sessions.filter((s) => s.id !== session.id)]),
        activeSessionId: session.id,
      }));
      return session;
    } catch { return null; }
  },

  ensureSession: async () => {
    const { activeSessionId, sessions } = get();
    if (activeSessionId) return activeSessionId;
    if (sessions.length > 0) {
      const first = sessions[0];
      set({ activeSessionId: first.id });
      return first.id;
    }
    const session = await get().createSession();
    return session?.id || null;
  },

  setActiveSession: async (sessionId: string) => {
    set({ activeSessionId: sessionId });
    const { messages } = get();
    if (!messages[sessionId]) {
      await get().loadMessages(sessionId);
    }
  },

  loadMessages: async (sessionId: string) => {
    set({ messagesLoading: true });
    try {
      const msgs = await MimoClient.getSessionMessages(sessionId);
      set((state) => ({
        messages: { ...state.messages, [sessionId]: Array.isArray(msgs) ? msgs : [] },
        messagesLoading: false,
      }));
    } catch {
      set((state) => ({ messages: { ...state.messages, [sessionId]: [] }, messagesLoading: false }));
    }
  },

  sendMessage: async (text: string, retryCount = 0) => {
    let sessionId = get().activeSessionId;
    if (!sessionId) {
      const session = await get().createSession();
      if (!session) return;
      sessionId = session.id;
    }

    const userMsg: Message = { info: { id: `u-${Date.now()}`, role: "user" }, parts: [{ type: "text", text }] };
    const assistantMsg: Message = { info: { id: `s-${Date.now()}`, role: "assistant" }, parts: [{ type: "text", text: "" }] };
    const controller = new AbortController();

    set((state) => {
      const existing = state.messages[sessionId!] || [];
      return {
        activeSessionId: sessionId,
        messages: { ...state.messages, [sessionId!]: [...existing, userMsg, assistantMsg] },
        stream: { loading: true, abortController: controller, currentText: "", phase: "sending" as const },
      };
    });

    try {
      const response = await MimoClient.sendMessageStream(
        sessionId!, text, controller.signal,
        (newChars) => {
          set((state) => {
            const newText = state.stream.currentText + newChars;
            const msgs = state.messages[sessionId!] || [];
            return {
              messages: { ...state.messages, [sessionId!]: msgs.map((m) =>
                m.info.id === assistantMsg.info.id ? { ...m, parts: [{ type: "text" as const, text: newText }] } : m
              )},
              stream: { ...state.stream, currentText: newText, phase: "text" as const },
            };
          });
        },
        (reasoningChars) => {
          // Update reasoning phase
          set((state) => ({
            stream: { ...state.stream, phase: "reasoning" as const },
          }));
        }
      );
      set((state) => {
        const existing = state.messages[sessionId!] || [];
        return {
          messages: { ...state.messages, [sessionId!]: [...existing.filter((m) => m.info.id !== assistantMsg.info.id), { info: response.info, parts: response.parts }] },
          stream: { loading: false, abortController: null, currentText: "", phase: undefined },
        };
      });
      // Auto-update session title if it's still default
      const sessions = get().sessions;
      const currentSession = sessions.find(s => s.id === sessionId);
      if (currentSession && currentSession.title?.startsWith("New session")) {
        const autoTitle = text.slice(0, 20) + (text.length > 20 ? "..." : "");
        try {
          await MimoClient.updateSessionTitle(sessionId!, autoTitle);
        } catch {
          // ignore title update failure
        }
      }
      get().loadSessions(); // refresh session list for title updates
    } catch (err) {
      const isAbort = err instanceof DOMException && err.name === "AbortError";
      const errorMsg = (err as Error).message || "";
      const isBusy = errorMsg.includes("busy") || errorMsg.includes("Session is busy");

      // Auto-retry if session is busy (max 2 retries)
      if (isBusy && retryCount < 2) {
        try {
          await MimoClient.abortSession(sessionId!);
          // Remove the user message we added, then retry
          set((state) => {
            const existing = state.messages[sessionId!] || [];
            return {
              messages: { ...state.messages, [sessionId!]: existing.filter((m) => m.info.id !== userMsg.info.id && m.info.id !== assistantMsg.info.id) },
              stream: { loading: false, abortController: null, currentText: "", phase: undefined },
            };
          });
          get().notify("Session was busy, retrying...", "info");
          // Wait a moment then retry
          await new Promise(resolve => setTimeout(resolve, 1000));
          return get().sendMessage(text, retryCount + 1);
        } catch {
          // If abort fails, fall through to error handling
        }
      }

      const finalText = isAbort ? (get().stream.currentText || "") : `Error: ${errorMsg}`;
      set((state) => {
        const existing = state.messages[sessionId!] || [];
        return {
          messages: { ...state.messages, [sessionId!]: [...existing.filter((m) => m.info.id !== assistantMsg.info.id), { info: { id: isAbort ? `c-${Date.now()}` : `e-${Date.now()}`, role: "assistant" }, parts: [{ type: "text", text: finalText || (isAbort ? "[Cancelled]" : "") }] }] },
          stream: { loading: false, abortController: null, currentText: "", phase: undefined },
        };
      });
      if (!isAbort) get().notify(`Send failed: ${errorMsg}`, "error");
    }
  },

  cancelStream: () => {
    const { stream } = get();
    if (stream.abortController) stream.abortController.abort();
  },

  addMessageToSession: (sessionId: string, message: Message) => {
    set((state) => ({ messages: { ...state.messages, [sessionId]: [...(state.messages[sessionId] || []), message] } }));
  },

  deleteSession: async (sessionId: string) => {
    try {
      await MimoClient.deleteSession(sessionId);
      set((state) => {
        const newSessions = state.sessions.filter((s) => s.id !== sessionId);
        const newMessages = { ...state.messages };
        delete newMessages[sessionId];
        return { sessions: newSessions, messages: newMessages, activeSessionId: state.activeSessionId === sessionId ? newSessions[0]?.id || null : state.activeSessionId };
      });
      const { activeSessionId } = get();
      if (activeSessionId) get().loadMessages(activeSessionId);
      get().notify("Session deleted", "info");
    } catch {
      get().notify("Failed to delete session", "error");
    }
  },

  deleteMessage: async (sessionId: string, messageId: string) => {
    try {
      await MimoClient.deleteMessage(sessionId, messageId);
      set((state) => ({ messages: { ...state.messages, [sessionId]: (state.messages[sessionId] || []).filter((m) => m.info.id !== messageId) } }));
    } catch {
      get().notify("Failed to delete message", "error");
    }
  },

  abortSession: async (sessionId: string) => {
    try {
      await MimoClient.abortSession(sessionId);
      get().notify("Session aborted", "info");
    } catch {
      get().notify("Failed to abort session", "error");
    }
  },

  renameSession: async (sessionId: string, title: string) => {
    try {
      await MimoClient.updateSessionTitle(sessionId, title);
      set((state) => ({ sessions: state.sessions.map((s) => s.id === sessionId ? { ...s, title } : s) }));
      get().notify("Session renamed", "success");
    } catch {
      get().notify("Failed to rename session", "error");
    }
  },

  deleteAllSessions: async () => {
    const { sessions } = get();
    let deleted = 0;
    for (const s of sessions) {
      try {
        await MimoClient.deleteSession(s.id);
        deleted++;
      } catch { /* skip failed */ }
    }
    set({ sessions: [], messages: {}, activeSessionId: null });
    get().notify(`Deleted ${deleted} sessions`, "info");
  },

  notify: (message: string, severity: "error" | "success" | "info" = "info") => {
    const id = ++toastId;
    set((state) => ({ toasts: [...state.toasts, { id, message, severity }] }));
    setTimeout(() => get().dismissToast(id), 4000);
  },

  dismissToast: (id: number) => {
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) }));
  },
}));
