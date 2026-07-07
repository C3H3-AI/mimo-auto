import { useEffect, useState, useMemo, useRef } from "react";
import {
  Box,
  Typography,
  List,
  ListItemButton,
  ListItemText,
  IconButton,
  Divider,
  Button,
  TextField,
  InputAdornment,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import ChatIcon from "@mui/icons-material/Chat";
import SettingsIcon from "@mui/icons-material/Settings";
import DarkModeIcon from "@mui/icons-material/DarkMode";
import LightModeIcon from "@mui/icons-material/LightMode";
import DeleteIcon from "@mui/icons-material/Delete";
import DeleteSweepIcon from "@mui/icons-material/DeleteSweep";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import SearchIcon from "@mui/icons-material/Search";
import { useSessionStore } from "../store/sessionStore";
import { useConfigStore } from "../store/configStore";
import { useUiStore } from "../store/uiStore";
import { LoadingSkeleton } from "./LoadingSkeleton";

interface SidebarProps {
  drawerWidth: number;
}

export function Sidebar({ drawerWidth }: SidebarProps) {
  const sessions = useSessionStore((s) => s.sessions);
  const sessionsLoading = useSessionStore((s) => s.sessionsLoading);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const loadSessions = useSessionStore((s) => s.loadSessions);
  const createSession = useSessionStore((s) => s.createSession);
  const setActiveSession = useSessionStore((s) => s.setActiveSession);
  const deleteSession = useSessionStore((s) => s.deleteSession);
  const deleteAllSessions = useSessionStore((s) => s.deleteAllSessions);
  const renameSession = useSessionStore((s) => s.renameSession);

  const loadAll = useConfigStore((s) => s.loadAll);

  const themeMode = useUiStore((s) => s.themeMode);
  const setThemeMode = useUiStore((s) => s.setThemeMode);
  const setSettingsOpen = useUiStore((s) => s.setSettingsOpen);
  const setSidebarOpen = useUiStore((s) => s.setSidebarOpen);

  const [creating, setCreating] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const renameRef = useRef<HTMLInputElement>(null);

  // Filter sessions by search query
  const filteredSessions = useMemo(() => {
    if (!searchQuery.trim()) return sessions;
    const q = searchQuery.toLowerCase();
    return sessions.filter(
      (s) =>
        (s.title || "").toLowerCase().includes(q) ||
        (s.id || "").toLowerCase().includes(q)
    );
  }, [sessions, searchQuery]);

  // Format relative time
  const formatRelativeTime = (timestamp?: number) => {
    if (!timestamp) return "";
    const now = Date.now();
    const diff = now - timestamp;
    if (diff < 60000) return "刚刚";
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}天前`;
    return new Date(timestamp).toLocaleDateString();
  };

  // Load sessions on mount
  useEffect(() => {
    loadSessions();
    loadAll();
  }, [loadSessions, loadAll]);

  const handleCreateSession = async () => {
    setCreating(true);
    const session = await createSession();
    setCreating(false);
    // Don't auto-close sidebar on desktop
  };

  const handleSelectSession = (sessionId: string) => {
    setActiveSession(sessionId);
    // Don't close sidebar — user can close it manually
  };

  const toggleTheme = () => {
    const next =
      themeMode === "light"
        ? "dark"
        : themeMode === "dark"
        ? "system"
        : "light";
    setThemeMode(next);
  };

  const content = (
    <Box className="flex flex-col h-full">
      {/* Header with back button */}
      <Box className="p-3 flex items-center justify-between">
        <Box className="flex items-center gap-1">
          <IconButton size="small" onClick={() => setSidebarOpen(false)} title="Close sidebar">
            <ChevronLeftIcon fontSize="small" />
          </IconButton>
          <Typography variant="subtitle1" fontWeight={700}>
            MiMo Code
          </Typography>
        </Box>
        <IconButton size="small" onClick={toggleTheme}>
          {themeMode === "dark" ? (
            <LightModeIcon fontSize="small" />
          ) : (
            <DarkModeIcon fontSize="small" />
          )}
        </IconButton>
      </Box>

      <Divider />

      {/* New Session Button */}
      <Box className="p-3">
        <Button
          fullWidth
          variant="outlined"
          startIcon={<AddIcon />}
          onClick={handleCreateSession}
          disabled={creating}
          size="small"
          sx={{ borderRadius: "8px" }}
        >
          {creating ? "Creating..." : "New Session"}
        </Button>
      </Box>

      <Divider />

      {/* Search sessions */}
      <Box className="px-3 pt-2 pb-1">
        <TextField
          fullWidth
          size="small"
          placeholder="Search sessions..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          variant="outlined"
          sx={{
            "& .MuiOutlinedInput-root": { borderRadius: "8px", fontSize: "0.8rem" },
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" sx={{ opacity: 0.5 }} />
              </InputAdornment>
            ),
          }}
        />
      </Box>

      {/* Session List */}
      <Box className="flex-1 overflow-y-auto">
        {sessionsLoading ? (
          <LoadingSkeleton variant="list" lines={5} />
        ) : filteredSessions.length === 0 ? (
          <Box className="p-4 text-center">
            <Typography variant="body2" color="text.secondary">
              {searchQuery ? "No matching sessions" : "No sessions yet. Click \"New Session\" to start."}
            </Typography>
          </Box>
        ) : (
          <List dense disablePadding>
            {filteredSessions.map((session) => (
              <ListItemButton
                key={session.id}
                selected={session.id === activeSessionId}
                onClick={() => handleSelectSession(session.id)}
                sx={{ borderRadius: "8px", mx: 1, my: 0.25, "&.Mui-selected": { backgroundColor: "action.selected" } }}
              >
                <ChatIcon fontSize="small" sx={{ mr: 1.5, opacity: 0.6, flexShrink: 0 }} />
                {renamingId === session.id ? (
                  <TextField
                    inputRef={renameRef}
                    size="small"
                    fullWidth
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onBlur={() => { if (renameValue.trim()) renameSession(session.id, renameValue.trim()); setRenamingId(null); }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") { if (renameValue.trim()) renameSession(session.id, renameValue.trim()); setRenamingId(null); }
                      if (e.key === "Escape") setRenamingId(null);
                    }}
                    autoFocus
                    onClick={(e) => e.stopPropagation()}
                    sx={{ "& .MuiOutlinedInput-root": { fontSize: "0.85rem" } }}
                  />
                ) : (
                  <ListItemText
                    primary={session.title || `Session ${(session.id || "").slice(0, 8)}`}
                    secondary={formatRelativeTime(session.time?.updated || session.time?.created)}
                    primaryTypographyProps={{ variant: "body2", noWrap: true, fontSize: "0.85rem" }}
                    secondaryTypographyProps={{ variant: "caption", fontSize: "0.7rem" }}
                    onDoubleClick={(e) => { e.stopPropagation(); setRenamingId(session.id); setRenameValue(session.title || ""); }}
                  />
                )}
                {renamingId !== session.id && (
                  <IconButton size="small" onClick={(e) => { e.stopPropagation(); if (window.confirm("Delete this session?")) deleteSession(session.id); }} sx={{ opacity: 0.4, "&:hover": { opacity: 1 } }}>
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                )}
              </ListItemButton>
            ))}
          </List>
        )}
      </Box>

      <Divider />

      {/* Footer */}
      <Box className="p-2 space-y-1">
        <ListItemButton
          onClick={() => setSettingsOpen(true)}
          sx={{ borderRadius: "8px" }}
        >
          <SettingsIcon fontSize="small" sx={{ mr: 1.5, opacity: 0.6 }} />
          <ListItemText
            primary="Settings"
            primaryTypographyProps={{ variant: "body2" }}
          />
        </ListItemButton>
        {sessions.length > 0 && (
          <ListItemButton
            onClick={() => { if (window.confirm(`Delete all ${sessions.length} sessions?`)) deleteAllSessions(); }}
            sx={{ borderRadius: "8px" }}
          >
            <DeleteSweepIcon fontSize="small" sx={{ mr: 1.5, opacity: 0.6 }} />
            <ListItemText
              primary="Clear All Conversations"
              primaryTypographyProps={{ variant: "body2" }}
            />
          </ListItemButton>
        )}
      </Box>
    </Box>
  );

  return (
    <Box
      className="flex flex-col h-full border-r border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 flex-shrink-0"
      sx={{ width: drawerWidth }}
    >
      {content}
    </Box>
  );
}
