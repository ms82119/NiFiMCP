## NiFi MCP (Model Context Protocol)

This repository contains the NiFi MCP project. It can be used in two ways, either:

- As a plain MCP Server that tools like Cursor AI, Claude Code, Antigravity etc. can connect to via stdio
- Or, with a built-in (contained in this repo) chat bot that you can connect to from your browser.

Either approach allows you to interact with Apache NiFi instances using natural language. There are currently around 30 tools available, covering building, updating, running, debugging, and documenting nifi flows.  This has been tested with Nifi versions 1.23 and 1.28, but should work on other versions like 2.x assuming the Nifi REST Api stays consistent. 

The built-in chat bot requires you to supply your own LLM API keys, better models give better results and selecting higher token context can help too.  I have however found that using Cursor AI in Auto mode gives the best performance and results.  It turned out to be a much better interface than I could build with my own chat bot.  So I only recommend the built-in chat bot if you have no other options.

 
### Example Conversations (Using built-in chat bot)
- [Build A New Flow From A Spec](./docs/examples/ExampleConversation-Build-o4-mini.md) - See how the system creates a complete NiFi flow from requirements
- [Debug An Existing Flow](./docs/examples/ExampleConversationForDebugging.md) - Watch the system diagnose and fix issues in an existing flow

## Recent Updates

**Version 2.0 - Major UI Overhaul**
- Added direct MCP server support for IDE's like Cursor AI (best option)
- Enhanced the built in chatbot
   - Migrated from Streamlit to FastAPI/JavaScript client for improved performance and user experience
   - Enhanced real-time WebSocket communication for better workflow status updates
   - Improved message handling and duplicate message prevention
   - Modern, responsive UI with better accessibility and mobile support
   - Added a new workflow "Flow Documentation" to help generate better documentation about a flow

For the latest updates and release notes, see the [GitHub Releases page](https://github.com/ms82119/NiFiMCP/releases).



## Setup Instructions

**Note:** These instructions should also be followed after pulling a new version as there may be new package requirements.

### Common Setup Steps (All Versions)

1. **Clone the Repository:**
   ```bash
   git clone git@github.com:ms82119/NiFiMCP.git
   cd NiFiMCP
   ```

2. **Set Up a Virtual Environment:**
   It's recommended to use a virtual environment to manage dependencies. You can create one using `venv`:
   ```bash
   python3 -m venv .venv
   ```

3. **Activate the Virtual Environment:**
   - On macOS and Linux:
     ```bash
     source .venv/bin/activate
     ```
   - On Windows:
     ```bash
     .venv\Scripts\activate
     ```

4. **Install Dependencies:**
   Use `uv` to install dependencies based on `pyproject.toml` and `uv.lock`:
   ```bash
   uv sync
   ```

5. **Update the config.yaml file with your Nifi server details and LLM API keys**
   Use the config.example.yaml as your guide for format and structure
   Note that the LLM API keys are only needed if you are using the built-in client

## Using the NiFi MCP Server in Cursor IDE or other MCP clients

You can use the same NiFi MCP tools directly in Cursor (or other MCP clients) over **stdio**, without running the REST server or chat UI. The project includes a stdio entrypoint and a Cursor MCP config.

- **Setup**: Open this project in Cursor; the `.cursor/mcp.json` config will start the NiFi MCP server when Cursor connects. Ensure at least one NiFi server is configured in `config.yaml`.
- **Server selection**: Set the `NIFI_SERVER_ID` environment variable in the MCP config to a server id from `config.yaml`, or leave it unset to use the first configured server.
- **Details**: See [Cursor MCP Setup](./docs/Cursor-MCP-Setup.md).  


## Using the Built-In Chat Bot

**Run the MCP Server:**
   Start the MCP server with:
   ```bash
   uvicorn nifi_mcp_server.server:app --reload --port 8000
   ```
   If port 8000 is in use, set `MCP_SERVER_PORT` and use the same value for `--port` (and for the chat UI, see step 7):
   ```bash
   MCP_SERVER_PORT=8001 uvicorn nifi_mcp_server.server:app --reload --port 8001
   ```


**Run the FastAPI Client:**
   Start the FastAPI client with:
   ```bash
   uvicorn api.main:app --reload --port 3000
   ```
   If you ran the MCP server on a different port (e.g. 8001), set the same port for the chat UI so it can reach the API:
   ```bash
   MCP_SERVER_PORT=8001 uvicorn api.main:app --reload --port 3000
   ```

**Access the Application:**
   Open your browser and navigate to: `http://localhost:3000`


## Usage Tips

For detailed usage information, tips, and UI features, see the [Usage Guide](./docs/UsageGuide.md).



## Running Automated Tests

For comprehensive testing information and examples, see the [Testing Guide](./tests/README.md).
