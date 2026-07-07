import { Box } from "@mui/material";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";

export function ChatView() {
  return (
    <Box className="flex-1 flex flex-col min-h-0 bg-white dark:bg-gray-900">
      {/* Messages — fills remaining space, scrollable */}
      <MessageList />
      {/* Input — always at the bottom */}
      <ChatInput />
    </Box>
  );
}
