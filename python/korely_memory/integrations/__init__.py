"""Optional framework integrations.

Each module needs its own extra, and ``import korely_memory`` imports none of
them, so the core package keeps zero runtime dependencies:

- ``korely_memory.integrations.langgraph``: a LangGraph store, two tools and a
  context helper. ``pip install 'korely-memory[langgraph]'``
"""
