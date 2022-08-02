from typing import Optional, Tuple, Union

import numpy as np
import pyvista as pv
from pyvista import PolyData

try:
    from typing import Literal
except ImportError:
    from typing_extensions import Literal

from ..utilities import add_model_labels, collect_model, merge_models


def construct_line(
    start_point: Union[list, tuple, np.ndarray],
    end_point: Union[list, tuple, np.ndarray],
    style: Literal["line", "arrow"] = "line",
    key_added: str = "line",
    label: str = "line",
    color: str = "gainsboro",
) -> PolyData:
    """
    Create a 3D line model.

    Args:
        start_point: Start location in [x, y, z] of the line.
        end_point: End location in [x, y, z] of the line.
        style: Line style. According to whether there is an arrow, it is divided into `'line'` and `'arrow'`.
        key_added: The key under which to add the labels.
        label: The label of lines model.
        color: Color to use for plotting model.

    Returns:
        Line model.
    """

    if style == "line":
        model = pv.Line(pointa=start_point, pointb=end_point, resolution=1)
    elif style == "arrow":
        model = pv.Arrow(
            start=start_point,
            direction=end_point - start_point,
            scale="auto",
            tip_length=0.1,
            tip_radius=0.02,
            shaft_radius=0.01,
        )
    else:
        raise ValueError("`style` value is wrong.")

    add_model_labels(
        model=model,
        key_added=key_added,
        labels=np.asarray([label] * model.n_points),
        where="point_data",
        colormap=color,
        inplace=True,
    )

    return model


def construct_polyline(
    points: Union[list, np.ndarray],
    style: Literal["line", "arrow"] = "line",
    key_added: str = "line",
    label: str = "polyline",
    color: str = "gainsboro",
) -> PolyData:
    """
    Create a 3D polyline model.

    Args:
        points: List of points defining a broken line.
        style: Line style. According to whether there is an arrow, it is divided into `'line'` and `'arrow'`.
        key_added: The key under which to add the labels.
        label: The label of lines model.
        color: Color to use for plotting model.

    Returns:
        Line mesh.
    """

    if style == "line":
        model = pv.MultipleLines(points=points)
        add_model_labels(
            model=model,
            key_added=key_added,
            labels=np.asarray([label] * model.n_points),
            where="point_data",
            colormap=color,
            inplace=True,
        )
    elif style == "arrow":
        arrows = [
            construct_line(
                start_point=start_point, end_point=end_point, style=style, key_added=key_added, label=label, color=color
            )
            for start_point, end_point in zip(points[:-1], points[1:])
        ]
        model = merge_models(models=arrows)
    else:
        raise ValueError("`style` value is wrong.")

    return model


def construct_tree(
    points: np.ndarray,
    edges: np.ndarray,
    style: Literal["line", "arrow"] = "line",
    key_added: str = "tree",
    label: str = "tree",
    color: str = "gainsboro",
) -> PolyData:
    """
    Create a 3D tree model of multiple discontinuous line segments.

    Args:
        points: List of points defining a tree.
        edges: The edges between points in the tree.
        style: Line style. According to whether there is an arrow, it is divided into `'line'` and `'arrow'`.
        key_added: The key under which to add the labels.
        label: The label of tree model.
        color: Color to use for plotting model.

    Returns:
        Tree model.
    """

    if style == "line":
        padding = np.array([2] * edges.shape[0], int)
        edges_w_padding = np.vstack((padding, edges.T)).T
        model = pv.PolyData(points, edges_w_padding)
        add_model_labels(
            model=model,
            key_added=key_added,
            labels=np.asarray([label] * model.n_points),
            where="point_data",
            colormap=color,
            inplace=True,
        )
    elif style == "arrow":
        arrows = [
            construct_line(
                start_point=points[i[0]],
                end_point=points[i[1]],
                style=style,
                key_added=key_added,
                label=label,
                color=color,
            )
            for i in edges
        ]
        model = merge_models(models=arrows)
    else:
        raise ValueError("`style` value is wrong.")

    return model


def construct_align_lines(
    model1_points: np.ndarray,
    model2_points: np.ndarray,
    style: Literal["line", "arrow"] = "line",
    key_added: str = "check_alignment",
    label: Union[str, list] = "align_mapping",
    color: str = "gainsboro",
) -> PolyData:
    """
    Construct alignment lines between models after model alignment.

    Args:
        model1_points: Start location in model1 of the line.
        model2_points: End location in model2 of the line.
        style: Line style. According to whether there is an arrow, it is divided into `'line'` and `'arrow'`.
        key_added: The key under which to add the labels.
        label: The label of alignment lines model.
        color: Color to use for plotting model.

    Returns:
        Alignment lines model.
    """
    assert model1_points.shape == model2_points.shape, "model1_points.shape is not equal to model2_points.shape"
    labels = [label] * model1_points.shape[0] if isinstance(label, str) else label

    lines_model = []
    for m1p, m2p, l in zip(model1_points, model2_points, labels):
        line = construct_line(start_point=m1p, end_point=m2p, style=style, key_added=key_added, label=l, color=color)
        lines_model.append(line)

    lines_model = merge_models(lines_model)
    return lines_model


def construct_axis_line(
    axis_points: np.ndarray,
    style: Literal["line", "arrow"] = "line",
    key_added: str = "axis",
    label: str = "axis_line",
    color: str = "gainsboro",
) -> PolyData:
    """
    Construct axis line.

    Args:
        axis_points:  List of points defining an axis.
        style: Line style. According to whether there is an arrow, it is divided into `'line'` and `'arrow'`.
        key_added: The key under which to add the labels.
        label: The label of axis line model.
        color: Color to use for plotting model.

    Returns:
        Axis line model.
    """

    axis_line = construct_polyline(points=axis_points, style=style, key_added=key_added, label=label, color=color)

    return axis_line
