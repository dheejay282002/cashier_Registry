# Registry - fully automatic MAIN HOST setup.
# Run via registry-host-setup.bat (it elevates). Safe to re-run any time.
#
# Phase A (no admin): detect IP/Apache, cert, Apache config, client-bat rewrite
# Phase B (admin):    trust cert, hosts, firewall, restart Apache, DB hosts, autostart

param(
    [string]$Ip,
    [switch]$Autostart
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Step($n, $msg) { Write-Host "[$n] $msg" -ForegroundColor Cyan }
function Ok($msg)       { Write-Host "       OK: $msg" -ForegroundColor Green }
function Warn($msg)     { Write-Host "       WARNING: $msg" -ForegroundColor Yellow }
function Err($msg)      { Write-Host "       ERROR: $msg" -ForegroundColor Red }

# Native tools (httpd, openssl, certutil) write to stderr as "errors";
# with EAP=Stop that kills the script. Run them with EAP=Continue.
function Invoke-Native {
    param([scriptblock]$Block)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Block } finally { $ErrorActionPreference = $prev }
}

try {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    # ── 1. LAN IP ─────────────────────────────────────────────────────────
    Step 1 'Detecting server LAN IP...'
    if (-not $Ip) {
        try {
            $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Sort-Object RouteMetric | Select-Object -First 1
            $Ip = (Get-NetIPAddress -InterfaceIndex $route.ifIndex -AddressFamily IPv4 | Select-Object -First 1).IPAddress
        } catch { }
        if (-not $Ip) {
            $Ip = (Get-NetIPAddress -AddressFamily IPv4 |
                Where-Object { $_.IPAddress -notlike '169.254*' -and $_.IPAddress -ne '127.0.0.1' } |
                Select-Object -First 1).IPAddress
        }
    }
    if (-not $Ip) { Err 'Could not detect LAN IP. Run: registry-host-setup.bat <your-ip>'; exit 1 }
    Ok "LAN IP: $Ip"

    # ── 2. Locate Apache ──────────────────────────────────────────────────
    Step 2 'Locating Apache...'
    $apache = $null; $svcName = $null
    $svc = Get-CimInstance Win32_Service -ErrorAction SilentlyContinue |
        Where-Object { $_.PathName -match 'httpd\.exe' } | Select-Object -First 1
    if ($svc) {
        $svcName = $svc.Name
        $m = [regex]::Match($svc.PathName, '(?:"([^"]+)"|(\S+))')
        $exePath = if ($m.Groups[1].Value) { $m.Groups[1].Value } else { $m.Groups[2].Value }
        $candidate = Split-Path (Split-Path $exePath -Parent) -Parent
        if (Test-Path "$candidate\bin\httpd.exe") { $apache = $candidate }
    }
    if (-not $apache) {
        foreach ($c in @('C:\xampdj\apache', 'C:\Apache24', 'C:\apache24')) {
            if (Test-Path "$c\bin\httpd.exe") { $apache = $c; break }
        }
    }
    if (-not $apache) {
        Warn 'Apache not found - skipping https://registry.'
        Warn "The system will only be reachable at http://${Ip}:8000 (start the Registry/Electron app)."
        Warn 'Install Apache/XAMPP and re-run this script to enable https://registry.'
        exit 0
    }
    Ok "Apache: $apache (service: $svcName)"

    # ── 3. Certificate ────────────────────────────────────────────────────
    Step 3 'Preparing certificate...'
    $openssl = $null
    if (Test-Path "$apache\bin\openssl.exe") { $openssl = "$apache\bin\openssl.exe" }
    else {
        $cmd = Get-Command openssl -ErrorAction SilentlyContinue
        if ($cmd) { $openssl = $cmd.Source }
    }
    $certFile = "$apache\conf\registry.crt"
    $keyFile  = "$apache\conf\registry.key"
    $needGen = $true
    if ($openssl -and (Test-Path $certFile) -and (Test-Path $keyFile)) {
        $txt = Invoke-Native { & $openssl x509 -in $certFile -noout -text 2>$null } | Out-String
        if ($txt -match [regex]::Escape("IP Address:$Ip") -and $txt -match 'CN\s*=\s*registry') {
            $needGen = $false
        }
    }
    $regenerated = $false
    if ($needGen) {
        if (-not $openssl) { Err "openssl not found ($apache\bin\openssl.exe or PATH). Cannot generate certificate."; exit 1 }
        $cnf = Join-Path $env:TEMP 'registry_openssl.cnf'
        @"
[ req ]
distinguished_name = dn
x509_extensions = v3_ca
prompt = no
[ dn ]
CN = registry
[ v3_ca ]
basicConstraints = critical,CA:TRUE
keyUsage = critical,digitalSignature,keyCertSign,cRLSign
extendedKeyUsage = serverAuth
subjectAltName = DNS:registry,DNS:localhost,IP:127.0.0.1,IP:$Ip
"@ | Set-Content -Path $cnf -Encoding ascii
        Invoke-Native { & $openssl req -x509 -newkey rsa:2048 -nodes -config $cnf -keyout $keyFile -out $certFile -days 3650 2>$null } | Out-Null
        if (-not (Test-Path $certFile)) { Err 'Certificate generation failed.'; exit 1 }
        $regenerated = $true
        Warn 'NEW certificate generated - every client must re-run registry-client-setup.bat once.'
    } else {
        Ok "Existing certificate already covers $Ip - reusing (already-trusted clients stay valid)."
    }

    # ── 4. Apache configuration ───────────────────────────────────────────
    Step 4 'Configuring Apache...'
    $httpdConf = "$apache\conf\httpd.conf"
    $raw = Get-Content $httpdConf -Raw
    $orig = $raw
    foreach ($mod in @('proxy_module', 'proxy_http_module', 'ssl_module', 'headers_module')) {
        $raw = $raw -replace "(?m)^#(LoadModule\s+$mod\s)", '$1'
    }
    if ($raw -notmatch '(?m)^Include\s+"?conf/extra/httpd-registry\.conf') {
        $raw = $raw + "`r`nInclude `"conf/extra/httpd-registry.conf`"`r`n"
    }
    if ($raw -ne $orig) {
        Set-Content -Path $httpdConf -Value $raw -Encoding ascii -NoNewline
        Ok 'httpd.conf updated (modules + include)'
    } else {
        Ok 'httpd.conf already configured'
    }

    $vhost = @"
# Registry app reverse proxy - auto-generated by registry-host-setup.ps1
<VirtualHost ${Ip}:443>
    ServerName registry
    ServerAlias registry.local ${Ip}

    SSLEngine on
    SSLCertificateFile "conf/registry.crt"
    SSLCertificateKeyFile "conf/registry.key"

    ProxyPreserveHost On
    ProxyRequests Off
    ProxyPass / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/

    RequestHeader set X-Forwarded-Proto "https"

    ProxyTimeout 120
</VirtualHost>

<VirtualHost 127.0.0.1:443>
    ServerName registry

    SSLEngine on
    SSLCertificateFile "conf/registry.crt"
    SSLCertificateKeyFile "conf/registry.key"

    ProxyPreserveHost On
    ProxyRequests Off
    ProxyPass / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/

    RequestHeader set X-Forwarded-Proto "https"

    ProxyTimeout 120
</VirtualHost>
"@
    Set-Content -Path "$apache\conf\extra\httpd-registry.conf" -Value $vhost -Encoding ascii
    Ok 'vhost written (LAN IP + 127.0.0.1)'

    $dr = [regex]::Match((Get-Content $httpdConf -Raw), '(?m)^DocumentRoot\s+"([^"]+)"').Groups[1].Value
    $apacheOk = $false
    $tOut = Invoke-Native { & "$apache\bin\httpd.exe" -t 2>&1 } | Out-String
    if ($LASTEXITCODE -ne 0) {
        Err "Apache config INVALID - will NOT restart. Fix these first:"
        Write-Host $tOut
    } else {
        Ok 'httpd -t syntax OK'
        $apacheOk = $true
        if ($dr -and (Test-Path $dr)) {
            Copy-Item $certFile "$dr\registry.crt" -Force
            Ok "phone certificate published: $dr\registry.crt"
        }
    }

    # ── 5. Rewrite client setup file + README (same folder) ───────────────
    Step 5 'Updating distributed files...'
    $clientBat = Join-Path $scriptDir 'registry-client-setup.bat'
    if (Test-Path $clientBat) {
        $cLines = @(Get-Content $clientBat)
        $b = [array]::IndexOf($cLines, 'REM <<<BEGIN CERT>>>')
        $e = [array]::IndexOf($cLines, 'REM <<<END CERT>>>')
        if ($b -ge 0 -and $e -gt $b) {
            $tmpEnc = Join-Path $env:TEMP 'registry_crt_b64.txt'
            Invoke-Native { certutil -encode $certFile $tmpEnc } | Out-Null
            $b64Lines = @(Get-Content $tmpEnc | Where-Object { $_ -match '\S' })
            $block = @('(') + ($b64Lines | ForEach-Object { "echo $_" }) + (') > "%B64%"')
            $cLines = $cLines[0..$b] + $block + $cLines[$e..($cLines.Count - 1)]
        } else {
            Warn 'Client bat has no cert markers - certificate block left as-is.'
        }
        $cLines = $cLines | ForEach-Object {
            if ($_ -match '(?i)registry' -and $_ -match '\d{1,3}(\.\d{1,3}){3}') {
                $_ -replace '\d{1,3}(\.\d{1,3}){3}', $Ip
            } else { $_ }
        }
        Set-Content -Path $clientBat -Value $cLines -Encoding ascii
        Ok "registry-client-setup.bat updated (IP $Ip$(if ($regenerated) { ', fresh certificate' }))"
    } else {
        Warn 'registry-client-setup.bat not found next to this script.'
    }

    $readme = Join-Path $scriptDir 'README.txt'
    if (Test-Path $readme) {
        $r = Get-Content $readme -Raw
        $r2 = $r -replace '\d{1,3}(\.\d{1,3}){3}', $Ip
        if ($r2 -ne $r) {
            Set-Content -Path $readme -Value $r2 -Encoding ascii -NoNewline
            Ok 'README.txt updated'
        }
    }

    # ── 6+. Admin-only steps ──────────────────────────────────────────────
    if (-not $isAdmin) {
        Warn 'Not elevated - skipped: certificate trust, hosts, firewall, Apache restart.'
        Warn 'Run registry-host-setup.bat (it asks for admin) to finish.'
        exit 0
    }

    Step 6 'Trusting certificate (machine + current user)...'
    Invoke-Native { certutil -f -addstore Root $certFile } | Out-Null
    Invoke-Native { certutil -f -user -addstore Root $certFile } | Out-Null
    Ok 'certificate trusted for all users on this PC'

    Step 7 'Hosts entry...'
    $hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
    $lines = @(Get-Content $hostsPath -ErrorAction SilentlyContinue |
        Where-Object { $_ -notmatch '(?i)\bregistry\s*(#.*)?$' })
    $lines += "$Ip registry"
    Set-Content -Path $hostsPath -Value $lines -Encoding ascii
    Ok "$Ip registry (stale entries removed)"

    Step 8 'Firewall rule for port 443...'
    netsh advfirewall firewall delete rule name='Registry - HTTPS 443' | Out-Null
    netsh advfirewall firewall add rule name='Registry - HTTPS 443' dir=in action=allow protocol=TCP localport=443 | Out-Null
    Ok 'firewall rule in place'

    if ($apacheOk) {
        Step 9 'Restarting Apache...'
        if ($svcName) {
            Restart-Service -Name $svcName -Force
            Ok "service '$svcName' restarted"
        } else {
            & "$apache\bin\httpd.exe" -k restart
            Ok 'Apache restarted'
        }
    } else {
        Warn 'Apache restart SKIPPED due to config errors above.'
    }

    Step 10 'Allowed Hosts in database (best effort)...'
    $cap = $null
    foreach ($c in @("$scriptDir\..", "$scriptDir\..\..")) {
        if (Test-Path (Join-Path $c 'manage.py')) { $cap = (Resolve-Path $c).Path; break }
    }
    $py = if ($cap) { "$cap\.venv\Scripts\python.exe" } else { $null }
    if ($py -and (Test-Path $py)) {
        Push-Location $cap
        & $py manage.py shell -c "from admin_panel.models import AllowedHost; [AllowedHost.objects.get_or_create(host=h) for h in ('$Ip','registry','registry.local')]" | Out-Null
        Pop-Location
        Ok "Allowed Hosts: $Ip, registry, registry.local"
    } else {
        Warn "Add '$Ip' via System Settings > Allowed Hosts if clients get DisallowedHost errors."
    }

    if ($Autostart) {
        Step 11 'Autostart task (launch Electron at logon)...'
        $exe = $null; $tr = $null
        if ($cap) {
            $dev = "$cap\node_modules\electron\dist\electron.exe"
            if (Test-Path $dev) { $exe = $dev; $tr = "`"$exe`" `"$cap`"" }
        }
        if (-not $exe) {
            $inst = "$env:LOCALAPPDATA\Programs\Registry\Registry.exe"
            if (Test-Path $inst) { $exe = $inst; $tr = "`"$exe`"" }
        }
        if ($tr) {
            schtasks /Create /TN 'Registry AutoStart' /TR $tr /SC ONLOGON /F | Out-Null
            Ok "scheduled task 'Registry AutoStart' created"
        } else {
            Warn 'Electron app not found - autostart skipped.'
        }
    }

    Write-Host ''
    Write-Host 'DONE. https://registry now points at this server.' -ForegroundColor Green
    Write-Host "  PCs:     run registry-client-setup.bat once (send them that one file)" -ForegroundColor Green
    Write-Host "  Phones:  install http://$Ip/registry.crt once, then open https://$Ip" -ForegroundColor Green
} catch {
    Err $_.Exception.Message
    exit 1
}
