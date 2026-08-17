# LEco DevOps on Windows

**The platform runs inside WSL2, not natively.** All 29 orchestration scripts are bash and
the stack is Linux containers; there is no native-Windows port and there should not be one
— a PowerShell reimplementation would be a second copy of the rules, free to drift from
the first.

What lives here is only the set of things WSL2 *cannot* do for itself, because they belong
to Windows:

| Script | Does | Needs Admin |
|---|---|---|
| `Install-LecoWindows.ps1` | Orchestrates all of the below, in order | delegates |
| `Test-LecoPreflight.ps1` | WSL2 version, Docker Desktop integration, port/IIS conflicts | no |
| `Install-LhDns.ps1` | Makes `*.lh` resolve for Windows applications | **yes** |
| `Import-LecoRootCa.ps1` | Trusts the mkcert CA in the Windows certificate store | yes (`-Scope User` avoids it) |
| `leco.ps1` | Forwards `start`/`stop`/`status`/… into WSL | no |

## Quick start

```powershell
# In an Administrator PowerShell, from the repo's windows\ directory
.\Install-LecoWindows.ps1
```

That runs preflight, executes `./setup.sh --yes` inside the distro, writes the DNS
entries, and imports the CA.

Day to day:

```powershell
.\leco.ps1 status
.\leco.ps1 start
.\leco.ps1 stop
.\leco.ps1 logs traefik
```

## The two problems that are genuinely Windows-only

Everything else about this platform is indifferent to the host OS — Docker access goes
through the `docker` CLI or a socket inside a Linux container, which Docker Desktop
virtualises identically everywhere. These two do not:

**1 · `*.lh` does not resolve for Windows applications.**
`ecosystem-stack/scripts/dns-setup.sh` configures the *Linux* resolver inside the distro.
Your browser is a Windows application using the Windows resolver, which cannot see it. So
`curl dashboard.lh` succeeds inside WSL while the browser reports "site can't be reached".

Windows has no `/etc/resolver` equivalent and its hosts file has no wildcard support, so:

- `Install-LhDns.ps1` (default) enumerates the hostnames that exist right now and writes
  them to the Windows hosts file. **Not a wildcard** — re-run it after onboarding an app.
- `Install-LhDns.ps1 -Method Acrylic` uses Acrylic DNS Proxy for a real `*.lh` wildcard.
  Covers future hostnames automatically, but installs third-party software and repoints
  the adapter's DNS.

**2 · The certificate is trusted on the wrong side.**
`mkcert -install` inside WSL2 trusts the CA in the *Linux* trust stores. The browser reads
the *Windows* store, which never saw it — so a correctly issued certificate still shows as
untrusted. `Import-LecoRootCa.ps1` copies the CA that WSL2's mkcert actually used (via
`mkcert -CAROOT` and `wslpath`) and imports the same root on the Windows side.

Firefox additionally keeps its own NSS store — set
`security.enterprise_roots.enabled = true` in `about:config`.

## Two things worth getting right up front

**Clone inside the WSL2 filesystem, not `/mnt/c`.** A checkout on the Windows drive works,
but every file operation crosses the 9p boundary — it is dramatically slower, and bind
mounts behave differently. `~/local-ecosystem` is the right place.

**Line endings.** The repo ships a `.gitattributes` forcing LF on everything with a
shebang. Without it, Git for Windows' default `core.autocrlf=true` rewrites every script to
CRLF and WSL2 fails at the shebang line with
`bad interpreter: /usr/bin/env bash^M`. If you cloned before that file existed, run
`git add --renormalize .` once.

## Ports

`Test-LecoPreflight.ps1` checks 80, 443 and 8090 for listeners, and additionally checks
Windows' *reserved* TCP ranges (`netsh int ipv4 show excludedportrange`). Reserved ranges
are the confusing case: nothing is listening, but a bind still fails, so it presents as
"port already in use" with no process to blame. IIS / the World Wide Web Publishing Service
is the usual culprit on 80 and 443.

## Status

These scripts have not been executed on a real Windows host — they were written against the
documented behaviour of `wsl.exe`, `netsh`, and the certificate cmdlets. Treat the first run
as a shakedown, and report anything that misbehaves.
