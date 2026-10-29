Registry - Setup Package
========================

WHAT YOU NEED
  Main host laptop  : run registry-host-setup.bat  (once, with admin prompt)
  Every other PC    : run registry-client-setup.bat (once)
  Phones/tablets    : see PHONE ACCESS below (one-time certificate install)


1. MAIN HOST (the laptop that runs the registry app)
----------------------------------------------------
  - Keep the Registry app installed on this laptop.
  - Double-click:  registry-host-setup.bat
    * Approve the admin prompt.
    * It auto-detects this laptop's IP, generates the certificate,
      configures Apache (https://registry), updates hosts + firewall,
      trusts the certificate, and refreshes registry-client-setup.bat
      in this folder so it always carries the current IP/certificate.
  - Start the Registry/Electron app (it starts Django behind Apache).
  - Optional flags (type after the file name):
      registry-host-setup.bat /autostart    also launch the app at Windows logon
      registry-host-setup.bat 192.168.100.11      force a specific IP if detection is wrong
  - If the laptop's IP changes later: just re-run registry-host-setup.bat,
    then re-send registry-client-setup.bat to clients.


2. OTHER PCs
------------
  Copy ONE file to the PC and double-click it:
      registry-client-setup.bat
  It adds the hosts entry and trusts the certificate - no browser warnings.
  Start the Registry/Electron app there, or open https://registry directly.


3. PHONES / TABLETS (browsers cannot edit hosts files)
------------------------------------------------------
  Phones use the IP address directly (https://registry is PC-only):

    a. One-time certificate install (needed once per phone):
       - Open   http://<SERVER-IP>/registry.crt   in the phone browser
         (SERVER-IP is printed by registry-host-setup.bat; e.g. 192.168.100.11)
       - iOS: Settings > General > VPN & Device Management > install the
              profile, then Settings > General > About > Certificate Trust
              Settings > enable full trust for "registry"
       - Android: install the certificate (may ask for a screen lock)
    b. Then open:   https://<SERVER-IP>
       Still shows a warning? Tap "Advanced" > "Proceed" (once).
    c. Fastest fallback (no install at all):
       http://<SERVER-IP>:8000    (Django's direct port - plain HTTP)


TROUBLESHOOTING
  - "503" : the Registry app (Django) is not running on the main host. Start it.
  - "certificate error" on a PC : re-run registry-client-setup.bat on that PC.
  - New server / new IP : re-run registry-host-setup.bat on the new host,
    send the refreshed registry-client-setup.bat to everyone again, and have
    phones install http://<NEW-IP>/registry.crt once.
  - Phones on a different Wi-Fi/subnet than the server will not reach it.
