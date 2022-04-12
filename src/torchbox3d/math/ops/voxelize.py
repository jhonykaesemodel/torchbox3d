"""Nearest neighbor methods."""

from enum import Enum, unique
from typing import Optional, Tuple

import torch
import torch.nn.functional as F
from torch import Tensor

from torchbox3d.math.crop import crop_points
from torchbox3d.math.ops.index import ravel_multi_index, unravel_index
from torchbox3d.math.ops.pool import voxel_pool
from torchbox3d.structures.ndgrid import VoxelGrid


@unique
class VoxelizationType(str, Enum):
    """The type of reduction performed during voxelization."""

    CONCATENATE = "CONCATENATE"
    POOL = "POOL"


@unique
class VoxelizationPoolingType(str, Enum):
    """The pooling method used for 'pooling' voxelization."""

    MEAN = "MEAN"


# @torch.jit.script
def voxelize_pool_kernel(
    xyz: Tensor,
    values: Tensor,
    voxel_grid: VoxelGrid,
    pool_mode: Optional[str] = "mean",
) -> Tuple[Tensor, Tensor, Tensor, Tensor]:
    """Cluster a point cloud into a grid of voxels.

    Args:
        xyz: (N,3) Coordinates (x,y,z).
        values: (N,F) Features associated with the points.
        voxel_grid: Voxel grid metadata.
        pool_mode: Pooling method for collisions.

    Returns:
        Voxel indices, values, counts, and cropping mask.
    """
    points_xyz, mask = crop_points(
        xyz,
        list(voxel_grid.min_range_m),
        list(voxel_grid.max_range_m),
    )
    values = values[mask]
    indices, mask = voxel_grid.transform_to_grid_coordinates(points_xyz)
    indices = indices[mask]

    counts = torch.ones_like(indices[:, 0])
    if pool_mode is not None and pool_mode == VoxelizationPoolingType.MEAN:
        indices, values, counts = voxel_pool(
            indices, values, list(voxel_grid.dims)
        )
    return indices.int(), values, counts, mask


# @torch.jit.script
def voxelize_concatenate_kernel(
    pos: Tensor, values: Tensor, voxel_grid: VoxelGrid, max_num_pts: int = 20
) -> Tuple[Tensor, Tensor, Tensor, Tensor]:
    """Places a set of points in R^3 into a voxel grid.

    NOTE: This will not pool the points that fall into a voxel bin. Instead,
    this function will concatenate the points until they exceed a maximium
    size defined by max_num_pts.

    Args:
        pos: (N,3) Coordinates (x,y,z).
        values: (N,F) Features associated with the points.
        voxel_grid: Voxel grid metadata.
        max_num_pts: Max number of points per bin location.

    Returns:
        Voxel indices, values, counts, and cropping mask.
    """
    pos, roi_mask = crop_points(
        pos,
        list(voxel_grid.min_range_m),
        list(voxel_grid.max_range_m),
    )

    # Filter the values.
    values = values[roi_mask]

    indices, mask = voxel_grid.transform_to_grid_coordinates(pos)
    indices = indices[mask]
    raveled_indices = ravel_multi_index(indices, list(voxel_grid.dims))

    # Find indices which make bucket indices contiguous.
    perm = torch.argsort(raveled_indices)
    indices = indices[perm]
    raveled_indices = raveled_indices[perm]
    values = values[perm]

    # Compute unique values, inverse, and counts.
    out: Tuple[Tensor, Tensor, Tensor] = torch.unique_consecutive(
        raveled_indices, return_inverse=True, return_counts=True
    )

    output, inverse_indices, counts = out

    # Initialize vectors at each voxel (max_num_pts,F).
    # Instead of applying an information destroying reduction,
    # we concatenate the features until we reach a maximum size.
    voxelized_values = torch.zeros(
        (len(output), max_num_pts, values.shape[-1])
    )

    # Concatenating collisions requires counting how many collisions there are.
    # This computes offsets for all of the collisions in a vectorized fashion.
    # offset = torch.zeros((len(counts) + 1))
    # offset[1:] = torch.cumsum(counts, dim=0)
    offset = F.pad(counts, pad=[1, 0], mode="constant", value=0.0).cumsum(
        dim=0
    )[inverse_indices]

    index = torch.arange(0, len(inverse_indices)) - offset
    is_valid = index < max_num_pts
    offset = offset[is_valid]
    voxelized_values[
        inverse_indices[is_valid], index[is_valid].long()
    ] = values[is_valid]
    voxelized_indices = unravel_index(output, list(voxel_grid.dims))
    return voxelized_indices.int(), voxelized_values, counts, roi_mask