# examples/03_mcp_integration.py
"""
MCP Integration: Use fitz-forge as a tool server for Claude Code or Claude Desktop.

The MCP server exposes the same tools as the CLI but over the Model Context Protocol,
letting Claude Code create, monitor, and retrieve plans directly.
"""

# ============================================================
# Claude Code setup
# ============================================================
#
# Add to your project's .mcp.json or global MCP config:
#
#   {
#     "mcpServers": {
#       "fitz-forge": {
#         "command": "fitz",
#         "args": ["serve"]
#       }
#     }
#   }
#
# Then in Claude Code, you can say:
#   "Create a plan for adding OAuth2 authentication"
#   "Check the status of plan 1"
#   "Show me the completed plan"

# ============================================================
# Available MCP tools
# ============================================================
#
# | Tool            | Description                                    |
# |-----------------|------------------------------------------------|
# | create_plan     | Queue a new planning job                       |
# | check_status    | Check job progress (state, %, current phase)   |
# | get_plan        | Retrieve completed plan as markdown             |
# | list_plans      | List all planning jobs                         |
# | retry_job       | Retry a failed or interrupted job              |
# | confirm_review  | Approve optional API review (shows cost first) |
# | cancel_review   | Skip API review, finalize plan as-is           |

# ============================================================
# Testing the MCP server manually
# ============================================================

if __name__ == "__main__":
    print("Start the MCP server:")
    print("  fitz serve")
    print()
    print("Or run in stdio mode (for MCP clients):")
    print("  python -m fitz_forge")
    print()
    print("The server exposes 7 tools over the Model Context Protocol.")
    print("See README.md for the full MCP tool reference.")
