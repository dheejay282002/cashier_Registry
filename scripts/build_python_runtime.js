const { execFileSync } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const RUNTIME_DIR = path.join(ROOT, 'runtime');
const PY_DIR = path.join(RUNTIME_DIR, 'python');
const CACHE_DIR = path.join(RUNTIME_DIR, 'cache');
const INFO_FILE = path.join(RUNTIME_DIR, '.build-info.json');
const REQUIREMENTS = path.join(ROOT, 'requirements.txt');
const PYTHON_VERSION = process.env.RUNTIME_PYTHON_VERSION || '3.14.5';
const EMBED_URL = `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip`;
const GET_PIP_URL = 'https://bootstrap.pypa.io/get-pip.py';

function log(...args) {
  console.log('[runtime]', ...args);
}

function run(cmd, args, opts = {}) {
  log('>', cmd, args.join(' '));
  return execFileSync(cmd, args, { stdio: 'pipe', encoding: 'utf8', ...opts });
}

function download(url, dest) {
  if (fs.existsSync(dest)) {
    log('using cached', path.basename(dest));
    return;
  }
  log('downloading', url);
  run('curl.exe', ['-sL', '--retry', '3', '-o', dest, url]);
  if (!fs.existsSync(dest) || fs.statSync(dest).size === 0) {
    throw new Error(`Download failed: ${url}`);
  }
}

function requirementsHash() {
  const text = fs.readFileSync(REQUIREMENTS, 'utf8');
  return crypto.createHash('sha1').update(text).digest('hex').slice(0, 16);
}

function runtimeIsFresh(hash) {
  if (!fs.existsSync(path.join(PY_DIR, 'python.exe'))) return false;
  if (!fs.existsSync(INFO_FILE)) return false;
  try {
    const info = JSON.parse(fs.readFileSync(INFO_FILE, 'utf8'));
    return info.python === PYTHON_VERSION && info.requirements === hash;
  } catch (e) {
    return false;
  }
}

function extractZip(zipPath, destDir) {
  run('powershell.exe', [
    '-NoProfile',
    '-Command',
    `Expand-Archive -LiteralPath '${zipPath}' -DestinationPath '${destDir}' -Force`,
  ]);
}

function patchPthFile() {
  const dir = fs.readdirSync(PY_DIR);
  const pthName = dir.find((f) => f.endsWith('._pth'));
  if (!pthName) throw new Error('python._pth not found in embeddable package');
  const pthPath = path.join(PY_DIR, pthName);
  const lines = fs.readFileSync(pthPath, 'utf8')
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  const base = lines.filter((l) => l.startsWith('#') || l.startsWith('python'));
  if (!base.some((l) => l.includes('python3') && l.endsWith('.zip'))) {
    base.unshift(pthName.replace('._pth', '.zip'));
  }
  if (!base.includes('.')) base.push('.');
  if (!base.includes('Lib\\site-packages')) base.push('Lib\\site-packages');
  // App root (runtime/python -> runtime -> app) so manage.py can import the
  // Django project. The embeddable build never adds the script directory.
  if (!base.includes('..\\..')) base.push('..\\..');
  if (!base.includes('import site')) base.push('import site');
  fs.writeFileSync(pthPath, base.join('\r\n') + '\r\n', 'utf8');
  log('patched', pthName, '->', base.join(' | '));
}

function findBasePythonDir() {
  const candidates = [];
  try {
    const exe = run('py', ['-3.14', '-c', 'import sys; print(sys.base_prefix)']).trim();
    if (exe) candidates.push(exe);
  } catch (e) { /* launcher may be missing */ }
  const local = path.join(process.env.LOCALAPPDATA || '', 'Programs\\Python', 'Python314');
  const core = path.join(process.env.LOCALAPPDATA || '', 'Python', 'pythoncore-3.14-64');
  candidates.push(local, core);
  return candidates.find((p) => p && fs.existsSync(path.join(p, 'python.exe')));
}

function copyMissingRuntimeDlls() {
  const needed = ['vcruntime140.dll', 'vcruntime140_1.dll', 'python3.dll'];
  const missing = needed.filter((f) => !fs.existsSync(path.join(PY_DIR, f)));
  if (missing.length === 0) return;
  const baseDir = findBasePythonDir();
  if (!baseDir) {
    log('WARNING: could not locate a base Python install to copy', missing.join(', '));
    return;
  }
  for (const file of missing) {
    const src = path.join(baseDir, file);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, path.join(PY_DIR, file));
      log('copied', file, 'from', baseDir);
    } else {
      log('WARNING: missing', file, 'not found in', baseDir);
    }
  }
}

function main() {
  const hash = requirementsHash();
  if (runtimeIsFresh(hash)) {
    log('runtime already up to date');
    return;
  }

  fs.mkdirSync(CACHE_DIR, { recursive: true });
  const zipPath = path.join(CACHE_DIR, path.basename(new URL(EMBED_URL).pathname));
  const pipPath = path.join(CACHE_DIR, 'get-pip.py');
  download(EMBED_URL, zipPath);
  download(GET_PIP_URL, pipPath);

  log('extracting embeddable Python', PYTHON_VERSION);
  if (fs.existsSync(PY_DIR)) fs.rmSync(PY_DIR, { recursive: true, force: true });
  fs.mkdirSync(PY_DIR, { recursive: true });
  extractZip(zipPath, PY_DIR);
  patchPthFile();
  copyMissingRuntimeDlls();

  const py = path.join(PY_DIR, 'python.exe');
  log('bootstrapping pip');
  run(py, [pipPath, '--no-warn-script-location'], { cwd: PY_DIR });

  log('installing requirements');
  run(py, ['-m', 'pip', 'install', '--no-warn-script-location', '-r', REQUIREMENTS], { cwd: ROOT });

  log('verifying imports');
  const check = run(py, [
    '-c',
    'import django, pymysql, cryptography, pyotp, reportlab, openpyxl, ssl; print("django", django.get_version())',
  ]);
  log(check.trim());

  fs.writeFileSync(INFO_FILE, JSON.stringify({
    python: PYTHON_VERSION,
    requirements: hash,
    builtAt: new Date().toISOString(),
  }, null, 2));

  const sizeMb = (folderSize(RUNTIME_DIR) / 1048576).toFixed(1);
  log(`done - runtime is ${sizeMb} MB`);
}

function folderSize(dir) {
  let total = 0;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) total += folderSize(full);
    else total += fs.statSync(full).size;
  }
  return total;
}

try {
  main();
} catch (e) {
  console.error('[runtime] FAILED:', e.message || e);
  if (e.stdout) console.error(String(e.stdout).trim());
  if (e.stderr) console.error(String(e.stderr).trim());
  process.exit(1);
}
