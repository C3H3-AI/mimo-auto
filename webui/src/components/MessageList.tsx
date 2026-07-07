import { useRef } from "react";
import { Box, Typography } from "@mui/material";
import { useSessionStore } from "../store/sessionStore";
import { MessageBubble } from "./MessageBubble";
import { LoadingSkeleton } from "./LoadingSkeleton";
import { useAutoScroll } from "../hooks/useStreaming";

export function MessageList() {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const messages = useSessionStore((s) => activeSessionId ? s.messages[activeSessionId] || [] : []);
  const messagesLoading = useSessionStore((s) => s.messagesLoading);
  const streamLoading = useSessionStore((s) => s.stream.loading);
  const streamPhase = useSessionStore((s) => s.stream.phase);

  const containerRef = useRef<HTMLDivElement>(null);
  useAutoScroll(containerRef, [messages, streamLoading]);

  if (!activeSessionId) {
    return (
      <Box className="flex-1 flex items-center justify-center">
        <Typography variant="h6" color="text.secondary">Type a message to start a new conversation</Typography>
      </Box>
    );
  }

  if (messagesLoading) {
    return <Box className="flex-1 overflow-y-auto"><LoadingSkeleton variant="chat" /></Box>;
  }

  if (messages.length === 0 && !streamLoading) {
    return (
      <Box className="flex-1 flex items-center justify-center">
        <Box className="text-center">
          <Typography variant="h6" color="text.secondary" gutterBottom>Start a conversation</Typography>
          <Typography variant="body2" color="text.secondary">Type a message below to begin chatting with MiMo</Typography>
        </Box>
      </Box>
    );
  }

  return (
    <Box ref={containerRef} className="flex-1 overflow-y-auto py-4">
      {messages.map((msg, index) => (
        <MessageBubble
          key={msg.info.id}
          message={msg}
          sessionId={activeSessionId}
          isStreaming={streamLoading && msg.info.role === "assistant" && index === messages.length - 1}
          streamingPhase={streamLoading && msg.info.role === "assistant" && index === messages.length - 1 ? streamPhase : undefined}
        />
      ))}
    </Box>
  );
}
