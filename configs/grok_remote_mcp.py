"""Grok (xAI API) using this server as a *remote MCP tool*.

NOT TESTED end-to-end (no xAI key in the build environment). It follows xAI's
documented "Remote MCP Tools" feature; check the current xAI docs for the exact
parameter names before relying on it.

Requirements:
  * the server reachable from the internet over HTTPS (xAI's servers call it),
  * a dedicated token for Grok (rotate it independently of your other agents),
  * pip install xai-sdk

What leaves your server: tool results (asset names, metadata, sizes, file
paths/URLs). Meshes, textures and previews are NOT sent unless a tool returns
them; with allow_preview_images_to_agent=false no image bytes are returned.
"""

import os

from xai_sdk import Client
from xai_sdk.chat import user
from xai_sdk.tools import mcp

client = Client(api_key=os.environ["XAI_API_KEY"])
chat = client.chat.create(
    model="grok-4",
    tools=[
        mcp(
            server_url="https://synty.example.com/mcp",
            server_label="synty",
            authorization=os.environ["SYNTY_TOKEN"],  # sent as "Authorization: Bearer <token>"
            allowed_tool_names=[
                "search_assets", "suggest_assets_for_scene", "get_asset_details",
                "find_similar_assets", "list_pack_assets", "get_asset_dependencies",
                "prepare_asset_collection", "get_job",
            ],
        )
    ],
)
chat.append(user("I need three medieval houses, a well and a goblin character from my Synty assets. "
                 "Prepare a threejs_web collection and give me the manifest."))
response = chat.sample()
print(response.content)
