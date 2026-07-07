import { useState } from "react";
import { Box, Avatar, IconButton, Typography, Tooltip, Collapse, LinearProgress } from "@mui/material";
import PersonIcon from "@mui/icons-material/Person";
import SmartToyIcon from "@mui/icons-material/SmartToy";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import ReplayIcon from "@mui/icons-material/Replay";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import PsychologyIcon from "@mui/icons-material/Psychology";
import SendIcon from "@mui/icons-material/Send";
import type { Message } from "../types";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { useSessionStore } from "../store/sessionStore";

interface MessageBubbleProps {
  message: Message;
  isStreaming?: boolean;
  streamingPhase?: "sending" | "reasoning" | "text";
  sessionId?: string;
}

export function MessageBubble({ message, isStreaming, streamingPhase, sessionId }: MessageBubbleProps) {
  const { info, parts } = message;
  const isUser = info.role === "user";
  const isAssistant = info.role === "assistant";
  const isError = info.id?.startsWith("e-");
  const isCancelled = info.id?.startsWith("c-");
  const [showReasoning, setShowReasoning] = useState(false);

  const deleteMessage = useSessionStore((s) => s.deleteMessage);
  const notify = useSessionStore((s) => s.notify);

  // Extract text content
  const textContent = parts
    .filter((p) => p.type === "text")
    .map((p) => p.text || "")
    .join("\n");

  // Extract reasoning content
  const reasoningParts = parts.filter((p) => p.type === "reasoning");
  const reasoningText = reasoningParts
    .map((p) => p.text || "")
    .join("\n");
  const hasReasoning = reasoningText.length > 0;

  // Extract token usage from step-finish
  const stepFinish = parts.find((p) => p.type === "step-finish");
  const tokens = stepFinish?.tokens;

  // Extract time info
  const createdTime = info.time?.created;
  const completedTime = info.time?.completed;
  const durationMs = createdTime && completedTime ? completedTime - createdTime : null;

  const formatTime = (timestamp?: number) => {
    if (!timestamp) return "";
    const date = new Date(timestamp);
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };

  const formatDuration = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(textContent).then(() => notify("Copied!", "success")).catch(() => {});
  };

  const handleDelete = () => {
    if (sessionId && !isStreaming) {
      deleteMessage(sessionId, info.id);
    }
  };

  const handleRetry = () => {
    if (isError || isAssistant) {
      // For errors: retry with same user message
      // For assistant messages: regenerate
      window.dispatchEvent(new CustomEvent("mimo-retry", { detail: { type: isError ? "retry" : "regenerate" } }));
    }
  };

  const displayContent = textContent + (isStreaming ? " ▌" : "");

  return (
    <Box className={`flex gap-3 px-4 py-2 ${isUser ? "flex-row-reverse" : "flex-row"}`}>
      <Avatar sx={{ width: 32, height: 32, bgcolor: isUser ? "primary.main" : isError ? "error.main" : "secondary.main", flexShrink: 0, mt: 0.5 }}>
        {isUser ? <PersonIcon fontSize="small" /> : isError ? <Typography variant="caption" color="white">!</Typography> : <SmartToyIcon fontSize="small" />}
      </Avatar>

      <Box className={`max-w-[80%] rounded-2xl px-4 py-2.5 ${isUser ? "bg-[var(--bubble-user-bg,#1976d2)] text-white rounded-tr-sm" : isError ? "bg-red-50 dark:bg-red-900/20 rounded-tl-sm" : "bg-gray-100 dark:bg-gray-800 dark:text-gray-100 rounded-tl-sm"}`}>
        {/* Reasoning section */}
        {isAssistant && hasReasoning && !isStreaming && (
          <Box sx={{ mb: 1 }}>
            <Box
              onClick={() => setShowReasoning(!showReasoning)}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                cursor: "pointer",
                color: "text.secondary",
                fontSize: "0.75rem",
                py: 0.5,
                "&:hover": { color: "primary.main" },
              }}
            >
              <PsychologyIcon sx={{ fontSize: 16 }} />
              <Typography variant="caption" sx={{ fontWeight: 500 }}>
                Reasoning ({Math.round(reasoningText.length / 1000 * 10) / 10}k chars)
              </Typography>
              {showReasoning ? <ExpandLessIcon sx={{ fontSize: 16 }} /> : <ExpandMoreIcon sx={{ fontSize: 16 }} />}
            </Box>
            <Collapse in={showReasoning}>
              <Box
                sx={{
                  mt: 1,
                  p: 1.5,
                  borderRadius: "8px",
                  bgcolor: "action.hover",
                  border: "1px solid",
                  borderColor: "divider",
                  maxHeight: 300,
                  overflow: "auto",
                }}
              >
                <Typography
                  variant="body2"
                  sx={{
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    fontSize: "0.8rem",
                    color: "text.secondary",
                    fontFamily: "monospace",
                  }}
                >
                  {reasoningText}
                </Typography>
              </Box>
            </Collapse>
          </Box>
        )}

        {/* Streaming phase indicator */}
        {isAssistant && isStreaming && !textContent && (
          <Box sx={{ py: 1 }}>
            {streamingPhase === "sending" && (
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <SendIcon sx={{ fontSize: 16, color: "text.secondary" }} />
                <Typography variant="body2" color="text.secondary" sx={{ fontStyle: "italic" }}>
                  Sending...
                </Typography>
                <LinearProgress sx={{ flex: 1, height: 4 }} />
              </Box>
            )}
            {streamingPhase === "reasoning" && (
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <PsychologyIcon sx={{ fontSize: 16, color: "primary.main", animation: "pulse 1.5s infinite" }} />
                <Typography variant="body2" color="primary.main" sx={{ fontWeight: 500 }}>
                  Thinking...
                </Typography>
                <LinearProgress color="primary" sx={{ flex: 1, height: 4 }} />
              </Box>
            )}
            {!streamingPhase && (
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <PsychologyIcon sx={{ fontSize: 16, color: "text.secondary", animation: "pulse 1.5s infinite" }} />
                <Typography variant="body2" color="text.secondary" sx={{ fontStyle: "italic" }}>
                  Processing...
                </Typography>
              </Box>
            )}
          </Box>
        )}

        {/* Main content */}
        {isAssistant && displayContent && <MarkdownRenderer content={displayContent} />}
        {isUser && (
          <Typography variant="body1" sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{textContent}</Typography>
        )}

        {/* Token usage, time, and actions */}
        {!isStreaming && (textContent || hasReasoning) && (
          <Box className="flex justify-between items-center mt-1">
            {/* Left: Time and token stats */}
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
              {/* Time display */}
              {createdTime && (
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                  {formatTime(createdTime)}
                  {durationMs !== null && ` · ${formatDuration(durationMs)}`}
                </Typography>
              )}
              {/* Token stats */}
              {tokens && (
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                  {tokens.total ? `${Math.round(tokens.total / 1000)}k tokens` : ""}
                  {tokens.reasoning ? ` · ${Math.round(tokens.reasoning / 1000)}k reasoning` : ""}
                </Typography>
              )}
            </Box>

            {/* Right: Action buttons */}
            <Box className="flex gap-1">
              <Tooltip title="Copy" arrow><IconButton size="small" onClick={handleCopy} sx={{ opacity: 0.4, "&:hover": { opacity: 1 } }}><ContentCopyIcon fontSize="small" /></IconButton></Tooltip>
              {sessionId && (
                <Tooltip title="Delete" arrow><IconButton size="small" onClick={handleDelete} sx={{ opacity: 0.4, "&:hover": { opacity: 1, color: "error.main" } }}><DeleteOutlineIcon fontSize="small" /></IconButton></Tooltip>
              )}
              {(isError || isAssistant) && (
                <Tooltip title={isError ? "Retry" : "Regenerate"} arrow>
                  <IconButton size="small" onClick={handleRetry} sx={{ opacity: 0.4, "&:hover": { opacity: 1, color: "primary.main" } }}>
                    <ReplayIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              )}
            </Box>
          </Box>
        )}
      </Box>
    </Box>
  );
}
