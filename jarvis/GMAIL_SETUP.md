# Gmail Setup for Jarvis

## Step-by-Step Setup

1. **Go to Google Cloud Console**
   - Visit https://console.cloud.google.com
   - Sign in with the Google account you want to use

2. **Create a new project**
   - Click "New Project" → name it "Jarvis"
   - Wait for project creation

3. **Enable APIs**
   - Go to **APIs & Services → Library**
   - Search and enable: **Gmail API**
   - Search and enable: **Google Calendar API**

4. **Create OAuth 2.0 Credentials**
   - Go to **APIs & Services → Credentials**
   - Click **Create Credentials → OAuth client ID**
   - Application type: **Desktop App**
   - Name: "Jarvis Desktop"
   - Click **Create**

5. **Download credentials**
   - Click the download button (⬇) next to your new credential
   - Rename the file to `gmail_credentials.json`
   - Place it in the `jarvis/` folder (same directory as `main.py`)

6. **Configure Jarvis**
   - In `config.yaml`, set:
     ```yaml
     gmail:
       enabled: true
     ```

7. **First-time authentication**
   - Run `python main.py`
   - A browser window opens → sign in to Google → grant permissions
   - After approval, `gmail_token.json` is automatically created
   - Jarvis can now read and send your Gmail!

## What Jarvis Can Do

| Command | Description |
|---------|-------------|
| `/inbox` | Show unread emails |
| `/calendar` | Upcoming calendar events |
| `check my email` | Natural language inbox check |
| `send email to john@...` | Draft + confirm + send |
| `what's on my schedule today` | Today's events |

## Safety Features

- ✅ **Email sending always requires confirmation** — Jarvis will NEVER auto-send
- ✅ Token stored locally only — never uploaded anywhere
- ✅ Credentials are never logged
- ✅ Read-only by default until you confirm a send action

## Security

> [!CAUTION]
> **NEVER share `gmail_credentials.json` or `gmail_token.json`.**
> Add both to `.gitignore` immediately (already done if you cloned this repo).

```gitignore
gmail_credentials.json
gmail_token.json
```

## Troubleshooting

- **"Access blocked"**: Add your email as a test user in Google Cloud Console → APIs & Services → OAuth consent screen → Test users
- **Token expired**: Delete `gmail_token.json` and re-run `python main.py`
- **Missing scopes**: Delete `gmail_token.json` to force re-authentication with all required scopes
