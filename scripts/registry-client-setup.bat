@echo off
REM =========================================================================
REM  Registry - CLIENT setup for https://registry  (single file, self-contained)
REM  Copy THIS one file to the client PC, right-click -> "Run as administrator".
REM  Adds hosts entry (192.168.100.11 registry) and trusts the certificate.
REM =========================================================================
net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo Please right-click this file and select "Run as administrator".
    echo.
    pause
    exit /b 1
)

set HOSTS=%SystemRoot%\System32\drivers\etc\hosts
findstr /c:"192.168.100.11 registry" "%HOSTS%" >nul 2>&1
if errorlevel 1 (
    echo.>>"%HOSTS%"
    echo 192.168.100.11 registry>>"%HOSTS%"
    echo [1/2] hosts entry added.
) else (
    echo [1/2] hosts entry already present.
)

set B64=%TEMP%\registry_cert_b64.txt
set CRT=%TEMP%\registry_cert.crt
REM <<<BEGIN CERT>>>
(
echo -----BEGIN CERTIFICATE-----
echo LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tDQpNSUlETXpDQ0FodWdBd0lCQWdJ
echo VVAxNHB2blJCZzVIOHRlYkxxVmtrMlJCTFRpd3dEUVlKS29aSWh2Y05BUUVMDQpC
echo UUF3RXpFUk1BOEdBMVVFQXd3SWNtVm5hWE4wY25rd0hoY05Nall4TURFME1UY3dN
echo RE0xV2hjTk16WXhNREV4DQpNVGN3TURNMVdqQVRNUkV3RHdZRFZRUUREQWh5Wldk
echo cGMzUnllVENDQVNJd0RRWUpLb1pJaHZjTkFRRUJCUUFEDQpnZ0VQQURDQ0FRb0Nn
echo Z0VCQU1QaEZtRjRzK3hpQmMzU1RSQ3hTckJEOGE5U3BmS2ExTDYrU2wySnBGMzF6
echo YWs5DQp0WmRaWGl2QkpSUndOclljQVhtSDhDNEJFYmV6RXBWZExKSFJjNk9udVU0
echo WmVMNURxSjYwd2MwMWxIQmdVNU9RDQpKRFI5WmpjNGw4c2tzWGFBZ3dlanE3NWpx
echo N0IwS0VubklhOCtwRnVlVjkwZStscXlWdDJSM2xEWDlxTjNSZm1YDQp3bUxSRkJt
echo eGc2eDhQSXFMcTRqNlpyQkhKVVpSc0VMcFBUcUl0T0FuUkhvaVlXZ1dzRWk4d2tO
echo bVROa1k1eTJyDQo3THR0OGdQSHhCUVE1Wk9lcU9lcGd3clk4NERuaVBsSmpsTWY0
echo KzhJbWppMkdFSnVUWmZnN1pVL3Nxd1gyZFEvDQpFclFENWp6VXljdGJYdVRXUEdU
echo bElncSs5ZDEreDVDZDBpNXIwSE1DQXdFQUFhTi9NSDB3SFFZRFZSME9CQllFDQpG
echo S1pQdkVxUnNGUFJCci9uaFZPMnZoUnBCKzJjTUI4R0ExVWRJd1FZTUJhQUZLWlB2
echo RXFSc0ZQUkJyL25oVk8yDQp2aFJwQisyY01BOEdBMVVkRXdFQi93UUZNQU1CQWY4
echo d0tnWURWUjBSQkNNd0lZSUljbVZuYVhOMGNubUNDV3h2DQpZMkZzYUc5emRJY0Vm
echo d0FBQVljRXdLaGtDekFOQmdrcWhraUc5dzBCQVFzRkFBT0NBUUVBVG8vQ2c2ZGRV
echo L3dhDQpIWWF0RDB6SnlvN1NzM0NSVU4vc3I5d09NN3o1RXBKa2QzalgyRzVIVTNI
echo S08xcDd6SWJqNzVtZk5tNkJidGxuDQpsRnFEbE5hY1FaQm1Lb1lwbGV5Q0Z0M2ZI
echo Wk0vVktNeHlqSkJiY3lHcUtXSi9BOXpqR2cybzVuNG1pMFI2NnRGDQoyRDd4eGor
echo ZGNndTUwZDY0d0l0VWpTY3NqRjk5T0hXYWUzWXlXdGJzL0RmZnl0b2Npd0w5WXlK
echo aENjTDFtVnJCDQpsVUkvYThaa3ErMlRqSUJCYlpuUnJ4S0RSUnVDZHNFSWk3VHhp
echo VVBkUDBLU09Tc3lvdWMvSVhrMXJ0TWE5VVVHDQpsT0JnbEpscjdJaVR6cFJma3VV
echo SVBQMUxFTDRCNDFHU0dlbGxGLzJvWHdxbXl3SHBGaHNxdlhNTno3NXVZaHh6DQo3
echo RmNmSktFbkxBPT0NCi0tLS0tRU5EIENFUlRJRklDQVRFLS0tLS0NCg==
echo -----END CERTIFICATE-----
) > "%B64%"
REM <<<END CERT>>>
certutil -decode "%B64%" "%CRT%" >nul 2>&1
certutil -f -user -addstore Root "%CRT%" >nul 2>&1
del "%B64%" "%CRT%" >nul 2>&1
echo [2/2] certificate trusted (no browser warning).

echo.
echo Done. Open https://registry in your browser.
echo.
pause
