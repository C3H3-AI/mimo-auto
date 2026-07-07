import { useState, useRef, useCallback, useEffect } from "react";
import {
  Box,
  TextField,
  IconButton,
  Select,
  MenuItem,
  Tooltip,
  Chip,
  Typography,
} from "@mui/material";
import SendIcon from "@mui/icons-material/Send";
import StopIcon from "@mui/icons-material/Stop";
import PsychologyIcon from "@mui/icons-material/Psychology";
import BuildIcon from "@mui/icons-material/Build";
import VisibilityIcon from "@mui/icons-material/Visibility";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import { useSessionStore } from "../store/sessionStore";
import { useConfigStore } from "../store/configStore";
import { useUiStore } from "../store/uiStore";

const AGENT_ICONS: Record<string, React.ReactNode> = {
  build: <BuildIcon sx={{ fontSize: 16 }} />,
  plan: <VisibilityIcon sx={{ fontSize: 16 }} />,
  compose: <AccountTreeIcon sx={{ fontSize: 16 }} />,
};

const AGENT_COLORS: Record<string, string> = {
  build: "#4caf50",
  plan: "#2196f3",
  compose: "#9c27b0",
};

export function ChatInput() {
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const streamLoading = useSessionStore((s) => s.stream.loading);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const sessions = useSessionStore((s) => s.sessions);
  const sendMessage = useSessionStore((s) => s.sendMessage);
  const cancelStream = useSessionStore((s) => s.cancelStream);
  const ensureSession = useSessionStore((s) => s.ensureSession);

  const agents = useConfigStore((s) => s.agents);
  const currentAgent = useConfigStore((s) => s.currentAgent);
  const setCurrentAgent = useConfigStore((s) => s.setCurrentAgent);

  const setCommandPaletteOpen = useUiStore((s) => s.setCommandPaletteOpen);

  // Focus input on mount and after session switch
  useEffect(() => {
    // Small delay to let DOM render first
    const t = setTimeout(() => inputRef.current?.focus(), 100);
    return () => clearTimeout(t);
  }, [activeSessionId, sessions.length]);

  // Handle retry/regenerate events from MessageBubble
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (typeof detail === "string") {
        setText(detail);
      } else if (detail?.type === "retry" || detail?.type === "regenerate") {
        // Directly resend last user message without showing in input
        const state = useSessionStore.getState();
        const messages = state.messages[state.activeSessionId || ""];
        if (messages) {
          const lastUserMsg = [...messages].reverse().find(m => m.info.role === "user");
          if (lastUserMsg) {
            const userText = lastUserMsg.parts.filter(p => p.type === "text").map(p => p.text).join("\n");
            // Send directly without setting input text
            state.sendMessage(userText);
          }
        }
      }
    };
    window.addEventListener("mimo-retry", handler);
    return () => window.removeEventListener("mimo-retry", handler);
  }, []);

  const handleSend = useCallback(async () => {
    const trimmed = text.trim();
    if (!trimmed || streamLoading) return;

    if (!activeSessionId) {
      const sid = await ensureSession();
      if (!sid) return;
    }

    sendMessage(trimmed);
    setText("");
  }, [text, activeSessionId, streamLoading, sendMessage, ensureSession]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      // Ctrl+K / Cmd+K — open command palette
      if (e.key === "k" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        e.stopPropagation();
        setCommandPaletteOpen(true);
        return;
      }
      // Enter to send, Shift+Enter for newline
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend, setCommandPaletteOpen]
  );

  return (
    <Box className="border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-4 py-3 flex-shrink-0">
      <Box className="max-w-4xl mx-auto">
        {/* Agent selector bar */}
        <Box className="flex items-center gap-2 mb-2">
          <PsychologyIcon sx={{ fontSize: 16, color: "text.secondary" }} />
          <Chip
            icon={<BuildIcon sx={{ fontSize: 16 }} />}
            label={currentAgent}
            size="small"
            sx={{
              bgcolor: AGENT_COLORS[currentAgent] || "#666",
              color: "white",
              fontWeight: 500,
              "& .MuiChip-icon": { color: "white" },
            }}
          />
          <Select
            size="small"
            value={currentAgent}
            onChange={(e) => setCurrentAgent(e.target.value as string)}
            sx={{
              fontSize: "0.75rem",
              minWidth: 80,
              height: 28,
              "& .MuiSelect-select": { py: 0.25 },
            }}
          >
            {agents.map((a) => (
              <MenuItem key={a.name || a.slug || ""} value={a.name || a.slug || ""}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                  {a.name || a.slug}
                </Box>
              </MenuItem>
            ))}
            {agents.length === 0 && (
              <>
                <MenuItem value="build">Build</MenuItem>
                <MenuItem value="plan">Plan</MenuItem>
                <MenuItem value="compose">Compose</MenuItem>
              </>
            )}
          </Select>
          <Box sx={{ flex: 1 }} />
          <Tooltip title="Keyboard shortcuts: Ctrl+K (commands), Ctrl+N (new session), Ctrl+E (files)">
            <Typography variant="caption" color="text.secondary" sx={{ cursor: "help" }}>
              {currentAgent === "build" ? "Full access" : currentAgent === "plan" ? "Read-only" : "Orchestration"}
            </Typography>
          </Tooltip>
        </Box>

        {/* Input area */}
        <Box className="flex items-end gap-2">
          <TextField
            inputRef={inputRef}
            fullWidth
            multiline
            maxRows={6}
            placeholder="Type a message... (Shift+Enter for newline)"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            size="small"
            variant="outlined"
            sx={{
              "& .MuiOutlinedInput-root": { borderRadius: "12px" },
            }}
          />

          {streamLoading ? (
            <IconButton
              color="error"
              onClick={cancelStream}
              sx={{ width: 40, height: 40, flexShrink: 0 }}
            >
              <StopIcon />
            </IconButton>
          ) : (
            <IconButton
              color="primary"
              onClick={handleSend}
              disabled={!text.trim()}
              sx={{
                width: 40, height: 40, flexShrink: 0,
                bgcolor: text.trim() ? "primary.main" : "action.disabledBackground",
                color: text.trim() ? "white" : "action.disabled",
                "&:hover": text.trim() ? { bgcolor: "primary.dark" } : {},
              }}
            >
              <SendIcon fontSize="small" />
            </IconButton>
          )}
        </Box>
      </Box>
    </Box>
  );
}
