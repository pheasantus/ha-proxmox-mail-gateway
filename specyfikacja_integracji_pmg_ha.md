# Specyfikacja Techniczna i Środowiskowa: Integracja Proxmox Mail Gateway (PMG) dla Home Assistant (HACS)

* **Data opracowania:** 2026-11-18
* **Autor:** Antigravity (Google DeepMind)
* **Przeznaczenie:** Kompletna dokumentacja środowiskowa, referencja API oraz wytyczne architektoniczne dla Agenta Kodującego do implementacji custom integracji Home Assistant jako pakietu **HACS (Home Assistant Community Store)**.
* **Cel główny integracji:** Monitorowanie stanu kwarantanny PMG, rejestracja encji sensorów w Home Assistant, publikacja zdarzeń o zatrzymanych mailach oraz udostępnienie akcji (services) do natychmiastowego zwalniania (`deliver`), usuwania (`delete`) i dodawania do białej listy (`whitelist`) bezpośrednio z poziomu aplikacji mobilnej Home Assistant (powiadomienia actionable i karty Lovelace) bez konieczności logowania się przez przeglądarkę do interfejsu PMG.

---

## 1. Szczegóły Środowiska Docelowego

### 1.1. Węzeł Home Assistant
* **Typ instalacji:** Home Assistant OS (HAOS 18.4)
* **Wersja Core:** `2026.11.2` (Frontend: `20261028.6`, Supervisor: `2026.11.0`)
* **Wersja Pythona:** `Python 3.14.10`
* **Architektura:** `x86_64` (KVM VMID 101 na klastrze Proxmox VE `192.168.220.100`)
* **Adres IP:** `192.168.218.50` (VLAN Default: `192.168.218.0/24`)
* **Strefa czasowa:** `Europe/Warsaw`
* **Zarejestrowane urządzenia mobilne w HA:** W systemie aktywnych jest kilkanaście urządzeń `mobile_app` (m.in. smartfony Samsung Galaxy S24/S25, Xiaomi/Poco, zegarki Galaxy Watch oraz tablety), co umożliwia wysyłanie natywnych powiadomień `actionable notifications` push do użytkowników i administratorów.

### 1.2. Węzeł Proxmox Mail Gateway (PMG)
* **Wersja:** Proxmox Mail Gateway 8.x / Debian 12 (bookworm) – kontener LXC (VMID 110 na PVE `192.168.220.100`)
* **Nazwa węzła (nodename):** `proxmox-mail-gateway`
* **Adres IP w sieci:** `192.168.222.30` (strefa DMZ: `192.168.222.0/24`)
* **Port API:** `8006` (HTTPS, certyfikat lokalny/self-signed PMG)
* **Firewall (nftables) na PMG:**
  * W `/etc/nftables.conf` na PMG reguła łańcucha `input` zezwala na połączenia do portu `8006/tcp` ze zbioru `@mgmt_hosts`.
  * W zbiorze `@mgmt_hosts` znajduje się podsieć `192.168.218.0/24`.
  * **Wniosek:** Home Assistant (`192.168.218.50`) ma **otwartą i bezpośrednią łączność sieciową** do portu `8006` maszyny PMG (`192.168.222.30`).

---

## 2. Model Uwierzytelniania i Uprawnień PMG API

Proxmox Mail Gateway wykorzystuje mechanizm biletów sesyjnych (**Ticket-based Authentication**):

### 2.1. Protokół logowania
* **Żądanie:** `POST https://192.168.222.30:8006/api2/json/access/ticket`
* **Nagłówek:** `Content-Type: application/x-www-form-urlencoded` lub `application/json`
* **Body:**
  ```json
  {
    "username": "homeassistant@pmg",
    "password": "<HASLO_UZYTKOWNIKA>"
  }
  ```
* **Odpowiedź (HTTP 200 OK):**
  ```json
  {
    "data": {
      "ticket": "PMG:homeassistant@pmg:673C...==",
      "CSRFPreventionToken": "673C...:XyZ...",
      "username": "homeassistant@pmg"
    }
  }
  ```

### 2.2. Autoryzacja kolejnych zapytań
W każdym kolejnym zapytaniu API należy przekazywać:
* **Cookie:** `PMGAuthCookie=<ticket>`
* **Nagłówek HTTP (wymagany przy POST / PUT / DELETE):** `CSRFPreventionToken: <CSRFPreventionToken>`
* **Cykl życia biletu:** Bilet jest ważny przez 2 godziny. Klient API powinien obsłużyć kod HTTP `401 Unauthorized` poprzez automatyczne ponowienie uwierzytelnienia i ponowienie zapytania (tzw. auto-reauth loop).

### 2.3. Dedykowane konto PMG dla Home Assistanta
Aby zachować zasadę minimalnych uprawnień (Least Privilege), w PMG należy utworzyć dedykowanego użytkownika z rolą `admin` lub `qmanager` (Quarantine Manager):
```bash
# Wykonanie na hoście PMG (192.168.222.30):
pmgsh create /access/users --userid homeassistant@pmg --role admin --enable 1 --comment "Home Assistant PMG Integration" --password "<SUPER_BEZPIECZNE_HASLO>"
```

---

## 3. Pełna Referencja Punktów Końcowych PMG API dla Kwarantanny

Wszystkie zapytania są wykonywane pod bazowym URL: `https://{host}:{port}/api2/json`.

### 3.1. Ogólny status kwarantanny antyspamowej
* **Metoda i ścieżka:** `GET /quarantine/spamstatus`
* **Uprawnienia:** `admin`, `qmanager`, `audit`
* **Przykładowa odpowiedź:**
  ```json
  {
    "data": {
      "count": 2,
      "avgspam": "4.5000000000000000",
      "avgbytes": "51142.500000000000",
      "mbytes": 0.1015625
    }
  }
  ```

### 3.2. Lista odbiorców posiadających wiadomości w kwarantannie
* **Metoda i ścieżka:** `GET /quarantine/spamusers`
* **Parametry opcjonalne:** `starttime` (unix epoch), `endtime` (unix epoch)
* **Przykładowa odpowiedź:**
  ```json
  {
    "data": [
      { "mail": "kornelia@kwiatkowsky.eu" },
      { "mail": "administrator@kwiatkowsky.eu" }
    ]
  }
  ```

### 3.3. Lista wiadomości w kwarantannie dla danego odbiorcy
* **Metoda i ścieżka:** `GET /quarantine/spam?pmail={email}`
* **Parametry query:**
  * `pmail`: adres e-mail odbiorcy (np. `kornelia@kwiatkowsky.eu`)
  * `starttime`: początek zakresu (domyślnie: `now - 1 day`)
  * `endtime`: koniec zakresu (domyślnie: `now + 1 day`)
* **Przykładowa odpowiedź:**
  ```json
  {
    "data": [
      {
        "id": "C0R17T196878491",
        "pmail": "kornelia@kwiatkowsky.eu",
        "receiver": "kornelia@kwiatkowsky.eu",
        "from": "Kurier Balonowy <patryk.kurierbalonowy.pl@postermywall.com>",
        "envelope_sender": "010001a1eeb39167-2a3aee08-db44-444c-af1b-bb1ceb96b9e0-000000@mail.postermywall.com",
        "subject": "SPAM: 🎈 Nowa dostawa Party Deco — 25 nowych produktów",
        "spamlevel": 5,
        "score-positive": 6.113,
        "score-negative": -0.301,
        "bytes": 51059,
        "time": 1795006116,
        "seen": 0
      }
    ]
  }
  ```

### 3.4. Wykonanie akcji na wiadomości w kwarantannie (Uwolnienie / Usunięcie / Biała lista)
* **Metoda i ścieżka:** `POST /quarantine/content`
* **Nagłówki wymagane:** `Cookie: PMGAuthCookie=...`, `CSRFPreventionToken: ...`
* **Parametry (Form Data lub JSON):**
  * `id`: unikalny identyfikator wiadomości (np. `C0R17T196878491` lub lista oddzielona średnikami `id1;id2`)
  * `action`:
    * `deliver` – **Zwalnia wiadomość i dostarcza ją do skrzynki odbiorcy**
    * `delete` – Usuwa wiadomość z kwarantanny
    * `whitelist` (lub `welcomelist`) – Dodaje nadawcę do białej listy użytkownika i zwalnia wiadomość
    * `blacklist` (lub `blocklist`) – Dodaje nadawcę do czarnej listy użytkownika i usuwa wiadomość
* **Przykładowa odpowiedź:** `HTTP 200 OK` (JSON: `{"data": null}`)

### 3.5. Pobranie szczegółów i podglądu wiadomości
* **Metoda i ścieżka:** `GET /quarantine/content?id={id}`
* **Zwraca:** Nagłówki e-mail, treść (tekstową/HTML) oraz reguły SpamAssassina (`spaminfo`: `[{name, desc, score}, ...]`).
* **Format HTML:** `GET /api2/htmlmail/quarantine/content?id={id}` zwraca oczyszczony, bezpieczny kod HTML wiadomości.

### 3.6. Metryki węzła i kolejki Postfixa (opcjonalnie do sensorów)
* `GET /nodes/proxmox-mail-gateway/status` &rarr; CPU, RAM, uptime, obciążenie dysku.
* `GET /nodes/proxmox-mail-gateway/postfix/queue` &rarr; liczba maili w kolejkach `active`, `deferred`, `incoming`.

---

## 4. Architektura Integracji HACS (`proxmox_mail_gateway`)

Projekt integracji powinien być zbudowany ściśle według wytycznych **Home Assistant Architecture & Integration Quality Scale**:

### 4.1. Struktura katalogów pakietu
```text
custom_components/proxmox_mail_gateway/
├── __init__.py                # Inicjalizacja komponentu, setup wpisu konfiguracyjnego, rejestracja usług
├── manifest.json              # Metadane integracji (domena, wersja, wymagania, HACS)
├── const.py                   # Stałe (DOMAIN, DEFAULT_PORT, CONF_VERIFY_SSL itp.)
├── config_flow.py             # UI konfiguracji (host, port, username, password, verify_ssl, scan_interval)
├── coordinator.py             # DataUpdateCoordinator (cykliczny polling PMG, cache biletów, detekcja nowych maili)
├── api.py                     # Asynchroniczny klient PMG API (aiohttp) z obsługą ponawiania sesji
├── sensor.py                  # Encje sensorów (licznik kwarantanny, średni spam, metryki kolejek)
├── services.yaml              # Definicja usług: deliver_mail, delete_mail, whitelist_mail, quarantine_action
├── strings.json               # Schemat tekstów bazowych (en)
└── translations/
    ├── en.json                # Język angielski
    └── pl.json                # Język polski
```

### 4.2. Plik `manifest.json`
```json
{
  "domain": "proxmox_mail_gateway",
  "name": "Proxmox Mail Gateway",
  "codeowners": ["@jakub"],
  "config_flow": true,
  "documentation": "https://github.com/twoj-login/ha-proxmox-mail-gateway",
  "integration_type": "hub",
  "iot_class": "local_polling",
  "issue_tracker": "https://github.com/twoj-login/ha-proxmox-mail-gateway/issues",
  "requirements": ["aiohttp>=3.9.0"],
  "version": "1.0.0"
}
```

### 4.3. Plik `hacs.json` (w katalogu głównym repozytorium GitHub)
```json
{
  "name": "Proxmox Mail Gateway (Quarantine & Monitoring)",
  "render_readme": true,
  "homeassistant": "2024.1.0"
}
```

---

## 5. Projekt Encji, Usług i Zdarzeń (Core Functional Spec)

### 5.1. Sensor Kwarantanny (`sensor.pmg_quarantine_total_spam`)
* **Stan encji:** Liczba całkowita (np. `2`) oznaczająca aktualną liczbę maili oczekujących w kwarantannie.
* **Ikona:** `mdi:email-alert-outline` (gdy > 0: `mdi:email-alert`)
* **Atrybuty stanu (`extra_state_attributes`):**
  * `messages`: Lista słowników z ostatnio zatrzymanymi mailami:
    ```python
    [
      {
        "id": "C0R17T196878491",
        "sender": "Kurier Balonowy <patryk.kurierbalonowy.pl@postermywall.com>",
        "envelope_sender": "010001a1eeb39167...postermywall.com",
        "recipient": "kornelia@kwiatkowsky.eu",
        "subject": "SPAM: 🎈 Nowa dostawa Party Deco — 25 nowych produktów",
        "spam_score": 5.0,
        "bytes": 51059,
        "timestamp": 1795006116,
        "date_str": "2026-11-18 13:48:36"
      }
    ]
    ```
  * `avg_spam_level`: Średni wynik punktowy.
  * `recipients`: Lista unikalnych odbiorców (`["kornelia@kwiatkowsky.eu"]`).

### 5.2. Mechanizm Zdarzeń (Event Bus) dla Powiadomień Mobilnych
W `coordinator.py` podczas każdego odświeżenia danych:
1. Porównywana jest lista bieżących `mail_id` z listą zapamiętaną z poprzedniego cyklu.
2. Dla każdego nowo wykrytego maila integracja odpala zdarzenie na szynie Home Assistanta:
   * **Nazwa zdarzenia:** `pmg_mail_quarantined`
   * **Payload zdarzenia:**
     ```python
     {
       "mail_id": "C0R17T196878491",
       "sender": "Kurier Balonowy <patryk...>",
       "recipient": "kornelia@kwiatkowsky.eu",
       "subject": "SPAM: 🎈 Nowa dostawa Party Deco...",
       "spam_score": 5.0,
       "time": 1795006116
     }
     ```

### 5.3. Usługi (Services / Actions)
W pliku `services.yaml` oraz w `__init__.py`:

1. **`proxmox_mail_gateway.deliver_mail`**
   * **Opis:** Zwalnia wskazaną wiadomość z kwarantanny i przesyła ją do skrzynki odbiorcy.
   * **Pola:** `mail_id` (string, wymagane, np. `C0R17T196878491`).
2. **`proxmox_mail_gateway.delete_mail`**
   * **Opis:** Trwale usuwa wiadomość z kwarantanny.
   * **Pola:** `mail_id` (string, wymagane).
3. **`proxmox_mail_gateway.whitelist_mail`**
   * **Opis:** Dodaje nadawcę do białej listy odbiorcy i natychmiast zwalnia wiadomość do doręczenia.
   * **Pola:** `mail_id` (string, wymagane).
4. **`proxmox_mail_gateway.quarantine_action`**
   * **Opis:** Uniwersalna akcja wykonująca operację na kwarantannie.
   * **Pola:**
     * `mail_id` (string, wymagane)
     * `action` (string: `deliver` | `delete` | `whitelist` | `blacklist`)

---

## 6. Przykładowa Automatyzacja Powiadomień Actionable na Telefon

Poniższy kod YAML to gotowy wzorzec, który użytkownik może dodać w Home Assistant, aby natychmiast po zatrzymaniu spamu otrzymać powiadomienie push z przyciskami na smartfonie:

```yaml
alias: "PMG: Powiadomienie o mailu w kwarantannie"
description: "Wysyła powiadomienie z przyciskami akcji na telefon po wykryciu spamu w PMG"
trigger:
  - platform: event
    event_type: pmg_mail_quarantined
action:
  - service: notify.mobile_app_sm_s921b  # Zastąp swoim urządzeniem mobilnym
    data:
      title: "📬 Kwarantanna PMG [{{ trigger.event.data.recipient }}]"
      message: >
        Od: {{ trigger.event.data.sender }}
        Temat: {{ trigger.event.data.subject }}
        Punkty: {{ trigger.event.data.spam_score }}
      data:
        tag: "pmg_{{ trigger.event.data.mail_id }}"
        actions:
          - action: "PMG_DELIVER_{{ trigger.event.data.mail_id }}"
            title: "🟢 Zwolnij do skrzynki"
          - action: "PMG_WHITELIST_{{ trigger.event.data.mail_id }}"
            title: "⭐ Zaufaj i zwolnij"
          - action: "PMG_DELETE_{{ trigger.event.data.mail_id }}"
            title: "🔴 Usuń"
            destructive: true

# Obsługa kliknięcia w powiadomieniu:
mode: parallel
```

Oraz automatyzacja obsługująca kliknięcie akcji:
```yaml
alias: "PMG: Obsługa zwalniania z powiadomienia"
trigger:
  - platform: event
    event_type: mobile_app_notification_action
    event_data:
      action: "PMG_DELIVER_"
condition:
  - condition: template
    value_template: "{{ trigger.event.data.action.startswith('PMG_DELIVER_') }}"
action:
  - variables:
      mail_id: "{{ trigger.event.data.action.replace('PMG_DELIVER_', '') }}"
  - service: proxmox_mail_gateway.deliver_mail
    data:
      mail_id: "{{ mail_id }}"
  - service: notify.mobile_app_sm_s921b
    data:
      message: "clear_notification"
      data:
        tag: "pmg_{{ mail_id }}"
```

---

## 7. Przykładowa Karta Lovelace na Pulpit Telefonu

Karta w Dashboardzie Lovelace (np. za pomocą karty Markdown lub `auto-entities` / `custom:button-card`), umożliwiająca zwalnianie maili jednym tapnięciem:

```yaml
type: markdown
title: "📬 Kwarantanna Poczty (PMG)"
content: >
  {% set messages = state_attr('sensor.pmg_quarantine_total_spam', 'messages') %}
  {% if not messages %}
    ✅ Brak wiadomości w kwarantannie.
  {% else %}
    | Data | Nadawca / Temat | Akcja |
    | :--- | :--- | :--- |
    {% for msg in messages %}
      | {{ msg.date_str }} | **{{ msg.sender }}**<br>{{ msg.subject }} | [Zwolnij](action:call-service/proxmox_mail_gateway.deliver_mail?mail_id={{ msg.id }}) |
    {% endfor %}
  {% endif %}
```

---

## 8. Kluczowe Wymagania Implementacyjne dla Agenta Kodującego

1. **Wymuszenie obsługi `verify_ssl: false`:**
   Domyślna instalacja PMG na porcie `8006` używa certyfikatu samopodpisanego. Klient `aiohttp` w konfiguracji musi mieć opcję `ssl=False` lub `ssl=context_with_disabled_check`, w zależności od flagi ustawionej przez użytkownika w `config_flow`.
2. **Auto-Reauthentication:**
   Bilet sesyjny wygasa po 120 minutach. Jeśli PMG zwróci `401 Unauthorized` przy zapytaniu, klient API musi ponowić logowanie (`/access/ticket`) i automatycznie powtórzyć zapytanie.
3. **Agregacja kwarantanny dla wszystkich użytkowników:**
   Endpoint `/quarantine/spam` wymaga parametru `pmail`. Aby pobrać maile z całego serwera, koordynator powinien najpierw wywołać `GET /quarantine/spamusers`, a następnie w pętli asynchronicznej (`asyncio.gather`) pobrać maile dla każdego użytkownika z listy i scalić je w jedną posortowaną listę w atrybutach sensora.
4. **Standard testowy:**
   Kod powinien być sformatowany zgodnie z `ruff` / `black` i zgodny ze standardem integracji HACS.
