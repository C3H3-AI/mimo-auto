/**
 * DevicePanel — 实时显示 HA 设备状态，支持点击控制。
 * 从 /api/devices 获取数据，支持自动刷新。
 */
import { useState, useEffect, useCallback } from "react";
import {
  Box,
  Typography,
  IconButton,
  Switch,
  Slider,
  Chip,
  CircularProgress,
  Collapse,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import LightbulbIcon from "@mui/icons-material/Lightbulb";
import ThermostatIcon from "@mui/icons-material/Thermostat";
import PowerIcon from "@mui/icons-material/Power";
import { API_BASE_URL } from "../api/mimoClient";

interface Device {
  entity_id: string;
  domain: string;
  state: string;
  friendly_name: string;
  brightness?: number;
  temperature?: number;
  current_temperature?: number;
  humidity?: number;
  hvac_mode?: string;
}

const DOMAIN_ICONS: Record<string, string> = {
  light: "💡",
  climate: "🌡️",
  switch: "🔌",
  cover: "🪟",
  media_player: "📺",
  fan: "🌀",
  lock: "🔒",
  vacuum: "🤖",
  camera: "📷",
};

const DOMAIN_LABELS: Record<string, string> = {
  light: "灯光",
  climate: "温控",
  switch: "开关",
  cover: "窗帘",
  media_player: "媒体",
  fan: "风扇",
  lock: "门锁",
  vacuum: "扫地机",
  camera: "摄像头",
};

export function DevicePanel() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedDomains, setExpandedDomains] = useState<Record<string, boolean>>({});
  const [controlling, setControlling] = useState<string | null>(null);

  const fetchDevices = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/devices`);
      const data = await res.json();
      setDevices(data.devices || []);
    } catch (err) {
      console.error("Failed to fetch devices:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDevices();
    const interval = setInterval(fetchDevices, 10000); // 10s 刷新
    return () => clearInterval(interval);
  }, [fetchDevices]);

  const controlDevice = async (entityId: string, action: string) => {
    setControlling(entityId);
    try {
      await fetch(`${API_BASE_URL}/devices/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity_id: entityId, action }),
      });
      // Refresh after control
      setTimeout(fetchDevices, 500);
    } catch (err) {
      console.error("Control failed:", err);
    } finally {
      setControlling(null);
    }
  };

  // Group devices by domain
  const grouped = devices.reduce<Record<string, Device[]>>((acc, d) => {
    (acc[d.domain] = acc[d.domain] || []).push(d);
    return acc;
  }, {});

  const toggleDomain = (domain: string) => {
    setExpandedDomains((p) => ({ ...p, [domain]: !p[domain] }));
  };

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Header */}
      <Box sx={{ p: 1, borderBottom: "1px solid", borderColor: "divider", display: "flex", alignItems: "center" }}>
        <Typography variant="subtitle2" fontWeight={600} sx={{ flex: 1 }}>
          设备控制
        </Typography>
        <Chip size="small" label={`${devices.length} 台`} sx={{ mr: 1 }} />
        <IconButton size="small" onClick={fetchDevices} title="刷新">
          <RefreshIcon fontSize="small" />
        </IconButton>
      </Box>

      {/* Device List */}
      <Box sx={{ flex: 1, overflow: "auto" }}>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
            <CircularProgress size={20} />
          </Box>
        ) : devices.length === 0 ? (
          <Box sx={{ p: 2, textAlign: "center" }}>
            <Typography variant="body2" color="text.secondary">无设备</Typography>
          </Box>
        ) : (
          Object.entries(grouped).map(([domain, domainDevices]) => (
            <Box key={domain}>
              {/* Domain header */}
              <Box
                sx={{ p: 1, cursor: "pointer", display: "flex", alignItems: "center", "&:hover": { bgcolor: "action.hover" } }}
                onClick={() => toggleDomain(domain)}
              >
                <Typography variant="body2" sx={{ mr: 1 }}>{DOMAIN_ICONS[domain] || "📦"}</Typography>
                <Typography variant="body2" fontWeight={500} sx={{ flex: 1 }}>{DOMAIN_LABELS[domain] || domain}</Typography>
                <Chip size="small" label={domainDevices.length} sx={{ mr: 0.5 }} />
                {expandedDomains[domain] ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
              </Box>

              {/* Device items */}
              <Collapse in={expandedDomains[domain] !== false}>
                {domainDevices.map((device) => (
                  <DeviceItem
                    key={device.entity_id}
                    device={device}
                    controlling={controlling === device.entity_id}
                    onControl={controlDevice}
                  />
                ))}
              </Collapse>
            </Box>
          ))
        )}
      </Box>
    </Box>
  );
}

function DeviceItem({
  device,
  controlling,
  onControl,
}: {
  device: Device;
  controlling: boolean;
  onControl: (entityId: string, action: string) => void;
}) {
  const isOn = device.state === "on" || device.state === "open" || device.state === "unlocked";

  const renderControl = () => {
    switch (device.domain) {
      case "light":
        return (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Switch
              size="small"
              checked={isOn}
              onChange={() => onControl(device.entity_id, isOn ? "turn_off" : "turn_on")}
              disabled={controlling}
            />
            {device.brightness !== undefined && (
              <Slider
                size="small"
                value={device.brightness || 0}
                min={0} max={255}
                onChange={(_, v) => onControl(device.entity_id, "set_brightness")}
                sx={{ width: 80 }}
                disabled={controlling}
              />
            )}
          </Box>
        );
      case "switch":
      case "lock":
      case "cover":
        return (
          <Switch
            size="small"
            checked={isOn}
            onChange={() => onControl(device.entity_id, isOn ? "turn_off" : "turn_on")}
            disabled={controlling}
          />
        );
      case "climate":
        return (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Switch
              size="small"
              checked={isOn}
              onChange={() => onControl(device.entity_id, isOn ? "turn_off" : "turn_on")}
              disabled={controlling}
            />
            {device.current_temperature !== undefined && (
              <Typography variant="caption" color="text.secondary">
                {device.current_temperature}°C
              </Typography>
            )}
          </Box>
        );
      default:
        return (
          <Switch
            size="small"
            checked={isOn}
            onChange={() => onControl(device.entity_id, isOn ? "turn_off" : "turn_on")}
            disabled={controlling}
          />
        );
    }
  };

  return (
    <Box sx={{ px: 2, py: 0.5, display: "flex", alignItems: "center", "&:hover": { bgcolor: "action.hover" } }}>
      {controlling && <CircularProgress size={12} sx={{ mr: 1 }} />}
      <Typography variant="body2" sx={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {device.friendly_name}
      </Typography>
      {renderControl()}
    </Box>
  );
}
