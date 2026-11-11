import numpy as np

from agentic_cad import visual_hull as vh
from agentic_cad.views import IsoCamera, OrthoCamera



def test_cameras_put_the_box_where_the_views_show_it():
    dims = np.array([10.0, 20.0, 30.0])
    front = OrthoCamera("front", dims, (301, 101))  # X right, Z up
    assert np.allclose(front(np.array([[0, 0, 0], [10, 5, 30]])), [[0, 300], [100, 0]])
    back = OrthoCamera("back", dims, (301, 101))  # X left
    assert np.allclose(back(np.array([[0, 0, 0]])), [[100, 300]])
    iso = IsoCamera(vh.ISO_RIGHT, dims, 1000, 0.1)
    centre = iso(dims[None] / 2)[0]
    assert np.allclose(centre, [499.5, 499.5])
    # from (+X, -Y, +Z) the +X end of the box is to the right and the +Z end is higher up
    assert iso(np.array([[10, 10, 15]]))[0][0] > centre[0]
    assert iso(np.array([[5, 10, 30]]))[0][1] < centre[1]

