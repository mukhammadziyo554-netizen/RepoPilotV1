# RepoPilot V1 — Vercel Deployment Guide

This guide explains how to deploy RepoPilot to Vercel with either Anthropic or IBM Bob as the AI engine.

## Quick Start — Anthropic (Recommended)

Anthropic works out-of-the-box on Vercel and is the easiest deployment path.

### 1. Prepare your GitHub repository

Ensure your changes are committed and pushed:

```bash
git push origin main
```

### 2. Connect to Vercel

1. Go to [vercel.com](https://vercel.com)
2. Sign in with GitHub
3. Click "Add New" → "Project"
4. Select your `RepoPilotV1` repository
5. Click "Import"

### 3. Configure Environment Variables

On the "Configure" step, add:

- `ANALYSIS_PROVIDER` = `anthropic`
- `ANTHROPIC_API_KEY` = `sk-ant-...` (from [console.anthropic.com](https://console.anthropic.com/))
- `ANTHROPIC_MODEL` = `claude-haiku-4-5-20251001`
- `GITHUB_TOKEN` = `github_pat_...` (optional, but recommended for higher API limits)

### 4. Deploy

Click "Deploy" and wait for the build to complete.

### 5. Test

Visit your Vercel URL and submit a public GitHub repository like:
```
https://github.com/pallets/flask
```

---

## Advanced — IBM Bob on Vercel

IBM Bob works on Vercel via its HTTP inference API. This requires an active IBM Bob subscription and instance.

### Prerequisites

- Active IBM Bob subscription at [bob.ibm.com](https://bob.ibm.com)
- Access to your subscription's API key management dashboard

### 1. Get Your Bob Inference Endpoint

1. Log in to [bob.ibm.com](https://bob.ibm.com)
2. Open your subscription instance
3. Navigate to **API Key Management** or **Settings**
4. Look for "Inference Endpoint" or "API Endpoint" — it should look like:
   ```
   https://bob-prod.your-account.ibm.com/api/v1/inference
   ```
5. Create an **"Inference"** type API key (not "General" — Inference keys are simpler)
6. Copy both the endpoint URL and the API key immediately

### 2. Prepare your GitHub repository

Ensure changes are committed and pushed:

```bash
git push origin main
```

### 3. Connect to Vercel

1. Go to [vercel.com](https://vercel.com)
2. Sign in with GitHub
3. Click "Add New" → "Project"
4. Select `RepoPilotV1`
5. Click "Import"

### 4. Configure Environment Variables

On the "Configure" step, add:

- `ANALYSIS_PROVIDER` = `bob`
- `BOB_API_KEY` = `bob_prod_...` (your Inference API key)
- `BOB_INFERENCE_ENDPOINT` = `https://bob-prod.your-account.ibm.com/api/v1/inference`
- `GITHUB_TOKEN` = `github_pat_...` (optional)

**Do NOT include:**
- `BOB_TEAM_ID` (only needed for "General" type keys; Inference keys don't need it)

### 5. Deploy

Click "Deploy" and wait for the build to complete.

### 6. Test

Visit your Vercel URL and submit a public GitHub repository:
```
https://github.com/pallets/flask
```

If you get an error like "IBM Bob is not configured", the endpoint URL or API key is likely incorrect. Double-check in your Bob subscription dashboard.

---

## Troubleshooting

### "The configured analysis provider is not available"

**Cause:** Environment variable `ANALYSIS_PROVIDER` is not set.  
**Solution:** Set `ANALYSIS_PROVIDER=anthropic` or `ANALYSIS_PROVIDER=bob` in Vercel environment variables.

### "Anthropic is not configured. Add ANTHROPIC_API_KEY"

**Cause:** Anthropic provider is selected but no API key is set.  
**Solution:** 
1. Go to [console.anthropic.com](https://console.anthropic.com/)
2. Create an API key
3. Add `ANTHROPIC_API_KEY=sk-ant-...` to Vercel environment variables

### "IBM Bob is not configured"

**Cause:** Bob provider is selected but credentials/endpoint are missing.  
**Solution:**
1. Ensure `BOB_API_KEY` is set in Vercel environment variables
2. Ensure `BOB_INFERENCE_ENDPOINT` is set with your instance-specific URL from bob.ibm.com
3. Log into bob.ibm.com and verify your Inference API key is valid (not expired)

### "Bob inference failed: HTTP 401" or "HTTP 403"

**Cause:** API key is invalid or endpoint URL is wrong.  
**Solution:**
1. Log into bob.ibm.com
2. Verify your API key in API Key Management
3. Verify the endpoint URL matches your subscription instance
4. Try creating a new Inference API key if the old one is suspected to be compromised

### "Bob analysis timed out"

**Cause:** Bob inference took too long (>120 seconds).  
**Solution:** Try again. This can happen if Bob's service is under heavy load.

---

## Local Development vs. Production

| Setting | Local Dev | Vercel |
|---------|-----------|--------|
| `ANALYSIS_PROVIDER=bob` (CLI) | ✅ Works — uses Bob Shell 2.0.5 | ❌ No Node.js runtime |
| `ANALYSIS_PROVIDER=bob` (HTTP) | Works if `BOB_INFERENCE_ENDPOINT` set | ✅ Works — uses HTTP API |
| `ANALYSIS_PROVIDER=anthropic` | ✅ Works | ✅ Works |

**Summary:** For local development with Bob CLI, don't set `BOB_INFERENCE_ENDPOINT`. For Vercel, always set it.

---

## Performance Notes

- First request: ~2-5 seconds (GitHub discovery + retrieval)
- AI analysis: ~5-15 seconds (depends on repository size and provider)
- Streaming: incremental tokens arrive as they're generated
- Serverless timeout: Vercel allows up to 60 seconds for Pro plans, 10 seconds for free

If your analysis timeouts on Vercel, consider:
- Using a smaller repository for testing
- Upgrading to a Vercel Pro plan for longer timeouts
- Running a self-hosted Flask server instead

---

## Example Vercel Configuration File (Optional)

If you want to version-control your deployment settings, create `vercel.json`:

```json
{
  "buildCommand": "pip install -r requirements.txt",
  "outputDirectory": ".",
  "env": {
    "FLASK_APP": "app.py"
  }
}
```

However, this is optional — Vercel will auto-detect Flask apps.

---

## Post-Deployment

1. Test the health endpoint:
   ```bash
   curl https://your-vercel-url.vercel.app/api/health
   ```

2. Test repository discovery:
   ```bash
   curl -X POST https://your-vercel-url.vercel.app/api/discover \
     -H "Content-Type: application/json" \
     -d '{"url":"https://github.com/pallets/flask"}'
   ```

3. Test streaming analysis:
   ```bash
   curl -X POST https://your-vercel-url.vercel.app/api/analyze/stream \
     -H "Content-Type: application/json" \
     -d '{"url":"https://github.com/pallets/flask"}' \
     -N
   ```

---

## Rolling Back

If deployment fails:

1. Go to your Vercel project
2. Click "Deployments"
3. Find the last successful deployment
4. Click "Promote to Production"

Or via CLI:
```bash
vercel promote <deployment-url>
```

---

## Support

- **Anthropic issues:** Check [Anthropic documentation](https://docs.anthropic.com/)
- **Bob issues:** Log into [bob.ibm.com](https://bob.ibm.com) → Help/Support
- **Vercel issues:** See [Vercel documentation](https://vercel.com/docs)
- **RepoPilot issues:** Check [GitHub repository](https://github.com/mukhammadziyo554-netizen/RepoPilotV1)
