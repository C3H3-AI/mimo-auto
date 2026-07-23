/**
 * Channel Settings — multi-account with popup dialogs.
 * Each channel tab: list of accounts + "Add Account" button → popup form.
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
  const [loginStates, setLoginStates] = useState<Record<string, { status: string; qrCode?: string; sessionKey?: string; message?: string }>>({});
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

      {/* Feishu */}
      {tab === 0 && (
        <Box>
          {feishuList.map((acct) => (
            <AccountCard key={acct.id} acct={acct} onDelete={() => deleteAccount("feishu", acct.id)} />
          ))}
          <Button variant="outlined" startIcon={<AddIcon />} onClick={() => openAddDialog("feishu")}>添加飞书账号</Button>
        </Box>
      )}

      {/* WeChat Work */}
      {tab === 1 && (
        <Box>
          {wechatList.map((acct) => (
            <AccountCard key={acct.id} acct={acct} onDelete={() => deleteAccount("wechat", acct.id)} />
          ))}
          <Button variant="outlined" startIcon={<AddIcon />} onClick={() => openAddDialog("wechat")}>添加企业微信账号</Button>
        </Box>
      )}

      {/* Personal WeChat */}
      {tab === 2 && (
        <Box>
          {wxList.map((acct) => {
            const ls = loginStates[acct.id];
            return (
              <Card key={acct.id} sx={{ mb: 2 }}>
                <CardContent>
                  <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <Typography variant="subtitle2" fontWeight={600}>{acct.label || acct.id}</Typography>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Chip size="small"
                        label={acct.connected ? "已连接" : ls?.status === "waiting" ? "扫码中..." : "未连接"}
                        color={acct.connected ? "success" : "default"} />
                      <IconButton size="small" onClick={() => deleteAccount("personal_wechat", acct.id)}><DeleteIcon fontSize="small" /></IconButton>
                    </Box>
                  </Box>
                  <Divider sx={{ my: 1 }} />
                  {!acct.connected && !ls && (
                    <Button size="small" variant="contained" onClick={() => startWxLogin(acct.id)}>扫码登录</Button>
                  )}
                  {ls?.status === "loading" && <CircularProgress size={20} />}
                  {ls?.qrCode && (ls.status === "qr_ready" || ls.status === "waiting") && (
                    <Box textAlign="center" py={1}>
                      <Box sx={{ p: 1, bgcolor: "white", borderRadius: 1, display: "inline-block", mb: 1 }}>
                        <QRCode value={ls.qrCode} size={150} />
                      </Box>
                      <Typography variant="caption" color="text.secondary">请用微信扫描二维码</Typography>
                    </Box>
                  )}
                  {ls?.status === "success" && <Alert severity="success">{ls.message}</Alert>}
                  {ls?.status === "error" && <Alert severity="error">{ls.message}</Alert>}
                </CardContent>
              </Card>
            );
          })}
          <Button variant="outlined" startIcon={<AddIcon />} onClick={() => openAddDialog("personal_wechat")}>添加微信账号</Button>
        </Box>
      )}

      {/* Add Account Dialog */}
      <AddAccountDialog
        open={dialogOpen} type={dialogType}
        onClose={() => setDialogOpen(false)}
        onAdded={() => { setDialogOpen(false); loadAccounts(); }}
        setMessage={setMessage}
        setLoginStates={setLoginStates}
      />
    </Box>
  );

  async function startWxLogin(accountId: string) {
    setLoginStates((p) => ({ ...p, [accountId]: { status: "loading" } }));
    try {
      const res = await fetch(`${C_API}/accounts/personal_wechat`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_id: accountId }),
      });
      const data = await res.json();
      if (data.qrcode) {
        setLoginStates((p) => ({ ...p, [accountId]: { status: "qr_ready", qrCode: data.qrcode_url, sessionKey: data.session_key } }));
        pollWxLogin(accountId, data.session_key);
      } else {
        setLoginStates((p) => ({ ...p, [accountId]: { status: "error", message: data.error || "获取二维码失败" } }));
      }
    } catch {
      setLoginStates((p) => ({ ...p, [accountId]: { status: "error", message: "连接失败" } }));
    }
  }

  async function pollWxLogin(accountId: string, sessionKey: string) {
    setLoginStates((p) => ({ ...p, [accountId]: { ...p[accountId], status: "waiting", sessionKey } }));
    try {
      const res = await fetch(`${C_API}/wechat/login/status`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_key: sessionKey }),
      });
      const data = await res.json();
      if (data.status === "success") {
        setLoginStates((p) => ({ ...p, [accountId]: { status: "success", message: "登录成功！" } }));
        loadAccounts();
      } else {
        setLoginStates((p) => ({ ...p, [accountId]: { status: "error", message: data.message || "登录超时" } }));
      }
    } catch {
      setLoginStates((p) => ({ ...p, [accountId]: { status: "error", message: "连接失败" } }));
    }
  }
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
  open, type, onClose, onAdded, setMessage, setLoginStates,
}: {
  open: boolean;
  type: "feishu" | "wechat" | "personal_wechat";
  onClose: () => void;
  onAdded: () => void;
  setMessage: (m: { type: "success" | "error"; text: string }) => void;
  setLoginStates: React.Dispatch<React.SetStateAction<Record<string, any>>>;
}) {
  const [loading, setLoading] = useState(false);
  const [label, setLabel] = useState("");
  // Feishu fields
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  // WeChat Work fields
  const [corpId, setCorpId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [secret, setSecret] = useState("");
  const [token, setToken] = useState("");
  const [aesKey, setAesKey] = useState("");

  const title = type === "feishu" ? "添加飞书账号" : type === "wechat" ? "添加企业微信账号" : "添加个人微信账号";

  const handleSubmit = async () => {
    setLoading(true);
    try {
      if (type === "personal_wechat") {
        // Start QR login
        const res = await fetch(`${C_API}/accounts/personal_wechat`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ label: label || undefined }),
        });
        const data = await res.json();
        if (data.qrcode) {
          // Close dialog, show QR in the list
          onAdded();
          // Trigger login display in parent via loginStates
          const accountId = `wx_${Date.now()}`;
          setLoginStates((p) => ({ ...p, [accountId]: { status: "qr_ready", qrCode: data.qrcode_url, sessionKey: data.session_key } }));
          // TODO: poll login status
        } else {
          setMessage({ type: "error", text: data.error || "获取二维码失败" });
        }
      } else {
        // Feishu or WeChat Work — POST config
        const body: any = { label: label || undefined };
        if (type === "feishu") {
          body.app_id = appId;
          body.app_secret = appSecret;
          body.enabled = true;
        } else {
          body.corp_id = corpId;
          body.agent_id = agentId;
          body.secret = secret;
          body.token = token;
          body.encoding_aes_key = aesKey;
          body.enabled = true;
        }
        const res = await fetch(`${C_API}/accounts/${type}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.success || data.account_id) {
          setMessage({ type: "success", text: "账号已添加" });
          onAdded();
        } else {
          setMessage({ type: "error", text: data.error || "添加失败" });
        }
      }
    } catch {
      setMessage({ type: "error", text: "连接服务器失败" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <TextField fullWidth size="small" label="备注名（可选）" value={label} onChange={(e) => setLabel(e.target.value)} sx={{ mt: 1 }} />

        {type === "feishu" && (
          <>
            <TextField fullWidth size="small" label="App ID" value={appId} onChange={(e) => setAppId(e.target.value)} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="App Secret" type="password" value={appSecret} onChange={(e) => setAppSecret(e.target.value)} sx={{ mt: 2 }} />
            <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
              在飞书开放平台「事件订阅」中开启「长连接」模式。
            </Typography>
          </>
        )}

        {type === "wechat" && (
          <>
            <TextField fullWidth size="small" label="Corp ID" value={corpId} onChange={(e) => setCorpId(e.target.value)} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="Agent ID" value={agentId} onChange={(e) => setAgentId(e.target.value)} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="Secret" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="Token" value={token} onChange={(e) => setToken(e.target.value)} sx={{ mt: 2 }} />
            <TextField fullWidth size="small" label="Encoding AES Key" value={aesKey} onChange={(e) => setAesKey(e.target.value)} sx={{ mt: 2 }} />
          </>
        )}

        {type === "personal_wechat" && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
            点击确认后将获取二维码，请用微信扫码登录。
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>取消</Button>
        <Button variant="contained" onClick={handleSubmit} disabled={loading}>
          {loading ? <CircularProgress size={16} sx={{ mr: 1 }} /> : null}
          {type === "personal_wechat" ? "获取二维码" : "添加"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
