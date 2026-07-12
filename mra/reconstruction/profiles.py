"""Compatibility shim: profile utilities now live in ``mra.core.loops``."""

from mra.core.loops import boundary_loops_3d, loop_is_circle, simplify_loop

__all__ = ["boundary_loops_3d", "loop_is_circle", "simplify_loop"]
