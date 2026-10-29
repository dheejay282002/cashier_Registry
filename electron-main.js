const { app, BrowserWindow, shell, dialog, ipcMain, Menu, Tray } = require('electron');
const path = require('path');
const http = require('http');
const https = require('https');
const net = require('net');
const { spawn, execFile } = require('child_process');
const fs = require('fs');
const os = require('os');

app.disableHardwareAcceleration();

let tray = null;
let loadingWindow = null;
let djangoProcess = null;
let djangoLog = '';
const DJANGO_PORT = 8000;
const DJANGO_URL = `http://127.0.0.1:${DJANGO_PORT}`;
const PYTHON_VERSION = '3.12.4';
const PYTHON_INSTALLER = `python-${PYTHON_VERSION}-amd64.exe`;
const PYTHON_URL = `https://www.python.org/ftp/python/${PYTHON_VERSION}/${PYTHON_INSTALLER}`;
const TEMP_DIR = path.join(os.tmpdir(), 'registry-setup');

function getAppDir() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'app');
  }
  return __dirname;
}

function getUserDataDir() {
  const dir = path.join(app.getPath('userData'));
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  return dir;
}

function getDbConfigPath() {
  return path.join(getUserDataDir(), 'config.json');
}

function isValidDbConfig(cfg) {
  return !!(cfg && cfg.host && cfg.database && cfg.user &&
    !/change-?me/i.test(String(cfg.host)) &&
    !/change-?me/i.test(String(cfg.password || '')));
}

function loadDbConfig() {
  // 1) Per-device override (written by the setup dialog or admin)
  try {
    const p = getDbConfigPath();
    if (fs.existsSync(p)) {
      const cfg = JSON.parse(fs.readFileSync(p, 'utf8'));
      if (cfg && cfg.host && cfg.database && cfg.user) return cfg;
    }
  } catch (e) {
    console.warn('Failed to read config.json:', e.message);
  }
  // 2) Auto-config bundled into the installer (server-config.json)
  try {
    const bundled = path.join(getAppDir(), 'server-config.json');
    if (fs.existsSync(bundled)) {
      const cfg = JSON.parse(fs.readFileSync(bundled, 'utf8'));
      if (isValidDbConfig(cfg)) {
        console.log(`Using bundled server-config.json (host=${cfg.host})`);
        try { saveDbConfig(cfg); } catch (e) { console.warn('Could not persist config:', e.message); }
        return cfg;
      }
    }
  } catch (e) {
    console.warn('Failed to read bundled server-config.json:', e.message);
  }
  return null;
}

function saveDbConfig(cfg) {
  fs.writeFileSync(getDbConfigPath(), JSON.stringify(cfg, null, 2), 'utf8');
}

function getDbInitMarkerPath() {
  return path.join(getUserDataDir(), 'db-initialized.json');
}

function isDbInitialized(dbConfig) {
  try {
    const p = getDbInitMarkerPath();
    if (!fs.existsSync(p)) return false;
    const marker = JSON.parse(fs.readFileSync(p, 'utf8'));
    const host = dbConfig ? String(dbConfig.host) : 'local';
    const database = dbConfig ? String(dbConfig.database) : 'sqlite';
    return marker.host === host && marker.database === database;
  } catch (e) {
    return false;
  }
}

function markDbInitialized(dbConfig) {
  try {
    const host = dbConfig ? String(dbConfig.host) : 'local';
    const database = dbConfig ? String(dbConfig.database) : 'sqlite';
    fs.writeFileSync(getDbInitMarkerPath(), JSON.stringify({ host, database }), 'utf8');
  } catch (e) {
    console.warn('Could not write db init marker:', e.message);
  }
}

function applyDbConfigToEnv(cfg) {
  if (!cfg) return;
  process.env.MYSQL_HOST = String(cfg.host);
  process.env.MYSQL_PORT = String(cfg.port || 3306);
  process.env.MYSQL_DATABASE = String(cfg.database);
  process.env.MYSQL_USER = String(cfg.user);
  process.env.MYSQL_PASSWORD = cfg.password != null ? String(cfg.password) : '';
}

function promptDbConfig() {
  return new Promise((resolve, reject) => {
    const win = new BrowserWindow({
      width: 520,
      height: 620,
      resizable: false,
      frame: false,
      transparent: true,
      modal: true,
      webPreferences: {
        nodeIntegration: true,
        contextIsolation: false,
      },
    });

    const iconPath = getIconPath();
    const iconSrc = fs.existsSync(iconPath) ? iconPath.replace(/\\/g, '/') : '';

    // Prefill dialog from bundled server-config.json (or saved config) so the
    // dialog and config file always show the same values.
    const prefill = { host: '', port: '3306', database: '', user: '', password: '' };
    for (const candidate of [getDbConfigPath(), path.join(getAppDir(), 'server-config.json')]) {
      try {
        if (!fs.existsSync(candidate)) continue;
        const cfg = JSON.parse(fs.readFileSync(candidate, 'utf8'));
        prefill.host = String(cfg.host || prefill.host);
        prefill.port = String(cfg.port || prefill.port);
        prefill.database = String(cfg.database || prefill.database);
        prefill.user = String(cfg.user || prefill.user);
        prefill.password = cfg.password != null ? String(cfg.password) : '';
        if (prefill.host) break;
      } catch (e) { /* try next */ }
    }
    const escapeAttr = (s) => String(s)
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;');

    const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      font-family: 'Segoe UI', sans-serif;
      background:
        radial-gradient(circle at 15% 10%, rgba(59,130,246,0.22), transparent 45%),
        radial-gradient(circle at 85% 90%, rgba(16,185,129,0.18), transparent 45%),
        radial-gradient(circle at 70% 20%, rgba(139,92,246,0.15), transparent 40%),
        linear-gradient(135deg, #0a0c14 0%, #06070c 50%, #0a0c14 100%);
      color: #e8edf5;
      border-radius: 16px;
      overflow: hidden;
      user-select: none;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }
    .card {
      width: 100%;
      background: rgba(255, 255, 255, 0.06);
      border: 0.5px solid rgba(255, 255, 255, 0.16);
      backdrop-filter: blur(60px) saturate(180%);
      border-radius: 20px;
      padding: 30px 30px 26px;
      box-shadow:
        0 2px 4px rgba(0,0,0,0.2),
        0 16px 40px rgba(0,0,0,0.45),
        inset 0 1px 0 rgba(255,255,255,0.14);
      position: relative;
    }
    .card::before {
      content: "";
      position: absolute;
      inset: -0.5px;
      border-radius: 20.5px;
      padding: 1px;
      background: linear-gradient(160deg, rgba(255,255,255,0.28), rgba(255,255,255,0.03) 50%, rgba(59,130,246,0.25));
      -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
      -webkit-mask-composite: xor;
      mask-composite: exclude;
      pointer-events: none;
    }
    .header { display: flex; align-items: center; gap: 14px; margin-bottom: 6px; }
    .header .logo-wrap {
      width: 46px; height: 46px; border-radius: 50%;
      background: linear-gradient(135deg, rgba(59,130,246,0.35), rgba(16,185,129,0.3));
      display: flex; align-items: center; justify-content: center;
      border: 1px solid rgba(255,255,255,0.18);
      box-shadow: 0 4px 14px rgba(59,130,246,0.25);
      flex-shrink: 0;
    }
    .header img { width: 34px; height: 34px; border-radius: 50%; }
    .title { font-size: 17px; font-weight: 700; color: #fff; letter-spacing: 0.2px; }
    .title span {
      display: block; font-size: 11px; font-weight: 500;
      color: rgba(232,237,245,0.45); letter-spacing: 0.6px; text-transform: uppercase; margin-top: 2px;
    }
    .subtitle { font-size: 12.5px; color: rgba(232,237,245,0.5); line-height: 1.55; margin: 12px 0 20px; }
    .field { margin-bottom: 14px; }
    .field-row { display: flex; gap: 12px; }
    .field-row .field { flex: 1; }
    label {
      display: flex; align-items: center; gap: 6px;
      font-size: 10.5px; font-weight: 700; color: rgba(232,237,245,0.55);
      text-transform: uppercase; letter-spacing: 1px; margin-bottom: 7px;
    }
    label .dot { width: 5px; height: 5px; border-radius: 50%; background: #3b82f6; box-shadow: 0 0 6px rgba(59,130,246,0.8); }
    input {
      width: 100%;
      padding: 11px 13px;
      background: rgba(0, 0, 0, 0.35);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 10px;
      color: #fff;
      font-size: 13.5px;
      font-family: 'Segoe UI', sans-serif;
      outline: none;
      transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
      user-select: text;
    }
    input::placeholder { color: rgba(232,237,245,0.28); }
    input:focus {
      border-color: rgba(59,130,246,0.75);
      background: rgba(0,0,0,0.5);
      box-shadow: 0 0 0 3px rgba(59,130,246,0.18), 0 0 16px rgba(59,130,246,0.15);
    }
    .actions { display: flex; gap: 10px; margin-top: 22px; }
    button {
      padding: 12px 16px;
      border: none;
      border-radius: 10px;
      font-size: 13.5px;
      font-weight: 600;
      cursor: pointer;
      font-family: 'Segoe UI', sans-serif;
      transition: all 0.18s ease;
      letter-spacing: 0.2px;
    }
    button:active { transform: scale(0.97); }
    button:disabled { opacity: 0.55; cursor: wait; }
    #saveBtn {
      flex: 1;
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #fff;
      box-shadow: 0 4px 16px rgba(37,99,235,0.4), inset 0 1px 0 rgba(255,255,255,0.2);
    }
    #saveBtn:hover { background: linear-gradient(135deg, #1d4ed8, #1e40af); box-shadow: 0 6px 22px rgba(37,99,235,0.55), inset 0 1px 0 rgba(255,255,255,0.2); }
    #testBtn {
      flex: 0 0 auto;
      background: rgba(255,255,255,0.07);
      color: #93c5fd;
      border: 1px solid rgba(59,130,246,0.35);
    }
    #testBtn:hover { background: rgba(59,130,246,0.16); border-color: rgba(59,130,246,0.6); }
    #cancelBtn {
      flex: 0 0 auto;
      background: rgba(255,255,255,0.05);
      color: rgba(232,237,245,0.55);
      border: 1px solid rgba(255,255,255,0.12);
    }
    #cancelBtn:hover { background: rgba(255,255,255,0.1); color: #e8edf5; }
    #status {
      font-size: 12.5px; min-height: 20px; margin-top: 15px;
      text-align: center; color: rgba(232,237,245,0.45);
      display: flex; align-items: center; justify-content: center; gap: 7px;
      transition: color 0.2s;
    }
    #status .ind { width: 7px; height: 7px; border-radius: 50%; background: rgba(232,237,245,0.3); flex-shrink: 0; }
    #status.ok { color: #4ade80; }
    #status.ok .ind { background: #4ade80; box-shadow: 0 0 8px rgba(74,222,128,0.9); }
    #status.err { color: #f87171; }
    #status.err .ind { background: #f87171; box-shadow: 0 0 8px rgba(248,113,113,0.9); }
    #status.testing { color: #93c5fd; }
    #status.testing .ind { background: #3b82f6; animation: pulse 0.9s ease-in-out infinite; }
    @keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.7); } }
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <div class="logo-wrap"><img id="logoImg" alt=""></div>
      <div class="title">Database Connection<span>Central MySQL Server</span></div>
    </div>
    <div class="subtitle">Connect this device to the shared database. Settings are saved locally on this device only.</div>
    <div class="field">
      <label><span class="dot"></span>Server Host</label>
      <input id="host" value="${escapeAttr(prefill.host)}" placeholder="e.g. 192.168.1.10 or db.example.com" autocomplete="off" spellcheck="false">
    </div>
    <div class="field-row">
      <div class="field">
        <label><span class="dot"></span>Port</label>
        <input id="port" value="${escapeAttr(prefill.port)}" autocomplete="off">
      </div>
      <div class="field">
        <label><span class="dot"></span>Database Name</label>
        <input id="database" value="${escapeAttr(prefill.database)}" autocomplete="off" spellcheck="false">
      </div>
    </div>
    <div class="field-row">
      <div class="field">
        <label><span class="dot"></span>Username</label>
        <input id="user" value="${escapeAttr(prefill.user)}" autocomplete="off" spellcheck="false">
      </div>
      <div class="field">
        <label><span class="dot"></span>Password</label>
        <input id="password" type="password" value="${escapeAttr(prefill.password)}" autocomplete="off">
      </div>
    </div>
    <div class="actions">
      <button id="testBtn">Test Connection</button>
      <button id="saveBtn">Save &amp; Continue</button>
      <button id="cancelBtn">Cancel</button>
    </div>
    <div id="status"><span class="ind"></span><span id="statusText">Verify the connection before saving</span></div>
  </div>
  <script>
    const { ipcRenderer } = require('electron');
    const fs = require('fs');
    const path = require('path');

    const logoPath = ${JSON.stringify(iconSrc)};
    if (logoPath && fs.existsSync(logoPath)) {
      const data = fs.readFileSync(logoPath);
      const ext = path.extname(logoPath).slice(1).toLowerCase();
      const mime = ext === 'jpg' ? 'jpeg' : ext === 'ico' ? 'x-icon' : ext;
      document.getElementById('logoImg').src = 'data:image/' + mime + ';base64,' + data.toString('base64');
    } else {
      document.getElementById('logoImg').style.display = 'none';
    }

    const status = document.getElementById('status');
    const statusText = document.getElementById('statusText');
    function setStatus(msg, cls) {
      statusText.textContent = msg;
      status.className = cls || '';
    }
    function collect() {
      return {
        host: document.getElementById('host').value.trim(),
        port: document.getElementById('port').value.trim() || '3306',
        database: document.getElementById('database').value.trim(),
        user: document.getElementById('user').value.trim(),
        password: document.getElementById('password').value,
      };
    }
    document.getElementById('testBtn').addEventListener('click', async () => {
      const cfg = collect();
      if (!cfg.host || !cfg.user) { setStatus('Host and username are required.', 'err'); return; }
      const btn = document.getElementById('testBtn');
      btn.disabled = true;
      setStatus('Testing real login...', 'testing');
      const result = await ipcRenderer.invoke('test-db-connection', cfg);
      setStatus(result.ok ? (result.note || 'Connection successful.') : ('Failed: ' + result.error), result.ok ? 'ok' : 'err');
      btn.disabled = false;
    });
    document.getElementById('saveBtn').addEventListener('click', () => {
      const cfg = collect();
      if (!cfg.host || !cfg.database || !cfg.user) { setStatus('Host, database, and username are required.', 'err'); return; }
      ipcRenderer.send('db-config-save', cfg);
    });
    document.getElementById('cancelBtn').addEventListener('click', () => {
      ipcRenderer.send('db-config-cancel');
    });
    ipcRenderer.on('db-config-result', (event, msg) => setStatus(msg, 'err'));
  </script>
</body>
</html>`;

    win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html));

    const onSave = (event, cfg) => {
      try {
        saveDbConfig(cfg);
        cleanup();
        win.close();
        resolve(cfg);
      } catch (e) {
        event.sender.send('db-config-result', 'Could not save config: ' + e.message);
      }
    };
    const onCancel = () => {
      cleanup();
      win.close();
      reject(new Error('Database configuration was cancelled.'));
    };
    const onClose = () => {
      cleanup();
      reject(new Error('Database configuration was cancelled.'));
    };
    function cleanup() {
      ipcMain.removeListener('db-config-save', onSave);
      ipcMain.removeListener('db-config-cancel', onCancel);
      win.removeAllListeners('closed');
    }
    ipcMain.on('db-config-save', onSave);
    ipcMain.on('db-config-cancel', onCancel);
    win.on('closed', onClose);
  });
}

function testTcpConnection(host, port, timeoutMs = 4000) {
  return new Promise((resolve) => {
    const socket = net.connect({ host, port: Number(port) || 3306 });
    const done = (ok, error) => {
      socket.destroy();
      resolve({ ok, error });
    };
    socket.setTimeout(timeoutMs);
    socket.once('connect', () => done(true, null));
    socket.once('timeout', () => done(false, 'Timeout'));
    socket.once('error', (err) => done(false, err.message));
  });
}

async function testDbConnection(cfg) {
  const tcp = await testTcpConnection(cfg.host, cfg.port);
  if (!tcp.ok) {
    return { ok: false, error: `Cannot reach ${cfg.host}:${cfg.port} — ${tcp.error}` };
  }

  const script = path.join(getAppDir(), 'scripts', 'test_db_connection.py');
  if (!fs.existsSync(script)) {
    return { ok: true, note: 'Port reachable (full login test unavailable)' };
  }

  const pythonPath = getPythonPath();
  if (!fs.existsSync(pythonPath)) {
    return { ok: true, note: 'Port reachable (full login test after first run)' };
  }

  return new Promise((resolve) => {
    const proc = spawn(pythonPath, [script, JSON.stringify(cfg)], {
      cwd: getUserDataDir(),
      stdio: ['ignore', 'pipe', 'pipe'],
      env: Object.assign({}, process.env, { PYTHONUTF8: '1', PYTHONNOUSERSITE: '1' }),
    });
    let out = '';
    let err = '';
    const timer = setTimeout(() => {
      proc.kill();
      resolve({ ok: false, error: 'Connection test timed out' });
    }, 10000);
    proc.stdout.on('data', (d) => { out += d.toString(); });
    proc.stderr.on('data', (d) => { err += d.toString(); });
    proc.on('error', (e) => {
      clearTimeout(timer);
      resolve({ ok: false, error: e.message });
    });
    proc.on('exit', () => {
      clearTimeout(timer);
      try {
        const line = out.trim().split('\n').pop();
        const result = JSON.parse(line);
        if (result.ok) {
          result.note = `MySQL ${result.version} — login OK, ${result.tables} tables`;
        }
        resolve(result);
      } catch (e) {
        resolve({ ok: false, error: (err || out || 'Test failed').trim().slice(-400) });
      }
    });
  });
}

function getBundledPythonPath() {
  const candidates = [
    path.join(getAppDir(), 'runtime', 'python', 'python.exe'),
    path.join(__dirname, 'runtime', 'python', 'python.exe'),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

function getDevPythonPath() {
  const devVenv = path.join(__dirname, '.venv', 'Scripts', 'python.exe');
  return fs.existsSync(devVenv) ? devVenv : null;
}

function getPythonPath() {
  return getBundledPythonPath()
    || getDevPythonPath()
    || path.join(getUserDataDir(), '.venv', 'Scripts', 'python.exe');
}

function getPipPath() {
  return path.join(getUserDataDir(), '.venv', 'Scripts', 'pip.exe');
}

function getManagePyPath() {
  return path.join(getAppDir(), 'manage.py');
}

function getRequirementsPath() {
  return path.join(getAppDir(), 'requirements.txt');
}

function isPortAvailable(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.once('listening', () => {
      server.close(() => resolve(true));
    });
    server.listen(port, '127.0.0.1');
  });
}

function killPortProcess(port) {
  return new Promise((resolve) => {
    const script = `Get-NetTCPConnection -LocalPort ${port} -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }`;
    execFile('powershell.exe', ['-NoProfile', '-Command', script], (err) => {
      if (err) console.log('Port cleanup note:', err.message);
      resolve();
    });
  });
}

function waitForServer(maxRetries = 120, interval = 1000) {
  return new Promise((resolve, reject) => {
    let retries = 0;
    const fail = () => {
      const output = (djangoLog || '').trim();
      reject(new Error(
        'Server did not start in time' +
        (output ? `\n\nServer output:\n${output.slice(-1500)}` : '')
      ));
    };
    const check = () => {
      const req = http.get(DJANGO_URL, (res) => {
        res.resume();
        if (res.statusCode < 500) {
          resolve();
        } else if (retries < maxRetries) {
          retries++;
          setTimeout(check, interval);
        } else {
          fail();
        }
      });
      req.on('error', () => {
        if (retries < maxRetries) {
          retries++;
          setTimeout(check, interval);
        } else {
          fail();
        }
      });
      req.end();
    };
    check();
  });
}

function runCommand(cmd, args, cwd) {
  return new Promise((resolve, reject) => {
    console.log(`Running: ${cmd} ${args.join(' ')}`);
    const proc = spawn(cmd, args, { cwd, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (d) => { stdout += d.toString(); });
    proc.stderr.on('data', (d) => { stderr += d.toString(); });
    proc.on('error', reject);
    proc.on('exit', (code) => {
      if (code === 0) {
        resolve({ stdout, stderr });
      } else {
        reject(new Error(`Command failed (exit ${code}): ${stderr || stdout}`));
      }
    });
  });
}

function runCommandDetached(cmd, args, cwd) {
  return new Promise((resolve, reject) => {
    console.log(`Running (detached): ${cmd} ${args.join(' ')}`);
    const proc = spawn(cmd, args, {
      cwd,
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: true,
    });
    let stderr = '';
    proc.stderr.on('data', (d) => { stderr += d.toString(); });
    proc.on('error', reject);
    proc.unref();
    proc.on('exit', (code) => {
      if (code === 0) {
        resolve({ stderr });
      } else {
        reject(new Error(`Command failed (exit ${code}): ${stderr}`));
      }
    });
  });
}

function updateLoading(message, percent) {
  if (loadingWindow && !loadingWindow.isDestroyed()) {
    loadingWindow.webContents.send('setup-status', message, percent);
  }
}

function downloadFile(url, destPath, onProgress) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(destPath);
    const protocol = url.startsWith('https') ? https : http;

    const request = protocol.get(url, (response) => {
      if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
        file.close();
        fs.unlinkSync(destPath);
        downloadFile(response.headers.location, destPath, onProgress).then(resolve).catch(reject);
        return;
      }

      if (response.statusCode !== 200) {
        file.close();
        fs.unlinkSync(destPath);
        reject(new Error(`Download failed with status ${response.statusCode}`));
        return;
      }

      const totalBytes = parseInt(response.headers['content-length'], 10);
      let downloadedBytes = 0;

      response.on('data', (chunk) => {
        downloadedBytes += chunk.length;
        if (onProgress && totalBytes) {
          onProgress(downloadedBytes, totalBytes);
        }
      });

      response.pipe(file);

      file.on('finish', () => {
        file.close(resolve);
      });
    });

    request.on('error', (err) => {
      file.close();
      if (fs.existsSync(destPath)) fs.unlinkSync(destPath);
      reject(err);
    });

    request.setTimeout(120000, () => {
      request.destroy();
      reject(new Error('Download timed out'));
    });
  });
}

function isSupportedPythonVersion(output) {
  const match = /Python\s+(\d+)\.(\d+)\.(\d+)([^\s]*)/i.exec(String(output || ''));
  if (!match) return false;
  const suffix = (match[4] || '').toLowerCase();
  // Skip pre-releases (alpha/beta/rc): pip is broken on some of them.
  if (/^(a|b|c|rc|alpha|beta|candidate)/.test(suffix)) return false;
  const major = Number(match[1]);
  const minor = Number(match[2]);
  return major > 3 || (major === 3 && minor >= 10);
}

function probePythonVersion(cmd) {
  return new Promise((resolve) => {
    execFile(cmd, ['--version'], { shell: true }, (err, stdout, stderr) => {
      const out = `${stdout || ''}\n${stderr || ''}`;
      if (!isSupportedPythonVersion(out)) {
        resolve(null);
        return;
      }
      const version = (out.match(/Python\s+(\d+\.\d+\.\d+[^\s]*)/i) || [])[1] || '';
      resolve({ cmd, version });
    });
  });
}

function pipWorks(pythonPath) {
  if (!fs.existsSync(pythonPath)) return Promise.resolve(false);
  return runCommand(pythonPath, ['-m', 'pip', '--version'], getUserDataDir())
    .then(() => true)
    .catch(() => false);
}

async function findSystemPython() {
  const candidates = ['python', 'python3', 'py'];
  for (const cmd of candidates) {
    const found = await probePythonVersion(cmd);
    if (found) {
      console.log(`Found Python ${found.version} at: ${cmd}`);
      return found.cmd;
    }
  }
  return null;
}

async function installPython() {
  updateLoading('Python not found. Downloading Python installer...', 0);

  if (!fs.existsSync(TEMP_DIR)) {
    fs.mkdirSync(TEMP_DIR, { recursive: true });
  }

  const installerPath = path.join(TEMP_DIR, PYTHON_INSTALLER);

  // Download if not cached
  if (!fs.existsSync(installerPath)) {
    await downloadFile(PYTHON_URL, installerPath, (downloaded, total) => {
      const pct = Math.round((downloaded / total) * 100);
      const mb = (downloaded / 1048576).toFixed(1);
      const totalMb = (total / 1048576).toFixed(1);
      updateLoading(`Downloading Python ${PYTHON_VERSION}... ${mb}/${totalMb} MB (${pct}%)`, pct);
    });
  } else {
    updateLoading('Using cached Python installer...', 0);
  }

  // Silent install: per-user, add to PATH, no test suite
  updateLoading('Installing Python (this may take a minute)...', -1);

  const installArgs = [
    '/quiet',
    'InstallAllUsers=0',
    'PrependPath=1',
    'Include_test=0',
    'Include_launcher=1',
    `TargetDir=${path.join(TEMP_DIR, 'python')}`,
  ];

  try {
    await runCommandDetached(installerPath, installArgs, TEMP_DIR);
  } catch (e) {
    // The installer may exit with non-zero but still succeed, or need admin
    console.log('Installer exit:', e.message);
  }

  // Wait a few seconds for files to settle
  await new Promise((r) => setTimeout(r, 3000));

  // Prefer the freshly installed interpreter: PATH may still point at an older one
  const localPython = path.join(TEMP_DIR, 'python', 'python.exe');
  if (fs.existsSync(localPython)) {
    console.log(`Found local install: ${localPython}`);
    return localPython;
  }

  // Try to find the freshly installed python
  const freshPython = await findSystemPython();
  if (freshPython) {
    console.log(`Python installed successfully, found: ${freshPython}`);
    return freshPython;
  }

  throw new Error(
    `Python installation completed but Python could not be found.\n\n` +
    `Please restart the application. If the issue persists, install Python ${PYTHON_VERSION} manually\n` +
    `from https://www.python.org/downloads/ and ensure "Add Python to PATH" is checked.`
  );
}

async function setupVirtualEnv({ appDir, dataDir, pythonPath, pipPath, requirementsPath, venvDir }) {
  // Step 1: Find or install Python
  updateLoading('Checking for Python installation...', -1);
  let systemPython = await findSystemPython();
  if (!systemPython) {
    systemPython = await installPython();
  }
  console.log(`Using Python: ${systemPython}`);

  // Step 2: Create virtual environment if missing, or repair a broken one
  if (fs.existsSync(pythonPath) && !(await pipWorks(pythonPath))) {
    updateLoading('Repairing Python environment...', -1);
    console.warn('Existing virtual environment has a broken pip. Recreating it...');
    try {
      fs.rmSync(venvDir, { recursive: true, force: true });
    } catch (e) {
      throw new Error(`Could not repair the Python environment: ${e.message}`);
    }
  }
  if (!fs.existsSync(pythonPath)) {
    updateLoading('Creating Python virtual environment...', -1);
    console.log(`Creating venv in ${venvDir}...`);
    await runCommand(systemPython, ['-m', 'venv', venvDir]);
    console.log('Venv created.');
    if (!(await pipWorks(pythonPath))) {
      throw new Error(
        'pip is not working inside the Python virtual environment. ' +
        'Please install a stable Python 3.10 or newer release (not an alpha/beta) ' +
        'and start the application again.'
      );
    }
  }

  // Step 3: Upgrade pip
  updateLoading('Upgrading pip...', -1);
  try {
    await runCommand(pythonPath, ['-m', 'pip', 'install', '--upgrade', 'pip'], appDir);
  } catch (e) {
    console.warn('pip upgrade warning:', e.message);
  }

  // Step 4: Install requirements
  if (fs.existsSync(requirementsPath)) {
    updateLoading('Installing dependencies...', -1);
    console.log('Installing requirements...');
    try {
      await runCommand(pipPath, ['install', '-r', requirementsPath], appDir);
      console.log('Requirements installed.');
    } catch (e) {
      const detail = String(e.message || e);
      throw new Error(
        'Could not install the application dependencies:\n\n' +
        (detail.length > 900 ? '...' + detail.slice(-900) : detail)
      );
    }
  }
}

async function ensurePythonEnvironment() {
  const appDir = getAppDir();
  const dataDir = getUserDataDir();
  const bundledPython = getBundledPythonPath();
  const devPython = getDevPythonPath();
  const pythonPath = getPythonPath();
  const pipPath = getPipPath();
  const requirementsPath = getRequirementsPath();
  const venvDir = path.join(dataDir, '.venv');

  if (bundledPython) {
    console.log(`Using bundled Python runtime: ${bundledPython}`);
  } else if (devPython) {
    console.log(`Using project virtual environment: ${devPython}`);
  } else {
    await setupVirtualEnv({ appDir, dataDir, pythonPath, pipPath, requirementsPath, venvDir });
  }

  // Step 5: Seed local SQLite only when no central DB config exists (dev/local mode)
  const dbConfig = loadDbConfig();
  if (!dbConfig) {
    const dbSource = path.join(appDir, 'db.sqlite3');
    const dbDest = path.join(dataDir, 'db.sqlite3');
    if (fs.existsSync(dbSource) && !fs.existsSync(dbDest)) {
      fs.copyFileSync(dbSource, dbDest);
    }
  }

  // Step 6: Ensure media directory exists in user data dir
  const mediaDir = path.join(dataDir, 'media');
  if (!fs.existsSync(mediaDir)) {
    fs.mkdirSync(mediaDir, { recursive: true });
  }

  // Step 7: Run migrations only on first install / first run against this DB
  const managePy = getManagePyPath();
  if (isDbInitialized(dbConfig)) {
    console.log('Database already initialized, skipping migrations.');
  } else {
    updateLoading('Setting up database (first run)...', -1);
    try {
      await runCommand(pythonPath, [managePy, 'migrate', '--run-syncdb'], dataDir);
      console.log('Migrations applied.');
      markDbInitialized(dbConfig);
    } catch (e) {
      console.warn('Migration warning:', e.message);
    }
  }
}

function startDjango() {
  const pythonPath = getPythonPath();
  const managePy = getManagePyPath();
  const dataDir = getUserDataDir();
  djangoLog = '';

  console.log(`Starting Django: ${pythonPath} ${managePy}`);
  console.log(`Working directory: ${dataDir}`);

  const env = Object.assign({}, process.env, {
    REGISTRY_DATA_DIR: dataDir,
    REGISTRY_DB_PATH: path.join(dataDir, 'db.sqlite3'),
    REGISTRY_MEDIA_ROOT: path.join(dataDir, 'media'),
    REGISTRY_MEDIA_FALLBACK_ROOT: path.join(getAppDir(), 'media'),
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    PYTHONNOUSERSITE: '1',
  });

  djangoProcess = spawn(pythonPath, [managePy, 'runserver', `0.0.0.0:${DJANGO_PORT}`, '--noreload'], {
    cwd: dataDir,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: env,
  });

  djangoProcess.stdout.on('data', (data) => {
    const text = data.toString().trim();
    console.log(`[Django] ${text}`);
    djangoLog = `${djangoLog}\n${text}`.slice(-4000);
  });

  djangoProcess.stderr.on('data', (data) => {
    const text = data.toString().trim();
    console.log(`[Django] ${text}`);
    djangoLog = `${djangoLog}\n${text}`.slice(-4000);
  });

  djangoProcess.on('error', (err) => {
    console.error('Failed to start Django:', err);
  });

  djangoProcess.on('exit', (code, signal) => {
    console.log(`Django process exited with code ${code}, signal ${signal}`);
    djangoProcess = null;
  });
}

function createLoadingWindow() {
  loadingWindow = new BrowserWindow({
    width: 520,
    height: 420,
    resizable: false,
    frame: false,
    transparent: true,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
  });

  const iconPath = getIconPath();
  const iconSrc = fs.existsSync(iconPath) ? iconPath.replace(/\\/g, '/') : '';

  const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: 'Segoe UI', sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      border-radius: 14px;
      overflow: hidden;
      user-select: none;
    }
    .loader-container {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 28px;
      padding: 40px;
    }
    .logo-wrap {
      position: relative;
      width: 96px;
      height: 96px;
    }
    .logo-img {
      width: 96px;
      height: 96px;
      border-radius: 50%;
      object-fit: contain;
      position: relative;
      z-index: 1;
      filter: drop-shadow(0 0 20px rgba(59,130,246,0.3));
    }
    .logo-ring {
      position: absolute;
      inset: -6px;
      border-radius: 50%;
      border: 2px solid transparent;
      border-top-color: #3b82f6;
      border-right-color: #3b82f6;
      animation: ringRotate 2s linear infinite;
      opacity: 0.6;
    }
    .logo-ring-2 {
      position: absolute;
      inset: -14px;
      border-radius: 50%;
      border: 1px solid transparent;
      border-bottom-color: #1e40af;
      border-left-color: #1e40af;
      animation: ringRotate 3s linear infinite reverse;
      opacity: 0.3;
    }
    @keyframes ringRotate { to { transform: rotate(360deg); } }
    .vline-track {
      width: 3px;
      height: 48px;
      background: #1e293b;
      border-radius: 2px;
      position: relative;
      overflow: hidden;
    }
    .vline-fill {
      position: absolute;
      bottom: 0;
      width: 100%;
      height: 0%;
      background: linear-gradient(to top, #3b82f6, #60a5fa, #93c5fd);
      border-radius: 2px;
      animation: vlineGrow 2s ease-in-out infinite;
      box-shadow: 0 0 12px rgba(59,130,246,0.5);
    }
    @keyframes vlineGrow {
      0%   { height: 0%;  bottom: 0; opacity: 0.4; }
      50%  { height: 100%; bottom: 0; opacity: 1; }
      100% { height: 0%;  bottom: 100%; opacity: 0.4; }
    }
    .app-title {
      font-size: 22px;
      font-weight: 700;
      color: #f8fafc;
      letter-spacing: 0.5px;
    }
    .status-text {
      font-size: 13px;
      color: #64748b;
      min-height: 18px;
      text-align: center;
      transition: opacity 0.3s;
    }
    .steps {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .step-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #1e293b;
      transition: all 0.4s ease;
    }
    .step-dot.active {
      background: #3b82f6;
      box-shadow: 0 0 8px rgba(59,130,246,0.6);
      transform: scale(1.3);
    }
    .step-dot.done {
      background: #22c55e;
      box-shadow: 0 0 6px rgba(34,197,94,0.4);
    }
  </style>
</head>
<body>
  <div class="loader-container">
    <div class="logo-wrap">
      <div class="logo-ring-2"></div>
      <div class="logo-ring"></div>
      <img class="logo-img" id="logoImg" alt="Logo">
    </div>
    <div class="vline-track">
      <div class="vline-fill" id="vline"></div>
    </div>
    <div class="app-title">Registry</div>
    <div class="status-text" id="status">Initializing...</div>
    <div class="steps">
      <div class="step-dot" id="step0"></div>
      <div class="step-dot" id="step1"></div>
      <div class="step-dot" id="step2"></div>
      <div class="step-dot" id="step3"></div>
      <div class="step-dot" id="step4"></div>
    </div>
  </div>
  <script>
    const fs = require('fs');
    const path = require('path');

    // Load the logo image as base64
    const logoPath = ${JSON.stringify(iconSrc)};
    if (logoPath && fs.existsSync(logoPath)) {
    const data = fs.readFileSync(logoPath);
    const ext = path.extname(logoPath).slice(1).toLowerCase();
    const mime = ext === 'jpg' ? 'jpeg' : ext === 'ico' ? 'x-icon' : ext;
    document.getElementById('logoImg').src = 'data:image/' + mime + ';base64,' + data.toString('base64');
    } else {
      document.getElementById('logoImg').style.display = 'none';
    }

    const { ipcRenderer } = require('electron');
    let currentStep = 0;
    const totalSteps = 5;

    function setStep(n) {
      for (let i = 0; i < totalSteps; i++) {
        const dot = document.getElementById('step' + i);
        if (!dot) continue;
        dot.className = 'step-dot';
        if (i < n) dot.classList.add('done');
        else if (i === n) dot.classList.add('active');
      }
    }

    ipcRenderer.on('setup-status', (event, msg, pct) => {
      document.getElementById('status').textContent = msg;
      const vline = document.getElementById('vline');

      if (pct === -1) {
        vline.style.animationPlayState = 'running';
      } else if (pct !== undefined && pct >= 0) {
        vline.style.animation = 'none';
        vline.style.height = '100%';
        vline.style.bottom = '0';
        vline.style.opacity = '1';
      }

      if (msg.includes('Checking') || msg.includes('Python')) setStep(0);
      else if (msg.includes('virtual') || msg.includes('Upgrading')) setStep(1);
      else if (msg.includes('dependencies') || msg.includes('Installing')) setStep(2);
      else if (msg.includes('database') || msg.includes('migrations')) setStep(3);
      else if (msg.includes('Starting') || msg.includes('server')) setStep(4);
    });
  </script>
</body>
</html>`;

  loadingWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html));

  loadingWindow.on('closed', () => { loadingWindow = null; });
}

function getIconPath() {
  const candidates = [
    path.join(getAppDir(), 'static', 'icon.ico'),
    path.join(__dirname, 'static', 'icon.ico'),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return candidates[0];
}

function assertAppFiles() {
  const managePy = getManagePyPath();
  if (!fs.existsSync(managePy) || managePy.includes('app.asar')) {
    throw new Error(
      'Application files were not found at "' + path.dirname(managePy) + '". ' +
      'Rebuild the installer with "asar": false in the package.json build config.'
    );
  }
}

function openInBrowser() {
  // Prefer https://registry (Apache reverse proxy) when it's configured;
  // fall back to the direct URL if it doesn't resolve/respond.
  let done = false;
  const open = (url) => {
    if (done) return;
    done = true;
    shell.openExternal(url);
  };
  try {
    const probe = https.request({
      host: 'registry',
      port: 443,
      path: '/',
      method: 'GET',
      timeout: 1500,
      rejectUnauthorized: false,
      headers: { Host: 'registry' },
    }, (res) => {
      res.resume();
      open('https://registry');
    });
    probe.on('error', () => open(DJANGO_URL));
    probe.on('timeout', () => {
      probe.destroy();
      open(DJANGO_URL);
    });
    probe.end();
  } catch (e) {
    open(DJANGO_URL);
  }
}

function createTray() {
  if (tray) return;
  try {
    tray = new Tray(getIconPath());
  } catch (err) {
    console.warn('Tray icon unavailable:', err.message);
    return;
  }
  tray.setToolTip(`Registry server running at ${DJANGO_URL}`);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open in Browser', click: () => openInBrowser() },
    { type: 'separator' },
    { label: 'Quit Registry', click: async () => { await stopDjango(); app.quit(); } },
  ]));
  tray.on('click', () => openInBrowser());
}

function stopDjango() {
  return new Promise((resolve) => {
    if (!djangoProcess) { resolve(); return; }
    djangoProcess.on('exit', () => resolve());
    djangoProcess.kill();
    setTimeout(() => {
      if (djangoProcess) djangoProcess.kill('SIGKILL');
      resolve();
    }, 5000);
  });
}

app.whenReady().then(async () => {
  try {
    assertAppFiles();

    ipcMain.handle('test-db-connection', async (event, cfg) => {
      return testDbConnection(cfg);
    });

    // Load central DB config; prompt on first run
    let dbConfig = loadDbConfig();
    if (!dbConfig) {
      dbConfig = await promptDbConfig();
    }
    applyDbConfigToEnv(dbConfig);
    console.log(`Database config: host=${process.env.MYSQL_HOST}:${process.env.MYSQL_PORT} db=${process.env.MYSQL_DATABASE} user=${process.env.MYSQL_USER}`);

    createLoadingWindow();

    const available = await isPortAvailable(DJANGO_PORT);
    if (!available) {
      updateLoading('Clearing port 8000...', -1);
      await killPortProcess(DJANGO_PORT);
      await new Promise((r) => setTimeout(r, 1000));
    }

    await ensurePythonEnvironment();
    updateLoading('Starting application server...', -1);
    startDjango();
    await waitForServer();

    // The Electron shell only boots the server — the app itself opens in the
    // default browser. A tray icon keeps the running server controllable.
    if (loadingWindow && !loadingWindow.isDestroyed()) loadingWindow.close();
    openInBrowser();
    createTray();

    app.on('activate', () => {
      if (!tray) createTray();
      openInBrowser();
    });
  } catch (err) {
    if (loadingWindow && !loadingWindow.isDestroyed()) loadingWindow.close();
    dialog.showErrorBox('Startup Error', err.message);
    app.quit();
  }
});

app.on('window-all-closed', async () => {
  // Browser mode has no windows by design — keep running so the tray icon
  // stays available. Without a tray (creation failed) there is no way to
  // control the server, so fall back to quitting.
  if (tray) return;
  await stopDjango();
  app.quit();
});

app.on('before-quit', async () => {
  await stopDjango();
});
