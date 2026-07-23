/**
 * Channel Settings — unified "Add Account" button + grouped collapsible account list.
 * All channel types in one flat view, grouped by type with collapsible sections.
 */
import { useState, useEffect, useRef, useCallback } from "react";
import {
  Box, Typography, TextField, Button, Divider, Alert, CircularProgress,
  Card, CardContent, Chip, IconButton, Dialog, DialogTitle, DialogContent, DialogActions,
  Collapse, List, ListItem, ListItemText, ListItemSecondaryAction,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ChatIcon from "@mui/icons-material/Chat";
import WorkIcon from "@mui/icons-material/Work";
import PhoneAndroidIcon from "@mui/icons-material/PhoneAndroid";
import QRCode from "qrcode.react";
import { API_BASE_URL } from "../api/mimoClient";
const C_API = API_BASE_URL;

interface AccountEntry {
  type: string; id: string; label: string; enabled: boolean;
  show_reasoning: boolean; has_credentials: boolean; status: string; connected: boolean;
}

const CHANNEL_TYPES = [
  { type: "feishu" as const, label: "飞书", icon: <ChatIcon fontSize="small" /> },
  { type: "wechat" as const, label: "企业微信", icon: <WorkIcon fontSize="small" /> },
  { type: "personal_wechat" as const, label: "个人微信", icon: <PhoneAndroidIcon fontSize="small" /> },
];

const TYPE_LABELS: Record<string, string> = { feishu: "飞书", wechat: "企业微信", personal_wechat: "个人微信" };

export function ChannelSettings() {
  const [accounts, setAccounts] = useState<AccountEntry[]>([]);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ feishu: true, wechat: true, personal_wechat: true });
  const pollRef = useRef<number | null>(null);

  const loadAccounts = useCallback(async () => {
    try {
      const res = await fetch(`${C_API}/accounts`);
      const data = await res.json();
      setAccounts(data.accounts || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    loadAccounts();
    pollRef.current = window.setInterval(loadAccounts, 5000);
    return () => { if (pollRef.current) window.clearInterval(pollRef.current); };
  }, [loadAccounts]);

  const deleteAccount = async (type: string, id: string) => {
    if (!window.confirm(`删除账号 ${id}？`)) return;
    try {
      await fetch(`${C_API}/accounts/${type}/${id}`, { method: "DELETE" });
      loadAccounts();
    } catch { /* ignore */ }
  };

  const toggleExpand = (type: string) => setExpanded((p) => ({ ...p, [type]: !p[type] }));

  // Group accounts by type
  const grouped = CHANNEL_TYPES.map((ct) => ({
    ...ct,
    accounts: accounts.filter((a) => a.type === ct.type),
  }));

  return (
    <Box sx={{ p: 2 }}>
      {message && <Alert severity={message.type} sx={{ mb: 2 }} onClose={() => setMessage(null)}>{message.text}</Alert>}

      {/* Grouped account list */}
      {grouped.map((group) => (
        <Card key={group.type} sx={{ mb: 2 }}>
          <CardContent sx={{ pb: "8px !important" }}>
            <Box sx={{ display: "flex", alignItems: "center", cursor: "pointer" }} onClick={() => toggleExpand(group.type)}>
              {group.icon}
              <Typography variant="subtitle1" fontWeight={600} sx={{ ml: 1, flex: 1 }}>
                {group.label}
              </Typography>
              <Chip size="small" label={`${group.accounts.length} 个`} sx={{ mr: 1 }} />
              {expanded[group.type] ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            </Box>
            <Collapse in={expanded[group.type]}>
              <List dense disablePadding sx={{ mt: 1 }}>
                {group.accounts.length === 0 ? (
                  <ListItem>
                    <ListItemText primary="暂无账号" primaryTypographyProps={{ color: "text.secondary", variant: "body2" }} />
                  </ListItem>
                ) : (
                  group.accounts.map((acct) => (
                    <ListItem key={acct.id} sx={{ py: 0.5 }}>
                      <ListItemText
                        primary={acct.label || acct.id}
                        secondary={acct.id}
                        primaryTypographyProps={{ variant: "body2", fontWeight: 500 }}
                        secondaryTypographyProps={{ variant: "caption" }}
                      />
                      <ListItemSecondaryAction>
                        <Chip size="small" label={acct.connected ? "已连接" : "未连接"} color={acct.connected ? "success" : "default"} sx={{ mr: 1 }} />
                        <IconButton size="small" onClick={() => deleteAccount(acct.type, acct.id)}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </ListItemSecondaryAction>
                    </ListItem>
                  ))
                )}
              </List>
            </Collapse>
          </CardContent>
        </Card>
      ))}

      {/* Single Add Account button */}
      <Button variant="contained" startIcon={<AddIcon />} onClick={() => setAddDialogOpen(true)} sx={{ mt: 1 }}>
        添加账号
      </Button>

      {/* Add Account Dialog — Step 1: Choose type */}
      <AddAccountDialog
        open={addDialogOpen}
        onClose={() => setAddDialogOpen(false)}
        onAdded={() => { setAddDialogOpen(false); loadAccounts(); }}
        setMessage={setMessage}
      />
    </Box>
  );
}

function AddAccountDialog({
  open, onClose, onAdded, setMessage,
}: {
  open: boolean;
  onClose: () => void;
  onAdded: () => void;
  setMessage: (m: { type: "success" | "error"; text: string }) => void;
}) {
  const [step, setStep] = useState<"choose" | "form" | "restart">("choose");
  const [selectedType, setSelectedType] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [restarting, setRestarting] = useState(false);

  // Form fields
  const [label, setLabel] = useState("");
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [corpId, setCorpId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [secret, setSecret] = useState("");
  const [token, setToken] = useState("");
  const [aesKey, setAesKey] = useState("");

  // WeChat QR states
  const [qrState, setQrState] = useState<"idle" | "loading" | "qr" | "naming" | "done">("idle");
  const [qrCodeUrl, setQrCodeUrl] = useState("");
  const [sessionKey, setSessionKey] = useState("");
  const [wxAccountId, setWxAccountId] = useState("");
  const [wxAccountName, setWxAccountName] = useState("");

  const resetAll = () => {
    setStep("choose");
    setSelectedType("");
    setLabel(""); setAppId(""); setAppSecret("");
    setCorpId(""); setAgentId(""); setSecret(""); setToken(""); setAesKey("");
    setQrState("idle"); setQrCodeUrl(""); setSessionKey("");
    setWxAccountId(""); setWxAccountName("");
  };

  const handleClose = () => { resetAll(); onClose(); };

  const handleChooseType = (type: string) => {
    setSelectedType(type);
    setStep("form");
  };

  // ---- Feishu / WeChat Work submit ----
  const handleSubmit = async () => {
    setLoading(true);
    try {
      const body: any = { label: label || undefined, enabled: true };
      if (selectedType === "feishu") {
        body.app_id = appId; body.app_secret = appSecret;
      } else {
        body.corp_id = corpId; body.agent_id = agentId;
        body.secret = secret; body.token = token; body.encoding_aes_key = aesKey;
      }
      const res = await fetch(`${C_API}/accounts/${selectedType}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (data.success || data.account_id) {
        setStep("restart");
      } else {
        setMessage({ type: "error", text: data.error || "添加失败" });
      }
    } catch {
      setMessage({ type: "error", text: "连接服务器失败" });
    } finally { setLoading(false); }
  };

  // ---- WeChat QR login ----
  const startWxLogin = async () => {
    setQrState("loading");
    try {
      const res = await fetch(`${C_API}/accounts/personal_wechat`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: label || undefined }),
      });
      const data = await res.json();
      if (data.qrcode) {
        setQrCodeUrl(data.qrcode_url); setSessionKey(data.session_key);
        setQrState("qr"); pollWxScan(data.session_key);
      } else {
        setMessage({ type: "error", text: data.error || "获取二维码失败" });
        setQrState("idle");
      }
    } catch {
      setMessage({ type: "error", text: "连接服务器失败" });
      setQrState("idle");
    }
  };

  const pollWxScan = async (sk: string) => {
    try {
      const res = await fetch(`${C_API}/wechat/login/status`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_key: sk }),
      });
      const data = await res.json();
      if (data.status === "success") {
        setWxAccountId(data.account_id || ""); setQrState("naming");
      } else if (data.status === "expired") {
        setMessage({ type: "error", text: "二维码已过期" }); setQrState("idle");
      } else {
        setMessage({ type: "error", text: data.message || "登录超时" }); setQrState("idle");
      }
    } catch {
      setMessage({ type: "error", text: "连接失败" }); setQrState("idle");
    }
  };

  const submitWxName = async () => {
    setLoading(true);
    try {
      if (wxAccountId) {
        await fetch(`${C_API}/accounts/personal_wechat/${wxAccountId}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ label: wxAccountName || label || wxAccountId }),
        });
      }
      setQrState("done"); setStep("restart");
    } catch { setMessage({ type: "error", text: "保存失败" }); }
    finally { setLoading(false); }
  };

  // ---- Restart ----
  const handleRestart = async () => {
    setRestarting(true);
    try {
      await fetch(`${C_API}/channels/restart`, { method: "POST" });
      setMessage({ type: "success", text: "正在重启，页面将自动刷新..." });
      setTimeout(() => window.location.reload(), 3000);
    } catch {
      setMessage({ type: "success", text: "请手动重启 addon 后刷新页面" });
      setStep("choose"); resetAll(); onAdded();
    } finally { setRestarting(false); }
  };

  const title = step === "choose" ? "添加账号" : step === "restart" ? "重启生效" : `添加${TYPE_LABELS[selectedType] || ""}`;

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent sx={{ minHeight: step === "form" && selectedType === "personal_wechat" && qrState === "qr" ? 350 : undefined }}>

        {/* Step 1: Choose type */}
        {step === "choose" && (
          <List>
            {CHANNEL_TYPES.map((ct) => (
              <ListItem key={ct.type} button onClick={() => handleChooseType(ct.type)} sx={{ borderRadius: 1, mb: 1, border: "1px solid", borderColor: "divider" }}>
                {ct.icon}
                <ListItemText primary={ct.label} sx={{ ml: 1 }} />
              </ListItem>
            ))}
          </List>
        )}

        {/* Step 2: Form */}
        {step === "form" && (
          <Box>
            <TextField fullWidth size="small" label="备注名（可选）" value={label} onChange={(e) => setLabel(e.target.value)} sx={{ mt: 1 }} />

            {/* Feishu form */}
            {selectedType === "feishu" && (<>
              <TextField fullWidth size="small" label="App ID" value={appId} onChange={(e) => setAppId(e.target.value)} sx={{ mt: 2 }} />
              <TextField fullWidth size="small" label="App Secret" type="password" value={appSecret} onChange={(e) => setAppSecret(e.target.value)} sx={{ mt: 2 }} />
              <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>在飞书开放平台「事件订阅」中开启「长连接」模式。</Typography>
            </>)}

            {/* WeChat Work form */}
            {selectedType === "wechat" && (<>
              <TextField fullWidth size="small" label="Corp ID" value={corpId} onChange={(e) => setCorpId(e.target.value)} sx={{ mt: 2 }} />
              <TextField fullWidth size="small" label="Agent ID" value={agentId} onChange={(e) => setAgentId(e.target.value)} sx={{ mt: 2 }} />
              <TextField fullWidth size="small" label="Secret" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} sx={{ mt: 2 }} />
              <TextField fullWidth size="small" label="Token" value={token} onChange={(e) => setToken(e.target.value)} sx={{ mt: 2 }} />
              <TextField fullWidth size="small" label="Encoding AES Key" value={aesKey} onChange={(e) => setAesKey(e.target.value)} sx={{ mt: 2 }} />
            </>)}

            {/* Personal WeChat flow */}
            {selectedType === "personal_wechat" && (<Box>
              {qrState === "idle" && <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>点击下方按钮获取二维码，请用微信扫码登录。</Typography>}
              {qrState === "loading" && <Box textAlign="center" py={3}><CircularProgress size={32} /><Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>正在获取二维码...</Typography></Box>}
              {qrState === "qr" && (
                <Box textAlign="center" py={2}>
                  <Box sx={{ p: 2, bgcolor: "white", borderRadius: 2, display: "inline-block", mb: 2 }}><QRCode value={qrCodeUrl} size={200} /></Box>
                  <Typography variant="body2" color="text.secondary">请用微信扫描上方二维码</Typography>
                  <CircularProgress size={20} sx={{ mt: 1 }} />
                  <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>等待扫码确认中...</Typography>
                </Box>
              )}
              {qrState === "naming" && (<>
                <Alert severity="success" sx={{ mb: 2 }}>微信扫码成功！</Alert>
                <TextField fullWidth size="small" label="微信账号" value={wxAccountId} disabled sx={{ mb: 2 }} />
                <TextField fullWidth size="small" label="备注名称" value={wxAccountName} onChange={(e) => setWxAccountName(e.target.value)} placeholder={label || wxAccountId} sx={{ mb: 1 }} />
                <Typography variant="caption" color="text.secondary">给这个微信账号起个名字，方便识别。</Typography>
              </>)}
            </Box>)}
          </Box>
        )}

        {/* Step 3: Restart */}
        {step === "restart" && (
          <Alert severity="info" sx={{ mt: 1 }}>新账号已保存，需要重启 addon 才能生效。</Alert>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={handleClose}>{step === "restart" ? "稍后重启" : "取消"}</Button>

        {step === "choose" && null}

        {step === "form" && (<>
          {(selectedType === "feishu" || selectedType === "wechat") && (
            <Button variant="contained" onClick={handleSubmit} disabled={loading}>
              {loading ? <CircularProgress size={16} sx={{ mr: 1 }} /> : null}添加
            </Button>
          )}
          {selectedType === "personal_wechat" && qrState === "idle" && (
            <Button variant="contained" onClick={startWxLogin}>获取二维码</Button>
          )}
          {selectedType === "personal_wechat" && qrState === "naming" && (
            <Button variant="contained" onClick={submitWxName} disabled={loading}>
              {loading ? <CircularProgress size={16} sx={{ mr: 1 }} /> : null}确认添加
            </Button>
          )}
        </>)}

        {step === "restart" && (
          <Button variant="contained" color="warning" onClick={handleRestart} disabled={restarting}>
            {restarting ? <CircularProgress size={16} sx={{ mr: 1 }} /> : null}立即重启
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
