# OIDC Token Management Guide

## Overview

OIDC tokens expire after ~5 minutes. This guide explains how to manage tokens for testing and development.

## Token Storage

Tokens are stored in `nifi_tokens.json` in the project root. This file is shared between:
- The API server
- The MCP server
- Test scripts

**Location**: `nifi_tokens.json` (project root)

**Format**:
```json
{
  "proserve-f": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "other-server": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

## Quick Start

### 1. Check if you have a token

```bash
python test_doc_workflow_auto.py --check-token-only proserve-f
```

This will tell you:
- ✅ If a token exists
- ✅ If the token is valid
- ⚠️ If the token has expired

### 2. Submit a new token

When your token expires, submit a new one:

```bash
python test_doc_workflow_auto.py --submit-token YOUR_TOKEN_HERE proserve-f
```

This will:
- Store the token in `nifi_tokens.json`
- Submit it to the API server
- Make it available for all future requests

### 3. Run tests

Once a token is stored, you can run tests normally:

```bash
python test_doc_workflow_auto.py PROCESS_GROUP_ID proserve-f
```

The script will automatically:
- Read the token from `nifi_tokens.json`
- Submit it to the API if needed
- Use it for authentication

## Methods to Provide Tokens

### Method 1: Command Line (One-Time Use)

```bash
python test_doc_workflow_auto.py PG_ID proserve-f --token YOUR_TOKEN
```

This uses the token for this run only (doesn't save it).

### Method 2: Submit and Store (Recommended)

```bash
python test_doc_workflow_auto.py --submit-token YOUR_TOKEN proserve-f
```

This stores the token permanently in `nifi_tokens.json` and submits it to the API.

### Method 3: Manual Edit

Edit `nifi_tokens.json` directly:

```json
{
  "proserve-f": "your-token-here"
}
```

Then restart the API server if it's running.

## Token Expiration

### How to Know When Token Expires

1. **Check token validity**:
   ```bash
   python test_doc_workflow_auto.py --check-token-only proserve-f
   ```

2. **Look for errors**:
   - `401 Unauthorized` errors
   - `Token expired` messages in logs
   - Health check failures

### When Token Expires

1. Get a new token from your OIDC provider (browser, CLI, etc.)
2. Submit it using Method 2 above
3. Continue testing

## Workflow Example

```bash
# Step 1: Check current token status
python test_doc_workflow_auto.py --check-token-only proserve-f

# Step 2: If expired, submit new token
python test_doc_workflow_auto.py --submit-token NEW_TOKEN proserve-f

# Step 3: Verify token is valid
python test_doc_workflow_auto.py --check-token-only proserve-f

# Step 4: Run your test
python test_doc_workflow_auto.py de659067-d65e-3e66-836b-f73e841862af proserve-f
```

## API Endpoint

You can also submit tokens directly via the API:

```bash
curl -X POST http://localhost:3000/api/nifi-servers/proserve-f/credentials \
  -H "Content-Type: application/json" \
  -d '{"token": "YOUR_TOKEN_HERE"}'
```

## Troubleshooting

### "No token found"

**Solution**: Submit a token using Method 2 above.

### "Token expired" or "401 Unauthorized"

**Solution**: Get a new token and submit it using Method 2.

### "Token store file corrupted"

**Solution**: Delete `nifi_tokens.json` and create a new one:
```bash
rm nifi_tokens.json
python test_doc_workflow_auto.py --submit-token NEW_TOKEN proserve-f
```

### Token works in browser but not in script

**Solution**: Make sure you're using the **access token**, not the ID token or refresh token. The access token is what NiFi needs.

## Security Notes

⚠️ **Important**: 
- `nifi_tokens.json` contains sensitive authentication tokens
- Add it to `.gitignore` (should already be there)
- Don't commit tokens to version control
- Tokens expire after ~5 minutes, so even if leaked, they have limited lifetime

## Future Improvements

Potential enhancements (not yet implemented):
- Automatic token refresh using refresh tokens
- Token expiration detection and warnings
- Integration with OIDC provider CLI tools

