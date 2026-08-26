import json, sys
sys.path.insert(0, "tests")
from test_cli_mcp import make_client
from kvmctl.mcp_surface import TOOL_SPEC, dispatch_tool

c, _ = make_client()
ctx = {"client": c}
print(dispatch_tool("capabilities", {}, context=ctx))
print(json.dumps([t["name"] for t in TOOL_SPEC]))
