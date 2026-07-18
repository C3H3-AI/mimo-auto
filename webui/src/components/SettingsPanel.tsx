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
import ChatIcon from "@mui/icons-material/Chat";
import { useUiStore } from "../store/uiStore";
import { useConfigStore } from "../store/configStore";
import { useSessionStore } from "../store/sessionStore";
import { MimoClient } from "../api/mimoClient";
import { ChannelSettings } from "./ChannelSettings";

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
    { value: "light", label: "亮色", icon: <LightModeIcon fontSize="small" /> },
    { value: "dark", label: "暗色", icon: <DarkModeIcon fontSize="small" /> },
    { value: "system", label: "跟随系统", icon: <SettingsBrightnessIcon fontSize="small" /> },
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
        <Typography variant="h6" fontWeight={600}>设置</Typography>
        <IconButton onClick={() => setSettingsOpen(false)} size="small">
          <CloseIcon />
        </IconButton>
      </Box>
      <Divider />

      {/* Tabs */}
      <Box className="px-4 pt-3 pb-1 flex gap-1 flex-wrap">
        <SettingsTab label="通用" icon={<PsychologyIcon fontSize="small" />} active={tab === 0} onClick={() => setTab(0)} />
        <SettingsTab label="提供商" icon={<CloudIcon fontSize="small" />} active={tab === 1} onClick={() => setTab(1)} />
        <SettingsTab label="通道" icon={<ChatIcon fontSize="small" />} active={tab === 2} onClick={() => setTab(2)} />
        <SettingsTab label="技能" icon={<ExtensionIcon fontSize="small" />} active={tab === 3} onClick={() => setTab(3)} />
        <SettingsTab label="命令" icon={<TerminalIcon fontSize="small" />} active={tab === 4} onClick={() => setTab(4)} />
      </Box>
      <Divider />

      <Box className="flex-1 overflow-y-auto">
        {tab === 0 && (
          <Box className="p-4 space-y-4">
            {/* Theme */}
            <Box>
              <Typography variant="subtitle2" gutterBottom fontWeight={600}>
                主题
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
                AI 模型
              </Typography>
              <FormControl fullWidth size="small">
                <InputLabel>模型</InputLabel>
                <Select
                  value={selectedModel}
                  label="模型"
                  onChange={(e) => handleModelChange(e.target.value)}
                  disabled={saving || allModels.length === 0}
                >
                  {allModels.length === 0 ? (
                    <MenuItem value="">加载模型中...</MenuItem>
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
                状态
              </Typography>
              <Box className="space-y-1">
                <Typography variant="body2" color="text.secondary">
                  活跃会话: {useSessionStore.getState?.()?.sessions?.length || 0}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  已连接提供商: {connectedSet.size}
                </Typography>
              </Box>
            </Box>
          </Box>
        )}

        {tab === 1 && (
          <Box className="p-4">
            <Typography variant="subtitle2" gutterBottom fontWeight={600}>
              AI 提供商 ({providers?.all?.length || 0})
            </Typography>
            {!providers ? (
              <Box className="flex justify-center py-4">
                <CircularProgress size={24} />
              </Box>
            ) : providers.all.length === 0 ? (
              <Typography variant="body2" color="text.secondary">暂无可用提供商</Typography>
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
                        label={connected ? "已连接" : "未连接"}
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

        {/* Channels Tab */}
        {tab === 2 && (
          <ChannelSettings />
        )}

        {/* Skills Tab */}
        {tab === 3 && (
          <Box className="p-4">
            <Typography variant="subtitle2" gutterBottom fontWeight={600}>
              技能 ({skills.length})
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 2 }}>
              按 Ctrl+K 然后输入技能名称来使用。技能安装/删除通过服务器上的 <code>mimo</code> CLI 管理。
            </Typography>
            {skills.length === 0 ? (
              <Typography variant="body2" color="text.secondary">暂无可用技能</Typography>
            ) : (
              <List dense disablePadding>
                {skills.map((s, i) => (
                  <ListItem key={s.name || i} disableGutters sx={{ py: 0.5 }}>
                    <ListItemText primary={s.name} secondary={s.description || "暂无描述"} primaryTypographyProps={{ variant: "body2", fontWeight: 500 }} secondaryTypographyProps={{ variant: "caption" }} />
                  </ListItem>
                ))}
              </List>
            )}
          </Box>
        )}

        {/* Commands Tab */}
        {tab === 4 && (
          <Box className="p-4">
            <Typography variant="subtitle2" gutterBottom fontWeight={600}>
              命令 ({commands.length})
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 2 }}>
              在聊天中输入 / 或按 Ctrl+K 执行命令。
            </Typography>
            {commands.length === 0 ? (
              <Typography variant="body2" color="text.secondary">暂无可用命令</Typography>
            ) : (
              <List dense disablePadding>
                {commands.map((c, i) => (
                  <ListItem key={c.name || c.command || i} disableGutters sx={{ py: 0.5 }}>
                    <ListItemText primary={`/${c.command || c.name}`} secondary={c.description || "暂无描述"} primaryTypographyProps={{ variant: "body2", fontWeight: 500, fontFamily: "monospace" }} secondaryTypographyProps={{ variant: "caption" }} />
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
