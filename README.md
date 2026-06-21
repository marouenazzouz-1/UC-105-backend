# UC-105 AI-Recommendations-Church-Commissioners Strategy - Backend
 
An **Azure Function App** that powers a domain-scoped chatbot. It answers user questions by deciding in real time whether a web search is needed or whether the LLM can answer from its own knowledge — a two-node agentic RAG pattern built with LangGraph.
 
---
 
## What it does
 
Each call to `POST /api/chat` runs through a small LangGraph workflow:
 
1. **Search-or-answer node** — the LLM receives the conversation window and decides whether to invoke the `get_search_results` tool (backed by Tavily). If no tool call is made, the direct answer is stored in state and the next node is skipped cheaply.
2. **Synthesize node** — if a search was performed, the raw results are injected back into the prompt as assistant context and the LLM produces a final, grounded answer. If no search was needed, the node is a no-op.
The graph is domain-aware: Tavily is restricted to a whitelist of `authorized-websites` defined in `config/config.yaml`, and only results from those domains are forwarded to the LLM.
 
Conversation history is persisted to a local SQLite database via `DBHandler`. A **sliding window of the last K turns** is sent to the LLM on every request; older turns are stored in the DB but not included in the prompt, capping token usage automatically.
 
---
 
## Folder structure
 
```
.
├── config/
│   └── config.yaml          # authorized-websites whitelist, other static config
├── src/
│   ├── db.py                # DBHandler — SQLite persistence, session stats
│   ├── graph.py             # LangGraph workflow (search_or_answer → synthesize)
│   └── utils.py             # _error() helper and shared utilities
├── function_app.py          # Azure Function entry-point, HTTP routes
├── host.json                # Functions runtime config (route prefix, extension bundle)
├── local.settings.json      # Local-dev env vars — NOT committed to git
└── requirements.txt
```
 
---
 
## Running locally
 
### Prerequisites
 
- Python 3.11+
- [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local)
- API keys for NVIDIA NIM (LLM) and Tavily (search)
### Setup
 
```bash
pip install -r requirements.txt
```
 
### Start the Function App
 
```bash
func start
```
 
The three endpoints will be available at `http://localhost:7071/api/`:
 
| Method | Route | Description |
|--------|-------|-------------|
| `POST` | `/api/chat` | Send a message; receive a reply |
| `GET` | `/api/session/{session_id}/history` | Inspect stored messages + stats |
| `DELETE` | `/api/session/{session_id}` | Erase all history for a session |
 
#### Example request
 
```bash
curl -X POST http://localhost:7071/api/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-123", "message": "What is the latest guidance on X?"}'
```
 
```json
{
  "reply": "...",
  "session_id": "test-123",
  "history_length": 2,
  "window_k": 10,
  "max_memory_turns": 100
}
```
 
---
 
## Environment variables
 
| Variable | Default | Description |
|----------|---------|-------------|
| `NVIDIA_API_KEY` | — | Required. NVIDIA NIM auth key |
| `TAVILY_API_KEY` | — | Required. Tavily search auth key |
| `CONVERSATION_WINDOW_K` | `10` | Number of recent messages sent to the LLM |
| `MAX_MEMORY_TURNS` | `100` | Maximum messages retained in the DB per session |
| `DB_DIR` | `.` | Directory where the SQLite file is written |
 
---
 
## TODO — Azure deployment
 
The steps below are not yet scripted. Work through them in order before the first deployment.
 
### 1. Azure account & subscription
 
- [ ] Confirm which Azure subscription will host the app
- [ ] Verify you have at least **Contributor** access on the subscription
- [ ] Enable any required resource providers (`Microsoft.Web`, `Microsoft.Storage`)
### 2. Resource group
 
```bash
# TODO: replace placeholders
az group create \
  --name rg-uc105-chatbot \
  --location <azure-region>   # e.g. westeurope
```
 
### 3. Storage account (required by Functions runtime)
 
```bash
az storage account create \
  --name stuc105chatbot \
  --resource-group rg-uc105-chatbot \
  --sku Standard_LRS \
  --location <azure-region>
```
 
### 4. Function App
 
```bash
az functionapp create \
  --name fn-uc105-chatbot \
  --resource-group rg-uc105-chatbot \
  --consumption-plan-location <azure-region> \
  --runtime python \
  --runtime-version 3.11 \
  --storage-account stuc105chatbot \
  --functions-version 4
```
 
### 5. Application settings (secrets)
 
```bash
az functionapp config appsettings set \
  --name fn-uc105-chatbot \
  --resource-group rg-uc105-chatbot \
  --settings \
    NVIDIA_API_KEY=<key> \
    TAVILY_API_KEY=<key> \
    CONVERSATION_WINDOW_K=10 \
    MAX_MEMORY_TURNS=100
```
 
- [ ] Decide whether to store secrets in **Azure Key Vault** and reference them via Key Vault references instead of plain app settings
- [ ] Set `DB_DIR` to a writable path, or replace SQLite with **Azure Table Storage** / **Azure Cosmos DB** for durability across cold starts and scale-out
### 6. CORS
 
```bash
az functionapp cors add \
  --name fn-uc105-chatbot \
  --resource-group rg-uc105-chatbot \
  --allowed-origins <frontend-url>
```
 
- [ ] Replace `<frontend-url>` with the actual Streamlit / Static Web App origin; avoid `*` in production
### 7. Deploy
 
```bash
func azure functionapp publish fn-uc105-chatbot
```
 
### 8. Post-deploy checks
 
- [ ] Hit `GET /api/session/test/history` and confirm a 200 response
- [ ] Send a test chat message and verify the graph executes end-to-end
- [ ] Review Application Insights logs for cold-start latency and any errors
- [ ] Rotate the Function App host key and share it securely with the frontend team
