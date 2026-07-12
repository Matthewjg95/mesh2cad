"""3D viewport for meshes, detected surfaces and CAD previews.

Built on pyqtgraph's ``GLViewWidget`` (OpenGL). The viewport owns three
display layers matching the pipeline:

  * ``original`` — the imported/repaired STL (grey)
  * ``patches``  — Stage-2 surface patches, one colour per patch
  * ``preview``  — tessellated preview of the reconstructed CAD (blue)

Layers can be shown or hidden independently so the user can compare the
evidence mesh against the reconstruction.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph.opengl as gl
import trimesh
from pyqtgraph.opengl.shaders import (
    FragmentShader,
    ShaderProgram,
    VertexShader,
)
from PySide6.QtWidgets import QWidget, QVBoxLayout

# Two-light shading ported from the user's Tab5 renderer
# (AI Camera Project/Tab5 3D Render/src/renderer.h, _shade_color):
# a warm key light from upper-front-right, a cool fill from the opposite
# side, and a dark blue-grey ambient floor. Light directions live in eye
# space so the lighting follows the camera, like the firmware where the
# model rotates under fixed lights. Registered once at import.
ShaderProgram("tab5TwoLight", [
    VertexShader("""
        uniform mat4 u_mvp;
        uniform mat3 u_normal;
        attribute vec4 a_position;
        attribute vec3 a_normal;
        attribute vec4 a_color;
        varying vec4 v_color;
        varying vec3 v_normal;
        void main() {
            v_normal = normalize(u_normal * a_normal);
            v_color = a_color;
            gl_Position = u_mvp * a_position;
        }
    """),
    FragmentShader("""
        #ifdef GL_ES
        precision mediump float;
        #endif
        varying vec4 v_color;
        varying vec3 v_normal;
        void main() {
            vec3 n = normalize(v_normal);
            if (!gl_FrontFacing) { n = -n; }
            vec3 key  = normalize(vec3( 0.577,  0.577,  0.577));
            vec3 fill = normalize(vec3(-0.577, -0.300, -0.577));
            float kd = max(dot(n, key),  0.0);
            float fd = max(dot(n, fill), 0.0);
            // Ambient + warm key + cool fill (renderer.h constants /255).
            vec3 shade = vec3(0.118, 0.118, 0.157)
                       + kd * vec3(0.706, 0.686, 0.608)
                       + fd * vec3(0.118, 0.110, 0.235);
            vec3 rgb = v_color.rgb * shade * 1.45;
            gl_FragColor = vec4(clamp(rgb, 0.0, 1.0), v_color.a);
        }
    """),
])

# Distinct, colour-blind-friendly palette for surface patches (RGBA 0-1).
_PATCH_PALETTE = np.array([
    (0.90, 0.60, 0.00, 1.0),  # orange
    (0.35, 0.70, 0.90, 1.0),  # sky blue
    (0.00, 0.60, 0.50, 1.0),  # teal
    (0.95, 0.90, 0.25, 1.0),  # yellow
    (0.00, 0.45, 0.70, 1.0),  # blue
    (0.80, 0.40, 0.00, 1.0),  # vermillion
    (0.80, 0.60, 0.70, 1.0),  # pink
    (0.60, 0.60, 0.60, 1.0),  # grey
])


class Viewport(QWidget):
    """The central 3D view.

    Public methods take plain trimesh / numpy data so the widget stays
    decoupled from pipeline internals and is testable with synthetic input.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = gl.GLViewWidget()
        self._view.setBackgroundColor((28, 30, 34))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        self._grid = gl.GLGridItem()
        self._view.addItem(self._grid)

        self._original_item: gl.GLMeshItem | None = None
        self._patch_item: gl.GLMeshItem | None = None
        self._preview_item: gl.GLMeshItem | None = None

    # ------------------------------------------------------------ layers

    def show_mesh(self, mesh: trimesh.Trimesh) -> None:
        """Display ``mesh`` on the original layer and frame the camera."""
        # Near-white base so the two-light warm/cool character carries
        # (the firmware's shade IS the colour; a grey base mutes it).
        self._original_item = self._replace_item(
            self._original_item,
            mesh,
            face_colors=np.tile((0.92, 0.92, 0.94, 1.0), (len(mesh.faces), 1)),
        )
        self._frame(mesh)

    def show_patches(
        self, mesh: trimesh.Trimesh, patch_face_ids: np.ndarray
    ) -> None:
        """Colour mesh triangles by patch id (-1 = unassigned, dark grey).

        Args:
            mesh: The mesh whose triangles are being classified.
            patch_face_ids: Per-triangle patch id array, len == len(faces).
        """
        colors = np.tile((0.25, 0.25, 0.28, 1.0), (len(mesh.faces), 1))
        assigned = patch_face_ids >= 0
        colors[assigned] = _PATCH_PALETTE[
            patch_face_ids[assigned] % len(_PATCH_PALETTE)
        ]
        self._patch_item = self._replace_item(
            self._patch_item, mesh, face_colors=colors
        )
        if self._original_item is not None:
            self._original_item.setVisible(False)

    def highlight_patches(
        self,
        mesh: trimesh.Trimesh,
        patch_face_ids: np.ndarray,
        selected: set[int],
    ) -> None:
        """Highlight ``selected`` patch ids; everything else goes dim.

        Passing an empty set restores the normal all-patches colouring.
        """
        if not selected:
            self.show_patches(mesh, patch_face_ids)
            return
        colors = np.tile((0.30, 0.31, 0.34, 1.0), (len(mesh.faces), 1))
        mask = np.isin(patch_face_ids, list(selected))
        colors[mask] = (1.0, 0.55, 0.10, 1.0)  # highlight orange
        self._patch_item = self._replace_item(
            self._patch_item, mesh, face_colors=colors
        )
        if self._original_item is not None:
            self._original_item.setVisible(False)

    def show_preview(self, mesh: trimesh.Trimesh) -> None:
        """Display a tessellated CAD preview on the preview layer."""
        self._preview_item = self._replace_item(
            self._preview_item,
            mesh,
            face_colors=np.tile((0.30, 0.55, 0.90, 1.0), (len(mesh.faces), 1)),
        )

    def set_layer_visible(self, layer: str, visible: bool) -> None:
        """Toggle a layer: 'original', 'patches' or 'preview'."""
        item = {
            "original": self._original_item,
            "patches": self._patch_item,
            "preview": self._preview_item,
        }.get(layer)
        if item is not None:
            item.setVisible(visible)

    def clear(self) -> None:
        """Remove all mesh layers (keeps the grid)."""
        for item in (self._original_item, self._patch_item, self._preview_item):
            if item is not None:
                self._view.removeItem(item)
        self._original_item = self._patch_item = self._preview_item = None

    # ----------------------------------------------------------- helpers

    def _replace_item(
        self,
        old: gl.GLMeshItem | None,
        mesh: trimesh.Trimesh,
        face_colors: np.ndarray,
    ) -> gl.GLMeshItem:
        if old is not None:
            self._view.removeItem(old)
        md = gl.MeshData(
            vertexes=np.asarray(mesh.vertices, dtype=np.float32),
            faces=np.asarray(mesh.faces, dtype=np.int32),
            faceColors=np.asarray(face_colors, dtype=np.float32),
        )
        item = gl.GLMeshItem(
            meshdata=md,
            smooth=False,
            drawEdges=False,
            shader="tab5TwoLight",
        )
        self._view.addItem(item)
        return item

    def _frame(self, mesh: trimesh.Trimesh) -> None:
        """Centre the camera on the mesh and zoom to fit."""
        center = mesh.bounds.mean(axis=0)
        diagonal = float(np.linalg.norm(mesh.extents))
        self._view.opts["center"] = _to_vector(center)
        self._view.setCameraPosition(distance=diagonal * 1.3)
        self._grid.resetTransform()
        self._grid.setSize(diagonal * 2, diagonal * 2)
        self._grid.setSpacing(diagonal / 20, diagonal / 20)
        self._grid.translate(center[0], center[1], float(mesh.bounds[0][2]))


def _to_vector(xyz: np.ndarray):
    from pyqtgraph import Vector

    return Vector(float(xyz[0]), float(xyz[1]), float(xyz[2]))
