"""On-device document intake for Tijori.

Nothing in this package talks to the network except the one-time Hugging Face
download of the translation weights. Extraction and translation are local.

Import from the submodules directly (``ingest.extract``, ``ingest.language``,
``ingest.translate``); the package deliberately re-exports nothing so the
submodule names stay unambiguous.
"""
