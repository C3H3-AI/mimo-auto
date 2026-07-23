import { Box, IconButton } from "@mui/material";
import MenuIcon from "@mui/icons-material/Menu";
import FolderOpenIcon from "@mui/icons-material/FolderOpen";
import DeviceHubIcon from "@mui/icons-material/DeviceHub";
import { useUiStore } from "../store/uiStore";
import { Sidebar } from "./Sidebar";
import { ChatView } from "./ChatView";
import { SettingsPanel } from "./SettingsPanel";
import { CommandPalette } from "./CommandPalette";
import { FileExplorer } from "./FileExplorer";
import { DevicePanel } from "./DevicePanel";
import { useKeyboard } from "../hooks/useKeyboard";

export function Layout() {
  const sidebarOpen = useUiStore((s) => s.sidebarOpen);
  const setSidebarOpen = useUiStore((s) => s.setSidebarOpen);
  const fileExplorerOpen = useUiStore((s) => s.fileExplorerOpen);
  const setFileExplorerOpen = useUiStore((s) => s.setFileExplorerOpen);
  const devicePanelOpen = useUiStore((s) => s.devicePanelOpen);
  const setDevicePanelOpen = useUiStore((s) => s.setDevicePanelOpen);

  // Register keyboard shortcuts
  useKeyboard();

  // Check if mobile
  const isMobile = typeof window !== "undefined" && window.innerWidth < 768;

  return (
    <Box className="flex h-screen w-screen overflow-hidden bg-gray-50 dark:bg-gray-900">
      {/* Mobile overlay — click to close sidebar */}
      {sidebarOpen && isMobile && (
        <Box
          onClick={() => setSidebarOpen(false)}
          sx={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            bgcolor: "rgba(0,0,0,0.5)",
            zIndex: 999,
          }}
        />
      )}

      {/* Sidebar */}
      <Box
        sx={{
          position: isMobile ? "fixed" : "relative",
          zIndex: isMobile ? 1000 : "auto",
          transform: isMobile && !sidebarOpen ? "translateX(-100%)" : "none",
          transition: "transform 0.3s ease",
        }}
      >
        <Sidebar drawerWidth={280} />
      </Box>

      {/* Main Content */}
      <Box className="flex flex-col flex-1 min-w-0 bg-white dark:bg-gray-900">
        {/* Top bar with hamburger */}
        <Box className="flex items-center h-12 px-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 flex-shrink-0">
          <IconButton onClick={() => setSidebarOpen(!sidebarOpen)} size="small">
            <MenuIcon />
          </IconButton>
          <Box sx={{ flex: 1 }} />
          <IconButton
            size="small"
            onClick={() => setDevicePanelOpen(!devicePanelOpen)}
            sx={{ color: devicePanelOpen ? "primary.main" : "text.secondary" }}
            title="Toggle device panel (Ctrl+D)"
          >
            <DeviceHubIcon fontSize="small" />
          </IconButton>
          <IconButton
            size="small"
            onClick={() => setFileExplorerOpen(!fileExplorerOpen)}
            sx={{ color: fileExplorerOpen ? "primary.main" : "text.secondary" }}
            title="Toggle file explorer (Ctrl+E)"
          >
            <FolderOpenIcon fontSize="small" />
          </IconButton>
        </Box>

        {/* Chat area */}
        <ChatView />
      </Box>

      {/* File Explorer Panel */}
      {fileExplorerOpen && (
        <Box
          className="border-l border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900"
          sx={{ width: 300, flexShrink: 0, overflow: "hidden" }}
        >
          <FileExplorer />
        </Box>
      )}

      {/* Device Panel */}
      {devicePanelOpen && (
        <Box
          className="border-l border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900"
          sx={{ width: 320, flexShrink: 0, overflow: "hidden" }}
        >
          <DevicePanel />
        </Box>
      )}

      {/* Settings Drawer */}
      <SettingsPanel />

      {/* Command Palette */}
      <CommandPalette />
    </Box>
  );
}
