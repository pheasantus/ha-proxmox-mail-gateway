# Proxmox Mail Gateway (PMG) Integration for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Quality Scale](https://img.shields.io/badge/Quality%20Scale-Gold-gold.svg)](https://developers.home-assistant.io/docs/core/integration-quality-scale)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A secure, high-performance Home Assistant integration for monitoring and managing spam quarantine on **Proxmox Mail Gateway (PMG)**.

Release, delete, or whitelist blocked emails directly from mobile actionable push notifications (Android / iOS) and Lovelace dashboard cards—**without ever having to log into the PMG web interface**.

---

## 🌟 Highlights

- 📬 **Real-Time Quarantine Tracking**: Monitor total quarantined messages, average spam scores, and recipient distribution.
- ⚡ **Instant Actionable Push Notifications**: Get notified on your phone the moment an email is held, with instant action buttons:
  - 🟢 **Deliver**: Release email to recipient mailbox.
  - ⭐ **Whitelist & Deliver**: Add sender to recipient whitelist and deliver.
  - 🔴 **Delete**: Permanently discard the quarantined email.
- 🖥️ **Lovelace Ready**: Built-in attributes allow embedding rich quarantine tables with one-tap action links directly in your dashboards.
- 📊 **Optional Host & Queue Metrics**: Toggle monitoring of Postfix mail queues (`active`, `deferred`, `incoming`) and host node CPU utilization.
- 🔒 **Security-by-Design**:
  - **Least Privilege**: Operates securely under the restricted `qmanager` (Quarantine Manager) role.
  - **Zero Secrets in Code**: 100% UI-based configuration via `ConfigFlow` and `OptionsFlow`.
  - **Auto Re-Authentication**: Manages PMG ticket expiration (2h validity) with transparent auto-renewal and CSRF token transmission on modifying requests.
  - **Input Sanitization**: Email senders, subjects, and headers are stripped of non-printable control characters before reaching HA.
  - **Injection Prevention**: Action targets (`mail_id`) are validated against strict alphanumeric patterns before dispatching network requests.

---

## 📋 Requirements

| Component | Minimum Version | Notes |
| :--- | :--- | :--- |
| **Home Assistant** | `2024.1.0` or newer | Compatible with HAOS 18+, Supervised, and Container |
| **Proxmox Mail Gateway** | `7.x` or `8.x` | API accessible on port `8006` (HTTPS) |
| **Network Path** | TCP `8006` | Firewall on PMG must allow traffic from Home Assistant IP |

---

## 🔑 PMG Account Setup (Least Privilege)

To maintain security, create a dedicated user on your Proxmox Mail Gateway with the minimal required role.

### Step 1: Create user on PMG host
Log in to your PMG console (via SSH or web terminal) and run:

```bash
pmgsh create /access/users \
  --userid homeassistant@pmg \
  --role qmanager \
  --enable 1 \
  --comment "Home Assistant Quarantine Integration" \
  --password "YOUR_SUPER_SECURE_PASSWORD"
```

> [!TIP]
> The `qmanager` role grants full access to view and manage quarantined emails across all domains without granting administrative access to mail routing, certificates, or firewall rules.

---

## 📥 Installation

### Method 1: Via HACS (Recommended)

1. Open **HACS** in Home Assistant.
2. Click the three dots `⋮` in the top-right corner and select **Custom repositories**.
3. Add repository URL: `https://github.com/pheasantus/ha-proxmox-mail-gateway`
4. Type: **Integration**.
5. Click **Add**, search for `Proxmox Mail Gateway`, and click **Download**.
6. Restart Home Assistant.

### Method 2: Manual Installation

1. Download the latest release `.zip` from GitHub.
2. Copy the `custom_components/proxmox_mail_gateway` directory into your Home Assistant `<config>/custom_components/` directory.
3. Restart Home Assistant.

---

## ⚙️ Configuration

1. In Home Assistant, navigate to **Settings** → **Devices & Services** → **Add Integration**.
2. Search for **Proxmox Mail Gateway**.
3. Fill in the connection form:
   - **Host**: IP address or hostname of your PMG server (e.g., `192.168.222.30`).
   - **Port**: `8006` (default).
   - **Username**: `homeassistant@pmg`.
   - **Password**: Your user password.
   - **Verify SSL Certificate**: Disable if using PMG's default self-signed certificate.
4. Click **Submit**.

### Options Flow (Settings Customization)

Click **Configure** on the integration card at any time to adjust:
- **Scan Interval**: Polling frequency (10 to 3600 seconds, default: `60s`).
- **Verify SSL Certificate**: Toggle certificate validation.
- **Enable Postfix Queue Sensors**: Active, Deferred, Incoming queue depths.
- **Enable Host Node Sensors**: Host CPU utilization percentage.

---

## 📊 Entities & Sensors

| Entity ID | Name | Unit / State | Description |
| :--- | :--- | :--- | :--- |
| `sensor.pmg_<host>_quarantine_count` | Quarantine Count | `messages` | Number of messages currently in quarantine. Attributes contain full message details. |
| `sensor.pmg_<host>_avg_spam_score` | Average Spam Score | Points | Mean spam level across all held messages. |
| `sensor.pmg_<host>_postfix_active` | Postfix Active Queue | `messages` | Active queue depth *(Optional)*. |
| `sensor.pmg_<host>_postfix_deferred` | Postfix Deferred Queue | `messages` | Deferred delivery queue depth *(Optional)*. |
| `sensor.pmg_<host>_postfix_incoming` | Postfix Incoming Queue | `messages` | Incoming mail queue depth *(Optional)*. |
| `sensor.pmg_<host>_node_cpu` | Node CPU Usage | `%` | PMG host processor utilization *(Optional)*. |

### Quarantine Count Sensor Attributes

The `sensor.pmg_<host>_quarantine_count` entity provides rich metadata in `extra_state_attributes`:
- `messages`: List of objects, each containing:
  - `id`: Unique PMG quarantine identifier (e.g., `C0R17T196878491`).
  - `sender`: Sanitized sender display name / email.
  - `recipient`: Intended mailbox address.
  - `subject`: Sanitized email subject.
  - `spam_score`: Calculated spam level.
  - `bytes`: Message size in bytes.
  - `date_str`: Formatted ISO timestamp.
- `avg_spam_level`: Mean spam score.
- `recipients`: Sorted list of distinct recipient addresses.

---

## 🛠️ Home Assistant Services / Actions

The integration registers 4 dedicated actions under the `proxmox_mail_gateway` domain:

### 1. `proxmox_mail_gateway.deliver_mail`
Releases a message from quarantine and delivers it to the recipient's inbox.
```yaml
action: proxmox_mail_gateway.deliver_mail
data:
  mail_id: "C0R17T196878491"
```

### 2. `proxmox_mail_gateway.delete_mail`
Permanently deletes a message from quarantine.
```yaml
action: proxmox_mail_gateway.delete_mail
data:
  mail_id: "C0R17T196878491"
```

### 3. `proxmox_mail_gateway.whitelist_mail`
Adds the sender to the recipient's whitelist and immediately delivers the message.
```yaml
action: proxmox_mail_gateway.whitelist_mail
data:
  mail_id: "C0R17T196878491"
```

### 4. `proxmox_mail_gateway.quarantine_action`
Generic dispatcher supporting batch operations and custom actions.
```yaml
action: proxmox_mail_gateway.quarantine_action
data:
  mail_id: "C0R17T196878491"
  action: "blacklist"  # deliver | delete | whitelist | blacklist
```

---

## 🔔 Actionable Push Notifications

Automate instant notifications with one-tap action buttons on Android and iOS:

### Automation 1: Send notification when email is quarantined
```yaml
alias: "PMG: Quarantine Push Notification"
description: "Dispatches mobile push notification with action buttons when mail is held"
trigger:
  - platform: event
    event_type: pmg_mail_quarantined
action:
  - action: notify.notify  # Replace with notify.mobile_app_<your_device>
    data:
      title: "📬 PMG Quarantine [{{ trigger.event.data.recipient }}]"
      message: >
        From: {{ trigger.event.data.sender }}
        Subject: {{ trigger.event.data.subject }}
        Score: {{ trigger.event.data.spam_score }}
      data:
        tag: "pmg_{{ trigger.event.data.mail_id }}"
        actions:
          - action: "PMG_DELIVER_{{ trigger.event.data.mail_id }}"
            title: "🟢 Deliver"
          - action: "PMG_WHITELIST_{{ trigger.event.data.mail_id }}"
            title: "⭐ Whitelist & Deliver"
          - action: "PMG_DELETE_{{ trigger.event.data.mail_id }}"
            title: "🔴 Delete"
            destructive: true
mode: parallel
```

### Automation 2: Handle button press from notification
```yaml
alias: "PMG: Handle Notification Action"
description: "Executes deliver, delete, or whitelist when notification action is tapped"
trigger:
  - platform: event
    event_type: mobile_app_notification_action
condition:
  - condition: template
    value_template: >
      {{ trigger.event.data.action.startswith('PMG_DELIVER_') or
         trigger.event.data.action.startswith('PMG_WHITELIST_') or
         trigger.event.data.action.startswith('PMG_DELETE_') }}
action:
  - choose:
      - conditions:
          - condition: template
            value_template: "{{ trigger.event.data.action.startswith('PMG_DELIVER_') }}"
        sequence:
          - variables:
              target_id: "{{ trigger.event.data.action.replace('PMG_DELIVER_', '') }}"
          - action: proxmox_mail_gateway.deliver_mail
            data:
              mail_id: "{{ target_id }}"

      - conditions:
          - condition: template
            value_template: "{{ trigger.event.data.action.startswith('PMG_WHITELIST_') }}"
        sequence:
          - variables:
              target_id: "{{ trigger.event.data.action.replace('PMG_WHITELIST_', '') }}"
          - action: proxmox_mail_gateway.whitelist_mail
            data:
              mail_id: "{{ target_id }}"

      - conditions:
          - condition: template
            value_template: "{{ trigger.event.data.action.startswith('PMG_DELETE_') }}"
        sequence:
          - variables:
              target_id: "{{ trigger.event.data.action.replace('PMG_DELETE_', '') }}"
          - action: proxmox_mail_gateway.delete_mail
            data:
              mail_id: "{{ target_id }}"
mode: single
```

---

## 📱 Lovelace Dashboard Card

Add an interactive Markdown card to your Home Assistant dashboard:

```yaml
type: markdown
title: "📬 Mail Quarantine"
content: >
  {% set messages = state_attr('sensor.pmg_192_168_222_30_quarantine_count', 'messages') %}
  {% if not messages %}
    ✅ No messages currently in quarantine.
  {% else %}
    | Received | Sender & Subject | Action |
    | :--- | :--- | :--- |
    {% for msg in messages %}
      | {{ msg.date_str }} | **{{ msg.sender }}**<br>{{ msg.subject }} | [Deliver](action:call-service/proxmox_mail_gateway.deliver_mail?mail_id={{ msg.id }}) |
    {% endfor %}
  {% endif %}
```

---

## 🛡️ Security & Privacy Assurance

1. **Zero Hardcoded Credentials**: Passwords and tokens are stored exclusively inside Home Assistant's encrypted storage (`.storage/core.config_entries`).
2. **Session Ticket Protection**: Tickets and CSRF tokens are stored only in memory and never written to debug logs.
3. **Strict Validation**: All service calls validate `mail_id` against alphanumeric and delimiter characters (`^[A-Za-z0-9_;-]+$`), completely blocking SQL injection, command execution, and directory traversal.
4. **Data Sanitization**: Control characters (`\x00-\x1f\x7f`) are stripped from all incoming email headers and subjects to prevent UI injection and control character exploits.

---

## 🧪 Testing & Code Quality

The integration maintains a comprehensive automated test suite:
- **130+ unit and integration tests** with **96% code coverage**.
- Validated with **strict typing** (`mypy --strict`).
- Linted and formatted with **Ruff**.
- Security-audited with **Bandit** (0 issues).

To run tests locally:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run full test suite:
pytest tests/ --cov=custom_components.proxmox_mail_gateway -v

# Run linters and security scan:
ruff check custom_components/ tests/
mypy custom_components/ tests/
bandit -r custom_components/ -c pyproject.toml
```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

Developed and maintained by **[@pheasantus](https://github.com/pheasantus)**.
