## NiFi MCP (Model Context Protocol)

This repository contains the NiFi MCP project. It provides a chat interface allowing users to interact with Apache NiFi instances using natural language queries. The system uses a Large Language Model (LLM) integrated with custom tools (MCP Tools) that can communicate with the NiFi API to retrieve information, document flows, and create and perform actions on NiFi components.

It has been tested with Nifi versions 1.23 and 1.28, but could work on other versions assuming the Nifi REST Api stays consistent.  While it is quite functional, you will find that the type of LLM model you use will have a big effect on how well it follows your instructions.  I have found o4-mini and gpt-4.1 to be good.

It's ability to read and document existing flows is very good.  It's ability to create new and modify existing flows is OK, but often takes several iterations and some user help. My future plans are to refine the tools to improve this aspect.  I may also introduce some tools to help it debug flows itself.

## Version Information

**Current Version: 2.0** - FastAPI/JavaScript Client

This repository contains two different UI implementations:

- **Version 2.0+ (Current)**: Modern FastAPI backend with JavaScript frontend
- **Version 1.4 and earlier**: Streamlit-based interface

Please follow the appropriate setup instructions below based on the version you're using.

### Example Conversations
- [Build A New Flow From A Spec](./docs/examples/ExampleConversation-Build-o4-mini.md) - See how the system creates a complete NiFi flow from requirements
- [Debug An Existing Flow](./docs/examples/ExampleConversationForDebugging.md) - Watch the system diagnose and fix issues in an existing flow

## Recent Updates

**Version 2.0 - Major UI Overhaul**
- Migrated from Streamlit to FastAPI/JavaScript client for improved performance and user experience
- Enhanced real-time WebSocket communication for better workflow status updates
- Improved message handling and duplicate message prevention
- Modern, responsive UI with better accessibility and mobile support

For the latest updates and release notes, see the [GitHub Releases page](https://github.com/ms82119/NiFiMCP/releases).

## Roadmap

My future plan is to work on improving the tool's accuracy and efficiency (reducing the number of tokens needed to acheive the goal).  To do this I will focus on introducing guided workflows which can provide more granular control over the steps and quality, which may also lead to the ability to use less powerful and cheaper LLM's while improving the results.  I feel creating new flows and documenting large flows will particularly benefit from this.

After this I will consider looking into support for more models.  I will say that the Gemini support has been harder than expected due to its protobuf usage, and has slowed my progress along the roadmap, so for this reason I am going to hold off adding other models for now.

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

5. **Update the config.yaml file with your Nifi details and LLM API keys**
   Use the config.example.yaml as your guide for format and structure

6. **Run the MCP Server:**
   Start the MCP server with:
   ```bash
   uvicorn nifi_mcp_server.server:app --reload --port 8000
   ```

### Version 2.0+ (Current) - FastAPI/JavaScript Client

7. **Run the FastAPI Client:**
   Start the FastAPI client with:
   ```bash
   uvicorn api.main:app --reload --port 3000
   ```

8. **Access the Application:**
   Open your browser and navigate to: `http://localhost:3000`

### Version 1.4 and Earlier - Streamlit Client

7. **Run the Streamlit Client:**
   Start the Streamlit client with:
   ```bash
   python -m streamlit run nifi_chat_ui/app.py
   ```

8. **Access the Application:**
   Open your browser and navigate to the URL shown in the terminal (typically `http://localhost:8501`)

## Usage Tips

For detailed usage information, tips, and UI features, see the [Usage Guide](./docs/UsageGuide.md).

## Running Automated Tests

For comprehensive testing information and examples, see the [Testing Guide](./tests/README.md).
