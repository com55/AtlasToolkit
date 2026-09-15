"""Stateful orchestration layer (architecture spec §1): holds runtime
state, calls RasterBackend, may import domain/ freely. Nothing under
domain/ may import from this package."""
