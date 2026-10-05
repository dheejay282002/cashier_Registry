const { app, BrowserWindow, shell, dialog } = require('electron');
const path = require('path');
const http = require('http');
const https = require('https');
const net = require('net');
const { spawn, execFile } = require('child_process');
const fs = require('fs');
const os = require('os');

let mainWindow = null;
let loadingWindow = null;
let djangoProcess = null;
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

function getPythonPath() {
  return path.join(getUserDataDir(), '.venv', 'Scripts', 'python.exe');
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

function waitForServer(maxRetries = 60, interval = 1000) {
  return new Promise((resolve, reject) => {
    let retries = 0;
    const check = () => {
      const req = http.get(DJANGO_URL, (res) => {
        res.resume();
        if (res.statusCode < 500) {
          resolve();
        } else if (retries < maxRetries) {
          retries++;
          setTimeout(check, interval);
        } else {
          reject(new Error('Server did not start in time'));
        }
      });
      req.on('error', () => {
        if (retries < maxRetries) {
          retries++;
          setTimeout(check, interval);
        } else {
          reject(new Error('Server did not start in time'));
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

function findSystemPython() {
  const candidates = ['python', 'python3', 'py'];
  return new Promise((resolve) => {
    let checked = 0;
    if (candidates.length === 0) { resolve(null); return; }
    candidates.forEach((cmd) => {
      execFile(cmd, ['--version'], { shell: true }, (err, stdout) => {
        checked++;
        if (!err && stdout && stdout.includes('Python 3')) {
          if (!resolve.called) {
            resolve.called = true;
            resolve(cmd);
          }
        } else if (checked === candidates.length && !resolve.called) {
          resolve.called = true;
          resolve(null);
        }
      });
    });
  });
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

  // Try to find the freshly installed python
  const freshPython = await findSystemPython();
  if (freshPython) {
    console.log(`Python installed successfully, found: ${freshPython}`);
    return freshPython;
  }

  // Check if our TargetDir install worked
  const localPython = path.join(TEMP_DIR, 'python', 'python.exe');
  if (fs.existsSync(localPython)) {
    console.log(`Found local install: ${localPython}`);
    return localPython;
  }

  throw new Error(
    `Python installation completed but Python could not be found.\n\n` +
    `Please restart the application. If the issue persists, install Python ${PYTHON_VERSION} manually\n` +
    `from https://www.python.org/downloads/ and ensure "Add Python to PATH" is checked.`
  );
}

async function ensurePythonEnvironment() {
  const appDir = getAppDir();
  const dataDir = getUserDataDir();
  const pythonPath = getPythonPath();
  const pipPath = getPipPath();
  const requirementsPath = getRequirementsPath();
  const venvDir = path.join(dataDir, '.venv');

  // Step 1: Find or install Python
  updateLoading('Checking for Python installation...', -1);
  let systemPython = await findSystemPython();
  if (!systemPython) {
    systemPython = await installPython();
  }
  console.log(`Using Python: ${systemPython}`);

  // Step 2: Create virtual environment if missing
  if (!fs.existsSync(pythonPath)) {
    updateLoading('Creating Python virtual environment...', -1);
    console.log(`Creating venv in ${venvDir}...`);
    await runCommand(systemPython, ['-m', 'venv', venvDir]);
    console.log('Venv created.');
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
    await runCommand(pipPath, ['install', '-r', requirementsPath], appDir);
    console.log('Requirements installed.');
  }

  // Step 5: Copy database to user data dir if it doesn't exist there
  const dbSource = path.join(appDir, 'db.sqlite3');
  const dbDest = path.join(dataDir, 'db.sqlite3');
  if (fs.existsSync(dbSource) && !fs.existsSync(dbDest)) {
    fs.copyFileSync(dbSource, dbDest);
  }

  // Step 6: Ensure media directory exists in user data dir
  const mediaDir = path.join(dataDir, 'media');
  if (!fs.existsSync(mediaDir)) {
    fs.mkdirSync(mediaDir, { recursive: true });
  }

  // Step 7: Run migrations
  updateLoading('Setting up database...', -1);
  const managePy = getManagePyPath();
  try {
    await runCommand(pythonPath, [managePy, 'migrate', '--run-syncdb'], dataDir);
    console.log('Migrations applied.');
  } catch (e) {
    console.warn('Migration warning:', e.message);
  }
}

function startDjango() {
  const pythonPath = getPythonPath();
  const managePy = getManagePyPath();
  const dataDir = getUserDataDir();

  console.log(`Starting Django: ${pythonPath} ${managePy}`);
  console.log(`Working directory: ${dataDir}`);

  const env = Object.assign({}, process.env, {
    REGISTRY_DATA_DIR: dataDir,
    REGISTRY_DB_PATH: path.join(dataDir, 'db.sqlite3'),
    REGISTRY_MEDIA_ROOT: path.join(dataDir, 'media'),
  });

  djangoProcess = spawn(pythonPath, [managePy, 'runserver', `0.0.0.0:${DJANGO_PORT}`, '--noreload'], {
    cwd: dataDir,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: env,
  });

  djangoProcess.stdout.on('data', (data) => {
    console.log(`[Django] ${data.toString().trim()}`);
  });

  djangoProcess.stderr.on('data', (data) => {
    console.log(`[Django] ${data.toString().trim()}`);
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
      document.getElementById('logoImg').style.background = '#1e293b';
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

function isLocalUrl(url) {
  try {
    const parsed = new URL(url);
    return (
      parsed.hostname === '127.0.0.1' ||
      parsed.hostname === 'localhost' ||
      parsed.hostname === '0.0.0.0' ||
      parsed.protocol === 'data:' ||
      parsed.protocol === 'about:'
    );
  } catch {
    return false;
  }
}

function getIconPath() {
  return path.join(getAppDir(), 'static', 'icon.ico');
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: 'Registry',
    icon: getIconPath(),
    webPreferences: {
      preload: path.join(__dirname, 'electron-preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      javascript: true,
    },
    show: false,
    autoHideMenuBar: true,
  });

  mainWindow.loadURL(DJANGO_URL);

  mainWindow.once('ready-to-show', () => {
    if (loadingWindow && !loadingWindow.isDestroyed()) {
      loadingWindow.close();
    }
    mainWindow.show();
  });

  // Allow popup windows for printing (internal URLs only)
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isLocalUrl(url)) {
      return {
        action: 'allow',
        overrideBrowserWindowOptions: {
          webPreferences: {
            javascript: true,
            contextIsolation: false,
            nodeIntegration: false,
            sandbox: false,
          },
        },
      };
    }
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Allow navigation within the app
  mainWindow.webContents.on('will-navigate', (e, url) => {
    if (!isLocalUrl(url)) {
      e.preventDefault();
      shell.openExternal(url);
    }
  });

  mainWindow.on('close', (e) => {
    if (djangoProcess) {
      e.preventDefault();
      stopDjango().then(() => app.quit());
    }
  });

  mainWindow.on('closed', () => { mainWindow = null; });
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

    createMainWindow();

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
    });
  } catch (err) {
    if (loadingWindow && !loadingWindow.isDestroyed()) loadingWindow.close();
    dialog.showErrorBox('Startup Error', err.message);
    app.quit();
  }
});

app.on('window-all-closed', async () => {
  await stopDjango();
  app.quit();
});

app.on('before-quit', async () => {
  await stopDjango();
});
