"""Shaders for rendering.

The goal of these functions is three-fold:
    1. Provide _fast_ operations that one would expect in a renderer.
    2. Support both cpu and gpu (by solely operating on PyTorch tensors).
    3. Fully differentiable operations.

Reference: https://en.wikipedia.org/wiki/Shader
"""

from typing import Tuple

import torch
import torch.nn.functional as F
from torch import Tensor

from torchbox3d.math.constants import EPS
from torchbox3d.math.kernels import gaussian_kernel
from torchbox3d.math.ops.index import (
    ogrid_sparse_neighborhoods,
    ravel_multi_index,
    unravel_index,
)


@torch.jit.script
def normal2(p1: Tensor, p2: Tensor, eps: float = EPS) -> Tensor:
    """Compute the 2D normal vector to the line defined by (p1,p2).

    Args:
        p1: (2,) Line start point.
        p2: (2,) Line end point.
        eps: Smoothing parameter to prevent divide by zero.

    Returns:
        The vector normal to the line defined by (p1,p2).
    """
    grad = p2 - p1
    du, dv = grad[0], grad[1]
    normal = torch.stack([-dv, du], dim=-1).float()
    unit_normal: Tensor = normal / torch.linalg.norm(normal).clamp(eps, None)
    return unit_normal


@torch.jit.script
def linear_interpolation(points: Tensor, num_samples: int) -> Tensor:
    """Linearly interpolate two points.

    Args:
        points: (N,2) Points to interpolate between.
        num_samples: The number of uniform samples between points.

    Returns:
        The interpolant sampled uniformly over the interval.
    """
    interp: Tensor = F.interpolate(
        points, size=num_samples, mode="linear", align_corners=True
    ).squeeze()
    return interp


@torch.jit.script
def clip_to_viewport(
    uv: Tensor, tex: Tensor, width_px: int, height_px: int
) -> Tuple[Tensor, Tensor, Tensor]:
    """Clip the points to the viewport.

    Reference: https://en.wikipedia.org/wiki/Viewport

    Args:
        uv: (N,2) UV coordinates.
            Reference: https://en.wikipedia.org/wiki/UV_mapping
        tex: (N,3) Texture intensity values.
        width_px: Width of the viewport in pixels.
        height_px: Height of the viewport in pixels.

    Returns:
        The clipped UV coordinates and texture, and the boolean validity mask.
    """
    size = torch.as_tensor([width_px, height_px], device=uv.device)
    is_inside_viewport = torch.logical_and(uv >= 0, uv < size).all(dim=-1)
    return uv[is_inside_viewport], tex[is_inside_viewport], is_inside_viewport


@torch.jit.script
def blend(
    foreground_pixels: Tensor, background_pixels: Tensor, alpha: float
) -> Tensor:
    """Blend the foreground and background pixels.

    Args:
        foreground_pixels: (...,3,H,W) Source RGB image.
        background_pixels: (...,3,H,W) Target RGB image.
        alpha: Alpha blending coefficient.

    Returns:
        (...,3,H,W) The blended pixels.

    Raises:
        ValueError: If the foreground and background pixels
            do not have the same shape.
    """
    if foreground_pixels.shape != background_pixels.shape:
        raise ValueError(
            "Foreground pixels and background pixels must have the same shape!"
        )
    pix_blended = foreground_pixels * alpha + background_pixels * (1 - alpha)
    return pix_blended


@torch.jit.script
def circles(
    uvz: Tensor,
    tex: Tensor,
    img: Tensor,
    radius: int = 10,
    antialias: bool = True,
) -> Tensor:
    """Draw circles on a 3 channel image.

    Image plane coordinate system:
      (0,0)----------+v
        |
        |
        |
        |
        +u

    Args:
        uvz: (N,3) Texture coordinates.
        tex: (N,3) Texture pixel intensities.
        img: (...,H,W,3) Image.
        radius: Radius of the circle.
        antialias: Boolean flag to enable anti-aliasing.

    Returns:
        (...,H,W,3) Image with circles overlaid.
    """
    uv = uvz[..., :2].flatten(0, -2)
    ogrid_uv = ogrid_sparse_neighborhoods(uv, [radius, radius])
    tex = tex.repeat_interleave(int(radius**2), dim=0)

    if antialias:
        mu = uv.repeat_interleave(int(radius**2), 0)
        sigma = torch.ones_like(mu[:, 0:1])
        alpha = gaussian_kernel(ogrid_uv, mu, sigma).prod(dim=-1, keepdim=True)
        tex *= alpha

    H = img.shape[-2]
    W = img.shape[-1]
    ogrid_uv, tex, _ = clip_to_viewport(ogrid_uv, tex, W, H)

    raveled_indices = ravel_multi_index(ogrid_uv, [W, H])
    out: Tuple[Tensor, Tensor] = torch.unique(
        raveled_indices, return_inverse=True
    )
    raveled_indices, inverse_indices = out

    index = inverse_indices[:, None].repeat(1, tex.shape[1])
    tex = torch.scatter_reduce(tex, dim=0, index=index, reduce="amax")
    unraveled_coords = unravel_index(raveled_indices, [W, H])
    u, v = unraveled_coords[:, 0], unraveled_coords[:, 1]
    blended_pixels = blend(
        tex.flatten(0, -2).mT, img.view(-1, H, W)[:, u, v], alpha=1.0
    )
    img.view(-1, H, W)[:, u, v] = blended_pixels
    return img


@torch.jit.script
def line2(
    p1: Tensor,
    p2: Tensor,
    color: Tensor,
    img: Tensor,
    width_px: int = 5,
    width_density: int = 5,
    length_density: int = 1024,
    eps: float = EPS,
) -> Tensor:
    """Draw a line on the image.

    Steps:
        1. Compute the unit normal to _both_ endpoints, p1 and p2.
        2. Interpolate between the normal and negated normal at both endpoints.
        3. Scale the interpolated intervals by the width in pixels.
        4. Interpolate the orthogonal "widths" along the line segment.

    Image plane coordinate system:
      (0,0)----------+v
        |
        |
        |
        |
        +u

    Args:
        p1: (2,) Line starting point (uv coordinates).
        p2: (2,) Line ending point (uv coordinates).
        color: (3,) 3-channel color.
        img: (3,H,W) 3-channel image.
        width_px: Thickness of the line (in pixels).
        width_density: Multiplier for increasing the pixel density
            of the line width. Increasing this value will fill in any "holes"
            between values at the cost of computation.
        length_density: Multiplier for increasing the pixel density
            of the line length. Increasing this value will fill in any "holes"
            between values at the cost of computation.
        eps: Smoothing parameter to prevent division-by-zero.

    Returns:
        The image with a line drawn from p1 to p2.
    """
    points = torch.stack([p1, p2], dim=0)

    line = p2 - p1
    unit_line: Tensor = line / torch.linalg.norm(line).clamp(eps, None)

    # Adjust endpoints to properly enclose meeting endpoints.
    points[0] -= unit_line * width_px
    points[1] += unit_line * width_px

    unit_normal = normal2(p1, p2)
    offsets = torch.stack((-unit_normal, unit_normal), dim=0)
    offsets = (offsets * width_px).round()
    offsets = offsets.T[None]

    # Compute the "width" of the two endpoints of the line.
    width_uv = linear_interpolation(
        offsets, num_samples=(width_density * width_px)
    )
    width_uv = width_uv[..., None] + points.T[:, None]
    width_uv.view(-1, 2)[:, 0].clamp_(0, img.shape[-2] - 1)
    width_uv.view(-1, 2)[:, 1].clamp_(0, img.shape[-1] - 1)

    # Interpolate the two orthogonal endpoint vectors across the line segment.
    line_uv = (
        linear_interpolation(width_uv, num_samples=length_density)
        .view(2, -1)
        .round()
        .long()
    ).transpose(0, 1)
    colors = color[:, None].repeat(1, len(line_uv))
    raveled_indices = ravel_multi_index(line_uv, list(img.shape[1:]))
    img.reshape(3, -1).scatter_(
        dim=-1, index=raveled_indices[None].repeat(3, 1), src=colors
    )
    return img


@torch.jit.script
def polygon(
    vertices: Tensor,
    edge_indices: Tensor,
    colors: Tensor,
    img: Tensor,
    width_px: int = 3,
) -> None:
    """Draw a polygon on the image.

    Image plane coordinate system:
      (0,0)----------+v
        |
        |
        |
        |
        +u

    NOTE: Vertices are in UV coordinates. The edge indices indicate which
        vertex pairs form edges.

    Example:
        vertices = torch.as_tensor([[1, 1], [2, 2], [3, 3]])
        edge_indices = torch.as_tensor([[0, 1], [2, 0]])
        edges = vertices[edge_indices]

        # Edges consist of [(1, 1), (2, 2)] and [(3, 3), (1, 1)].

    Args:
        vertices: (N,2) Vertices of the polygon.
        edge_indices: (K,2) Vertex indices which construct an edge.
        colors: (len(edges),3) 3-channel colors of the polygon edges.
        img: (3,H,W) 3-channel image.
        width_px: Width of the polygon lines.
    """
    num_edges = len(edge_indices)
    edges = vertices[edge_indices]
    for i in range(num_edges):
        color = colors[i]
        edge = edges[i]
        line2(edge[0], edge[1], color, img, width_px=width_px)
