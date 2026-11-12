# Running millwork-mcp in Azure with a Microsoft Foundry agent

Copyright (c) 2026 Prince Boafo. PolyForm Noncommercial 1.0.0. Hosting this for a business requires a commercial license (see COMMERCIAL.md).

## Architecture

```
Foundry agent (Claude or GPT model)
   │  MCP tool call over HTTPS, key in a Foundry project connection
   ▼
Azure Container Apps: millwork-mcp (streamable HTTP, /mcp)
   │  key check → stateless MCP server → deterministic engine
   ▼
estimate, cut list, nest summary returned to the agent
```

- **Auth:** one shared key, sent as `Authorization: Bearer <key>` (or `x-api-key`). It's stored as a Container Apps secret and a Foundry project connection, never in code. Keys are compared in constant time. The server refuses to start without a key of 32+ characters. Two comma-separated keys are accepted at once, so the key can be rotated with no downtime.
- **Network:** HTTPS only, host header pinned to the app's own hostname, 1 MB request cap, and `/healthz` as the only open route. The container runs as an unprivileged user.
- **Cost:** scales to zero when idle. On Container Apps the image is built in Azure, and Container Registry Basic is the only standing cost at a few dollars a month. On App Service F1 it is free, with a daily CPU quota. Delete everything with `az group delete -n millwork-rg`.

## 1. Deploy (Azure Cloud Shell, about 10 minutes)

1. Open https://portal.azure.com, click the Cloud Shell icon (`>_`) in the top bar, and choose **Bash**.
2. Get the code into Cloud Shell, either way:
   - **Upload:** Cloud Shell → *Manage files* → *Upload* → `millwork-mcp.zip`, then `unzip millwork-mcp.zip && cd millwork-mcp`
   - **Git:** `git clone https://github.com/Toolibi/millwork-mcp && cd millwork-mcp`
3. Run one of the deploy scripts:
   - **Free trial or free tier:** `bash deploy/azure_appservice.sh`. This deploys to App Service. Free-trial subscriptions block ACR Tasks (`TasksOperationsNotAllowed`), so the container build fails there. App Service builds the Python code itself, and the script picks a region that has free F1 quota.
   - **Pay-as-you-go:** `bash deploy/azure_deploy.sh`. This deploys to Container Apps, which builds the image in ACR and scales to zero.

Either script prints the MCP server URL and saves the API key to `~/millwork-mcp-api-key.txt`. It never prints the key.

## 2. Connect a Foundry agent

1. In the Foundry portal (https://ai.azure.com), create or open a project and deploy a model.
   - Claude models in Foundry are Azure Marketplace offerings billed separately. They may not be available on a free-trial or credit-only subscription.
   - If Claude can't be deployed on your subscription, use a GPT model for the demo. The MCP server works the same with either.
2. Create a **project connection** for the key. Use the auth type for custom keys, header `Authorization`, value `Bearer <your key>`. Paste the key yourself from `~/millwork-mcp-api-key.txt`.
3. Create an agent and add an **MCP** tool:
   - Server label: `millwork`
   - Server URL: `https://<your-app>.<region>.azurecontainerapps.io/mcp`
   - Connection: the one from step 2
   - Allowed tools: `review_cabinet_schedule`, `build_cutlist`, `nest_sheets`, `get_machine_profile`, `list_joinery_methods`, `get_pricing`, `estimate_and_export`
   - Approval: require approval for `estimate_and_export` (it produces shop files) and for every inventory tool that changes stock (`reserve_job_materials`, `consume_job_materials`, `release_job_materials`, `receive_stock`, `count_stock`, `log_remnant`). The read-only tools (`inventory_status`, `check_job_materials`, `purchase_list` and the review/nest/pricing tools) can run without approval.
4. Agent instructions (suggested):

   > You are a millwork estimator for a frameless cabinet shop. Turn the user's drawings or cabinet list into a millwork-mcp job (inches). Always run review_cabinet_schedule first and show its flags before estimating. Never invent prices or machine settings: use get_pricing and get_machine_profile. Machine programs are UNVERIFIED until simulated in woodWOP and run by a person.

5. Test it: *"Estimate this kitchen: B1 base 18x34.5x24 one door one drawer, DB1 drawer base 30 wide four drawers, W1 wall 30x30x12 two doors, rift white oak fronts, grain matched."*

## Limits to know

- **Timeout:** Foundry tool calls time out after 100 seconds. A full kitchen runs in about 1–2 seconds.
- **Files:** in hosted mode, shop files (DXFs, CSVs) are written inside the container and are temporary. The agent gets the estimate, flags and nest summary back. Saving files to Azure Blob Storage is the next step.
- **Inventory:** stock lives in one JSON file at `MILLWORK_INVENTORY`. On App Service the deploy script puts it in `/home/data`, which survives restarts and redeploys. It starts from the sample stock in `examples/inventory.json`, so count your real stock in (`count_stock`) before relying on it. One instance only: the file lock doesn't span scaled-out instances (use a database for that).
- **Pricing:** to use your real prices, mount them with `MILLWORK_PRICING` (an Azure Files mount or a secret volume). Don't put them in the image.
