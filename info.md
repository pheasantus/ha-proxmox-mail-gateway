# Proxmox Mail Gateway (PMG) Integration for Home Assistant

Monitor and manage your **Proxmox Mail Gateway** quarantine directly from Home Assistant.

## Key Features

- 📬 **Live Quarantine Monitoring**: Track quarantined spam count and mean spam scores.
- ⚡ **Actionable Mobile Push Notifications**: Release (`deliver`), delete (`delete`), or whitelist (`whitelist`) blocked emails directly from Android / iOS push notifications without logging into the PMG web interface.
- 📊 **Lovelace Dashboards**: Interactive UI cards with one-tap mail release links.
- 📈 **Optional System & Queue Metrics**: Monitor Postfix queues (`active`, `deferred`, `incoming`) and PMG host CPU utilization.
- 🛡️ **Security-by-Design**:
  - Least privilege: compatible with dedicated `qmanager` role.
  - Zero hardcoded secrets, full ConfigFlow setup.
  - Automatic session ticket renewal and CSRF prevention token handling.
  - Strict input validation against injection and path traversal attacks.
  - Automatic XSS sanitization of email subject and sender fields.
