import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  TextField,
  Box,
  Typography,
  Chip,
  Divider,
  Snackbar,
  Alert,
} from "@mui/material";
import TerminalIcon from "@mui/icons-material/Terminal";
import ExtensionIcon from "@mui/icons-material/Extension";
import PsychologyIcon from "@mui/icons-material/Psychology";
import SearchIcon from "@mui/icons-material/Search";
import { useUiStore } from "../store/uiStore";
import { useConfigStore } from "../store/configStore";
import { useSessionStore } from "../store/sessionStore";
import { MimoClient } from "../api/mimoClient";

type PaletteItem = {
  _type: "command" | "skill" | "agent";
  label: string;
  desc?: string;
  /** Raw command string to execute */
  command?: string;
};

export function CommandPalette() {
  const open = useUiStore((s) => s.commandPaletteOpen);
  const setOpen = useUiStore((s) => s.setCommandPaletteOpen);

  const commands = useConfigStore((s) => s.commands);
  const skills = useConfigStore((s) => s.skills);
  const agents = useConfigStore((s) => s.agents);

  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const ensureSession = useSessionStore((s) => s.ensureSession);
  const notify = useSessionStore((s) => s.notify);
  const loadSessions = useSessionStore((s) => s.loadSessions);

  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const [executing, setExecuting] = useState(false);

  useEffect(() => {
    if (open) {
      setTimeout(() => {
        inputRef.current?.focus();
        setQuery("");
        setSelectedIndex(0);
      }, 50);
    }
  }, [open]);

  const filtered = useMemo(() => {
    const all: PaletteItem[] = [
      ...commands.map((c) => ({ _type: "command" as const, label: c.name || c.command || "", desc: c.description, command: c.command })),
      ...skills.map((s) => ({ _type: "skill" as const, label: s.name || "", desc: s.description })),
      ...agents.map((a) => ({ _type: "agent" as const, label: a.name || a.slug || "", desc: a.description })),
    ];
    if (!query) return all;
    const q = query.toLowerCase();
    return all.filter((item) =>
      item.label.toLowerCase().includes(q) || (item.desc || "").toLowerCase().includes(q)
    );
  }, [commands, skills, agents, query]);

  const handleExecute = useCallback(async (item: PaletteItem) => {
    setOpen(false);
    if (item._type === "command" && item.command) {
      setExecuting(true);
      try {
        // Ensure we have an active session
        let sid = activeSessionId;
        if (!sid) sid = await ensureSession();
        if (!sid) { notify("Please create a session first", "error"); return; }

        // Execute the command in the session
        await MimoClient.executeCommand(sid, item.command);
        notify(`Command /${item.command} executed`, "success");
        loadSessions();
      } catch (e) {
        notify(`Command failed: ${(e as Error).message}`, "error");
      }
      setExecuting(false);
    } else if (item._type === "agent" || item._type === "skill") {
      // For agents/skills, fill the chat input with a mention
      const text = item._type === "agent" ? `@${item.label} ` : `/${item.label} `;
      window.dispatchEvent(new CustomEvent("mimo-retry", { detail: text }));
      notify(`Type "${text}" to use this ${item._type}`, "info");
    }
  }, [activeSessionId, ensureSession, notify, loadSessions, setOpen]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") { e.preventDefault(); setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setSelectedIndex((i) => Math.max(i - 1, 0)); }
      else if (e.key === "Enter" && filtered[selectedIndex]) { e.preventDefault(); handleExecute(filtered[selectedIndex]); }
      else if (e.key === "Escape") { setOpen(false); }
    },
    [filtered, selectedIndex, setOpen, handleExecute]
  );

  const getIcon = (type: string) => {
    switch (type) { case "command": return <TerminalIcon fontSize="small" />; case "skill": return <ExtensionIcon fontSize="small" />; case "agent": return <PsychologyIcon fontSize="small" />; default: return <SearchIcon fontSize="small" />; }
  };

  return (
    <>
      <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="sm" PaperProps={{ sx: { borderRadius: "12px", maxHeight: "60vh" } }}>
        <DialogTitle sx={{ p: 0 }}>
          <TextField
            inputRef={inputRef}
            fullWidth
            placeholder="Search commands, skills, agents... (Enter to execute)"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setSelectedIndex(0); }}
            onKeyDown={handleKeyDown}
            variant="outlined"
            sx={{ "& .MuiOutlinedInput-root": { borderRadius: "12px 12px 0 0", "& fieldset": { border: "none" } } }}
            InputProps={{ startAdornment: (<Box className="mr-2 text-gray-400"><SearchIcon fontSize="small" /></Box>) }}
          />
        </DialogTitle>
        <Divider />
        <DialogContent sx={{ p: 0 }}>
          {filtered.length === 0 ? (
            <Box className="p-6 text-center"><Typography variant="body2" color="text.secondary">No results found for "{query}"</Typography></Box>
          ) : (
            <List dense disablePadding>
              {filtered.map((item, index) => (
                <ListItemButton
                  key={`${item._type}-${item.label}`}
                  selected={index === selectedIndex}
                  onClick={() => handleExecute(item)}
                  sx={{ px: 3, py: 1.5, "&.Mui-selected": { backgroundColor: "action.selected" } }}
                >
                  <ListItemIcon sx={{ minWidth: 36 }}>{getIcon(item._type)}</ListItemIcon>
                  <ListItemText
                    primary={item.label}
                    secondary={item.desc || (item._type === "command" && item.command ? `/${item.command}` : undefined)}
                    primaryTypographyProps={{ variant: "body2" }}
                    secondaryTypographyProps={{ variant: "caption" }}
                  />
                  <Chip label={item._type} size="small" variant="outlined" sx={{ ml: 1, textTransform: "capitalize" }} />
                </ListItemButton>
              ))}
            </List>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
