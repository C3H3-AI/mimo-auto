import { useEffect } from "react";
import { useUiStore } from "../store/uiStore";
import { useSessionStore } from "../store/sessionStore";

export function useKeyboard() {
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const toggleSettings = useUiStore((s) => s.toggleSettings);
  const setCommandPaletteOpen = useUiStore((s) => s.setCommandPaletteOpen);
  const toggleFileExplorer = useUiStore((s) => s.toggleFileExplorer);
  const createSession = useSessionStore((s) => s.createSession);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      // Don't trigger shortcuts when typing in input fields (except Ctrl combos)
      const target = e.target as HTMLElement;
      const isInput =
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.isContentEditable;

      // Ctrl+K / Cmd+K — Command palette (works everywhere)
      if (e.key === "k" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        setCommandPaletteOpen(true);
        return;
      }

      // Ctrl+N / Cmd+N — New session (must prevent default to avoid browser new window)
      if (e.key === "n" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        e.stopPropagation();
        createSession();
        return;
      }

      // Ctrl+E / Cmd+E — Toggle file explorer
      if (e.key === "e" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        toggleFileExplorer();
        return;
      }

      // Skip the rest if inside an input
      if (isInput) return;

      // Ctrl+B / Cmd+B — Toggle sidebar
      if (e.key === "b" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        toggleSidebar();
        return;
      }

      // Escape — Close panels
      if (e.key === "Escape") {
        const uiState = useUiStore.getState();
        if (uiState.commandPaletteOpen) {
          setCommandPaletteOpen(false);
          return;
        }
        if (uiState.settingsOpen) {
          toggleSettings();
          return;
        }
        return;
      }

      // Ctrl+Shift+C / Cmd+Shift+C — Toggle settings
      if (
        e.key === "C" &&
        (e.ctrlKey || e.metaKey) &&
        e.shiftKey
      ) {
        e.preventDefault();
        toggleSettings();
        return;
      }
    }

    window.addEventListener("keydown", handleKeyDown, true);
    return () => window.removeEventListener("keydown", handleKeyDown, true);
  }, [toggleSidebar, toggleSettings, setCommandPaletteOpen, toggleFileExplorer, createSession]);
}
