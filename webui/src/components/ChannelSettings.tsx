/**
 * Channel Settings Component
 * Configure Feishu (WebSocket long-connection) channel and persist to the addon.
 */
import { useState, useEffect, useRef } from "react";
import {
  Box,
  Typography,
  TextField,
  Switch,
  FormControlLabel,
  Button,
  Divider,
  Alert,
  CircularProgress,
  Card,
  CardContent,
  Tabs,
  Tab,
  Chip,
} from "@mui/material";
import SaveIcon from "@mui/icons-material/Save";
import RefreshIcon from "@mui/icons-material/Refresh";
import QRCode from "qrcode.react";
import { API_BASE_URL } from "../api/mimoClient";
const C_API = API_BASE_URL;

interface ChannelConfig {
  feishu: { enabled: boolean; app_id: string; app_secret: string };
  wechat: {
    enabled: boolean;
    corp_id: string;
    agent_id: string;
    secret: string;
    token: string;
    encoding_aes_key: string;
  };
  personal_wechat: { enabled: boolean };
  ha_mcp_url: string;
}

interface StatusMap {
  [key: string]: { connected: boolean; status?: string; error?: string | null };
}

const EMPTY: ChannelConfig = {
  feishu: { enabled: false, app_id: "", app_secret: "" },
  wechat: {
    enabled: false,
    corp_id: "",
    agent_id: "",
    secret: "",
    token: "",
    encoding_aes_key: "",
  },
  personal_wechat: { enabled: false },
  ha_mcp_url: "",
};

export function ChannelSettings() {
  const [tab, setTab] = useState(0);
  const [config, setConfig] = useState<ChannelConfig>(EMPTY);
  const [status, setStatus] = useState<StatusMap>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error" | "info"; text: string } | null>(null);
  const [loginState, setLoginState] = useState<{ status: string; message?: string; qrCode?: string; sessionKey?: string }>({ status: "idle" });
  const pollRef = useRef<number | null>(null);

  // Load config + start status polling
  useEffect(() => {
    loadConfig();
    pollStatus();
    pollRef.current = window.setInterval(pollStatus, 5000);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadConfig = async () => {
    try {
      const res = await fetch(`${C_API}/channels`);
      const data = await res.json();
      if (data.channels) {
        setConfig({
          feishu: { ...EMPTY.feishu, ...(data.channels.feishu || {}) },
          wechat: { ...EMPTY.wechat, ...(data.channels.wechat || {}) },
          personal_wechat: { ...EMPTY.personal_wechat, ...(data.channels.personal_wechat || {}) },
          ha_mcp_url: (data.channels.ha_mcp_url || (data.ha_mcp_url || "")),
        });
      }
    } catch (err) {
      console.error("加载通道配置失败:", err);
    }
  };

  const pollStatus = async () => {
    try {
      const res = await fetch(`${C_API}/channels/status`);
      const data = await res.json();
      setStatus(data.status || {});
    } catch {
      /* ignore */
    }
  };

  const saveConfig = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const res = await fetch(`${C_API}/channels`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channels: config }),
      });
      const data = await res.json();
      if (data.success) {
        setMessage({ type: "success", text: "配置已保存，通道已重新加载" });
        setStatus(data.status || {});
      } else {
        setMessage({ type: "error", text: data.error || "保存失败" });
      }
    } catch (err) {
      setMessage({ type: "error", text: "保存失败：无法连接服务器" });
    } finally {
      setSaving(false);
    }
  };

  const testFeishu = async () => {
    setTesting(true);
    setMessage(null);
    try {
      const res = await fetch(`${C_API}/feishu/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          app_id: config.feishu.app_id,
          app_secret: config.feishu.app_secret,
        }),
      });
      const data = await res.json();
      if (data.success) {
        setMessage({ type: "success", text: "飞书长连接测试成功，已收到事件通道" });
      } else {
        setMessage({ type: "error", text: `飞书连接失败：${data.error || "未知错误"}` });
      }
    } catch {
      setMessage({ type: "error", text: "测试失败：无法连接服务器" });
    } finally {
      setTesting(false);
    }
  };

  const setFeishu = (patch: Partial<ChannelConfig["feishu"]>) =>
    setConfig({ ...config, feishu: { ...config.feishu, ...patch } });
  const setWechat = (patch: Partial<ChannelConfig["wechat"]>) =>
    setConfig({ ...config, wechat: { ...config.wechat, ...patch } });

  const startWechatLogin = async () => {
    setLoginState({ status: "loading" });
    try {
      const response = await fetch(`${C_API}/wechat/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "start" }),
      });
      const data = await response.json();
      if (data.qrcode) {
        setLoginState({ status: "qr_ready", qrCode: data.qrcode_url, sessionKey: data.session_key });
        // Start polling immediately — confirmed status may be transient
        confirmWechatLogin(data.session_key);
      } else {
        setLoginState({ status: "error", message: data.error || "获取二维码失败" });
      }
    } catch {
      setLoginState({ status: "error", message: "连接服务器失败" });
    }
  };

  const confirmWechatLogin = async (sessionKey: string) => {
    setLoginState({ status: "waiting", sessionKey });
    // Single long request — server waits up to 8 minutes (same as cn_im_hub)
    try {
      const response = await fetch(`${C_API}/wechat/login/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_key: sessionKey }),
      });
      const data = await response.json();
      if (data.status === "success") {
        setLoginState({ status: "success", message: "登录成功！" });
      } else if (data.status === "expired") {
        setLoginState({ status: "error", message: "二维码已过期" });
      } else {
        setLoginState({ status: "error", message: data.message || "登录超时" });
      }
    } catch {
      setLoginState({ status: "error", message: "连接服务器失败" });
    }
  };

  const feishuStatus = status["feishu"];
  const wechatStatus = status["personal_wechat_default"];

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h6" gutterBottom>
        通道设置
      </Typography>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="飞书" />
        <Tab label="企业微信" />
        <Tab label="个人微信" />
      </Tabs>

      {message && (
        <Alert severity={message.type} sx={{ mb: 2 }} onClose={() => setMessage(null)}>
          {message.text}
        </Alert>
      )}

      {/* Feishu */}
      {tab === 0 && (
        <Card>
          <CardContent>
            <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <FormControlLabel
                control={
                  <Switch
                    checked={config.feishu.enabled}
                    onChange={(e) => setFeishu({ enabled: e.target.checked })}
                  />
                }
                label="启用飞书"
              />
              {feishuStatus && (
                <Chip
                  size="small"
                  label={feishuStatus.connected ? "已连接" : feishuStatus.status === "pending_login" ? "待登录" : "未连接"}
                  color={feishuStatus.connected ? "success" : "default"}
                />
              )}
            </Box>

            <TextField
              fullWidth size="small" label="App ID"
              value={config.feishu.app_id}
              onChange={(e) => setFeishu({ app_id: e.target.value })}
              disabled={!config.feishu.enabled}
              sx={{ mt: 2 }}
            />
            <TextField
              fullWidth size="small" label="App Secret" type="password"
              value={config.feishu.app_secret}
              onChange={(e) => setFeishu({ app_secret: e.target.value })}
              disabled={!config.feishu.enabled}
              helperText="留空表示不修改已保存的密钥"
              sx={{ mt: 2 }}
            />

            {feishuStatus?.error && (
              <Alert severity="warning" sx={{ mt: 2 }}>
                {feishuStatus.error}
              </Alert>
            )}

            <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
              飞书采用官方 WebSocket 长连接，无需公网 IP / 域名。
              请在飞书开放平台「事件订阅」中开启「长连接」模式，并订阅 <code>im.message.receive_v1</code> 消息事件。
            </Typography>

            <Box sx={{ mt: 2, display: "flex", gap: 1 }}>
              <Button variant="outlined" onClick={testFeishu} disabled={testing || !config.feishu.enabled}>
                {testing ? <CircularProgress size={16} /> : "测试连接"}
              </Button>
              <Button variant="outlined" onClick={pollStatus} startIcon={<RefreshIcon />}>
                刷新状态
              </Button>
            </Box>
          </CardContent>
        </Card>
      )}

      {/* WeChat Work */}
      {tab === 1 && (
        <Card>
          <CardContent>
            <FormControlLabel
              control={
                <Switch
                  checked={config.wechat.enabled}
                  onChange={(e) => setWechat({ enabled: e.target.checked })}
                />
              }
              label="启用企业微信"
            />
            <TextField fullWidth size="small" label="企业 ID (Corp ID)" value={config.wechat.corp_id}
              onChange={(e) => setWechat({ corp_id: e.target.value })} disabled={!config.wechat.enabled} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="应用 ID (Agent ID)" value={config.wechat.agent_id}
              onChange={(e) => setWechat({ agent_id: e.target.value })} disabled={!config.wechat.enabled} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="应用 Secret" type="password" value={config.wechat.secret}
              onChange={(e) => setWechat({ secret: e.target.value })} disabled={!config.wechat.enabled} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="验证 Token" value={config.wechat.token}
              onChange={(e) => setWechat({ token: e.target.value })} disabled={!config.wechat.enabled} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="编码 AES Key" value={config.wechat.encoding_aes_key}
              onChange={(e) => setWechat({ encoding_aes_key: e.target.value })} disabled={!config.wechat.enabled} sx={{ mt: 2 }} />
            <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
              企业微信需公网回调地址（Webhook）。家庭网络下建议优先使用飞书长连接。
            </Typography>
          </CardContent>
        </Card>
      )}

      {/* Personal WeChat */}
      {tab === 2 && (
        <Card>
          <CardContent>
            <FormControlLabel
              control={
                <Switch
                  checked={config.personal_wechat.enabled}
                  onChange={(e) => setConfig({ ...config, personal_wechat: { enabled: e.target.checked } })}
                />
              }
              label="启用个人微信"
            />
            {config.personal_wechat.enabled && (
              <Box sx={{ mt: 2 }}>
                <Divider sx={{ mb: 2 }} />
                {loginState.status === "idle" && wechatStatus?.connected && (
                  <Alert severity="success" sx={{ mb: 1 }}>已连接</Alert>
                )}
                {loginState.status === "idle" && !wechatStatus?.connected && (
                  <Button variant="contained" onClick={startWechatLogin}>扫码登录</Button>
                )}
                {loginState.status === "loading" && (
                  <Box textAlign="center">
                    <CircularProgress size={24} />
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>正在获取二维码...</Typography>
                  </Box>
                )}
                {loginState.status === "qr_ready" && loginState.qrCode && (
                  <Box textAlign="center">
                    <Box sx={{ p: 2, bgcolor: "white", borderRadius: 2, display: "inline-block", mb: 2 }}>
                      <QRCode value={loginState.qrCode} size={200} />
                    </Box>
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>请用微信扫描上方二维码</Typography>
                    <CircularProgress size={20} />
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>等待扫码确认中...</Typography>
                  </Box>
                )}
                {loginState.status === "success" && <Alert severity="success">{loginState.message}</Alert>}
                {loginState.status === "error" && (
                  <Box>
                    <Alert severity="error" sx={{ mb: 1 }}>{loginState.message}</Alert>
                    <Button variant="outlined" onClick={startWechatLogin}>重试</Button>
                  </Box>
                )}
              </Box>
            )}
          </CardContent>
        </Card>
      )}

      {/* HA-MCP URL */}
      <Card sx={{ mt: 2 }}>
        <CardContent>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>Home Assistant MCP</Typography>
          <TextField
            fullWidth
            size="small"
            label="ha-mcp Webhook URL"
            placeholder="https://api.homediy.top:8443/api/webhook/mcp_..."
            value={config.ha_mcp_url}
            onChange={(e) => setConfig({ ...config, ha_mcp_url: e.target.value })}
            helperText="配置后重启 add-on 生效，mimo 可通过 ha-mcp 直接控制 HA"
          />
        </CardContent>
      </Card>

      <Box sx={{ mt: 3, display: "flex", justifyContent: "flex-end" }}>
        <Button
          variant="contained"
          startIcon={saving ? <CircularProgress size={16} /> : <SaveIcon />}
          onClick={saveConfig}
          disabled={saving}
        >
          {saving ? "保存中..." : "保存配置"}
        </Button>
      </Box>
    </Box>
  );
}
