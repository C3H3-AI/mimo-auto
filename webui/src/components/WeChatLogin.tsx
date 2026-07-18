/**
 * WeChat Login Component
 * Shows QR code for scanning and handles login flow
 */
import { useState, useEffect } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  CircularProgress,
  Alert,
  Stepper,
  Step,
  StepLabel,
} from '@mui/material';
import QRCode from 'qrcode.react';

interface LoginState {
  status: 'idle' | 'loading' | 'qr_ready' | 'scanning' | 'success' | 'error';
  qrCode?: string;
  qrCodeUrl?: string;
  message?: string;
  error?: string;
}

const steps = ['启动登录', '扫描二维码', '确认登录'];

export default function WeChatLogin() {
  const [loginState, setLoginState] = useState<LoginState>({ status: 'idle' });
  const [activeStep, setActiveStep] = useState(0);

  const startLogin = async () => {
    setLoginState({ status: 'loading' });
    setActiveStep(0);

    try {
      const response = await fetch('/api/wechat/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'start' }),
      });

      const data = await response.json();

      if (data.qrcode) {
        setLoginState({
          status: 'qr_ready',
          qrCode: data.qrcode,
          qrCodeUrl: data.qrcode_url,
        });
        setActiveStep(1);
        // Start polling for login status
        pollLoginStatus(data.qrcode);
      } else {
        setLoginState({
          status: 'error',
          error: data.error || '获取二维码失败',
        });
      }
    } catch (err) {
      setLoginState({
        status: 'error',
        error: '连接服务器失败',
      });
    }
  };

  const pollLoginStatus = async (qrcode: string) => {
    const maxAttempts = 120; // 4 minutes
    let attempts = 0;

    while (attempts < maxAttempts) {
      try {
        const response = await fetch('/api/wechat/login/status', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ qrcode }),
        });

        const data = await response.json();

        if (data.status === 'confirmed') {
          setLoginState({
            status: 'success',
            message: '登录成功！',
          });
          setActiveStep(2);
          return;
        }

        if (data.status === 'expired') {
          setLoginState({
            status: 'error',
            error: '二维码已过期，请重新获取',
          });
          setActiveStep(0);
          return;
        }

        // Update step if scanning
        if (data.status === 'scanned' && activeStep < 2) {
          setActiveStep(2);
          setLoginState(prev => ({ ...prev, status: 'scanning' }));
        }

      } catch (err) {
        // Ignore polling errors
      }

      attempts++;
      await new Promise(resolve => setTimeout(resolve, 2000));
    }

    setLoginState({
      status: 'error',
      error: '登录超时，请重试',
    });
    setActiveStep(0);
  };

  return (
    <Card sx={{ maxWidth: 400, mx: 'auto', mt: 4 }}>
      <CardContent>
        <Typography variant="h6" gutterBottom align="center">
          微信登录
        </Typography>

        <Stepper activeStep={activeStep} sx={{ mb: 3 }}>
          {steps.map((label) => (
            <Step key={label}>
              <StepLabel>{label}</StepLabel>
            </Step>
          ))}
        </Stepper>

        {loginState.status === 'idle' && (
          <Box textAlign="center">
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              点击下方按钮开始登录微信
            </Typography>
            <Button
              variant="contained"
              onClick={startLogin}
              fullWidth
            >
              开始登录
            </Button>
          </Box>
        )}

        {loginState.status === 'loading' && (
          <Box textAlign="center" py={3}>
            <CircularProgress />
            <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
              正在获取二维码...
            </Typography>
          </Box>
        )}

        {(loginState.status === 'qr_ready' || loginState.status === 'scanning') && loginState.qrCode && (
          <Box textAlign="center">
            <Box
              sx={{
                p: 2,
                bgcolor: 'white',
                borderRadius: 2,
                display: 'inline-block',
                mb: 2,
              }}
            >
              <QRCode value={loginState.qrCode} size={200} />
            </Box>
            <Typography variant="body2" color="text.secondary">
              {loginState.status === 'scanning'
                ? '已扫码，请在手机上确认'
                : '请用微信扫描上方二维码'}
            </Typography>
          </Box>
        )}

        {loginState.status === 'success' && (
          <Box textAlign="center">
            <Alert severity="success" sx={{ mb: 2 }}>
              {loginState.message || '登录成功！'}
            </Alert>
            <Button
              variant="outlined"
              onClick={() => setLoginState({ status: 'idle' })}
            >
              重新登录
            </Button>
          </Box>
        )}

        {loginState.status === 'error' && (
          <Box textAlign="center">
            <Alert severity="error" sx={{ mb: 2 }}>
              {loginState.error}
            </Alert>
            <Button
              variant="contained"
              onClick={startLogin}
            >
              重试
            </Button>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}
