/**
 * Channel Settings — multi-account with popup dialogs.
 * Personal WeChat: dialog shows QR → scan → name input → done.
 * Feishu/WeChat Work: dialog shows form fields.
 */
import { useState, useEffect, useRef, useCallback } from "react";
import {
  Box, Typography, TextField, Switch, FormControlLabel, Button, Divider,
  Alert, CircularProgress, Card, CardContent, Tabs, Tab, Chip, IconButton,
  Dialog, DialogTitle, DialogContent, DialogActions,
} from "@mui/material";
import SaveIcon from "@mui/icons-material/Save";
import RefreshIcon from "@mui/icons-material/Refresh";
import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import QRCode from "qrcode.react";
import { API_BASE_URL } from "../api/mimoClient";
const C_API = API_BASE_URL;

interface AccountEntry {
  type: string; id: string; label: string; enabled: boolean;
  show_reasoning: boolean; has_credentials: boolean; status: string; connected: boolean;
}

export function ChannelSettings() {
  const [tab, setTab] = useState(0);
  const [accounts, setAccounts] = useState<AccountEntry[]>([]);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogType, setDialogType] = useState<"feishu" | "wechat" | "personal_wechat">("feishu");
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

  const openAddDialog = (type: "feishu" | "wechat" | "personal_wechat") => {
    setDialogType(type);
    setDialogOpen(true);
  };

  const deleteAccount = async (type: string, id: string) => {
    if (!window.confirm(`删除账号 ${id}？`)) return;
    try {
      await fetch(`${C_API}/accounts/${type}/${id}`, { method: "DELETE" });
      loadAccounts();
    } catch { /* ignore */ }
  };

  const feishuList = accounts.filter((a) => a.type === "feishu");
  const wechatList = accounts.filter((a) => a.type === "wechat");
  const wxList = accounts.filter((a) => a.type === "personal_wechat");

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h6" gutterBottom>通道设置</Typography>
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label={`飞书 (${feishuList.length})`} />
        <Tab label={`企业微信 (${wechatList.length})`} />
        <Tab label={`个人微信 (${wxList.length})`} />
      </Tabs>

      {message && <Alert severity={message.type} sx={{ mb: 2 }} onClose={() => setMessage(null)}>{message.text}</Alert>}

      {tab === 0 && (
        <Box>
          {feishuList.map((acct) => (
            <AccountCard key={acct.id} acct={acct} onDelete={() => deleteAccount("feishu", acct.id)} />
          ))}
          <Button variant="outlined" startIcon={<AddIcon />} onClick={() => openAddDialog("feishu")}>添加飞书账号</Button>
        </Box>
      )}

      {tab === 1 && (
        <Box>
          {wechatList.map((acct) => (
            <AccountCard key={acct.id} acct={acct} onDelete={() => deleteAccount("wechat", acct.id)} />
          ))}
          <Button variant="outlined" startIcon={<AddIcon />} onClick={() => openAddDialog("wechat")}>添加企业微信账号</Button>
        </Box>
      )}

      {tab === 2 && (
        <Box>
          {wxList.map((acct) => (
            <AccountCard key={acct.id} acct={acct} onDelete={() => deleteAccount("personal_wechat", acct.id)} />
          ))}
          <Button variant="outlined" startIcon={<AddIcon />} onClick={() => openAddDialog("personal_wechat")}>添加微信账号</Button>
        </Box>
      )}

      <AddAccountDialog
        open={dialogOpen} type={dialogType}
        onClose={() => setDialogOpen(false)}
        onAdded={() => { setDialogOpen(false); loadAccounts(); }}
        setMessage={setMessage}
      />
    </Box>
  );
}

function AccountCard({ acct, onDelete }: { acct: AccountEntry; onDelete: () => void }) {
  return (
    <Card sx={{ mb: 2 }}>
      <CardContent>
        <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <Box>
            <Typography variant="subtitle2" fontWeight={600}>{acct.label || acct.id}</Typography>
            <Typography variant="caption" color="text.secondary">{acct.id}</Typography>
          </Box>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Chip size="small" label={acct.connected ? "已连接" : "未连接"} color={acct.connected ? "success" : "default"} />
            <IconButton size="small" onClick={onDelete}><DeleteIcon fontSize="small" /></IconButton>
          </Box>
        </Box>
      </CardContent>
    </Card>
  );
}

function AddAccountDialog({
  open, type, onClose, onAdded, setMessage,
}: {
  open: boolean;
  type: "feishu" | "wechat" | "personal_wechat";
  onClose: () => void;
  onAdded: () => void;
  setMessage: (m: { type: "success" | "error"; text: string }) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [label, setLabel] = useState("");
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [corpId, setCorpId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [secret, setSecret] = useState("");
  const [token, setToken] = useState("");
  const [aesKey, setAesKey] = useState("");

  // WeChat QR login states (stays in dialog)
  const [qrState, setQrState] = useState<"idle" | "loading" | "qr" | "naming" | "done">("idle");
  const [qrCodeUrl, setQrCodeUrl] = useState("");
  const [sessionKey, setSessionKey] = useState("");
  const [wxAccountId, setWxAccountId] = useState("");
  const [wxAccountName, setWxAccountName] = useState("");

  const title = type === "feishu" ? "添加飞书账号" : type === "wechat" ? "添加企业微信账号" : "添加微信账号";

  const resetWxState = () => {
    setQrState("idle");
    setQrCodeUrl("");
    setSessionKey("");
    setWxAccountId("");
    setWxAccountName("");
    setLabel("");
  };

  const handleClose = () => {
    resetWxState();
    onClose();
  };

  // Start WeChat QR login
  const startWxLogin = async () => {
    setQrState("loading");
    try {
      const res = await fetch(`${C_API}/accounts/personal_wechat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: label || undefined }),
      });
      const data = await res.json();
      if (data.qrcode) {
        setQrCodeUrl(data.qrcode_url);
        setSessionKey(data.session_key);
        setQrState("qr");
        // Start polling for scan result
        pollWxScan(data.session_key);
      } else {
        setMessage({ type: "error", text: data.error || "获取二维码失败" });
        setQrState("idle");
      }
    } catch {
      setMessage({ type: "error", text: "连接服务器失败" });
      setQrState("idle");
    }
  };

  // Poll for scan confirmation (long poll)
  const pollWxScan = async (sk: string) => {
    try {
      const res = await fetch(`${C_API}/wechat/login/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_key: sk }),
      });
      const data = await res.json();
      if (data.status === "success") {
        // Login success — show naming step
        setWxAccountId(data.account_id || "");
        setQrState("naming");
      } else if (data.status === "expired") {
        setMessage({ type: "error", text: "二维码已过期" });
        setQrState("idle");
      } else {
        setMessage({ type: "error", text: data.message || "登录超时" });
        setQrState("idle");
      }
    } catch {
      setMessage({ type: "error", text: "连接失败" });
      setQrState("idle");
    }
  };

  // Submit name for WeChat account
  const submitWxName = async () => {
    setLoading(true);
    try {
      // The account is already added by the backend during QR login
      // Just update the label
      if (wxAccountId) {
        await fetch(`${C_API}/accounts/personal_wechat/${wxAccountId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ label: wxAccountName || label || wxAccountId }),
        });
      }
      setMessage({ type: "success", text: `微信账号已添加：${wxAccountId}` });
      setQrState("done");
      setTimeout(() => { onAdded(); }, 1000);
    } catch {
      setMessage({ type: "error", text: "保存失败" });
    } finally {
      setLoading(false);
    }
  };

  // Submit for Feishu / WeChat Work
  const handleSubmit = async () => {
    setLoading(true);
    try {
      const body: any = { label: label || undefined, enabled: true };
      if (type === "feishu") {
        body.app_id = appId;
        body.app_secret = appSecret;
      } else {
        body.corp_id = corpId;
        body.agent_id = agentId;
        body.secret = secret;
        body.token = token;
        body.encoding_aes_key = aesKey;
      }
      const res = await fetch(`${C_API}/accounts/${type}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (data.success || data.account_id) {
        setMessage({ type: "success", text: "账号已添加" });
        onAdded();
      } else {
        setMessage({ type: "error", text: data.error || "添加失败" });
      }
    } catch {
      setMessage({ type: "error", text: "连接服务器失败" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent sx={{ minHeight: qrState === "qr" ? 350 : undefined }}>

        {/* Feishu form */}
        {type === "feishu" && (
          <>
            <TextField fullWidth size="small" label="备注名（可选）" value={label} onChange={(e) => setLabel(e.target.value)} sx={{ mt: 1 }} />
            <TextField fullWidth size="small" label="App ID" value={appId} onChange={(e) => setAppId(e.target.value)} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="App Secret" type="password" value={appSecret} onChange={(e) => setAppSecret(e.target.value)} sx={{ mt: 2 }} />
            <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
              在飞书开放平台「事件订阅」中开启「长连接」模式。
            </Typography>
          </>
        )}

        {/* WeChat Work form */}
        {type === "wechat" && (
          <>
            <TextField fullWidth size="small" label="备注名（可选）" value={label} onChange={(e) => setLabel(e.target.value)} sx={{ mt: 1 }} />
            <TextField fullWidth size="small" label="Corp ID" value={corpId} onChange={(e) => setCorpId(e.target.value)} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="Agent ID" value={agentId} onChange={(e) => setAgentId(e.target.value)} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="Secret" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="Token" value={token} onChange={(e) => setToken(e.target.value)} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="Encoding AES Key" value={aesKey} onChange={(e) => setAesKey(e.target.value)} sx={{ mt: 2 }} />
          </>
        )}

        {/* Personal WeChat — multi-step flow inside dialog */}
        {type === "personal_wechat" && (
          <Box>
            {/* Step 0: idle — show name input + start button */}
            {qrState === "idle" && (
              <>
                <TextField fullWidth size="small" label="备注名（可选）" value={label} onChange={(e) => setLabel(e.target.value)} sx={{ mt: 1 }} />
                <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
                  点击下方按钮获取二维码，请用微信扫码登录。
                </Typography>
              </>
            )}

            {/* Step 1: loading */}
            {qrState === "loading" && (
              <Box textAlign="center" py={3}>
                <CircularProgress size={32} />
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>正在获取二维码...</Typography>
              </Box>
            )}

            {/* Step 2: showing QR code */}
            {qrState === "qr" && (
              <Box textAlign="center" py={2}>
                <Box sx={{ p: 2, bgcolor: "white", borderRadius: 2, display: "inline-block", mb: 2 }}>
                  <QRCode value={qrCodeUrl} size={200} />
                </Box>
                <Typography variant="body2" color="text.secondary">请用微信扫描上方二维码</Typography>
                <CircularProgress size={20} sx={{ mt: 1 }} />
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>等待扫码确认中...</Typography>
              </Box>
            )}

            {/* Step 3: naming — after scan success */}
            {qrState === "naming" && (
              <>
                <Alert severity="success" sx={{ mb: 2 }}>微信扫码成功！</Alert>
                <TextField fullWidth size="small" label="微信账号" value={wxAccountId} disabled sx={{ mb: 2 }} />
                <TextField fullWidth size="small" label="备注名称" value={wxAccountName} onChange={(e) => setWxAccountName(e.target.value)}
                  placeholder={label || wxAccountId} sx={{ mb: 1 }} />
                <Typography variant="caption" color="text.secondary">给这个微信账号起个名字，方便识别。</Typography>
              </>
            )}

            {/* Step 4: done */}
            {qrState === "done" && (
              <Alert severity="success">微信账号已添加完成！</Alert>
            )}
          </Box>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={handleClose}>{qrState === "done" ? "关闭" : "取消"}</Button>

        {/* Feishu / WeChat Work: submit button */}
        {(type === "feishu" || type === "wechat") && (
          <Button variant="contained" onClick={handleSubmit} disabled={loading}>
            {loading ? <CircularProgress size={16} sx={{ mr: 1 }} /> : null}
            添加
          </Button>
        )}

        {/* Personal WeChat: step-dependent buttons */}
        {type === "personal_wechat" && qrState === "idle" && (
          <Button variant="contained" onClick={startWxLogin}>
            获取二维码
          </Button>
        )}
        {type === "personal_wechat" && qrState === "naming" && (
          <Button variant="contained" onClick={submitWxName} disabled={loading}>
            {loading ? <CircularProgress size={16} sx={{ mr: 1 }} /> : null}
            确认添加
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
