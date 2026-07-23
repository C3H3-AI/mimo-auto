import { create } from "zustand";
import type { ThemeMode } from "../types";

interface UiState {
  /* Theme */
  themeMode: ThemeMode;

  /* Panels */
  sidebarOpen: boolean;
  settingsOpen: boolean;
  commandPaletteOpen: boolean;
  fileExplorerOpen: boolean;
  devicePanelOpen: boolean;

  /* Actions */
  setThemeMode: (mode: ThemeMode) => void;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setSettingsOpen: (open: boolean) => void;
  toggleSettings: () => void;
  setCommandPaletteOpen: (open: boolean) => void;
  setFileExplorerOpen: (open: boolean) => void;
  toggleFileExplorer: () => void;
  setDevicePanelOpen: (open: boolean) => void;
  toggleDevicePanel: () => void;
}

function getStoredTheme(): ThemeMode {
  try {
    const stored = localStorage.getItem("mimo-theme-mode");
    if (stored === "light" || stored === "dark" || stored === "system") {
      return stored;
    }
  } catch {
    // ignore
  }
  return "system";
}

function getSystemTheme(): "light" | "dark" {
  if (typeof window !== "undefined" && window.matchMedia) {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }
  return "light";
}

function applyTheme(mode: ThemeMode) {
  const resolved = mode === "system" ? getSystemTheme() : mode;
  document.documentElement.classList.toggle("dark", resolved === "dark");
}

function isMobileDevice(): boolean {
  if (typeof window === "undefined") return false;
  return window.innerWidth < 768;
}

export const useUiStore = create<UiState>((set) => {
  const initialMode = getStoredTheme();
  applyTheme(initialMode);

  return {
    themeMode: initialMode,

    // Default sidebar closed on mobile
    sidebarOpen: !isMobileDevice(),
    settingsOpen: false,
    commandPaletteOpen: false,
    fileExplorerOpen: false,
    devicePanelOpen: false,

    setThemeMode: (mode: ThemeMode) => {
      localStorage.setItem("mimo-theme-mode", mode);
      applyTheme(mode);
      set({ themeMode: mode });
    },

    toggleSidebar: () => {
      set((state) => ({ sidebarOpen: !state.sidebarOpen }));
    },

    setSidebarOpen: (open: boolean) => {
      set({ sidebarOpen: open });
    },

    setSettingsOpen: (open: boolean) => {
      set({ settingsOpen: open });
    },

    toggleSettings: () => {
      set((state) => ({ settingsOpen: !state.settingsOpen }));
    },

    setCommandPaletteOpen: (open: boolean) => {
      set({ commandPaletteOpen: open });
    },

    setFileExplorerOpen: (open: boolean) => {
      set({ fileExplorerOpen: open });
    },

    toggleFileExplorer: () => {
      set((state) => ({ fileExplorerOpen: !state.fileExplorerOpen }));
    },

    setDevicePanelOpen: (open: boolean) => {
      set({ devicePanelOpen: open });
    },

    toggleDevicePanel: () => {
      set((state) => ({ devicePanelOpen: !state.devicePanelOpen }));
    },
  };
});

// Listen for system theme changes
if (typeof window !== "undefined") {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    const state = useUiStore.getState();
    if (state.themeMode === "system") {
      applyTheme("system");
    }
  });
}
