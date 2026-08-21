"""AI video generation — everything in one package.

`ls app/videogen/` is the whole feature: routes, models, one table, the ComfyUI
client, the workflow graph, storage, the SpacetimeDB writes and the poll loop.
See README.md for the flow and the pieces this replaces.
"""

from videogen.routes import router

__all__ = ["router"]
