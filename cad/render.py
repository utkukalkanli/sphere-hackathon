"""Render the 8 standard views (same cameras as the input images) into one contact sheet."""
import os
import tempfile
from pathlib import Path

import cadquery as cq
import vtk

RENDER_DIR = Path(os.environ.get("CAD_RENDER_DIR", tempfile.gettempdir())) / "cad_renders"

# name -> (direction camera looks along, view-up)
VIEWS = [
    ("front", (0, 1, 0), (0, 0, 1)),
    ("back", (0, -1, 0), (0, 0, 1)),
    ("left", (1, 0, 0), (0, 0, 1)),
    ("right", (-1, 0, 0), (0, 0, 1)),
    ("top", (0, 0, -1), (0, 1, 0)),
    ("bottom", (0, 0, 1), (0, -1, 0)),
    ("iso_front_right_top", (-1, 1, -1), (0, 0, 1)),
    ("iso_back_left_bottom", (1, -1, 1), (0, 0, 1)),
]


def render_views(shape, name, size=512, tol=0.05):
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    stl = RENDER_DIR / f"{name}.stl"
    cq.exporters.export(cq.Workplane(obj=shape), str(stl), tolerance=tol, angularTolerance=0.15)
    reader = vtk.vtkSTLReader()
    reader.SetFileName(str(stl))
    reader.Update()
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(reader.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(0.78, 0.6, 0.38)
    edges = vtk.vtkFeatureEdges()
    edges.SetInputConnection(reader.GetOutputPort())
    edges.BoundaryEdgesOff(); edges.NonManifoldEdgesOff(); edges.ManifoldEdgesOff()
    edges.FeatureEdgesOn(); edges.SetFeatureAngle(25)
    em = vtk.vtkPolyDataMapper(); em.SetInputConnection(edges.GetOutputPort()); em.ScalarVisibilityOff()
    ea = vtk.vtkActor(); ea.SetMapper(em); ea.GetProperty().SetColor(0, 0, 0); ea.GetProperty().SetLineWidth(1.5)

    win = vtk.vtkRenderWindow()
    win.SetOffScreenRendering(1)
    win.SetSize(size * 4, size * 2)
    b = reader.GetOutput().GetBounds()
    c = [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2]
    diag = ((b[1] - b[0]) ** 2 + (b[3] - b[2]) ** 2 + (b[5] - b[4]) ** 2) ** 0.5
    for i, (_, d, up) in enumerate(VIEWS):
        ren = vtk.vtkRenderer()
        ren.SetBackground(1, 1, 1)
        col, row = i % 4, 1 - i // 4
        ren.SetViewport(col / 4, row / 2, (col + 1) / 4, (row + 1) / 2)
        ren.AddActor(actor); ren.AddActor(ea)
        cam = ren.GetActiveCamera()
        cam.ParallelProjectionOn()
        n = sum(x * x for x in d) ** 0.5
        cam.SetFocalPoint(*c)
        cam.SetPosition(*[c[k] - d[k] / n * diag * 3 for k in range(3)])
        cam.SetViewUp(*up)
        ren.ResetCamera()
        cam.SetParallelScale(max(b[1] - b[0], b[3] - b[2], b[5] - b[4]) * 0.56)
        ren.ResetCameraClippingRange()
        win.AddRenderer(ren)
    win.Render()
    w2i = vtk.vtkWindowToImageFilter(); w2i.SetInput(win); w2i.Update()
    out = RENDER_DIR / f"{name}.png"
    wr = vtk.vtkPNGWriter(); wr.SetFileName(str(out)); wr.SetInputConnection(w2i.GetOutputPort()); wr.Write()
    return out
