import { useEffect, useMemo, useState } from "react";
import {
  Drawer,
  Box,
  Typography,
  IconButton,
  Divider,
  List,
  ListItem,
  ListItemText,
  Chip,
  CircularProgress,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Button,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import CloudIcon from "@mui/icons-material/Cloud";
import ExtensionIcon from "@mui/icons-material/Extension";
import TerminalIcon from "@mui/icons-material/Terminal";
import PsychologyIcon from "@mui/icons-material/Psychology";
import DarkModeIcon from "@mui/icons-material/DarkMode";
import LightModeIcon from "@mui/icons-material/LightMode";
import SettingsBrightnessIcon from "@mui/icons-material/SettingsBrightness";
import { useUiStore } from "../store/uiStore";
import { useConfigStore } from "../store/configStore";
import { useSessionStore } from "../store/sessionStore";
import { MimoClient } from "../api/mimoClient";

function SettingsTab({
  label,
  icon,
  active,
  onClick,
}: {
  label: string;
  icon: React.ReactNode;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      size="small"
      variant={active ? "contained" : "text"}
      onClick={onClick}
      startIcon={icon}
      sx={{ textTransform: "none", borderRadius: "8px", minWidth: 0, flex: 1 }}
    >
      {label}
    </Button>
  );
}

export function SettingsPanel() {
  const settingsOpen = useUiStore((s) => s.settingsOpen);
  const setSettingsOpen = useUiStore((s) => s.setSettingsOpen);
  const themeMode = useUiStore((s) => s.themeMode);
  const setThemeMode = useUiStore((s) => s.setThemeMode);

  const providers = useConfigStore((s) => s.providers);
  const config = useConfigStore((s) => s.config);
  const loadProviders = useConfigStore((s) => s.loadProviders);

  const commands = useConfigStore((s) => s.commands);
  const skills = useConfigStore((s) => s.skills);
  const agents = useConfigStore((s) => s.agents);
  const loadCommands = useConfigStore((s) => s.loadCommands);
  const loadSkills = useConfigStore((s) => s.loadSkills);
  const loadAgents = useConfigStore((s) => s.loadAgents);

  const [tab, setTab] = useState(0);
  const [saving, setSaving] = useState(false);
  const [selectedModel, setSelectedModel] = useState("");

  // Connected provider IDs from root-level connected list
  const connectedSet = useMemo(() => {
    return new Set(providers?.connected || []);
  }, [providers]);

  // Load data when panel opens
  useEffect(() => {
    if (settingsOpen) {
      loadProviders();
      loadCommands();
      loadSkills();
      loadAgents();
      if (config?.model) setSelectedModel(config.model);
    }
  }, [settingsOpen, loadProviders, loadCommands, loadSkills, loadAgents, config]);

  // All available models for selection
  const allModels = useMemo(() => {
    if (!providers?.all) return [];
    const models: { label: string; value: string }[] = [];
    providers.all.forEach((p) => {
      if (p.models) {
        Object.keys(p.models).forEach((m) => {
          models.push({ label: `${p.name || p.id} / ${m}`, value: m });
        });
      }
    });
    return models;
  }, [providers]);

  const handleModelChange = async (model: string) => {
    setSelectedModel(model);
    setSaving(true);
    try {
      await MimoClient.updateConfig({ model } as any);
    } catch (e) {
      console.error("Failed to update model:", e);
    }
    setSaving(false);
  };

  const themeOptions = [
    { value: "light", label: "Light", icon: <LightModeIcon fontSize="small" /> },
    { value: "dark", label: "Dark", icon: <DarkModeIcon fontSize="small" /> },
    { value: "system", label: "System", icon: <SettingsBrightnessIcon fontSize="small" /> },
  ];

  return (
    <Drawer
      anchor="right"
      open={settingsOpen}
      onClose={() => setSettingsOpen(false)}
      PaperProps={{ sx: { width: 380, maxWidth: "90vw" } }}
    >
      {/* Header */}
      <Box className="flex items-center justify-between p-4">
        <Typography variant="h6" fontWeight={600}>Settings</Typography>
        <IconButton onClick={() => setSettingsOpen(false)} size="small">
          <CloseIcon />
        </IconButton>
      </Box>
      <Divider />

      {/* Tabs */}
      <Box className="px-4 pt-3 pb-1 flex gap-1 flex-wrap">
        <SettingsTab label="General" icon={<PsychologyIcon fontSize="small" />} active={tab === 0} onClick={() => setTab(0)} />
        <SettingsTab label="Providers" icon={<CloudIcon fontSize="small" />} active={tab === 1} onClick={() => setTab(1)} />
        <SettingsTab label="Skills" icon={<ExtensionIcon fontSize="small" />} active={tab === 2} onClick={() => setTab(2)} />
        <SettingsTab label="Commands" icon={<TerminalIcon fontSize="small" />} active={tab === 3} onClick={() => setTab(3)} />
      </Box>
      <Divider />

      <Box className="flex-1 overflow-y-auto">
        {tab === 0 && (
          <Box className="p-4 space-y-4">
            {/* Theme */}
            <Box>
              <Typography variant="subtitle2" gutterBottom fontWeight={600}>
                Theme
              </Typography>
              <Box className="flex gap-2">
                {themeOptions.map((opt) => (
                  <Button
                    key={opt.value}
                    size="small"
                    variant={themeMode === opt.value ? "contained" : "outlined"}
                    onClick={() => setThemeMode(opt.value as any)}
                    startIcon={opt.icon}
                    sx={{ textTransform: "none", borderRadius: "8px", flex: 1 }}
                  >
                    {opt.label}
                  </Button>
                ))}
              </Box>
            </Box>

            <Divider />

            {/* Model Selection */}
            <Box>
              <Typography variant="subtitle2" gutterBottom fontWeight={600}>
                AI Model
              </Typography>
              <FormControl fullWidth size="small">
                <InputLabel>Model</InputLabel>
                <Select
                  value={selectedModel}
                  label="Model"
                  onChange={(e) => handleModelChange(e.target.value)}
                  disabled={saving || allModels.length === 0}
                >
                  {allModels.length === 0 ? (
                    <MenuItem value="">Loading models...</MenuItem>
                  ) : (
                    allModels.map((m) => (
                      <MenuItem key={m.value} value={m.value}>
                        {m.label}
                      </MenuItem>
                    ))
                  )}
                </Select>
              </FormControl>
              {saving && (
                <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
                  Saving...
                </Typography>
              )}
            </Box>

            <Divider />

            {/* Info */}
            <Box>
              <Typography variant="subtitle2" gutterBottom fontWeight={600}>
                Status
              </Typography>
              <Box className="space-y-1">
                <Typography variant="body2" color="text.secondary">
                  Active sessions: {useSessionStore.getState?.()?.sessions?.length || 0}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Connected providers: {connectedSet.size}
                </Typography>
              </Box>
            </Box>
          </Box>
        )}

        {tab === 1 && (
          <Box className="p-4">
            <Typography variant="subtitle2" gutterBottom fontWeight={600}>
              AI Providers ({providers?.all?.length || 0})
            </Typography>
            {!providers ? (
              <Box className="flex justify-center py-4">
                <CircularProgress size={24} />
              </Box>
            ) : providers.all.length === 0 ? (
              <Typography variant="body2" color="text.secondary">No providers available</Typography>
            ) : (
              <List dense disablePadding>
                {providers.all.map((p) => {
                  const connected = connectedSet.has(p.id);
                  return (
                    <ListItem key={p.id} disableGutters sx={{ py: 0.5 }}>
                      <ListItemText
                        primary={p.name || p.id}
                        primaryTypographyProps={{ variant: "body2" }}
                      />
                      <Chip
                        label={connected ? "Connected" : "Disconnected"}
                        size="small"
                        color={connected ? "success" : "default"}
                        variant="outlined"
                      />
                    </ListItem>
                  );
                })}
              </List>
            )}
          </Box>
        )}

        {/* Skills Tab */}
        {tab === 2 && (
          <Box className="p-4">
            <Typography variant="subtitle2" gutterBottom fontWeight={600}>
              Skills ({skills.length})
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 2 }}>
              Press Ctrl+K and type a skill name to use it. Skill install/delete is managed via the <code>mimo</code> CLI on the server.
            </Typography>
            {skills.length === 0 ? (
              <Typography variant="body2" color="text.secondary">No skills available</Typography>
            ) : (
              <List dense disablePadding>
                {skills.map((s, i) => (
                  <ListItem key={s.name || i} disableGutters sx={{ py: 0.5 }}>
                    <ListItemText primary={s.name} secondary={s.description || "No description"} primaryTypographyProps={{ variant: "body2", fontWeight: 500 }} secondaryTypographyProps={{ variant: "caption" }} />
                  </ListItem>
                ))}
              </List>
            )}
          </Box>
        )}

        {/* Commands Tab */}
        {tab === 3 && (
          <Box className="p-4">
            <Typography variant="subtitle2" gutterBottom fontWeight={600}>
              Commands ({commands.length})
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 2 }}>
              Type / in chat or press Ctrl+K to execute a command.
            </Typography>
            {commands.length === 0 ? (
              <Typography variant="body2" color="text.secondary">No commands available</Typography>
            ) : (
              <List dense disablePadding>
                {commands.map((c, i) => (
                  <ListItem key={c.name || c.command || i} disableGutters sx={{ py: 0.5 }}>
                    <ListItemText primary={`/${c.command || c.name}`} secondary={c.description || "No description"} primaryTypographyProps={{ variant: "body2", fontWeight: 500, fontFamily: "monospace" }} secondaryTypographyProps={{ variant: "caption" }} />
                  </ListItem>
                ))}
              </List>
            )}
          </Box>
        )}
      </Box>
    </Drawer>
  );
}
