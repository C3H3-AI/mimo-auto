/**
 * Channel Settings Component
 * Configure Feishu, WeChat Work, and Personal WeChat channels
 */
import { useState, useEffect } from "react";
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
  Tab,
  Tabs,
} from "@mui/material";
import SaveIcon from "@mui/icons-material/Save";
import QRCode from "qrcode.react";

interface ChannelConfig {
  feishu_enabled: boolean;
  feishu_app_id: string;
  feishu_app_secret: string;
  wechat_enabled: boolean;
  wechat_corp_id: string;
  wechat_agent_id: string;
  wechat_secret: string;
  wechat_token: string;
  wechat_encoding_aes_key: string;
  personal_wechat_enabled: boolean;
}

interface LoginState {
  status: "idle" | "loading" | "qr_ready" | "scanning" | "success" | "error";
  qrCode?: string;
  sessionKey?: string;
  message?: string;
}

export function ChannelSettings() {
  const [tab, setTab] = useState(0);
  const [config, setConfig] = useState<ChannelConfig>({
    feishu_enabled: false,
    feishu_app_id: "",
    feishu_app_secret: "",
    wechat_enabled: false,
    wechat_corp_id: "",
    wechat_agent_id: "",
    wechat_secret: "",
    wechat_token: "",
    wechat_encoding_aes_key: "",
    personal_wechat_enabled: false,
  });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [loginState, setLoginState] = useState<LoginState>({ status: "idle" });

  // Load config from addon options
  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    try {
      // In HA addon, config comes from environment variables
      // For now, load from localStorage or default values
      const saved = localStorage.getItem("mimo_channel_config");
      if (saved) {
        setConfig(JSON.parse(saved));
      }
    } catch (err) {
      console.error("Failed to load config:", err);
    }
  };

  const saveConfig = async () => {
    setSaving(true);
    setMessage(null);

    try {
      // Save to localStorage for now
      localStorage.setItem("mimo_channel_config", JSON.stringify(config));

      // In production, this would call an API to update addon config
      setMessage({ type: "success", text: "配置已保存" });
    } catch (err) {
      setMessage({ type: "error", text: "保存失败" });
    } finally {
      setSaving(false);
    }
  };

  const startWechatLogin = async () => {
    setLoginState({ status: "loading" });

    try {
      const response = await fetch("/api/wechat/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "start" }),
      });

      const data = await response.json();

      if (data.qrcode) {
        setLoginState({
          status: "qr_ready",
          qrCode: data.qrcode_url,
          sessionKey: data.session_key,
        });
        // Start polling
        pollLoginStatus(data.session_key);
      } else {
        setLoginState({ status: "error", message: data.error || "获取二维码失败" });
      }
    } catch (err) {
      setLoginState({ status: "error", message: "连接服务器失败" });
    }
  };

  const pollLoginStatus = async (sessionKey: string) => {
    for (let i = 0; i < 120; i++) {
      try {
        const response = await fetch("/api/wechat/login/status", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_key: sessionKey }),
        });

        const data = await response.json();

        if (data.status === "success") {
          setLoginState({ status: "success", message: "登录成功！" });
          return;
        }

        if (data.status === "expired") {
          setLoginState({ status: "error", message: "二维码已过期" });
          return;
        }
      } catch (err) {
        // Ignore polling errors
      }

      await new Promise((r) => setTimeout(r, 2000));
    }

    setLoginState({ status: "error", message: "登录超时" });
  };

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

      {/* Feishu Settings */}
      {tab === 0 && (
        <Card>
          <CardContent>
            <FormControlLabel
              control={
                <Switch
                  checked={config.feishu_enabled}
                  onChange={(e) => setConfig({ ...config, feishu_enabled: e.target.checked })}
                />
              }
              label="启用飞书"
            />
            <TextField
              fullWidth
              size="small"
              label="App ID"
              value={config.feishu_app_id}
              onChange={(e) => setConfig({ ...config, feishu_app_id: e.target.value })}
              disabled={!config.feishu_enabled}
              sx={{ mt: 2 }}
            />
            <TextField
              fullWidth
              size="small"
              label="App Secret"
              type="password"
              value={config.feishu_app_secret}
              onChange={(e) => setConfig({ ...config, feishu_app_secret: e.target.value })}
              disabled={!config.feishu_enabled}
              sx={{ mt: 2 }}
            />
          </CardContent>
        </Card>
      )}

      {/* WeChat Work Settings */}
      {tab === 1 && (
        <Card>
          <CardContent>
            <FormControlLabel
              control={
                <Switch
                  checked={config.wechat_enabled}
                  onChange={(e) => setConfig({ ...config, wechat_enabled: e.target.checked })}
                />
              }
              label="启用企业微信"
            />
            <TextField
              fullWidth
              size="small"
              label="企业 ID (Corp ID)"
              value={config.wechat_corp_id}
              onChange={(e) => setConfig({ ...config, wechat_corp_id: e.target.value })}
              disabled={!config.wechat_enabled}
              sx={{ mt: 2 }}
            />
            <TextField
              fullWidth
              size="small"
              label="应用 ID (Agent ID)"
              value={config.wechat_agent_id}
              onChange={(e) => setConfig({ ...config, wechat_agent_id: e.target.value })}
              disabled={!config.wechat_enabled}
              sx={{ mt: 2 }}
            />
            <TextField
              fullWidth
              size="small"
              label="应用 Secret"
              type="password"
              value={config.wechat_secret}
              onChange={(e) => setConfig({ ...config, wechat_secret: e.target.value })}
              disabled={!config.wechat_enabled}
              sx={{ mt: 2 }}
            />
            <TextField
              fullWidth
              size="small"
              label="验证 Token"
              value={config.wechat_token}
              onChange={(e) => setConfig({ ...config, wechat_token: e.target.value })}
              disabled={!config.wechat_enabled}
              sx={{ mt: 2 }}
            />
            <TextField
              fullWidth
              size="small"
              label="编码 AES Key"
              value={config.wechat_encoding_aes_key}
              onChange={(e) => setConfig({ ...config, wechat_encoding_aes_key: e.target.value })}
              disabled={!config.wechat_enabled}
              sx={{ mt: 2 }}
            />
          </CardContent>
        </Card>
      )}

      {/* Personal WeChat Settings */}
      {tab === 2 && (
        <Card>
          <CardContent>
            <FormControlLabel
              control={
                <Switch
                  checked={config.personal_wechat_enabled}
                  onChange={(e) => setConfig({ ...config, personal_wechat_enabled: e.target.checked })}
                />
              }
              label="启用个人微信"
            />

            {config.personal_wechat_enabled && (
              <Box sx={{ mt: 2 }}>
                <Divider sx={{ mb: 2 }} />

                {loginState.status === "idle" && (
                  <Button variant="contained" onClick={startWechatLogin}>
                    扫码登录
                  </Button>
                )}

                {loginState.status === "loading" && (
                  <Box textAlign="center">
                    <CircularProgress size={24} />
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                      正在获取二维码...
                    </Typography>
                  </Box>
                )}

                {loginState.status === "qr_ready" && loginState.qrCode && (
                  <Box textAlign="center">
                    <Box sx={{ p: 2, bgcolor: "white", borderRadius: 2, display: "inline-block", mb: 2 }}>
                      <QRCode value={loginState.qrCode} size={200} />
                    </Box>
                    <Typography variant="body2" color="text.secondary">
                      请用微信扫描上方二维码
                    </Typography>
                  </Box>
                )}

                {loginState.status === "success" && (
                  <Alert severity="success">{loginState.message}</Alert>
                )}

                {loginState.status === "error" && (
                  <Box>
                    <Alert severity="error" sx={{ mb: 1 }}>{loginState.message}</Alert>
                    <Button variant="outlined" onClick={startWechatLogin}>
                      重试
                    </Button>
                  </Box>
                )}
              </Box>
            )}
          </CardContent>
        </Card>
      )}

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
